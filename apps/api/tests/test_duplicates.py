"""Duplicados en Contactos: se detectan, se sugiere cuál conservar y se borran los demás."""

from __future__ import annotations

from fastapi.testclient import TestClient

from tests.conftest import register


def test_duplicates_are_detected_and_deleted(client: TestClient) -> None:
    register(client, "ana@lacteos.co")

    def add(**data: str) -> str:
        r = client.post("/api/contacts", json=data)
        assert r.status_code == 201, r.text
        return r.json()["id"]

    full = add(first_name="María", last_name="Paz", company="Banco Andino", job_title="CFO",
               email="maria.paz@bancoandino.co")  # fmt: skip
    bare = add(first_name="Maria", last_name="Paz", company="Banco Andino")
    add(first_name="Luis", last_name="Rojas", company="Banco Andino", email="luis@bancoandino.co")
    add(first_name="Luis", last_name="Rojas", company="Otra Empresa")
    found = client.get("/api/contacts/duplicates").json()
    assert found["count"] == 1 and len(found["groups"]) == 1
    group = found["groups"][0]
    assert group["reason"] == "Mismo nombre y empresa"
    assert [m["id"] for m in group["members"]] == [full, bare]
    assert group["members"][0]["keep"] and not group["members"][1]["keep"]
    assert client.post("/api/contacts/delete", json={"ids": [bare]}).json()["deleted"] == 1
    assert client.get("/api/contacts/duplicates").json() == {"groups": [], "count": 0}
