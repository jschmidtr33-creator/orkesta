"""Panel de administración de ORKESTA: acceso, empresas, equipo, permisos y configuración."""

from __future__ import annotations

import psycopg
from fastapi.testclient import TestClient

from tests.conftest import register

PASSWORD = "ClaveSegura2026"


def _role(database: tuple[str, str], email: str, role: str | None) -> None:
    with psycopg.connect(database[0], autocommit=True) as conn:
        conn.execute("UPDATE orkesta.users SET platform_role = %s WHERE email = %s", (role, email))


def _unlock(client: TestClient, email: str) -> None:
    r = client.post("/api/admin/unlock", json={"email": email, "password": PASSWORD})
    assert r.status_code == 200, r.text
    client.headers["X-Admin-Token"] = r.json()["token"]


def _login(client: TestClient, email: str, *, panel: bool = True) -> None:
    client.headers.pop("X-Admin-Token", None)
    r = client.post("/api/auth/login", json={"email": email, "password": PASSWORD})
    assert r.status_code == 200, r.text
    if panel:
        _unlock(client, email)


def test_admin_panel_access_team_permissions_and_settings(
    client: TestClient, database: tuple[str, str]
) -> None:
    register(client, "cliente@banco.co", company="Banco Andino")
    client.post("/api/contacts", json={"first_name": "Laura", "last_name": "Gómez",
                                       "company": "Banco Andino"})  # fmt: skip
    assert client.get("/api/admin/me").status_code == 403  # un usuario normal no entra

    register(client, "soporte@orkesta.co", company="Equipo ORKESTA")
    register(client, "jenn@orkesta.co", company="ORKESTA")
    _role(database, "jenn@orkesta.co", "owner")
    # Con sesión abierta igual se piden las credenciales antes de entrar al panel.
    assert client.get("/api/admin/access").status_code == 200
    assert client.get("/api/admin/me").status_code == 428
    bad = client.post("/api/admin/unlock", json={"email": "jenn@orkesta.co", "password": "x"})
    assert bad.status_code == 401
    other = client.post("/api/admin/unlock", json={"email": "soporte@orkesta.co",
                                                    "password": PASSWORD})  # fmt: skip
    assert other.status_code == 401
    client.headers["X-Admin-Token"] = "falso"
    assert client.get("/api/admin/me").status_code == 428
    _unlock(client, "jenn@orkesta.co")
    me = client.get("/api/admin/me").json()
    assert me["role"] == "owner" and "equipo" in me["permissions"]

    ov = client.get("/api/admin/overview").json()
    assert ov["tenants"] == 3 and ov["users"] == 3 and ov["totals"]["contacts"] == 1
    tenants = client.get("/api/admin/tenants").json()["items"]
    bank = next(t for t in tenants if t["name"] == "Banco Andino")
    assert bank["contacts"] == 1 and bank["members"] == 1
    assert bank["owner"]["email"] == "cliente@banco.co"
    users = client.get("/api/admin/users", params={"q": "cliente"}).json()["items"]
    assert users[0]["workspaces"] == ["Banco Andino (admin)"]

    # Suspender la empresa bloquea a sus usuarios.
    assert client.patch(f"/api/admin/tenants/{bank['id']}", json={"status": "suspended",
                                                                   "plan": "pro"}).status_code == 200  # fmt: skip
    jenn_token = client.headers.pop("X-Admin-Token")
    r = client.post("/api/auth/login", json={"email": "cliente@banco.co", "password": PASSWORD})
    assert r.status_code == 423 and "suspendido" in r.json()["detail"]
    _login(client, "jenn@orkesta.co")
    client.patch(f"/api/admin/tenants/{bank['id']}", json={"status": "active"})

    # Equipo: solo personas con cuenta; soporte no puede gestionar empresas ni permisos.
    assert (
        client.post("/api/admin/team", json={"email": "nadie@x.co", "role": "admin"}).status_code
        == 404
    )
    assert client.post("/api/admin/team", json={"email": "soporte@orkesta.co",
                                                "role": "soporte"}).status_code == 201  # fmt: skip
    _login(client, "soporte@orkesta.co", panel=False)
    client.headers["X-Admin-Token"] = jenn_token  # el pase de otra persona no sirve
    assert client.get("/api/admin/users").status_code == 428
    _unlock(client, "soporte@orkesta.co")
    assert client.get("/api/admin/users").status_code == 200
    assert (
        client.patch(f"/api/admin/tenants/{bank['id']}", json={"plan": "inicial"}).status_code
        == 403
    )
    assert client.put("/api/admin/permissions", json={}).status_code == 403
    assert (
        client.post("/api/admin/team", json={"email": "x@x.co", "role": "admin"}).status_code == 403
    )

    _login(client, "jenn@orkesta.co")
    team = client.get("/api/admin/team").json()
    matrix = team["matrix"]
    matrix["soporte"].append("gestionar_empresas")
    client.put("/api/admin/permissions", json=matrix)
    owner_id = next(m["id"] for m in team["members"] if m["role"] == "owner")
    assert client.patch(f"/api/admin/team/{owner_id}", json={"role": "lectura"}).status_code == 409
    _login(client, "soporte@orkesta.co")
    assert (
        client.patch(f"/api/admin/tenants/{bank['id']}", json={"plan": "inicial"}).status_code
        == 200
    )

    # Configuración global: anuncio, registro cerrado y mantenimiento.
    _login(client, "jenn@orkesta.co")
    client.put("/api/admin/settings", json={
        "signup_open": False, "maintenance": True, "maintenance_message": "Volvemos pronto",
        "announcement": {"active": True, "text": "Nueva versión disponible", "tone": "ok"},
    })  # fmt: skip
    config = client.get("/api/auth/config").json()
    assert (
        config["announcement"]["text"] == "Nueva versión disponible" and not config["signup_open"]
    )
    assert client.get("/api/contacts").status_code == 200  # el equipo interno sigue entrando
    _login(client, "cliente@banco.co", panel=False)
    r = client.get("/api/contacts")
    assert r.status_code == 503 and r.json()["detail"] == "Volvemos pronto"
    client.post("/api/auth/logout")
    closed = client.post("/api/auth/register", json={"company": "X", "full_name": "X Y",
                                                     "email": "nuevo@x.co", "password": PASSWORD})  # fmt: skip
    assert closed.status_code == 403

    _login(client, "jenn@orkesta.co")
    actions = {a["action"] for a in client.get("/api/admin/audit").json()}
    assert {"tenant.suspended", "team.added", "permissions.updated", "settings.updated",
            "panel.ingreso", "panel.ingreso_fallido"} <= actions  # fmt: skip
