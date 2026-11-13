"""Agregar contactos pegando direcciones de LinkedIn: ORKESTA no abre LinkedIn; usa su índice
propio y el nombre de la dirección, y deja el resto al enriquecimiento. Datos sintéticos."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session, sessionmaker

from orkesta.data.linkedin import parse
from tests.conftest import register


def test_parse_names_from_urls() -> None:
    profiles, invalid = parse(
        "https://www.linkedin.com/in/carlos-ramirez-4b2a91/\n"
        "co.linkedin.com/in/maria-de-la-torre?trk=x, https://linkedin.com/in/carlos-ramirez-4b2a91 "
        "https://google.com/algo"
    )
    assert [(p.first, p.last) for p in profiles] == [
        ("Carlos", "Ramirez"),
        ("Maria", "de la Torre"),
    ]
    assert invalid == ["https://google.com/algo"]


def test_add_from_linkedin_uses_index_and_queues_enrichment(
    client: TestClient, session_factory: sessionmaker[Session]
) -> None:
    from orkesta.research import people

    register(client, "ana@lacteos.co")
    people.upsert(session_factory, [{
        "key": "li:/in/laura-gomez-77", "country": "CO", "full_name": "Laura Gómez",
        "first_name": "Laura", "last_name": "Gómez", "title": "CTO", "level": "c_level",
        "area": "tecnologia", "company": "Andes Logística", "company_key": "andes logistica",
        "domain": "andeslogistica.co", "location": "Bogotá, Colombia",
        "linkedin": "https://www.linkedin.com/in/laura-gomez-77", "email": "lgomez@andeslogistica.co",
        "email_status": "publicado", "source": "busquedas",
    }])  # fmt: skip
    r = client.post("/api/contacts/linkedin", json={
        "urls": "https://www.linkedin.com/in/laura-gomez-77\nlinkedin.com/in/pedro-ruiz",
        "company": "Banco Andino", "country": "CO",
    })  # fmt: skip
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["created"] == 2 and body["from_index"] == 1 and body["with_email"] == 1
    assert body["enrichment_id"]  # Pedro (sin correo, con empresa) queda para enriquecer
    items = {c["full_name"]: c for c in client.get("/api/contacts").json()["items"]}
    assert items["Laura Gómez"]["email"] == "lgomez@andeslogistica.co"
    assert items["Pedro Ruiz"]["company"] == "Banco Andino"
    assert items["Pedro Ruiz"]["linkedin_url"] == "https://www.linkedin.com/in/pedro-ruiz"
    again = client.post("/api/contacts/linkedin", json={"urls": "linkedin.com/in/pedro-ruiz",
                                                         "company": "Banco Andino"}).json()  # fmt: skip
    assert again["created"] == 0 and again["existing"] == 1
    bad = client.post("/api/contacts/linkedin", json={"urls": "hola"})
    assert bad.status_code == 400


def test_profile_without_hyphens_gets_name_title_and_company_from_google(
    client: TestClient, session_factory: sessionmaker[Session], monkeypatch: pytest.MonkeyPatch
) -> None:
    """«/in/santiagojoyaibarra»: el resultado público de Google da nombre, cargo, empresa y
    ciudad; ORKESTA no entra a LinkedIn."""
    import json

    import httpx

    from orkesta.core.config import get_settings
    from orkesta.research import fetch

    monkeypatch.setattr(get_settings(), "serper_api_key", "clave-serper")
    asked: list[str] = []

    def route(request: httpx.Request) -> httpx.Response:
        q = json.loads(request.content)["q"]
        asked.append(q)
        if q == "site:linkedin.com/in/santiagojoyaibarra":
            return httpx.Response(200, json={"organic": [{
                "title": "Santiago Joya Ibarra - Gerente Comercial - Banco Andino | LinkedIn",
                "link": "https://co.linkedin.com/in/santiagojoyaibarra",
                "snippet": "Ubicación: Bogotá, Colombia · Experiencia: Banco Andino",
            }]})  # fmt: skip
        return httpx.Response(200, json={"organic": []})

    monkeypatch.setattr(fetch, "TRANSPORT", httpx.MockTransport(route))
    monkeypatch.setattr(fetch, "RESOLVER", lambda h: ["93.184.216.34"])
    register(client, "ana@lacteos.co")
    # Ya se había agregado incompleto (como antes de este cambio): se completa, sin duplicarlo.
    monkeypatch.setattr(get_settings(), "serper_api_key", "")
    client.post("/api/contacts/linkedin", json={"urls": "linkedin.com/in/santiagojoyaibarra"})
    monkeypatch.setattr(get_settings(), "serper_api_key", "clave-serper")
    r = client.post("/api/contacts/linkedin",
                    json={"urls": "https://www.linkedin.com/in/santiagojoyaibarra/"})  # fmt: skip
    body = r.json()
    assert r.status_code == 200 and body["from_search"] == 1 and body["updated"] == 1
    items = client.get("/api/contacts").json()["items"]
    assert len(items) == 1
    item = items[0]
    assert (item["first_name"], item["last_name"]) == ("Santiago", "Joya Ibarra")
    assert item["job_title"] == "Gerente Comercial" and item["company"] == "Banco Andino"
    assert item["country_code"] == "CO"
