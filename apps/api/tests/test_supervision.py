"""Supervisión: cada persona ve lo suyo; la jefatura ve además a su equipo, sin modificarlo."""

from __future__ import annotations

from fastapi.testclient import TestClient
from sqlalchemy import update
from sqlalchemy.orm import Session, sessionmaker

from orkesta.platform.models import Tenant
from tests.conftest import register


def _pro(session_factory: sessionmaker[Session]) -> None:
    with session_factory() as session:  # el plan inicial admite 3 personas
        session.execute(update(Tenant).values(plan="pro"))
        session.commit()


def _join(admin: TestClient, email: str, role: str, name: str) -> TestClient:
    link = admin.post("/api/team/invitations", json={"email": email, "role": role}).json()["link"]
    person = TestClient(admin.app)
    accepted = person.post(
        f"/api/invitations/{link.rsplit('/', 1)[1]}/accept",
        json={"full_name": name, "password": "ClaveSegura2026"},
    )
    assert accepted.status_code == 200, accepted.text
    return person


def _members(admin: TestClient) -> dict[str, dict]:
    return {m["email"]: m for m in admin.get("/api/team").json()["members"]}


def _deals(client: TestClient) -> set[str]:
    return {d["title"] for d in client.get("/api/crm/deals").json()["deals"]}


def _contact(client: TestClient, email: str) -> dict:
    response = client.post("/api/contacts", json={
        "first_name": "Sofía", "last_name": email.split("@")[0], "email": email,
        "company": "Retail Demo", "country": "Chile",
    })  # fmt: skip
    assert response.status_code == 201, response.text
    return response.json()


def test_each_person_sees_their_work_and_managers_see_their_team(
    client: TestClient, session_factory: sessionmaker[Session]
) -> None:
    register(client, "ana@lacteos.co")
    _pro(session_factory)
    jefa = _join(client, "jefa@lacteos.co", "supervisor", "Jefa Comercial")
    luis = _join(client, "luis@lacteos.co", "vendedor", "Luis")
    sara = _join(client, "sara@lacteos.co", "vendedor", "Sara")
    people = _members(client)
    assigned = client.put(
        f"/api/team/members/{people['jefa@lacteos.co']['id']}/assignments",
        json={"member_ids": [people["luis@lacteos.co"]["user_id"]]},
    )
    assert assigned.status_code == 200, assigned.text
    assert _members(client)["jefa@lacteos.co"]["manages"] == [people["luis@lacteos.co"]["user_id"]]

    for who, title in (
        (client, "De Ana"),
        (jefa, "De la jefa"),
        (luis, "De Luis"),
        (sara, "De Sara"),
    ):
        assert who.post("/api/crm/deals", json={"title": title}).status_code == 201
    luis_list = luis.post("/api/lists", json={"name": "Mis decisores"}).json()
    _contact(luis, "sofia@retaildemo.cl")

    # Vendedores: solo lo suyo; nunca lo de la jefa ni lo de la administradora.
    assert _deals(luis) == {"De Luis"}
    assert _deals(sara) == {"De Sara"}
    assert sara.get("/api/contacts").json()["total"] == 0
    # Jefatura: lo suyo y lo de su equipo (Luis), no lo de Sara ni lo de la administradora.
    assert _deals(jefa) == {"De la jefa", "De Luis"}
    assert jefa.get("/api/contacts").json()["total"] == 1
    assert {x["name"] for x in jefa.get("/api/lists").json()} == {"Mis decisores"}
    # Administradora: todo.
    assert _deals(client) == {"De Ana", "De la jefa", "De Luis", "De Sara"}

    # La jefatura ve, pero no modifica el trabajo de su equipo.
    luis_deal = next(
        d for d in jefa.get("/api/crm/deals").json()["deals"] if d["title"] == "De Luis"
    )
    assert jefa.patch(f"/api/crm/deals/{luis_deal['id']}", json={"value": 9}).status_code in (
        403,
        404,
    )
    renamed = jefa.patch(f"/api/lists/{luis_list['id']}", json={"name": "Cambiada"})
    assert renamed.status_code == 403
    assert {x["name"] for x in luis.get("/api/lists").json()} == {"Mis decisores"}

    # Un contacto que ya trabaja Luis no se duplica para Sara (ni se le muestra).
    taken = sara.post("/api/contacts", json={
        "first_name": "Sofía", "last_name": "sofia", "email": "sofia@retaildemo.cl",
        "company": "Retail Demo", "country": "Chile",
    })  # fmt: skip
    assert taken.status_code == 409 and "otra persona" in taken.json()["detail"]
    # Dos personas pueden tener listas con el mismo nombre.
    assert sara.post("/api/lists", json={"name": "Mis decisores"}).status_code == 201

    # Sin personas a cargo, la jefatura solo ve lo suyo.
    client.put(f"/api/team/members/{people['jefa@lacteos.co']['id']}/assignments",
               json={"member_ids": []})  # fmt: skip
    assert _deals(jefa) == {"De la jefa"}


def test_only_managers_get_assignments(client: TestClient) -> None:
    register(client, "ana@lacteos.co")
    luis = _join(client, "luis@lacteos.co", "vendedor", "Luis")
    people = _members(client)
    refused = client.put(
        f"/api/team/members/{people['luis@lacteos.co']['id']}/assignments",
        json={"member_ids": [people["ana@lacteos.co"]["user_id"]]},
    )
    assert refused.status_code == 400
    # Un vendedor no puede asignar personas.
    assert (
        luis.put(
            f"/api/team/members/{people['luis@lacteos.co']['id']}/assignments",
            json={"member_ids": []},
        ).status_code
        == 403
    )


def test_team_panel_shows_only_assigned_people(
    client: TestClient, session_factory: sessionmaker[Session]
) -> None:
    register(client, "ana@lacteos.co")
    _pro(session_factory)
    jefa = _join(client, "jefa@lacteos.co", "supervisor", "Jefa Comercial")
    luis = _join(client, "luis@lacteos.co", "vendedor", "Luis")
    sara = _join(client, "sara@lacteos.co", "vendedor", "Sara")
    people = _members(client)
    client.put(f"/api/team/members/{people['jefa@lacteos.co']['id']}/assignments",
               json={"member_ids": [people["luis@lacteos.co"]["user_id"]]})  # fmt: skip
    luis.post("/api/crm/deals", json={"title": "Venta Luis", "value": 1200})
    _contact(luis, "sofia@retaildemo.cl")
    sara.post("/api/crm/deals", json={"title": "Venta Sara", "value": 900})

    panel = jefa.get("/api/reports/team").json()
    assert [p["name"] for p in panel["people"]] == ["Luis"]
    row = panel["people"][0]
    assert row["open_deals"] == 1 and row["contacts_added"] == 1 and row["deals_created"] == 1
    assert panel["totals"]["open_value"] == {"USD": 1200.0}

    detail = jefa.get(f"/api/reports/team/{people['luis@lacteos.co']['user_id']}").json()
    assert [d["title"] for d in detail["deals"]] == ["Venta Luis"]
    assert any(t["action"] == "contact.created" for t in detail["timeline"])
    # Sara no está a su cargo: ni en el panel ni en el detalle.
    assert jefa.get(f"/api/reports/team/{people['sara@lacteos.co']['user_id']}").status_code == 404
    # La administración ve a todo el equipo; un vendedor no tiene este panel.
    assert {p["name"] for p in client.get("/api/reports/team").json()["people"]} == {
        "Jefa Comercial", "Luis", "Sara"}  # fmt: skip
    assert luis.get("/api/reports/team").status_code == 403
    # El reporte general de la jefatura no muestra a la administradora ni a Sara.
    names = {t["name"] for t in jefa.get("/api/reports/overview").json()["team"]}
    assert names == {"Jefa Comercial", "Luis"}


def test_invitation_is_emailed_from_the_inviters_mailbox(client: TestClient, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    import imaplib
    import smtplib
    from email import message_from_bytes, policy

    from tests.test_mail_providers import FakeIMAP, FakeSMTP

    monkeypatch.setattr(smtplib, "SMTP", FakeSMTP)
    monkeypatch.setattr(imaplib, "IMAP4_SSL", FakeIMAP)
    FakeSMTP.sent, FakeSMTP.fail = [], False
    register(client, "ana@lacteos.co")
    # Sin buzón: se crea igual y se avisa que hay que compartir el enlace.
    first = client.post(
        "/api/team/invitations", json={"email": "luis@lacteos.co", "role": "vendedor"}
    ).json()
    assert first["emailed"] is False and "/app/invitacion/" in first["link"]

    client.post("/api/mailboxes/smtp", json={"email": "ana@lacteos.co", "password": "clave-app",
                                             "smtp_host": "smtp.lacteos.co"})  # fmt: skip
    again = client.post(f"/api/team/invitations/{first['id']}/resend").json()
    assert again["emailed"] is True and again["link"] != first["link"]
    msg = message_from_bytes(FakeSMTP.sent[-1], policy=policy.default)
    assert msg["To"] == "luis@lacteos.co" and "te invitó a Lácteos Inventados" in msg["Subject"]
    html = next(p for p in msg.walk() if p.get_content_type() == "text/html").get_content()
    assert again["link"] in html and "Vendedor" in html
    # El enlace anterior ya no sirve; el nuevo sí.
    old_token, new_token = first["link"].rsplit("/", 1)[1], again["link"].rsplit("/", 1)[1]
    assert client.get(f"/api/invitations/{old_token}").status_code != 200
    assert client.get(f"/api/invitations/{new_token}").status_code == 200


def test_campaigns_and_proposals_are_only_their_owners(
    client: TestClient, session_factory: sessionmaker[Session]
) -> None:
    """Cada persona ve y edita solo sus campañas y propuestas; la administración y la jefatura
    ven el resumen en «Mi equipo», sin abrirlas ni editarlas."""
    register(client, "ana@lacteos.co")
    _pro(session_factory)
    jefa = _join(client, "jefa@lacteos.co", "supervisor", "Jefa Comercial")
    luis = _join(client, "luis@lacteos.co", "vendedor", "Luis")
    people = _members(client)
    client.put(f"/api/team/members/{people['jefa@lacteos.co']['id']}/assignments",
               json={"member_ids": [people["luis@lacteos.co"]["user_id"]]})  # fmt: skip
    mine = client.post("/api/campaigns", json={"name": "De Ana"}).json()
    his = luis.post("/api/campaigns", json={"name": "De Luis"}).json()
    proposal = luis.post("/api/proposals", json={}).json()

    assert [c["name"] for c in client.get("/api/campaigns").json()["items"]] == ["De Ana"]
    assert [c["name"] for c in luis.get("/api/campaigns").json()["items"]] == ["De Luis"]
    assert jefa.get("/api/campaigns").json()["items"] == []
    for who in (client, jefa):
        assert who.get(f"/api/campaigns/{his['id']}").status_code == 404
        assert who.patch(f"/api/campaigns/{his['id']}", json={"name": "X"}).status_code == 404
        assert who.get(f"/api/proposals/{proposal['id']}").status_code == 404
        assert who.get("/api/proposals").json() == []
    assert luis.get(f"/api/campaigns/{mine['id']}").status_code == 404
    assert luis.patch(f"/api/campaigns/{his['id']}", json={"name": "Nueva"}).status_code == 200

    uid = people["luis@lacteos.co"]["user_id"]
    for who in (client, jefa):
        detail = who.get(f"/api/reports/team/{uid}").json()
        assert [c["name"] for c in detail["campaigns"]] == ["Nueva"]
        assert [p["id"] for p in detail["proposals"]] == [proposal["id"]]
