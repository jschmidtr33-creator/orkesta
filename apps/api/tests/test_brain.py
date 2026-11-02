"""IA propia: librería de aprendizaje, registro de modelos versionados y panel «Cerebro»."""

from __future__ import annotations

import random
import time

import psycopg
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
        assert registry.train_due(session_factory, force=True) == []
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
    assert names[:5] == ["cargos", "respuestas", "formato_correo", "puntaje", "redaccion"]
    cargos = data["tasks"][0]
    assert (
        cargos["method"] == "Reglas fijas" and cargos["status"][0]["label"] == "Cargos en el índice"
    )
    blocked = client.post("/api/admin/brain/cargos/train")
    assert blocked.status_code == 409 and "paso 2" in blocked.json()["detail"]

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
