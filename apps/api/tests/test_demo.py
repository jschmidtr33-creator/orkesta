"""Demos de 2 semanas: acceso completo para una empresa y cierre limpio al vencer."""

from __future__ import annotations

import psycopg
import pytest
from fastapi.testclient import TestClient

from tests.conftest import register
from tests.test_admin import PASSWORD, _role, _unlock
from tests.test_platform import _new_client


def test_demo_gives_full_access_for_two_weeks_and_then_stops(
    client: TestClient, database: tuple[str, str]
) -> None:
    register(client, "jenn@orkesta.co", company="ORKESTA")
    _role(database, "jenn@orkesta.co", "owner")
    _unlock(client, "jenn@orkesta.co")

    made = client.post("/api/admin/demos/start",
                       json={"company": "Banco Andino", "email": "laura@bancoandino.co"})  # fmt: skip
    assert made.status_code == 201, made.text
    demo = made.json()
    assert "/app/invitacion/" in demo["link"] and demo["link"] in demo["message"]
    assert "14 días" in demo["message"]

    # La empresa acepta y entra como administradora, con todo activo.
    guest = _new_client(client)
    token = demo["link"].rsplit("/", 1)[1]
    accepted = guest.post(f"/api/invitations/{token}/accept",
                          json={"full_name": "Laura Gómez", "password": PASSWORD})  # fmt: skip
    assert accepted.status_code == 200, accepted.text
    ws = accepted.json()["workspace"]
    assert ws["role"] == "admin" and ws["plan"] == "demo" and ws["trial_ends_at"]
    assert guest.post("/api/contacts", json={"first_name": "Mario"}).status_code == 201
    assert guest.get("/api/concertino/info").status_code == 200
    assert guest.post("/api/team/invitations",
                      json={"email": "pedro@bancoandino.co", "role": "vendedor"}).status_code == 200  # fmt: skip

    trials = client.get("/api/admin/trials").json()
    row = next(t for t in trials if t["name"] == "Banco Andino")
    assert row["days_left"] == 14 and not row["expired"] and row["members"] == 1

    # Extender una semana.
    ext = client.patch(f"/api/admin/tenants/{row['id']}", json={"extend_days": 7}).json()
    assert ext["plan"] == "demo"
    assert next(t for t in client.get("/api/admin/trials").json()
                if t["id"] == row["id"])["days_left"] == 21  # fmt: skip

    # Al terminar, la empresa ya no trabaja pero sus datos quedan.
    client.patch(f"/api/admin/tenants/{row['id']}", json={"end_trial": True})
    blocked = guest.get("/api/contacts")
    assert blocked.status_code == 402 and "demo" in blocked.json()["detail"].lower()
    session = guest.get("/api/auth/session").json()
    assert session["workspace"]["plan"] == "demo"  # la sesión sigue para mostrar el aviso

    # Si contrata, todo vuelve tal como lo dejó.
    paid = client.patch(f"/api/admin/tenants/{row['id']}", json={"plan": "pro"}).json()
    assert paid["plan"] == "pro" and paid["trial_ends_at"] is None
    contacts = guest.get("/api/contacts").json()
    items = contacts["items"] if isinstance(contacts, dict) else contacts
    assert any(c["first_name"] == "Mario" for c in items)
    with psycopg.connect(database[0], autocommit=True) as conn:
        row2 = conn.execute(
            "SELECT plan FROM orkesta.tenants WHERE name = 'Banco Andino'"
        ).fetchone()
    assert row2 is not None and row2[0] == "pro"


def test_orkesta_is_invitation_only(
    client: TestClient, database: tuple[str, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    register(client, "jenn@orkesta.co", company="ORKESTA")
    _role(database, "jenn@orkesta.co", "owner")
    _unlock(client, "jenn@orkesta.co")
    from orkesta.admin import service as admin

    monkeypatch.setitem(admin.DEFAULT_APP, "signup_open", False)  # como en producción
    outsider = _new_client(client)
    closed = outsider.post("/api/auth/register", json={
        "company": "Curiosos SAS", "full_name": "Pedro", "email": "pedro@curiosos.co",
        "password": PASSWORD})  # fmt: skip
    assert closed.status_code == 403 and "invitación" in closed.json()["detail"]
    assert outsider.get("/api/auth/config").json()["signup_open"] is False

    # Solo con la invitación de la dueña se entra.
    demo = client.post("/api/admin/demos/start",
                       json={"company": "Curiosos SAS", "email": "pedro@curiosos.co"}).json()  # fmt: skip
    token = demo["link"].rsplit("/", 1)[1]
    ok = outsider.post(f"/api/invitations/{token}/accept",
                       json={"full_name": "Pedro", "password": PASSWORD})  # fmt: skip
    assert ok.status_code == 200 and ok.json()["user"]["can_create_workspace"] is False
    assert outsider.post("/api/workspaces", json={"name": "Otra"}).status_code == 403


def test_admin_adds_and_deletes_users(client: TestClient, database: tuple[str, str]) -> None:
    register(client, "jenn@orkesta.co", company="ORKESTA")
    _role(database, "jenn@orkesta.co", "owner")
    _unlock(client, "jenn@orkesta.co")
    demo = client.post("/api/admin/demos/start",
                       json={"company": "Banco Andino", "email": "laura@bancoandino.co"}).json()  # fmt: skip

    # Sumar a una persona a esa empresa desde el panel.
    added = client.post("/api/admin/users", json={
        "email": "pedro@bancoandino.co", "tenant_id": demo["tenant_id"], "role": "vendedor"})  # fmt: skip
    assert added.status_code == 201, added.text
    assert "/app/invitacion/" in added.json()["link"]
    pedro = _new_client(client)
    ok = pedro.post(f"/api/invitations/{added.json()['link'].rsplit('/', 1)[1]}/accept",
                    json={"full_name": "Pedro", "password": PASSWORD})  # fmt: skip
    assert ok.status_code == 200 and ok.json()["workspace"]["role"] == "vendedor"
    contact = pedro.post("/api/contacts", json={"first_name": "Mario", "email": "m@retail.co"})
    assert contact.status_code == 201

    users = client.get("/api/admin/users", params={"q": "pedro"}).json()["items"]
    assert users[0]["workspaces"] == ["Banco Andino (vendedor)"]

    # Eliminarla por completo: pierde el acceso y su trabajo queda en la empresa sin dueño.
    me = client.get("/api/admin/users", params={"q": "jenn"}).json()["items"][0]
    assert client.delete(f"/api/admin/users/{me['id']}").status_code == 409
    gone = client.delete(f"/api/admin/users/{users[0]['id']}")
    assert gone.status_code == 200, gone.text
    assert client.get("/api/admin/users", params={"q": "pedro"}).json()["items"] == []
    assert pedro.get("/api/contacts").status_code == 401
    with psycopg.connect(database[0], autocommit=True) as conn:
        row = conn.execute(
            "SELECT owner_user_id FROM orkesta.contacts WHERE first_name = 'Mario'"
        ).fetchone()
    assert row is not None and row[0] is None


def test_admin_deletes_a_company_with_all_its_data(
    client: TestClient, database: tuple[str, str]
) -> None:
    register(client, "jenn@orkesta.co", company="ORKESTA")
    _role(database, "jenn@orkesta.co", "owner")
    _unlock(client, "jenn@orkesta.co")
    demo = client.post("/api/admin/demos/start",
                       json={"company": "Pruebas Viejas", "email": "laura@viejas.co"}).json()  # fmt: skip
    laura = _new_client(client)
    laura.post(f"/api/invitations/{demo['link'].rsplit('/', 1)[1]}/accept",
               json={"full_name": "Laura", "password": PASSWORD})  # fmt: skip
    contact = laura.post("/api/contacts", json={"first_name": "Mario", "email": "m@x.co"}).json()
    deal = laura.post("/api/crm/deals", json={"title": "Piloto", "contact_id": contact["id"]})
    assert deal.status_code == 201, deal.text

    url = f"/api/admin/tenants/{demo['tenant_id']}/delete"
    assert client.post(url, json={"confirm": "otra"}).status_code == 400
    done = client.post(url, json={"confirm": "pruebas viejas"})
    assert done.status_code == 200, done.text
    assert done.json()["users_removed"] == 1
    assert all(
        t["name"] != "Pruebas Viejas" for t in client.get("/api/admin/tenants").json()["items"]
    )
    assert client.get("/api/admin/users", params={"q": "laura"}).json()["items"] == []
    with psycopg.connect(database[0], autocommit=True) as conn:
        left = conn.execute("SELECT count(*) FROM orkesta.contacts").fetchone()
    assert left is not None and left[0] == 0
    # La propia empresa no se puede borrar.
    mine = next(
        t for t in client.get("/api/admin/tenants").json()["items"] if t["name"] == "ORKESTA"
    )
    assert client.post(f"/api/admin/tenants/{mine['id']}/delete",
                       json={"confirm": "ORKESTA"}).status_code == 409  # fmt: skip
