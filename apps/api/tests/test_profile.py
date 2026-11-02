"""Mi perfil: datos personales, correo de recuperación, foto visible en la app y en todo lo
que la persona registra, y cambio de contraseña."""

from __future__ import annotations

from fastapi.testclient import TestClient

from tests.conftest import register

PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 64


def test_profile_photo_and_password(client: TestClient) -> None:
    register(client, "ana@lacteos.co")
    r = client.patch("/api/profile", json={"full_name": " Ana  María López ", "job_title": "Gerente",
                                           "phone": "+57 300 123 4567",
                                           "recovery_email": "ana.personal@correo.co",
                                           "timezone": "America/Bogota"})  # fmt: skip
    assert r.status_code == 200, r.text
    assert r.json()["full_name"] == "Ana María López" and r.json()["timezone"] == "America/Bogota"
    bad = client.patch("/api/profile", json={"recovery_email": "ana@lacteos.co"})
    assert bad.status_code == 422
    assert client.patch("/api/profile", json={"timezone": "Marte/Base"}).status_code == 422

    assert client.put("/api/profile/avatar", files={"file": ("x.txt", b"hola", "text/plain")}
                      ).status_code == 422  # fmt: skip
    up = client.put("/api/profile/avatar", files={"file": ("yo.png", PNG, "image/png")})
    url = up.json()["avatar_url"]
    assert url.startswith("/api/avatars/")
    img = client.get(url)
    assert img.status_code == 200 and img.headers["content-type"] == "image/png"
    session = client.get("/api/auth/me").json()
    assert session["user"]["avatar_url"] == url and session["user"]["job_title"] == "Gerente"
    team = client.get("/api/team").json()["members"]
    assert team[0]["avatar_url"] == url
    # La foto acompaña lo que registra: actividad del CRM y auditoría.
    contact = client.post("/api/contacts", json={"first_name": "Laura", "last_name": "Gómez",
                                                 "company": "Banco Andino"}).json()  # fmt: skip
    client.post("/api/crm/activities", json={"contact_id": contact["id"], "type": "note",
                                             "body": "Llamar el lunes"})  # fmt: skip
    timeline = client.get("/api/crm/timeline", params={"contacto": contact["id"]}).json()
    assert any(a["user"] and a["user"]["avatar_url"] == url for a in timeline)
    audit = client.get("/api/audit").json()
    assert any(a["actor_avatar"] == url and a["action"] == "profile.photo" for a in audit)

    # Otra empresa no ve la foto.
    other = TestClient(client.app)
    register(other, "otro@otra.co", company="Otra")
    assert other.get(url).status_code == 404

    assert client.post("/api/profile/password", json={"current": "mala", "new": "NuevaClave2026"}
                       ).status_code == 400  # fmt: skip
    ok = client.post("/api/profile/password", json={"current": "ClaveSegura2026",
                                                    "new": "NuevaClave2026"})  # fmt: skip
    assert ok.status_code == 200
    client.post("/api/auth/logout")
    assert client.post("/api/auth/login", json={"email": "ana@lacteos.co",
                                                "password": "NuevaClave2026"}).status_code == 200  # fmt: skip
    assert client.delete("/api/profile/avatar").json()["avatar_url"] is None
