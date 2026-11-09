"""IA propia: librería de aprendizaje, registro de modelos versionados y panel «Cerebro»."""

from __future__ import annotations

import random
import time

import psycopg
import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session, sessionmaker

from orkesta.brain import ml, registry
from tests.conftest import register
from tests.test_admin import PASSWORD

AREAS = {
    "compras": ["compras", "abastecimiento", "procurement", "purchasing", "adquisiciones"],
    "tecnologia": ["sistemas", "tecnologia", "it", "infraestructura", "software"],
    "rrhh": ["recursos humanos", "talento humano", "gestion humana", "people", "nomina"],
    "finanzas": ["finanzas", "contabilidad", "tesoreria", "finance", "controller"],
}
LEVELS = ["Gerente de", "Jefe de", "Director de", "Coordinador de", "Head of", "Analista de"]


def _titles(n: int, seed: int = 1) -> list[ml.Example]:
    rng = random.Random(seed)  # noqa: S311
    out = []
    for _ in range(n):
        area = rng.choice(list(AREAS))
        title = f"{rng.choice(LEVELS)} {rng.choice(AREAS[area])} {rng.choice(['', 'regional', 'senior', 'LATAM'])}"
        if rng.random() < 0.15:  # errores de tipeo: «Gerentte de Comprsa»
            i = rng.randrange(len(title) - 1)
            title = title[:i] + title[i + 1 :]
        out.append(ml.Example(key=ml.fold(title), x=ml.text_features(title), y=area, raw=title))
    return out


def test_ml_learns_titles_with_typos_and_roundtrips() -> None:
    assert ml.fold("Jefe de Operación!") == "jefe de operacion"
    data = _titles(900)
    train, test = ml.split(data)
    assert test and train and not {e.key for e in train} & {e.key for e in test}
    for model in (ml.NaiveBayes(), ml.LogisticRegression(epochs=6)):
        model.fit([e.x for e in train], [e.y for e in train])
        metrics = ml.evaluate(model, test)
        assert metrics.accuracy > 0.9, (model.kind, metrics.accuracy)
        clone = ml.loads(ml.dumps(model))
        assert ml.predict(clone, ml.text_features("Gerente de Abastecimeinto"))[0] == "compras"
        a = ml.predict(clone, ml.text_features("Talento Humano"))
        b = ml.predict(model, ml.text_features("Talento Humano"))
        assert a[0] == b[0] and abs(a[1] - b[1]) < 1e-3
        threshold = ml.best_threshold(model, test, 0.95)
        sure = ml.evaluate(model, test, threshold).confident
        assert sure["accuracy"] >= 0.95 and sure["coverage"] > 0.5


def _task(examples: list[ml.Example], baseline: float) -> registry.Task:
    def rules(session: Session, test: list[ml.Example]) -> ml.Metrics:
        rng = random.Random(3)  # noqa: S311
        return ml.score([(e.y, e.y if rng.random() < baseline else None, 1.0) for e in test])

    return registry.Task(name="prueba", label="Prueba", description="", step=1,
                         build=lambda s: list(examples), baseline=rules, min_examples=50)  # fmt: skip


def test_registry_trains_promotes_and_rolls_back(session_factory: sessionmaker[Session]) -> None:
    data = _titles(700)
    registry.TASKS["prueba"] = _task(data, baseline=0.5)
    try:
        with session_factory.begin() as session:
            first = registry.train(session, "prueba")
            assert first.active and first.version == 1
            assert first.metrics["accuracy"] > first.baseline["accuracy"]
            pred = registry.predict(session, "prueba", ml.text_features("Jefe de Tesorería"))
            assert pred and pred.label == "finanzas" and pred.version == 1
        # Datos peores (etiquetas al azar): la versión nueva no reemplaza a la buena.
        rng = random.Random(9)  # noqa: S311
        noisy = [ml.Example(e.key, e.x, rng.choice(list(AREAS))) for e in data]
        registry.TASKS["prueba"] = _task(noisy, baseline=0.0)
        with session_factory.begin() as session:
            second = registry.train(session, "prueba")
            assert not second.active and "menos que la versión activa" in second.notes
            assert registry.active_row(session, "prueba").version == 1
            registry.activate(session, "prueba", 2)
            assert registry.active_row(session, "prueba").version == 2
        # Nunca se activa un modelo que acierta menos que las reglas.
        registry.TASKS["prueba"] = _task(noisy, baseline=1.0)
        with session_factory.begin() as session:
            assert not registry.train(session, "prueba").active
        # El reentrenamiento nocturno no repite si los datos no cambiaron.
        registry.TASKS["prueba"] = _task(data, baseline=0.5)
        with session_factory.begin() as session:
            registry.activate(session, "prueba", 1)
        assert "prueba" not in registry.train_due(session_factory, force=True)
    finally:
        registry.TASKS.pop("prueba", None)


def test_brain_panel(client: TestClient, database: tuple[str, str],
                     session_factory: sessionmaker[Session]) -> None:  # fmt: skip
    register(client, "jenn@orkesta.co")
    with psycopg.connect(database[0], autocommit=True) as conn:
        conn.execute("UPDATE orkesta.users SET platform_role = 'owner'")
    r = client.post("/api/admin/unlock", json={"email": "jenn@orkesta.co", "password": PASSWORD})
    client.headers["X-Admin-Token"] = r.json()["token"]
    data = client.get("/api/admin/brain").json()
    names = [t["name"] for t in data["tasks"]]
    assert names[:6] == [
        "cargos",
        "cargos_nivel",
        "respuestas",
        "formato_correo",
        "puntaje",
        "redaccion",
    ]
    cargos = data["tasks"][0]
    assert (
        cargos["method"] == "Reglas fijas" and cargos["status"][0]["label"] == "Cargos en el índice"
    )

    registry.TASKS["prueba"] = _task(_titles(400), baseline=0.4)
    try:
        assert client.post("/api/admin/brain/prueba/train").status_code == 202
        for _ in range(100):
            if registry.RUNNING["prueba"]["state"] != "running":
                break
            time.sleep(0.1)
        assert registry.RUNNING["prueba"]["state"] == "done", registry.RUNNING["prueba"]
        task = next(
            t for t in client.get("/api/admin/brain").json()["tasks"] if t["name"] == "prueba"
        )
        assert task["method"] == "Modelo propio v1" and task["active"]["accuracy"] > 0.9
        assert task["active"]["confident"]["coverage"] > 0
    finally:
        registry.TASKS.pop("prueba", None)
        registry.RUNNING.pop("prueba", None)


def _person(i: int, title: str) -> dict:
    from orkesta.engine.taxonomy.titles import classify_title

    level, area = classify_title(title)
    return {"key": f"n:CO|empresa{i}|persona {i}", "country": "CO", "full_name": f"Persona {i}",
            "first_name": "Persona", "last_name": str(i), "title": title, "level": level,
            "area": area, "company": f"Empresa {i}", "company_key": f"empresa{i}",
            "domain": None, "location": "", "linkedin": None, "email": None,
            "email_status": None}  # fmt: skip


def test_titles_model_learns_relabels_and_teaches(
    client: TestClient, database: tuple[str, str], session_factory: sessionmaker[Session]
) -> None:
    from orkesta.brain import titles
    from orkesta.engine.taxonomy.titles import classify_rules, classify_title
    from orkesta.research import people

    base = ["Gerente de Compras", "Jefe de Sistemas", "Director Comercial", "Analista de Datos",
            "Gerente de Siniestros", "Jefe de Facilities", "Gerente General", "Docente"]  # fmt: skip
    people.upsert(session_factory, [_person(i, t) for i, t in enumerate(base * 3)])
    assert classify_rules("Gerente de Siniestros") == ("gerente", None)

    register(client, "jenn@orkesta.co")
    with psycopg.connect(database[0], autocommit=True) as conn:
        conn.execute("UPDATE orkesta.users SET platform_role = 'owner'")
    r = client.post("/api/admin/unlock", json={"email": "jenn@orkesta.co", "password": PASSWORD})
    client.headers["X-Admin-Token"] = r.json()["token"]
    doubts = client.get("/api/admin/brain/cargos/doubts").json()["items"]
    assert {"title": "Gerente de Siniestros", "count": 3} in doubts

    import orkesta.brain.capabilities  # noqa: F401

    with session_factory.begin() as session:
        row = registry.train(session, "cargos")
        assert row.active and row.algorithm == "lexicon"
        assert row.metrics["accuracy"] > row.baseline["accuracy"]
        extra = row.metrics["extra"]
        assert extra["coverage_model"] > extra["coverage_rules"]
    # Las reglas siguen igual; el modelo completa lo que no reconocen.
    assert classify_title("Gerente de Siniestros") == ("gerente", "finanzas")
    assert classify_title("Gerente de Compras") == ("gerente", "compras")
    assert classify_title("Docente")[1] is None
    assert classify_title("Cibersecurity Manager")[1] == "seguridad"  # error de tipeo
    with psycopg.connect(database[0], autocommit=True) as conn:
        areas = dict(conn.execute("SELECT title, area FROM orkesta.people_index").fetchall())
    assert (
        areas["Gerente de Siniestros"] == "finanzas"
        and areas["Jefe de Facilities"] == "operaciones"
    )

    tried = client.post("/api/admin/brain/cargos/try", json={"title": "Jefe de Siniestros"}).json()
    assert tried["rules"]["area"] is None and tried["final"]["area"] == "finanzas"
    assert "siniestros" in tried["cargos"]["evidence"]
    # Enseñar un cargo: queda como ejemplo humano y deja de aparecer entre las dudas.
    assert client.post("/api/admin/brain/cargos/teach",
                       json={"title": "Docente", "area": None}).status_code == 201  # fmt: skip
    taught = client.post("/api/admin/brain/cargos/teach", json={"title": "Gerente de Planta Piloto",
                                                                  "area": "operaciones"})  # fmt: skip
    assert taught.status_code == 201
    assert client.post("/api/admin/brain/cargos/teach",
                       json={"title": "X", "area": "inventada"}).status_code == 422  # fmt: skip
    with session_factory() as session:
        assert titles._feedback(session, "cargos")["gerente de planta piloto"][1] == "operaciones"
    panel = client.get("/api/admin/brain").json()["tasks"]
    cargos = next(t for t in panel if t["name"] == "cargos")
    assert cargos["method"] == "Modelo propio v1" and cargos["feedback"] == 2
    assert cargos["active"]["extra"]["coverage_model"] > 0


def test_replies_model_decides_asks_and_learns_privately(
    client: TestClient, session_factory: sessionmaker[Session],
    monkeypatch: pytest.MonkeyPatch, database: tuple[str, str],
) -> None:  # fmt: skip
    import imaplib
    import smtplib
    from datetime import timedelta
    from email import message_from_bytes

    import orkesta.brain.capabilities  # noqa: F401
    from orkesta.brain import replies
    from orkesta.campaigns import service as svc
    from tests.test_campaigns import _contacts, _flush, _rewind
    from tests.test_mail_providers import STEPS, FakeIMAP, FakeSMTP

    # Sin modelo activo, todo sigue como antes (solo listas de frases).
    assert replies.decide_with(None, "Lo pensaré y te aviso.").sure
    with session_factory.begin() as session:
        row = registry.train(session, "respuestas")
        assert row.active and row.algorithm == "ensemble"
        extra = row.metrics["extra"]
        assert extra["eval_model"] > extra["eval_rules"] and extra["eval_decides"] > 0.5
    with session_factory() as session:
        assert replies.decide(session, "Por favor sácame de tu lista").category == "baja"
        sure = replies.decide(session, "Agendemos una reunión el martes a las 10")
        assert sure.category == "reunion" and sure.sure
        assert not replies.decide(session, "Lo pensaré y te aviso.").sure

    monkeypatch.setattr(smtplib, "SMTP", FakeSMTP)
    monkeypatch.setattr(imaplib, "IMAP4_SSL", FakeIMAP)
    monkeypatch.setattr(svc, "_in_window", lambda _s: True)
    monkeypatch.setattr(svc, "_gap", lambda *a: timedelta(0))
    FakeSMTP.sent, FakeIMAP.inbox, FakeSMTP.fail = [], [], False
    register(client, "ana@lacteos.co")
    client.post("/api/mailboxes/smtp", json={"email": "ana@lacteos.co", "password": "clave-app",
                                             "smtp_host": "smtp.lacteos.co"})  # fmt: skip
    ids = _contacts(client)
    campaign = client.post("/api/campaigns", json={"name": "Dudas"}).json()
    client.patch(f"/api/campaigns/{campaign['id']}",
                 json={"steps": STEPS, "audience": {"contact_ids": [ids[0]]}})  # fmt: skip
    client.post(f"/api/campaigns/{campaign['id']}/launch")
    _flush(session_factory)
    rfc = message_from_bytes(FakeSMTP.sent[0])["Message-ID"]
    FakeIMAP.inbox.append(f"From: Laura <laura@bancoandino.co>\r\nSubject: Re: Hola\r\n"
                          f"In-Reply-To: {rfc}\r\n\r\nLo pensaré y te aviso.".encode())  # fmt: skip
    _rewind(database)
    _flush(session_factory)
    item = client.get(f"/api/campaigns/{campaign['id']}/recipients").json()["items"][0]
    # La IA dudó: guarda su apuesta, no crea oportunidad y pregunta.
    assert item["status"] == "replied" and item["reply_review"] and item["deal_id"] is None
    titles = [n["title"] for n in client.get("/api/notifications").json()["items"]]
    assert any(t.startswith("¿Qué quiso decir") for t in titles)
    timeline = client.get("/api/crm/timeline", params={"contacto": item["contact_id"]}).json()
    assert any("Confirmar qué quiso decir" in a["body"] for a in timeline)
    fixed = client.post(f"/api/campaigns/{campaign['id']}/recipients/{item['id']}/category",
                        json={"category": "no_ahora"})  # fmt: skip
    assert fixed.status_code == 200
    item = client.get(f"/api/campaigns/{campaign['id']}/recipients").json()["items"][0]
    assert item["reply_category"] == "no_ahora" and not item["reply_review"]

    # Privacidad: una palabra propia de una sola empresa nunca entra al modelo.
    with session_factory.begin() as session:
        registry.record_feedback(session, "respuestas", "interesado",
                                 text="Zorbatronix nos encanta, sigamos")  # fmt: skip
        examples = replies.build(session)
    learned = [ex for ex in examples if ex.key.startswith("f:")]
    assert {ex.y for ex in learned} == {"no_ahora", "interesado"}
    assert not any("zorbatronix" in k for ex in learned for k in ex.x)
    # Con una sola empresa, de sus correcciones solo entra el vocabulario común del curado.
    from orkesta.brain.data import replies_seed

    common = {k for text, _ in replies_seed.examples() for k in replies.features(text)}
    assert learned and all(k in common for ex in learned for k in ex.x)
