"""Concertino: responde con la ficha del producto y el oficio, solo cuando se le pide, aprende
de lo que hace el vendedor y de las campañas, y respeta la privacidad y los planes."""

from __future__ import annotations

import uuid

from fastapi.testclient import TestClient
from sqlalchemy import select, update
from sqlalchemy.orm import Session, sessionmaker

from orkesta.brain import capabilities, registry
from orkesta.campaigns.models import Recipient
from orkesta.concertino.models import AdviceCase
from orkesta.core.db import set_context
from orkesta.platform.models import Tenant
from tests.conftest import register
from tests.test_supervision import _join, _pro

FICHA = [
    ("producto", "Plataforma de prospección B2B para Latinoamérica", "", {}),
    ("publico", "Empresas medianas de Latinoamérica; decide la gerencia comercial", "", {}),
    ("problema", "El equipo comercial pierde horas buscando contactos", "", {}),
    ("valor", "Triplicar las reuniones agendadas sin sumar personas", "", {}),
    ("prueba", "Una empresa de software pasó de 4 a 12 reuniones al mes en 60 días", "", {}),
    ("diferencial", "Datos de Latinoamérica verificados", "", {}),
    ("precio", "Desde 99 dólares al mes por usuario", "", {}),
    ("competidor", "Apollo", "tenemos mejores datos de Latinoamérica y soporte en español", {}),
]


def _ficha(client: TestClient) -> None:
    for kind, title, detail, meta in FICHA:
        r = client.post("/api/concertino/facts", json={"kind": kind, "title": title,
                                                       "detail": detail, "meta": meta})  # fmt: skip
        assert r.status_code == 201, r.text
        assert r.json()["status"] == "confirmado"


def test_answers_objections_with_the_product_sheet_and_learns(
    client: TestClient, session_factory: sessionmaker[Session]
) -> None:
    register(client, "ana@lacteos.co")
    _pro(session_factory)
    sheet = client.get("/api/concertino/facts").json()
    assert sheet["complete"] == 0 and sheet["interview"][0]["kind"] == "producto"
    _ficha(client)
    assert client.get("/api/concertino/facts").json()["interview"] == []

    contact = client.post("/api/contacts", json={
        "first_name": "Laura", "last_name": "Gómez", "email": "laura@bancoandino.co",
        "company": "Banco Andino", "job_title": "Gerente de Compras"}).json()  # fmt: skip
    r = client.post("/api/concertino/ask", json={"question": "Me dijo que es muy caro",
                                                  "contact_id": contact["id"]})  # fmt: skip
    assert r.status_code == 200, r.text
    advice = r.json()
    assert advice["topic"] == "objecion" and advice["objection"] == "precio"
    assert len(advice["options"]) >= 2 and advice["options"][0]["text"].startswith("Hola, Laura:")
    joined = " ".join(o["text"] for o in advice["options"])
    assert "triplicar las reuniones" in joined and "de 4 a 12 reuniones" in joined
    assert not advice["missing"] and "Ficha del producto" in advice["sources"]
    # Gerente: trato de tú por defecto; con usted cambia todo el texto.
    assert "[" not in joined and "¿Te " in joined
    formal = client.post("/api/concertino/ask", json={"question": "Me dijo que es muy caro",
                                                       "trato": "usted"}).json()  # fmt: skip
    assert "¿Le " in " ".join(o["text"] for o in formal["options"])

    # Lo que hace el vendedor queda como caso y, con resultados, ordena los enfoques.
    for _ in range(3):
        case = client.post("/api/concertino/ask", json={"question": "Es caro para nosotros"}).json()
        chosen = "alcance"
        assert client.post(f"/api/concertino/cases/{case['case_id']}", json={
            "action": "usada", "chosen": chosen, "outcome": "reunion"}).json()["ok"]  # fmt: skip
    again = client.post("/api/concertino/ask", json={"question": "Le parece costoso"}).json()
    assert again["options"][0]["code"] == "alcance" and "3 de 3" in again["options"][0]["record"]

    # Una corrección queda para el Cerebro.
    fixed = client.post(
        f"/api/concertino/cases/{again['case_id']}", json={"objection": "competencia"}
    )
    assert fixed.status_code == 200
    assert capabilities and registry.TASKS["concertino_objecion"].build is not None


def test_topics_profiles_accounts_and_plan(
    client: TestClient, session_factory: sessionmaker[Session]
) -> None:
    register(client, "ana@lacteos.co")
    profile = client.post(
        "/api/concertino/ask", json={"question": "¿Cómo le hablo a un gerente de compras?"}
    )
    assert profile.json()["summary"].startswith("Esa consulta es parte del plan Pro")
    _pro(session_factory)
    _ficha(client)
    profile = client.post(
        "/api/concertino/ask", json={"question": "¿Cómo le hablo a un gerente de compras?"}
    ).json()
    assert profile["topic"] == "perfil"
    titles = [s["title"] for s in profile["sections"]]
    assert "Qué le importa" in titles and "Cómo escribirle" in titles and profile["options"]

    for question, topic in [("¿Cómo presento el precio en la propuesta?", "propuesta"),
                            ("Tengo reunión mañana con el gerente general", "reunion"),
                            ("¿A qué sector le apunto este mes?", "estrategia"),
                            ("¿Por qué mi campaña no tiene respuestas?", "campana"),
                            ("¿Qué nos diferencia?", "producto")]:  # fmt: skip
        got = client.post("/api/concertino/ask", json={"question": question}).json()
        assert got["topic"] == topic, (question, got["topic"])
        assert got["summary"]

    contact = client.post("/api/contacts", json={"first_name": "Pedro", "last_name": "Ruiz",
                          "company": "Banco Andino", "email": "pedro@bancoandino.co"}).json()  # fmt: skip
    deal = client.post(
        "/api/crm/deals", json={"title": "Banco Andino", "contact_id": contact["id"]}
    )
    assert deal.status_code in (200, 201), deal.text
    account = client.post("/api/concertino/ask", json={"question": "", "mode": "cuenta",
                                                        "deal_id": deal.json()["id"]}).json()  # fmt: skip
    assert (
        account["topic"] == "cuenta"
        and account["sections"][0]["title"] == "Lo que veo en la cuenta"
    )
    history = client.get("/api/concertino/history").json()
    assert len(history) >= 7


def test_sheet_permissions_privacy_and_learning(
    client: TestClient, session_factory: sessionmaker[Session]
) -> None:
    me = register(client, "ana@lacteos.co")
    tenant_id = uuid.UUID(me["workspace"]["id"])
    _pro(session_factory)
    luis = _join(client, "luis@lacteos.co", "vendedor", "Luis")
    proposed = luis.post("/api/concertino/facts", json={"kind": "valor", "title": "Ahorra tiempo"})
    assert proposed.status_code == 201 and proposed.json()["status"] == "aprendido"
    denied = luis.patch(
        f"/api/concertino/facts/{proposed.json()['id']}", json={"status": "confirmado"}
    )
    assert denied.status_code == 403
    ok = client.patch(
        f"/api/concertino/facts/{proposed.json()['id']}", json={"status": "confirmado"}
    )
    assert ok.json()["status"] == "confirmado"

    # Las consultas son privadas de quien pregunta.
    case = client.post(
        "/api/concertino/ask", json={"question": "Me dijo que ya tienen proveedor"}
    ).json()
    assert case["objection"] == "proveedor"
    assert (
        luis.post(f"/api/concertino/cases/{case['case_id']}", json={"action": "usada"}).status_code
        == 404
    )
    assert luis.get("/api/concertino/history").json() == []

    # Aprende de las respuestas de las campañas: quedan pendientes de confirmar.
    campaign = client.post("/api/campaigns", json={"name": "Prueba"}).json()
    snippets = ["Gracias, pero es muy caro para nosotros.", "Me parece costoso, no hay presupuesto.",
                "El precio es muy alto. ¿Tienen integración con SAP?",
                "Interesante. ¿Tienen integración con SAP Business One?"]  # fmt: skip
    with session_factory.begin() as session:
        set_context(session, tenant_id=tenant_id)
        for i, text in enumerate(snippets):
            c = client.post("/api/contacts", json={"first_name": f"P{i}", "last_name": "X",
                            "company": "Y", "email": f"p{i}@y{i}.co"}).json()  # fmt: skip
            session.add(Recipient(tenant_id=tenant_id, campaign_id=uuid.UUID(campaign["id"]),
                                  contact_id=uuid.UUID(c["id"]), email=f"p{i}@y{i}.co", sent_count=1,
                                  status="replied", reply_category="otro", reply_snippet=text))  # fmt: skip
    sheet = client.post("/api/concertino/facts/learn").json()
    learned = [f for f in sheet["items"] if f["status"] == "aprendido"]
    assert any(f["kind"] == "objecion" and f["meta"]["tipo"] == "precio" for f in learned)
    assert any(f["kind"] == "pregunta" and "SAP" in f["title"] for f in learned)

    # Un caso usado se cierra solo con lo que pasó después en la cuenta.
    contact = client.post("/api/contacts", json={"first_name": "Ana", "last_name": "Z", "company": "Q",
                                                  "email": "ana@q.co"}).json()  # fmt: skip
    case = client.post("/api/concertino/ask", json={"question": "Me dijo que no es prioridad",
                                                     "contact_id": contact["id"]}).json()  # fmt: skip
    client.post(
        f"/api/concertino/cases/{case['case_id']}", json={"action": "usada", "chosen": "fecha"}
    )
    client.post("/api/crm/activities", json={"type": "meeting", "body": "Reunión",
                                             "contact_id": contact["id"]})  # fmt: skip
    from orkesta.concertino import service as concertino_service

    concertino_service._LEARNED_AT.clear()
    client.post("/api/concertino/ask", json={"question": "hola"})
    with session_factory() as session:
        set_context(session, tenant_id=tenant_id)
        row = session.get(AdviceCase, uuid.UUID(case["case_id"]))
        assert row is not None and row.outcome == "reunion"

    imported = client.post("/api/concertino/facts/import", json={"text": (
        "Ayudamos a reducir el tiempo de prospección. Un cliente aumentó 40% sus reuniones. "
        "El plan cuesta 99 dólares al mes por usuario.")}).json()  # fmt: skip
    assert imported["added"] == 3


def test_brain_models_train(session_factory: sessionmaker[Session]) -> None:
    import orkesta.brain.capabilities  # noqa: F401

    with session_factory.begin() as session:
        for name in ("concertino_objecion", "concertino_tema"):
            row = registry.train(session, name)
            assert row.metrics["accuracy"] >= 0


def test_inicial_plan_keeps_objections(
    client: TestClient, session_factory: sessionmaker[Session]
) -> None:
    register(client, "ana@lacteos.co")
    with session_factory() as session:
        session.execute(update(Tenant).values(plan="inicial"))
        session.commit()
        assert session.scalar(select(Tenant.plan)) == "inicial"
    got = client.post("/api/concertino/ask", json={"question": "Me dijo que es muy caro"}).json()
    assert got["topic"] == "objecion" and got["options"]
    assert client.get("/api/concertino/info").json()["plan"]["full"] is False


def test_bulk_package_stays_in_its_company(
    client: TestClient, session_factory: sessionmaker[Session]
) -> None:
    """Un paquete de conocimiento revisado se carga en la ficha de una sola empresa: lo usa todo
    su equipo, no se duplica, respeta la vigencia y deja plantillas compartidas."""
    register(client, "ana@lacteos.co")
    _pro(session_factory)
    luis = _join(client, "luis@lacteos.co", "vendedor", "Luis")
    package = {
        "facts": [
            {"kind": "precio", "title": "Entrada general: US$740 por los 2 días", "prioridad": 3},
            {"kind": "precio", "title": "Promoción vencida: US$100", "prioridad": 5,
             "meta": {"vigencia": "2020-01-01"}},
            {"kind": "objecion", "title": "Es mucho presupuesto", "meta": {"tipo": "precio"},
             "answer": "Podemos empezar con un núcleo de 3 a 5 personas clave."},
        ],
        "plantillas": [{"name": "Respuesta a cuánto cuesta", "description": "Precio y siguiente paso",
                        "steps": [{"subject": "{{nombre}}, el valor de la entrada",
                                   "body": "Hola, {{nombre|}}: la entrada vale US$740.", "delay_days": 0}]}],
    }  # fmt: skip
    assert luis.post("/api/concertino/facts/bulk", json=package).status_code == 403
    r = client.post("/api/concertino/facts/bulk", json=package)
    assert r.status_code == 200, r.text
    assert r.json() == {"added": 3, "templates": 1, "skipped": 0}
    again = client.post("/api/concertino/facts/bulk", json=package).json()
    assert again == {"added": 0, "templates": 0, "skipped": 4}
    bad = client.post("/api/concertino/facts/bulk",
                      json={"facts": [{"kind": "objecion", "title": "x", "meta": {"tipo": "otro"}}]})  # fmt: skip
    assert bad.status_code == 400

    # Todo el equipo lo usa: la respuesta de la ficha y el precio vigente (no el vencido).
    advice = luis.post("/api/concertino/ask", json={"question": "Me dijo que es muy caro"}).json()
    assert "núcleo de 3 a 5 personas" in " ".join(o["text"] for o in advice["options"])
    product = luis.post("/api/concertino/ask", json={"question": "¿Cuánto vale la entrada?",
                                                     "mode": "producto"}).json()  # fmt: skip
    joined = " ".join(i for s in product["sections"] for i in s["items"])
    assert "US$740" in joined and "US$100" not in joined
    shared = luis.get("/api/templates?tipo=campaign").json()
    assert any(t["name"] == "Respuesta a cuánto cuesta" for t in shared)

    # Otra empresa no ve nada.
    other = TestClient(client.app)
    register(other, "eva@otra.co")
    assert other.get("/api/concertino/facts").json()["items"] == []
    assert other.get("/api/templates?tipo=campaign").json() == []
