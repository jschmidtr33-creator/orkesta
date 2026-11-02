"""Cada cuenta tiene un solo espacio de trabajo; solo la creadora de ORKESTA puede tener varios."""

from __future__ import annotations

import psycopg
from fastapi.testclient import TestClient

from tests.conftest import register


def test_single_workspace_except_owner(client: TestClient, database: tuple[str, str]) -> None:
    register(client, "ana@lacteos.co")
    assert client.get("/api/auth/me").json()["user"]["can_create_workspace"] is False
    r = client.post("/api/workspaces", json={"name": "Segundo espacio"})
    assert r.status_code == 403 and "solo uno" in r.json()["detail"]

    # Invitada a otra empresa: no puede sumarse mientras pertenezca a la suya.
    boss = TestClient(client.app)
    register(boss, "jefe@otra.co", company="Otra")
    link = boss.post("/api/team/invitations", json={"email": "ana@lacteos.co",
                                                   "role": "vendedor"}).json()["link"]  # fmt: skip
    token = link.rsplit("/", 1)[1]
    assert client.post(f"/api/invitations/{token}/accept", json={}).status_code == 403

    with psycopg.connect(database[0], autocommit=True) as conn:
        conn.execute(
            "UPDATE orkesta.users SET platform_role = 'owner' WHERE email = 'ana@lacteos.co'"
        )
    assert client.get("/api/auth/me").json()["user"]["can_create_workspace"] is True
    assert client.post("/api/workspaces", json={"name": "Segundo espacio"}).status_code == 200
    assert len(client.get("/api/auth/me").json()["workspaces"]) == 2
