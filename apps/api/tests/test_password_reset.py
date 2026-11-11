"""Recuperar la contraseña con el correo del sistema de ORKESTA."""

from __future__ import annotations

import re

from fastapi.testclient import TestClient

from tests.conftest import register
from tests.test_admin import PASSWORD, _role, _unlock
from tests.test_campaigns import FakeGoogle, _connect, google  # noqa: F401
from tests.test_platform import _new_client


def _link(fake: FakeGoogle) -> str:
    msg = fake.sent[-1]["msg"]
    body = msg.get_body(("plain",)) if msg.is_multipart() else msg
    text = body.get_payload(decode=True).decode()
    found = re.search(r"https?://\S+/app/restablecer/(\S+)", text)
    assert found, text
    return found.group(1)


def test_forgotten_password_is_reset_by_email(
    client: TestClient,
    google: FakeGoogle,  # noqa: F811
    database: tuple[str, str],
) -> None:
    register(client, "ana@lacteos.co", company="ORKESTA")
    _role(database, "ana@lacteos.co", "owner")
    _unlock(client, "ana@lacteos.co")

    # Sin correo del sistema: responde igual y no envía nada (no revela si la cuenta existe).
    other = _new_client(client)
    register(other, "luis@retail.co", company="Retail")
    assert client.post("/api/auth/password/forgot", json={"email": "luis@retail.co"}).json() == {
        "ok": True
    }
    assert google.sent == []

    _connect(client)
    configured = client.post("/api/admin/system-mail")
    assert configured.status_code == 200 and configured.json()["email"] == "ana@lacteos.co"

    assert client.post("/api/auth/password/forgot", json={"email": "nadie@x.co"}).json()["ok"]
    assert google.sent == []
    client.post("/api/auth/password/forgot", json={"email": "luis@retail.co"})
    assert google.sent[-1]["msg"]["To"] == "luis@retail.co"
    token = _link(google)

    bad = client.post("/api/auth/password/reset", json={"token": token, "password": "corta"})
    assert bad.status_code == 422
    done = client.post(
        "/api/auth/password/reset", json={"token": token, "password": "NuevaClave2026"}
    )
    assert done.status_code == 200, done.text
    # Sus sesiones abiertas no se pueden renovar y el enlace ya no sirve.
    assert other.post("/api/auth/refresh").status_code == 401
    again = client.post(
        "/api/auth/password/reset", json={"token": token, "password": "OtraClave2026"}
    )
    assert again.status_code == 400
    fresh = _new_client(client)
    assert fresh.post("/api/auth/login",
                      json={"email": "luis@retail.co", "password": "NuevaClave2026"}).status_code == 200  # fmt: skip
    assert fresh.post("/api/auth/login",
                      json={"email": "luis@retail.co", "password": PASSWORD}).status_code == 401  # fmt: skip

    # Desde el panel: enlace para cualquier persona (y se envía por correo).
    luis = client.get("/api/admin/users", params={"q": "luis"}).json()["items"][0]
    link = client.post(f"/api/admin/users/{luis['id']}/reset-link").json()
    assert link["sent"] and "/app/restablecer/" in link["link"]
