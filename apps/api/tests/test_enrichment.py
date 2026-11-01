"""Enriquecimiento de contactos importados: dominio, formato de correo y perfil público."""

from __future__ import annotations

import json
from collections.abc import Iterator

import httpx
import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session, sessionmaker

from orkesta.core.jobs import run_once
from orkesta.research import fetch
from tests.conftest import register
from tests.test_research import _internet

PROFILE = {
    "organic": [
        {"title": "Pedro Ríos - Director de Tecnología - Banco Andino | LinkedIn",
         "link": "https://co.linkedin.com/in/pedro-rios", "snippet": "Medellín, Antioquia, Colombia"},
    ]
}  # fmt: skip


@pytest.fixture()
def serper(monkeypatch: pytest.MonkeyPatch) -> Iterator[list[str]]:
    from orkesta.core.config import get_settings
    from orkesta.enrichment import router

    monkeypatch.setattr(get_settings(), "serper_api_key", "clave-serper")
    router.limiter.hits.clear()
    asked: list[str] = []

    def route(request: httpx.Request) -> httpx.Response:
        if request.url.host == "google.serper.dev":
            q = json.loads(request.content)["q"]
            asked.append(q)
            if q.startswith('"Banco Andino"') and "sitio oficial" in q:
                return httpx.Response(
                    200, json={"knowledgeGraph": {"website": "https://www.bancoandino.co"}}
                )
            if q.startswith('site:linkedin.com/in "Pedro Ríos"'):
                return httpx.Response(200, json=PROFILE)
            if q == '"@retailsur.com"':
                return httpx.Response(200, json={"organic": [{"title": "Prensa", "link": "https://x.co",
                    "snippet": "Contacto de prensa: Marta Ríos marta.rios@retailsur.com"}]})  # fmt: skip
            return httpx.Response(200, json={"organic": []})
        return _internet(request)

    fetch.TRANSPORT = httpx.MockTransport(route)
    fetch.RESOLVER = lambda h: ["93.184.216.34"]
    yield asked
    fetch.TRANSPORT, fetch.RESOLVER = None, None


def _contact(client: TestClient, **data: str) -> str:
    response = client.post("/api/contacts", json=data)
    assert response.status_code == 201, response.text
    return response.json()["id"]


def _enrich(client: TestClient, factory: sessionmaker[Session], **body: object) -> dict:
    created = client.post("/api/enrichment/runs", json=body)
    assert created.status_code == 201, created.text
    while run_once(factory, "pruebas"):
        pass
    return client.get(f"/api/enrichment/runs/{created.json()['id']}").json()


def test_imported_contacts_get_domain_email_and_profile(
    client: TestClient, session_factory: sessionmaker[Session], serper: list[str]
) -> None:
    register(client, "ana@lacteos.co")
    pedro = _contact(client, first_name="Pedro", last_name="Ríos", company="Banco Andino",
                     country="Colombia")  # fmt: skip
    # En otra empresa ya hay un correo real en la base: evidencia interna del formato.
    _contact(client, first_name="Lucía", last_name="Mora", company="Retail Sur",
             email="lucia.mora@retailsur.com")  # fmt: skip
    juan = _contact(client, first_name="Juan", last_name="Pérez", company="Retail Sur")
    sin = _contact(client, first_name="Ana", last_name="Solo")

    run = _enrich(client, session_factory)
    assert run["status"] == "completed", run
    t = run["totals"]
    assert t["domains_found"] == 1 and t["emails"] == 2 and t["profiles"] >= 1

    p = client.get(f"/api/contacts/{pedro}").json()
    # Dominio por el buscador; formato del sitio (publica dcastro@ junto a «Diana Castro»).
    assert p["email"] == "prios@bancoandino.co" and p["email_status"] == "estimado"
    assert p["linkedin_url"] == "https://co.linkedin.com/in/pedro-rios"
    assert p["job_title"] == "Director de Tecnología" and p["city"] == "Medellín"
    assert p["status"] == "enriquecido"
    email_field = next(f for f in p["fields"] if f["field"] == "email")
    assert email_field["sources"][0]["source_type"] == "enriquecimiento"

    j = client.get(f"/api/contacts/{juan}").json()
    assert j["email"] == "juan.perez@retailsur.com"
    assert "de tu base" in j["email_check_reason"]
    # Sin empresa no se inventa nada.
    assert client.get(f"/api/contacts/{sin}").json()["email"] is None


def test_existing_data_is_never_overwritten(
    client: TestClient, session_factory: sessionmaker[Session], serper: list[str]
) -> None:
    register(client, "ana@lacteos.co")
    pedro = _contact(client, first_name="Pedro", last_name="Ríos", company="Banco Andino",
                     job_title="Gerente de Innovación", city="Bogotá", country="Colombia")  # fmt: skip
    _enrich(client, session_factory, ids=[pedro])
    p = client.get(f"/api/contacts/{pedro}").json()
    assert p["job_title"] == "Gerente de Innovación" and p["city"] == "Bogotá"
    assert p["linkedin_url"] == "https://co.linkedin.com/in/pedro-rios"
