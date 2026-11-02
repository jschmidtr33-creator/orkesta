"""Etapa 0: registro, sesiones, equipo, invitaciones, roles, aislamiento y sitio público."""

from __future__ import annotations

import uuid

import psycopg
from fastapi.testclient import TestClient
from sqlalchemy import select, text
from sqlalchemy.orm import Session, sessionmaker

from orkesta.core.db import set_context
from orkesta.platform.models import AuditLog, AuthSession, Membership
from tests.conftest import register

PASSWORD = "ClaveSegura2026"


def _new_client(client: TestClient) -> TestClient:
    return TestClient(client.app)


def test_register_creates_workspace_and_session(client: TestClient) -> None:
    data = register(client, "ana@lacteos.mx")
    assert data["workspace"]["name"] == "Lácteos Inventados"
    assert data["workspace"]["role"] == "admin"
    assert "ork_at" in client.cookies and "ork_rt" in client.cookies
    me = client.get("/api/auth/me").json()
    assert me["user"]["email"] == "ana@lacteos.mx"
    # Correo repetido y contraseña débil se rechazan con mensajes claros.
    other = _new_client(client)
    dup = other.post(
        "/api/auth/register",
        json={"company": "X", "full_name": "Otra", "email": "ANA@lacteos.mx", "password": PASSWORD},
    )
    assert dup.status_code == 409
    weak = other.post(
        "/api/auth/register",
        json={"company": "X SA", "full_name": "Otra", "email": "o@x.com", "password": "corta"},
    )
    assert weak.status_code == 400 and "10 caracteres" in weak.json()["detail"]


def test_login_lockout_and_logout(client: TestClient) -> None:
    register(client, "ana@lacteos.mx")
    client.post("/api/auth/logout")
    assert client.get("/api/auth/me").status_code == 401
    bad = client.post("/api/auth/login", json={"email": "ana@lacteos.mx", "password": "mala"})
    assert bad.status_code == 401 and bad.json()["detail"] == "Correo o contraseña incorrectos."
    ok = client.post("/api/auth/login", json={"email": "Ana@Lacteos.mx", "password": PASSWORD})
    assert ok.status_code == 200
    for _ in range(8):
        client.post("/api/auth/login", json={"email": "ana@lacteos.mx", "password": "mala"})
    locked = client.post("/api/auth/login", json={"email": "ana@lacteos.mx", "password": PASSWORD})
    assert locked.status_code == 429


def test_refresh_rotates_and_reuse_revokes_everything(
    client: TestClient, session_factory: sessionmaker[Session]
) -> None:
    register(client, "ana@lacteos.mx")
    first = client.cookies.get("ork_rt")
    assert client.post("/api/auth/refresh").status_code == 200
    second = client.cookies.get("ork_rt")
    assert first and second and first != second
    # Reusar el token ya rotado (robo) revoca todas las sesiones del usuario.
    thief = _new_client(client)
    thief.cookies.set("ork_rt", first, path="/api/auth")
    assert thief.post("/api/auth/refresh").status_code == 401
    assert client.post("/api/auth/refresh").status_code == 401
    with session_factory() as session:
        open_sessions = session.scalars(
            select(AuthSession).where(AuthSession.revoked_at.is_(None))
        ).all()
        assert open_sessions == []


def test_invitation_flow_roles_and_last_admin(client: TestClient) -> None:
    register(client, "ana@lacteos.mx")
    link = client.post(
        "/api/team/invitations", json={"email": "luis@lacteos.mx", "role": "vendedor"}
    ).json()["link"]
    token = link.rsplit("/", 1)[1]
    guest = _new_client(client)
    info = guest.get(f"/api/invitations/{token}").json()
    assert info == {
        "workspace": "Lácteos Inventados",
        "email": "luis@lacteos.mx",
        "role": "Vendedor",
        "account_exists": "no",
    }
    accepted = guest.post(
        f"/api/invitations/{token}/accept", json={"full_name": "Luis Ruiz", "password": PASSWORD}
    )
    assert accepted.status_code == 200 and accepted.json()["workspace"]["role"] == "vendedor"
    assert guest.post(f"/api/invitations/{token}/accept", json={}).status_code == 410
    # Un vendedor no administra el equipo.
    assert (
        guest.post("/api/team/invitations", json={"email": "x@y.com", "role": "admin"}).status_code
        == 403
    )
    team = client.get("/api/team").json()
    luis = next(m for m in team["members"] if m["email"] == "luis@lacteos.mx")
    ana = next(m for m in team["members"] if m["is_me"])
    # El cambio de rol rige al instante, sin volver a iniciar sesión.
    client.patch(f"/api/team/members/{luis['id']}", json={"role": "admin"})
    assert guest.get("/api/audit").status_code == 200
    # Siempre debe quedar un administrador activo.
    client.patch(f"/api/team/members/{luis['id']}", json={"role": "vendedor"})
    last = client.patch(f"/api/team/members/{ana['id']}", json={"role": "vendedor"})
    assert last.status_code == 400 and "al menos un administrador" in last.json()["detail"]
    # Desactivar a alguien corta su acceso de inmediato.
    client.patch(f"/api/team/members/{luis['id']}", json={"active": False})
    assert guest.get("/api/team").status_code == 401
    actions = [a["action"] for a in client.get("/api/audit").json()]
    assert {"tenant.created", "member.invited", "invitation.accepted", "member.updated"} <= set(
        actions
    )


def test_workspaces_are_isolated(
    client: TestClient, session_factory: sessionmaker[Session], database: tuple[str, str]
) -> None:
    register(client, "ana@lacteos.mx", "Lácteos Inventados")
    other = _new_client(client)
    b = register(other, "jorge@banco.co", "Banco Ficticio")
    team_a = {m["email"] for m in client.get("/api/team").json()["members"]}
    assert team_a == {"ana@lacteos.mx"}
    # Cambiar a un espacio ajeno se rechaza.
    forced = client.post("/api/auth/switch", json={"tenant_id": b["workspace"]["id"]})
    assert forced.status_code == 403
    # A nivel de base de datos: con el contexto de A, las filas de B no existen.
    tenant_a = client.get("/api/auth/me").json()["workspace"]["id"]
    with session_factory() as session:
        set_context(session, tenant_id=uuid.UUID(tenant_a))
        rows = session.scalars(select(AuditLog)).all()
        assert rows and {str(r.tenant_id) for r in rows} == {tenant_a}
        members = session.scalars(select(Membership)).all()
        assert {str(m.tenant_id) for m in members} == {tenant_a}
        session.rollback()
        # Sin contexto no se ve nada, aunque la consulta no filtre.
        assert session.execute(text("SELECT count(*) FROM orkesta.audit_logs")).scalar() == 0
        # Ni siquiera se puede escribir en otra empresa.
        set_context(session, tenant_id=uuid.UUID(tenant_a))
        try:
            session.execute(
                text(
                    "INSERT INTO orkesta.audit_logs (id, tenant_id, action, detail) "
                    "VALUES (gen_random_uuid(), :t, 'intruso', '{}')"
                ),
                {"t": b["workspace"]["id"]},
            )
            raise AssertionError("RLS debió rechazar la escritura")
        except Exception as exc:
            assert "row-level security" in str(exc)
        session.rollback()
    # El rol de la aplicación no es superusuario (si lo fuera, RLS no aplicaría).
    with psycopg.connect(database[0]) as conn:
        row = conn.execute(
            "SELECT rolsuper, rolbypassrls FROM pg_roles WHERE rolname = 'orkesta_app_test'"
        ).fetchone()
        assert row == (False, False)


def test_multiple_workspaces_and_switch(client: TestClient, database: tuple[str, str]) -> None:
    import psycopg

    register(client, "ana@lacteos.mx", "Lácteos Inventados")
    # Solo la creadora de ORKESTA puede tener varios espacios de trabajo.
    with psycopg.connect(database[0], autocommit=True) as conn:
        conn.execute(
            "UPDATE orkesta.users SET platform_role = 'owner' WHERE email = 'ana@lacteos.mx'"
        )
    created = client.post("/api/workspaces", json={"name": "Nuevo Trabajo SAS"}).json()
    assert created["workspace"]["name"] == "Nuevo Trabajo SAS"
    names = {w["name"] for w in created["workspaces"]}
    assert names == {"Lácteos Inventados", "Nuevo Trabajo SAS"}
    first = next(w for w in created["workspaces"] if w["name"] == "Lácteos Inventados")
    switched = client.post("/api/auth/switch", json={"tenant_id": first["id"]}).json()
    assert switched["workspace"]["name"] == "Lácteos Inventados"


def test_dashboard_reports_only_real_data(client: TestClient) -> None:
    register(client, "ana@lacteos.mx")
    summary = client.get("/api/dashboard/summary").json()
    assert summary["team"] == {"members": 1, "pending_invitations": 0}
    # Los módulos que aún no existen no muestran cifras inventadas.
    kpis = summary["kpis"]
    assert kpis.pop("emails_validated") == 0  # real: todavía no hay correos validados
    assert kpis.pop("open_opportunities") == 0  # real: todavía no hay oportunidades
    assert kpis.pop("conversion_rate") is None  # sin oportunidades cerradas no hay tasa
    assert set(kpis.values()) == {None}


def test_demo_requests_consent_rate_limit_and_platform_view(client: TestClient) -> None:
    form = {
        "name": "María Pérez",
        "email": "maria@empresa.co",
        "company": "Empresa Ficticia",
        "job_title": "Gerente comercial",
        "country": "Colombia",
        "message": "Quiero conocer ORKESTA",
        "consent": False,
    }
    assert client.post("/api/public/demo-requests", json=form).status_code == 400
    accepted = client.post("/api/public/demo-requests", json={**form, "consent": True})
    assert accepted.status_code == 201
    register(client, "ana@lacteos.mx")
    assert client.get("/api/platform/overview").status_code == 403


def test_session_endpoint_without_login_is_not_an_error(client: TestClient) -> None:
    assert client.get("/api/auth/session").status_code == 200
    assert client.get("/api/auth/session").json() is None
    register(client, "ana@lacteos.mx")
    assert client.get("/api/auth/session").json()["user"]["email"] == "ana@lacteos.mx"
    # Sin la cookie de acceso (vencida), la sesión se renueva con el token de refresco.
    client.cookies.delete("ork_at")
    renewed = client.get("/api/auth/session")
    assert renewed.json()["user"]["email"] == "ana@lacteos.mx" and "ork_at" in renewed.cookies


def test_cross_site_writes_are_rejected(client: TestClient) -> None:
    response = client.post(
        "/api/auth/login",
        json={"email": "a@b.com", "password": "x"},
        headers={"Origin": "https://sitio-malicioso.com"},
    )
    assert response.status_code == 403
    page = client.get("/api/health")
    assert "default-src 'self'" in page.headers["content-security-policy"]


def test_release_bootstraps_platform_admin(
    client: TestClient, monkeypatch, database: tuple[str, str]
) -> None:  # type: ignore[no-untyped-def]
    from orkesta import cli
    from orkesta.core.config import get_settings

    monkeypatch.setenv("DATABASE_URL", database[1])
    monkeypatch.setenv("ORKESTA_BOOTSTRAP_ADMIN_EMAIL", "jenn@ejemplo.com")
    monkeypatch.setenv("ORKESTA_BOOTSTRAP_ADMIN_PASSWORD", PASSWORD)
    monkeypatch.setenv("ORKESTA_BOOTSTRAP_ORG_NAME", "Mi Empresa")
    get_settings.cache_clear()
    from orkesta.core import db as core_db

    core_db.get_engine.cache_clear()
    core_db.get_session_factory.cache_clear()
    try:
        cli.bootstrap_admin()
        cli.bootstrap_admin()  # idempotente
    finally:
        get_settings.cache_clear()
        core_db.get_engine.cache_clear()
        core_db.get_session_factory.cache_clear()
    login = client.post("/api/auth/login", json={"email": "jenn@ejemplo.com", "password": PASSWORD})
    assert login.status_code == 200
    data = login.json()
    assert data["user"]["platform_admin"] is True and data["workspace"]["name"] == "Mi Empresa"
    overview = client.get("/api/platform/overview").json()
    assert overview["tenants"] == 1
