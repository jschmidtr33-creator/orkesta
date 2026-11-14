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
from tests.test_admin import _role, _unlock


def test_extension_keys_contacts_and_revoke(
    client: TestClient, session_factory: sessionmaker[Session], database: tuple[str, str]
) -> None:
    register(client, "ana@lacteos.co")
    # Un usuario de la app no ve ni usa esto: es del panel de la plataforma.
    assert client.post("/api/admin/extension/tokens", json={}).status_code == 403
    _role(database, "ana@lacteos.co", "owner")
    _unlock(client, "ana@lacteos.co")
    made = client.post("/api/admin/extension/tokens", json={"name": "Chrome oficina"})
    assert made.status_code == 201, made.text
    token = made.json()["token"]
    assert token.startswith("ork_") and len(client.get("/api/admin/extension/tokens").json()) == 1
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
    # El encabezado que la persona ve en pantalla: nombre, cargo, empresa y ubicación.
    seen = ext.post("/api/ext/linkedin", headers=headers, json={
        "url": "https://www.linkedin.com/in/jennschmidtr/", "name": "Jenn Schmidt",
        "title": "Corporate Sales Team Leader", "company": "America Digital Congress",
        "location": "Bogotá, Distrito Capital, Colombia"})  # fmt: skip
    assert seen.status_code == 200 and seen.json()["created"] == 1
    jenn = next(
        c for c in client.get("/api/contacts").json()["items"] if c["last_name"] == "Schmidt"
    )
    assert jenn["company"] == "America Digital Congress" and jenn["country_code"] == "CO"
    assert jenn["job_title"] == "Corporate Sales Team Leader" and jenn["city"]
    assert client.get("/api/admin/extension/info").json()["version"]
    # Un perfil que se agregó antes con el nombre pegado se corrige con lo que se ve en pantalla.
    ext.post(
        "/api/ext/linkedin", headers=headers, json={"url": "linkedin.com/in/juanignacioscilino"}
    )
    fixed = ext.post("/api/ext/linkedin", headers=headers, json={
        "url": "https://www.linkedin.com/in/juanignacioscilino/", "name": "Juan Ignacio Scilino",
        "title": "Gerente Comercial", "company": "Banco Andino", "location": "Lima, Perú"})  # fmt: skip
    assert fixed.json()["updated"] == 1
    juan = next(
        c for c in client.get("/api/contacts").json()["items"] if c["first_name"].startswith("Juan")
    )
    assert (juan["first_name"], juan["last_name"]) == ("Juan Ignacio", "Scilino")
    assert juan["company"] == "Banco Andino" and juan["country_code"] == "PE"

    zipped = client.get("/api/admin/extension/download")
    names = zipfile.ZipFile(io.BytesIO(zipped.content)).namelist()
    assert "manifest.json" in names and "popup.js" in names and "icons/icon128.png" in names

    token_id = client.get("/api/admin/extension/tokens").json()[0]["id"]
    assert client.delete(f"/api/admin/extension/tokens/{token_id}").status_code == 200
    assert ext.get("/api/ext/me", headers=headers).status_code == 401
    assert ext.get("/api/ext/me").status_code == 401


def test_any_user_connects_the_extension_by_logging_in(client: TestClient) -> None:
    """Cualquier usuario instala la extensión y la conecta iniciando sesión en ORKESTA; volver
    a conectar revoca la clave anterior; «Desconectar» la anula."""
    register(client, "vendedor@lacteos.co")
    first = client.post("/api/extension/connect").json()["token"]
    second = client.post("/api/extension/connect").json()["token"]
    ext = TestClient(client.app)
    assert ext.get("/api/ext/me", headers={"Authorization": f"Bearer {first}"}).status_code == 401
    headers = {"Authorization": f"Bearer {second}", "Origin": "chrome-extension://abcdef"}
    assert ext.get("/api/ext/me", headers=headers).status_code == 200
    assert ext.post("/api/ext/logout", headers=headers).status_code == 200
    assert ext.get("/api/ext/me", headers=headers).status_code == 401
    assert TestClient(client.app).post("/api/extension/connect").status_code == 401  # sin sesión


def test_extension_saves_the_people_listed_on_screen(client: TestClient) -> None:
    """Lista en pantalla (búsqueda de leads o una tabla): marca quién ya está en Contactos y
    guarda los elegidos de una vez; los repetidos no se duplican."""
    register(client, "lista@lacteos.co")
    token = client.post("/api/extension/connect").json()["token"]
    ext = TestClient(client.app)
    headers = {"Authorization": f"Bearer {token}", "Origin": "chrome-extension://abcdef"}
    people = [
        {"first_name": "Marta Ríos", "job_title": "Directora de Compras",
         "company": "Lácteos del Valle", "location": "Cali, Valle del Cauca, Colombia",
         "linkedin_url": "https://www.linkedin.com/sales/lead/ACwAAB1,NAME_SEARCH"},
        {"first_name": "Tomás", "last_name": "Vera", "company": "Frutos Andinos",
         "email": "tomas.vera@frutosandinos.pe", "location": "Lima"},
        {"first_name": "Rosa Paz", "company": "Agro Sur", "email": "Ver correo",
         "location": "Área metropolitana", "linkedin_url": "https://www.linkedin.com/in/rosa-paz-9x/"},
    ]  # fmt: skip
    body = {"people": people, "source_url": "https://www.linkedin.com/sales/search/people"}
    assert ext.post("/api/ext/contacts/check", headers=headers, json=body).json()["exists"] == [
        False, False, False]  # fmt: skip
    saved = ext.post("/api/ext/contacts/bulk", headers=headers, json=body)
    assert saved.status_code == 200, saved.text
    assert saved.json()["created"] == 3 and saved.json()["existing"] == 0
    assert saved.json()["pending"] == 2  # los que no traen correo pasan a enriquecimiento
    check = ext.post("/api/ext/contacts/check", headers=headers, json=body).json()["exists"]
    assert check == [True, True, True]
    again = ext.post("/api/ext/contacts/bulk", headers=headers, json=body).json()
    assert again["created"] == 0 and again["existing"] == 3
    items = client.get("/api/contacts").json()["items"]
    rosa = next(c for c in items if c["first_name"] == "Rosa")
    assert rosa["last_name"] == "Paz" and not rosa["email"]
    marta = next(c for c in items if c["first_name"] == "Marta")
    assert marta["country_code"] == "CO" and marta["job_title"] == "Directora de Compras"
    too_many = {"people": [{"first_name": f"P{i}"} for i in range(101)]}
    assert ext.post("/api/ext/contacts/bulk", headers=headers, json=too_many).status_code == 422


def test_manifest_meets_chrome_web_store_limits() -> None:
    """La Chrome Web Store rechaza descripciones de más de 132 caracteres y nombres de más de 75."""
    import json

    from orkesta.extension.router import FILES

    manifest = json.loads((FILES / "manifest.json").read_text())
    assert len(manifest["description"]) <= 132 and len(manifest["name"]) <= 75
    assert manifest["manifest_version"] == 3 and manifest["version"]
    # El panel flotante: se abre con el ícono (sin ventana emergente) y la ventana de la extensión
    # solo se puede incrustar con la marca que registra el panel.
    assert "default_popup" not in manifest["action"]
    files = {f.name for f in FILES.rglob("*")}
    for script in manifest["content_scripts"]:
        assert all(js in files for js in script["js"])
    assert {"panel.js", "capture.js", "background.js", "popup.html"} <= files
    assert "panel-check" in (FILES / "popup.js").read_text()
