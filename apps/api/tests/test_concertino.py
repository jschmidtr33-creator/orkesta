"""Concertino: responde con la ficha del producto y el oficio, solo cuando se le pide, aprende
de lo que hace el vendedor y de las campañas, y respeta la privacidad y los planes."""

from __future__ import annotations

import uuid

import pytest
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


@pytest.fixture(autouse=True)
def _product_known(request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch) -> None:
    """Las pruebas de asesoría parten de una empresa que ya le enseñó su producto a Concertino;
    la entrevista en el chat se prueba aparte."""
    if "interview" in request.node.name:
        return
    from orkesta.concertino import interview

    monkeypatch.setattr(interview, "pending", lambda session, tenant_id: None)


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
    joined = product["summary"] + " ".join(i for s in product["sections"] for i in s["items"])
    assert "US$740" in joined and "US$100" not in joined
    shared = luis.get("/api/templates?tipo=campaign").json()
    assert any(t["name"] == "Respuesta a cuánto cuesta" for t in shared)

    # Otra empresa no ve nada.
    other = TestClient(client.app)
    register(other, "eva@otra.co")
    assert other.get("/api/concertino/facts").json()["items"] == []
    assert other.get("/api/templates?tipo=campaign").json() == []


def test_converses_by_name_with_catalog_follow_ups_and_suggestions(
    client: TestClient, session_factory: sessionmaker[Session]
) -> None:
    """Concertino conversa: saluda por el nombre, responde «¿qué vendo?» con la ficha, sigue el
    hilo de la pregunta anterior y propone cómo continuar."""
    register(client, "ana@lacteos.co")
    _pro(session_factory)
    client.post("/api/concertino/facts/bulk", json={"facts": [
        {"kind": "producto", "title": "Congresos de IA y negocios para C-Levels", "prioridad": 3},
        {"kind": "precio", "title": "Full Pass: US$740 en Chile y US$1.470 en Miami", "prioridad": 3},
        {"kind": "precio", "title": "Delegación Silver: 7 accesos por US$2.590"},
        {"kind": "pregunta", "title": "¿Hay entrada de un solo día?",
         "answer": "No existe pase de un solo día: el acceso cubre los 2 días."},
    ]})  # fmt: skip
    info = client.get("/api/concertino/info").json()
    name = info["user_name"]
    assert (
        name and info["welcome"].startswith(f"Hola, {name}.") and "¿Qué vendo?" in info["starters"]
    )

    def ask(question: str, history: list[dict[str, str]]) -> dict[str, object]:
        r = client.post("/api/concertino/ask", json={"question": question, "history": history})
        assert r.status_code == 200, r.text
        return r.json()

    hello = ask("Hola", [])
    assert hello["topic"] == "charla" and hello["summary"].startswith(f"Hola, {name}.")
    assert hello["case_id"] is None and hello["suggestions"]
    sold = ask("Qué vendo", [{"question": "Hola", "topic": "charla"}])
    assert f", {name}." in sold["summary"] and "congresos de IA" in sold["summary"]
    assert "Delegación Silver" in sold["sections"][0]["items"]
    price = ask("¿Cuánto cuesta la delegación silver?", [])
    assert "US$2.590" in price["summary"] and price["suggestions"]
    follow = ask("¿y en Miami?", [{"question": "¿Cuánto cuesta la delegación silver?",
                                   "topic": "general"}])  # fmt: skip
    assert "US$1.470" in follow["summary"]
    faq = ask("¿Hay entrada de un solo día?", [])
    assert "No existe pase de un solo día" in faq["summary"]
    who = ask("¿Qué puedes hacer?", [])
    assert "IA de ventas propia" in who["summary"]
    lost = ask("asdf qwer zxcv", [])
    assert lost["suggestions"]


def test_writes_sequences_and_adjusts_with_the_sheet(
    client: TestClient, session_factory: sessionmaker[Session]
) -> None:
    """«Crea la secuencia de 3 correos» redacta con la ficha; «más corto» o «ahora para un CEO de
    banca» ajustan lo último; la campaña no se crea sola."""
    register(client, "ana@lacteos.co")
    _pro(session_factory)
    _ficha(client)
    first = client.post(
        "/api/concertino/ask", json={"question": "Crea la secuencia de 3 correos"}
    ).json()
    assert first["topic"] == "redactar" and len(first["options"]) == 3
    assert first["options"][0]["text"].startswith("Asunto:")
    assert "triplicar las reuniones" in first["options"][0]["text"]
    assert first["actions"][0]["kind"] == "campana" and len(first["actions"][0]["steps"]) == 3
    # Nada se crea solo: la campaña existe solo si la persona toca el botón.
    assert first["actions"][0]["name"] not in client.get("/api/campaigns").text
    history = [{"question": "Crea la secuencia de 3 correos", "topic": "redactar"}]
    ceo = client.post("/api/concertino/ask", json={"question": "Ahora para un CEO de banca",
                                                    "history": history}).json()  # fmt: skip
    assert ceo["topic"] == "redactar" and "CEO del sector financiero" in ceo["summary"]
    assert "Buen día" in ceo["options"][0]["text"] and len(ceo["options"]) == 3
    whatsapp = client.post("/api/concertino/ask", json={"question": "Pásalo a WhatsApp", "history": [
        *history, {"question": "Ahora para un CEO de banca", "topic": "redactar"}]}).json()  # fmt: skip
    assert whatsapp["topic"] == "redactar" and "WhatsApp" in whatsapp["summary"]
    assert "CEO" in whatsapp["summary"]


def test_concertino_teaches_the_sales_craft(
    client: TestClient, session_factory: sessionmaker[Session]
) -> None:
    from tests.test_supervision import _pro

    register(client, "ana@lacteos.co")
    _pro(session_factory)

    def ask(q: str) -> dict:
        r = client.post("/api/concertino/ask", json={"question": q})
        assert r.status_code == 200, r.text
        return r.json()

    cold = ask("¿Cómo escribo un correo en frío?")
    assert cold["topic_label"] == "Oficio de ventas" and cold["lesson"]["code"] == "correo_frio"
    assert any("Estructura" in s["title"] for s in cold["sections"])
    assert ask("cuantos seguimientos debo hacer si no responde")["lesson"]["code"] == "secuencia"
    assert ask("que es meddicc")["lesson"]["code"] == "calificacion"
    assert ask("debo tratar de tu o usted en colombia")["lesson"]["code"] == "latam"
    # Una objeción concreta sigue yendo al mapa de objeciones.
    assert "lesson" not in ask("¿cómo respondo si me dicen que es muy caro?")


def test_event_sales_questions_and_specific_emails(
    client: TestClient, session_factory: sessionmaker[Session]
) -> None:
    """Preguntas reales de venta de un congreso: lista de objeciones, cómo dirigirse a un cargo
    para vender un stand, correos para una persona concreta y de seguimiento."""
    register(client, "ana@lacteos.co")
    _pro(session_factory)
    client.post("/api/concertino/facts/bulk", json={"facts": [
        {"kind": "producto", "title": "Congreso de IA y negocios para C-Levels", "prioridad": 3},
        {"kind": "publico", "title": "CEO, CIO y CMO de grandes empresas de Latinoamérica"},
        {"kind": "valor", "title": "Dos días con casos reales y networking 1 a 1"},
        {"kind": "producto", "title": "Patrocinio: Main Sponsor (stand y conferencia) o Expositor (stand)"},
        {"kind": "precio", "title": "Stands desde US$7.200"},
    ]})  # fmt: skip

    def ask(q: str) -> dict:
        r = client.post("/api/concertino/ask", json={"question": q})
        assert r.status_code == 200, r.text
        return r.json()

    objections = ask("¿Cuáles son las principales objeciones al vender delegaciones y tickets?")
    items = " ".join(objections["sections"][0]["items"])
    assert objections["topic"] == "objecion" and "Ya fui el año pasado" in items
    approach = ask("¿Cómo puedo dirigirme a un CMO de telecomunicaciones para venderle un stand?")
    assert approach["topic"] == "perfil" and "Patrocinio" in approach["summary"]
    assert "Expositor (stand)" in approach["options"][0]["text"]
    named = ask("Escribe un correo para Juan Perez, CFO de Bancolombia, invitándolo a Miami")
    text = named["options"][0]["text"]
    assert (
        "Juan" in text and "Bancolombia" in text and "en Miami" in text and "{{nombre" not in text
    )
    follow = ask("redacta un correo de seguimiento para alguien que no respondió")
    assert "retomar mi mensaje anterior" in follow["options"][0]["text"]
    said = ask("¿Qué le respondo si me dice que ya fue el año pasado?")
    assert said["topic"] == "objecion" and "Esta edición" in said["options"][0]["text"]


def test_concertino_interview_learns_the_product_in_the_chat(client: TestClient) -> None:
    """Sin ficha, Concertino pregunta en el chat qué vende (una pregunta a la vez), guarda las
    respuestas en la memoria de la empresa y al final pregunta qué se necesita."""
    register(client, "ana@lacteos.co")
    info = client.get("/api/concertino/info").json()
    assert info["interview"]["kind"] == "producto" and "Pregunta 1" in info["welcome"]

    def say(text: str, kind: str) -> dict:
        r = client.post("/api/concertino/ask", json={"question": text, "answering": kind})
        assert r.status_code == 200, r.text
        return r.json()

    r = say("Leche y yogures para cadenas de supermercados en Colombia", "producto")
    assert r["interview"]["kind"] == "publico"
    r = say("Cadenas medianas; decide el jefe de compras", "publico")
    r = say("Quiebres de inventario por proveedores incumplidos", "problema")
    r = say("Entregas en 24 horas y cero quiebres", "valor")
    assert r["interview"]["kind"] == "diferencial"
    for kind in ("diferencial", "prueba", "precio", "competidor", "objecion"):
        r = say("Saltar", kind)
    assert r["interview"] is None and "¿Qué necesitas hoy?" in r["summary"]
    facts = client.get("/api/concertino/facts").json()
    assert "Leche y yogures" in str(facts)
    # Ya conoce el producto: el saludo pregunta qué se necesita.
    again = client.get("/api/concertino/info").json()
    assert again["interview"] is None and "¿Qué necesitas hoy?" in again["welcome"]
    # «Actualizar mi producto» vuelve a la primera pregunta mostrando lo guardado.
    upd = client.post(
        "/api/concertino/ask", json={"question": "quiero actualizar mi producto"}
    ).json()
    assert upd["interview"]["kind"] == "producto" and "Leche y yogures" in upd["summary"]


def test_new_objections_are_recognized(client: TestClient) -> None:
    register(client, "ana@lacteos.co")
    _ficha(client)
    for text, code in (("Ya probamos algo parecido y no nos funcionó", "mala_experiencia"),
                       ("Tenemos un contrato vigente con otro proveedor", "contrato"),
                       ("Eso lo hacemos internamente", "interno")):  # fmt: skip
        r = client.post("/api/concertino/ask", json={"question": f"Me respondió: «{text}»"}).json()
        assert r["objection"] == code, (text, r.get("topic"), r.get("objection"), r.get("summary"))
        assert r["options"]


def test_concertino_trains_the_seller_with_role_play_and_school(
    client: TestClient, session_factory: sessionmaker[Session]
) -> None:
    register(client, "ana@lacteos.co")
    _pro(session_factory)
    _ficha(client)

    def ask(q: str, practicing: str | None = None) -> dict:
        r = client.post("/api/concertino/ask", json={"question": q, "practicing": practicing})
        assert r.status_code == 200, r.text
        return r.json()

    play = ask("Entréname")
    assert play["topic"] == "entreno" and play["practice"]["code"]
    code = play["practice"]["code"]
    assert "gerencia comercial" in play["summary"] or "gerente" in play["summary"]
    weak = ask("ok", practicing=code)
    assert weak["score"]["total"] <= 4 and weak["practice"]["done"]
    strong = ask(
        "Tiene todo el sentido, gracias por decírmelo. ¿Qué es lo que más te pesa hoy? Muchos equipos "
        "pierden horas buscando contactos cada mes; una empresa de software pasó de 4 a 12 reuniones "
        "en 60 días. ¿Te parece si lo revisamos 20 minutos el jueves?",
        practicing=code,
    )
    assert strong["score"]["total"] >= 9 and strong["score"]["count"] == 2
    assert strong["options"][0]["name"] == "Así lo diría un vendedor top"
    # Después de calificar, solo los botones siguen dentro del entrenamiento.
    assert ask("Otra objeción", practicing=f"fin:{code}")["practice"]["code"] != code
    assert ask("Repetir esta objeción", practicing=f"fin:{code}")["practice"]["code"] == code
    assert "cerramos el entrenamiento" in ask("Salir del entrenamiento", practicing=code)["summary"]
    # Escuela de ventas: menú y metodologías aplicadas al producto.
    menu = ask("Escuela de ventas")
    assert any("SPIN" in i for i in menu["sections"][0]["items"])
    spin = ask("Enséñame SPIN")
    applied = next(s for s in spin["sections"] if s["title"] == "Con tu producto")
    assert any("buscando contactos" in i for i in applied["items"])
    # El oficio sigue respondiendo lo suyo.
    assert ask("que es meddicc")["lesson"]["code"] == "calificacion"


def test_concertino_builds_strategies_and_knows_pains(
    client: TestClient, session_factory: sessionmaker[Session]
) -> None:
    register(client, "ana@lacteos.co")
    _pro(session_factory)
    _ficha(client)

    def ask(q: str) -> dict:
        r = client.post("/api/concertino/ask", json={"question": q})
        assert r.status_code == 200, r.text
        return r.json()

    plan = ask("Créame una estrategia para conseguir 12 reuniones con bancos")
    titles = [s["title"] for s in plan["sections"]]
    assert titles[:4] == ["1. A quién apuntar", "2. Qué decirle", "3. Cadencia por canal (3 semanas)",
                          "4. Cuánta actividad necesitas"]  # fmt: skip
    activity = " ".join(plan["sections"][3]["items"])
    assert "12 reuniones" in activity and "400 personas" in activity  # 12 / (5 % × 60 %)
    assert any("fintech" in i for i in plan["sections"][1]["items"])
    pain = ask("¿Qué le duele a un gerente de compras de un banco?")
    got = {s["title"] for s in pain["sections"]}
    assert "Lo que preocupa en el sector financiero" in got
    assert any(t.startswith("Lo que le duele a") for t in got)
    assert ask("qué le duele")["suggestions"]


def test_concertino_negotiates_discounts_and_writes_clean_emails(
    client: TestClient, session_factory: sessionmaker[Session]
) -> None:
    """«Quiere 30 % de descuento para firmar» es una negociación, no «es caro»; el correo dice
    el cargo con tildes, sin repetir el sector, y el nombre del producto nunca sale cortado."""
    register(client, "ana@lacteos.co")
    _pro(session_factory)
    _ficha(client)
    ask = lambda q: client.post("/api/concertino/ask", json={"question": q}).json()  # noqa: E731
    deal = ask("mi cliente quiere 30% de descuento para firmar este mes, qué le contesto")
    assert deal["objection"] == "descuento", deal
    assert [o["code"] for o in deal["options"]][:2] == ["intercambio", "entender"]
    assert ask("el cliente me dice que es muy caro")["objection"] == "precio"
    mail = ask(
        "escríbeme un correo en frío para un gerente de logística de una minorista en México"
    )
    assert "un gerente de logística de retail y consumo" in mail["summary"], mail["summary"]
    assert "…" not in mail["summary"] and "…" not in mail["options"][0]["text"]
    assert "líderes de logística" in mail["options"][0]["text"]
    advice = ask("qué hago si no me contestan después de 3 correos")
    assert advice["topic"] != "redactar"
    line = ask("resume en una línea por qué deberían comprarnos")
    assert line["options"][0]["code"] == "una_linea"
    assert (
        "Triplicar las reuniones" in line["options"][0]["text"]
        or "triplicar las reuniones" in line["options"][0]["text"]
    )


def test_concertino_prioritizes_the_sellers_opportunities(
    client: TestClient, session_factory: sessionmaker[Session]
) -> None:
    """«¿Qué oportunidad priorizo esta semana?» ordena las oportunidades del CRM y dice qué hacer
    con cada una."""
    from datetime import UTC, date, datetime, timedelta

    register(client, "ana@lacteos.co")
    _pro(session_factory)
    _ficha(client)
    empty = client.post(
        "/api/concertino/ask", json={"question": "cuál de mis oportunidades priorizo esta semana"}
    ).json()
    assert empty["topic"] == "cuenta" and "No tienes oportunidades" in empty["summary"]
    stages = {s["name"]: s for s in client.get("/api/crm/stages").json()["stages"]}
    contact = client.post("/api/contacts", json={"first_name": "Laura", "company": "Banco Andino",
                                                 "email": "laura@bancoandino.co"}).json()  # fmt: skip
    small = client.post("/api/crm/deals", json={"contact_id": contact["id"], "title": "Pequeña",
                                                "value": 1000}).json()  # fmt: skip
    big = client.post("/api/crm/deals", json={"contact_id": contact["id"], "title": "Grande",
                                              "value": 50000,
                                              "expected_close": (date.today() + timedelta(days=5)).isoformat()}).json()  # fmt: skip
    client.post(
        f"/api/crm/deals/{big['id']}/move", json={"stage_id": stages["Propuesta enviada"]["id"]}
    )
    due = (datetime.now(UTC) - timedelta(days=2)).isoformat()
    client.post("/api/crm/activities", json={"type": "task", "deal_id": big["id"],
                                             "body": "Llamar para cerrar", "due_at": due})  # fmt: skip
    r = client.post(
        "/api/concertino/ask", json={"question": "¿Qué oportunidad debería priorizar esta semana?"}
    ).json()
    assert r["topic"] == "cuenta" and "2 oportunidades abiertas" in r["summary"], r["summary"]
    items = r["sections"][0]["items"]
    assert items[0].startswith("1. Grande") and "Llamar para cerrar" in items[0]
    assert items[1].startswith("2. Pequeña") and small["id"]


def test_concertino_writes_linkedin_posts_and_messages_from_the_sheet(
    client: TestClient, session_factory: sessionmaker[Session]
) -> None:
    """Posts y mensajes de LinkedIn con lo que sabe de ventas y lo que dice la ficha: el caso real,
    el problema y el diferencial; invitación de menos de 300 caracteres y sin vender."""
    register(client, "ana@lacteos.co")
    _pro(session_factory)
    _ficha(client)
    ask = lambda q: client.post("/api/concertino/ask", json={"question": q}).json()  # noqa: E731
    post = ask("hazme un post de linkedin")
    assert post["topic"] == "redactar" and len(post["options"]) >= 2, post
    first = post["options"][0]["text"]
    assert first.startswith("Una empresa de software pasó de 4 a 12 reuniones")
    assert "#Ventas" in first and "http" not in first and first.count("#") <= 3
    joined = " ".join(o["text"] for o in post["options"])
    assert "el equipo comercial pierde horas buscando contactos" in joined
    assert any("primer comentario" in t for t in post["tips"])
    msgs = ask("escríbeme mensajes de linkedin para una gerente comercial")
    names = [o["name"] for o in msgs["options"]]
    assert names[0] == "Invitación a conectar" and len(names) == 4
    invite = msgs["options"][0]["text"]
    assert len(invite) <= 300 and "Plataforma" not in invite and "{{nombre}}" in invite
    assert "líderes comerciales" in invite
    assert "4 a 12 reuniones" in msgs["options"][2]["text"]
    ceo = ask("mensajes de linkedin para un CEO")
    assert "[tu|su]" not in ceo["options"][0]["text"] and "su perfil" in ceo["options"][0]["text"]


def test_concertino_handles_stalls_referrals_and_sales_moments(
    client: TestClient, session_factory: sessionmaker[Session]
) -> None:
    """Las preguntas que hacen la diferencia en el día a día: «lo voy a pensar», «lo iba a hablar
    con su jefe y no volvió», referidos, después de ganar, ánimo, ORKESTA paso a paso y textos
    para confirmar una reunión o hacer seguimiento después de una demo."""
    register(client, "ana@lacteos.co")
    _pro(session_factory)
    _ficha(client)
    ask = lambda q: client.post("/api/concertino/ask", json={"question": q}).json()  # noqa: E731
    think = ask("me dijo que lo va a pensar")
    assert think["objection"] == "pensar" and think["options"][0]["code"] == "aislar"
    assert ask("cómo manejo a un cliente que me pide más tiempo")["objection"] == "pensar"
    boss = ask("el gerente me dijo que lo hablaría con su jefe y no volvió a responder")
    assert boss["objection"] == "comite" and boss["options"][0]["code"] == "retomar"
    assert ask("me dicen que ya lo resolvieron internamente")["objection"] == "interno"
    ref = ask("qué le escribo a un cliente que ya compró para pedirle un referido")
    assert [o["name"] for o in ref["options"]][:2] == ["Pedir un referido", "Pedir un testimonio"]
    assert (
        "Triplicar las reuniones" in ref["options"][1]["text"]
        or "triplicar las reuniones" in ref["options"][1]["text"]
    )
    won = ask("el cliente aceptó la propuesta, qué sigue")
    assert won["sections"][0]["title"] == "Hoy" and "Felicitaciones" in won["summary"]
    mood = ask("estoy desmotivada, nadie me responde")
    assert "Te entiendo" in mood["summary"] and mood["objection"] is None
    how = ask("cómo uso ORKESTA para prospectar")
    assert "Prospección" in how["sections"][0]["items"][0]
    confirm = ask("escríbeme un whatsapp para confirmar la reunión de mañana")
    assert (
        confirm["options"][0]["name"] == "Confirmación"
        and "confirmar" in confirm["options"][0]["text"]
    )
    demo = ask("escríbeme un correo de seguimiento después de una demo")
    body = demo["options"][0]["text"]
    assert "Resumen de nuestra reunión" in body and "Lo que buscan resolver" in body
    cfo = ask("qué le digo a un CFO para que me reciba")
    assert cfo["topic"] == "redactar" and "CFO" in cfo["summary"]
    pitch = ask("explícame el producto en 30 segundos")
    assert pitch["options"] and "30 segundos" in pitch["options"][0]["name"]
    icp = ask("quién debería ser mi cliente ideal")
    assert icp["topic"] == "estrategia"


def test_concertino_answers_with_the_sellers_numbers_and_the_named_account(
    client: TestClient, session_factory: sessionmaker[Session]
) -> None:
    """«¿Cuántas propuestas envié este mes?» responde con datos reales; «prepárame la demo con
    Banco Andino» usa lo que el CRM sabe de esa cuenta."""
    from datetime import UTC, datetime, timedelta

    register(client, "ana@lacteos.co")
    _pro(session_factory)
    _ficha(client)
    contact = client.post("/api/contacts", json={"first_name": "Laura", "last_name": "Gómez",
        "company": "Banco Andino", "job_title": "Gerente de Compras", "email": "laura@bancoandino.co"}).json()  # fmt: skip
    deal = client.post("/api/crm/deals", json={"contact_id": contact["id"], "title": "Banco Andino · Licencias",
                                               "value": 25000}).json()  # fmt: skip
    due = (datetime.now(UTC) - timedelta(days=1)).isoformat()
    client.post("/api/crm/activities", json={"type": "task", "deal_id": deal["id"],
                                             "body": "Enviar caso de éxito", "due_at": due})  # fmt: skip
    p = client.post("/api/proposals", json={"deal_id": deal["id"]}).json()
    client.post(f"/api/proposals/{p['id']}/send")
    stats = client.post(
        "/api/concertino/ask", json={"question": "¿cuántas propuestas envié este mes?"}
    ).json()
    assert stats["summary"].startswith("Hola") and "enviaste 1 propuesta;" in stats["summary"], (
        stats["summary"]
    )
    titles = [s["title"] for s in stats["sections"]]
    assert titles == ["Prospección", "Ventas", "Lo primero que mejoraría"]
    assert any("Oportunidades nuevas: 1" in i for i in stats["sections"][1]["items"])
    prep = client.post(
        "/api/concertino/ask",
        json={"question": "ayúdame a preparar la demo de mañana con Banco Andino"},
    ).json()
    assert prep["topic"] == "reunion" and "Banco Andino" in prep["summary"]
    account = next(s for s in prep["sections"] if s["title"] == "Lo que ya sabes de la cuenta")[
        "items"
    ]
    joined = " ".join(account)
    assert (
        "Banco Andino · Licencias" in joined
        and "Enviar caso de éxito" in joined
        and "(vencida)" in joined
    )
    assert f"Propuesta #{p['number']}" in joined


def test_concertino_corrige_ortografia_y_se_deja_reensenar(client: TestClient) -> None:
    """Lo que se le enseña queda con buena ortografía y se puede volver a enseñar."""
    from orkesta.concertino.ortografia import corregir

    fixed, changes = corregir(
        "TEner una aplicacion que no se conecta entre si y tambien www.acme.com"
    )
    assert fixed == "Tener una aplicación que no se conecta entre sí y también www.acme.com"
    assert ("TEner", "Tener") in changes
    assert corregir("esta solo en LATAM y B2B")[0] == "Esta solo en LATAM y B2B"

    register(client, "ana@lacteos.co")

    def say(text: str, kind: str | None = None) -> dict:
        r = client.post("/api/concertino/ask", json={"question": text, "answering": kind})
        assert r.status_code == 200, r.text
        return r.json()

    r = say("Una aplicacion para TEner leche fresca en tiendas", "producto")
    assert "Corregí la ortografía" in r["summary"] and "aplicacion → aplicación" in r["summary"]
    for kind, text in (("publico", "Tiendas de barrio en Colombia"), ("problema", "Quiebres de inventario"),
                       ("valor", "Cero quiebres")):  # fmt: skip
        r = say(text, kind)
    facts = str(client.get("/api/concertino/facts").json())
    assert "Una aplicación para Tener leche" in facts
    # No le gustó lo que enseñó: vuelve a empezar desde la primera pregunta
    again = say("Vuélveme a hacer las preguntas iniciales para entender mi producto")
    assert again["interview"]["kind"] == "producto" and "reemplaza" in again["summary"]
    say("Yogures griegos para cafeterías", "producto")
    facts = str(client.get("/api/concertino/facts").json())
    assert "Yogures griegos" in facts and "leche fresca" not in facts
    # Un solo tema
    price = say("quiero corregir el precio que te enseñé")
    assert price["interview"]["kind"] == "precio"
