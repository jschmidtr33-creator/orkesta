"""Borrar un contacto lo quita de la base del cliente (y de sus listas), pero lo aprendido sigue
en el índice global de ORKESTA. Quitar de una lista no lo borra de Contactos. Datos sintéticos."""

from __future__ import annotations

from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from orkesta.research.models import PersonIndex
from tests.conftest import register


def test_remove_from_list_or_delete_but_index_keeps_it(
    client: TestClient, session_factory: sessionmaker[Session]
) -> None:
    register(client, "ana@lacteos.co")
    cid = client.post("/api/contacts", json={
        "first_name": "Marta", "last_name": "Ríos", "email": "mrios@frutosandinos.com.co",
        "company": "Frutos Andinos", "job_title": "Gerente de Compras", "country": "CO",
    }).json()["id"]  # fmt: skip
    lst = client.post("/api/lists", json={"name": "Feria"}).json()["id"]
    client.post(f"/api/lists/{lst}/members", json={"ids": [cid]})
    assert client.post(f"/api/lists/{lst}/remove", json={"ids": [cid]}).json()["removed"] == 1
    assert client.get(f"/api/contacts/{cid}").status_code == 200  # sigue en Contactos
    assert client.post("/api/contacts/delete", json={"ids": [cid]}).json()["deleted"] == 1
    assert client.get(f"/api/contacts/{cid}").status_code == 404
    with session_factory() as session:
        row = session.scalar(select(PersonIndex).where(PersonIndex.full_name == "Marta Ríos"))
    assert row is not None and row.email == "mrios@frutosandinos.com.co"
