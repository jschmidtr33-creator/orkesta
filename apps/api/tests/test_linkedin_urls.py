"""Agregar contactos pegando direcciones de LinkedIn: ORKESTA no abre LinkedIn; usa su índice
propio y el nombre de la dirección, y deja el resto al enriquecimiento. Datos sintéticos."""

from __future__ import annotations

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
