"""Extensión de Chrome: claves personales (solo huella), guardar un contacto elegido en una
página, agregar un perfil por su dirección y revocar. Datos sintéticos."""

from __future__ import annotations

import io
import zipfile

from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from orkesta.platform.models import ApiToken
from tests.conftest import register


def test_extension_keys_contacts_and_revoke(
    client: TestClient, session_factory: sessionmaker[Session]
) -> None:
    register(client, "ana@lacteos.co")
    made = client.post("/api/extension/tokens", json={"name": "Chrome oficina"})
    assert made.status_code == 201, made.text
    token = made.json()["token"]
    assert token.startswith("ork_") and len(client.get("/api/extension/tokens").json()) == 1
    with session_factory() as session:
        row = session.scalar(select(ApiToken))
        assert row is not None and token not in (row.token_hash, row.prefix)  # solo la huella

    ext = TestClient(client.app)  # sin cookies: solo la clave
    headers = {"Authorization": f"Bearer {token}", "Origin": "chrome-extension://abcdef"}
    assert ext.get("/api/ext/me", headers=headers).json()["name"]
    saved = ext.post("/api/ext/contacts", headers=headers, json={
        "first_name": "Laura Gómez", "job_title": "Gerente Comercial", "company": "Andes Logística",
        "website": "https://www.andeslogistica.co/nosotros", "country": "Colombia",
        "source_url": "https://www.andeslogistica.co/nosotros",
    })  # fmt: skip
    assert saved.status_code == 201, saved.text
    assert saved.json()["name"] == "Laura Gómez" and saved.json()["enrichment_id"]
    contact = client.get(f"/api/contacts/{saved.json()['id']}").json()
    assert contact["last_name"] == "Gómez" and contact["company"] == "Andes Logística"
    again = ext.post("/api/ext/contacts", headers=headers, json={
        "first_name": "Laura", "last_name": "Gómez", "company": "Andes Logística"})  # fmt: skip
    assert again.status_code == 409
    profile = ext.post("/api/ext/linkedin", headers=headers,
                       json={"url": "https://www.linkedin.com/in/pedro-ruiz-12ab34", "company": "Banco Andino"})  # fmt: skip
    assert profile.status_code == 200 and profile.json()["created"] == 1

    zipped = client.get("/api/extension/download")
    names = zipfile.ZipFile(io.BytesIO(zipped.content)).namelist()
    assert "manifest.json" in names and "popup.js" in names and "icons/icon128.png" in names

    token_id = client.get("/api/extension/tokens").json()[0]["id"]
    assert client.delete(f"/api/extension/tokens/{token_id}").status_code == 200
    assert ext.get("/api/ext/me", headers=headers).status_code == 401
    assert ext.get("/api/ext/me").status_code == 401
