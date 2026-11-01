"""Asistente de redacción con IA propia: secuencias, revisión y buyer personas."""

from __future__ import annotations

from fastapi.testclient import TestClient

from orkesta.campaigns import templating
from tests.conftest import register


def test_compose_sequence_with_persona_and_product(client: TestClient) -> None:
    register(client, "ana@lacteos.co", company="Lácteos Inventados")
    options = client.get("/api/copy/options").json()
    assert {o["value"] for o in options["frameworks"]} >= {"pas", "aida", "bab"}
    assert any(p["value"] == "compras" for p in options["personas"])

    product = client.post("/api/products", json={"name": "Leche deslactosada",
                                                 "description": "<p>Leche fresca sin lactosa.</p>",
                                                 "unit_price": 10}).json()  # fmt: skip
    r = client.post("/api/copy/compose", json={
        "product_id": product["id"], "benefits": ["reducir quiebres de inventario un 20%"],
        "proof": "Cadenas en 3 países ya la venden", "area": "compras",
        "industry": "retail_consumo", "goal": "reunion", "tone": "formal", "framework": "pas",
        "steps": 4, "link": "https://cal.com/ana",
    })  # fmt: skip
    assert r.status_code == 200, r.text
    steps = r.json()["steps"]
    assert len(steps) == 4 and [s["delay_days"] for s in steps] == [0, 3, 4, 5]
    first = steps[0]
    assert len(first["subjects"]) >= 3 and first["subject"] in first["subjects"]
    assert "Leche deslactosada" in first["body"] and "{{empresa|su equipo}}" in first["body"]
    assert "retail y consumo" in first["body"] and "líderes de compras" in first["body"]
    assert "https://cal.com/ana" in first["body"] and "Lácteos Inventados" in first["body"]
    assert first["review"]["score"] >= 80
    assert all(s["subject"] == "" for s in steps[1:])  # seguimientos en el mismo hilo

    # Persona automático: cada contacto recibe el reto de su área al enviar.
    auto = client.post("/api/copy/compose", json={"product": "ORKESTA", "area": "auto",
                                                  "framework": "bab"}).json()  # fmt: skip
    assert "{{reto|" in auto["steps"][0]["body"] and "{{industria|" in auto["steps"][0]["body"]
    assert client.post("/api/copy/compose", json={"product": ""}).status_code == 400


def test_review_and_saved_personas(client: TestClient) -> None:
    register(client, "ana@lacteos.co")
    bad = client.post("/api/copy/analyze", json={
        "subject": "OFERTA EXCLUSIVA GRATIS!!!",
        "body": "<p>Espero que estés bien. Mi nombre es Ana y nuestra empresa es líder. Nosotros "
                "somos los mejores y nuestro producto es increíble. Haz clic aquí.</p>",
    }).json()  # fmt: skip
    ids = {c["id"]: c["status"] for c in bad["checks"]}
    assert ids["mayusculas"] == "bad" and ids["spam"] == "bad" and ids["cta"] == "bad"
    assert ids["personalizacion"] == "bad" and ids["foco"] == "warn" and bad["score"] < 40

    created = client.post("/api/copy/personas", json={
        "name": "Jefes de compras retail", "area": "compras", "industry": "retail_consumo",
        "pains": ["proveedores que incumplen entregas"], "goals": ["ahorro"],
    })  # fmt: skip
    assert created.status_code == 201
    pid = created.json()["id"]
    assert (
        client.post("/api/copy/personas", json={"name": "Jefes de compras retail"}).status_code
        == 409
    )
    saved = client.get("/api/copy/options").json()["saved_personas"]
    assert saved[0]["pains"] == ["proveedores que incumplen entregas"]
    body = client.post("/api/copy/compose", json={"product": "Leche", "persona_id": pid}).json()
    assert "proveedores que incumplen entregas" in body["steps"][0]["body"]
    assert client.delete(f"/api/copy/personas/{pid}").json() == {"ok": True}


def test_contact_variables_reto_and_industria() -> None:
    class C:
        first_name, last_name, full_name = "Ana", "Ríos", "Ana Ríos"
        company_name, job_title, city = "Banco Andino", "Gerente de Compras", "Bogotá"
        area_code, industry_code, industry = "compras", "banca_finanzas", "Banca"

    values = templating.context(C(), "Jenn")
    assert values["industria"] == "el sector financiero"
    assert "proveedores" in values["reto"]


def test_symbols_are_not_false_spam() -> None:
    from orkesta.copywriter.analyzer import analyze

    r = analyze(
        "Idea para {{empresa}}", "<p>Hola {{nombre}}, en 2026 crecimos 100 clientes. ¿Hablamos?</p>"
    )
    assert next(c for c in r["checks"] if c["id"] == "spam")["status"] == "ok"
