"""Propuestas comerciales: catálogo, borrador propio, totales, envío y enlace público."""

from __future__ import annotations

from fastapi.testclient import TestClient

from tests.conftest import register


def _setup(client: TestClient) -> tuple[str, str]:
    register(client, "ana@lacteos.co")
    contact = client.post("/api/contacts", json={"first_name": "Laura", "last_name": "Gómez",
        "company": "Banco Andino", "industry": "Banca", "email": "laura@bancoandino.co"}).json()  # fmt: skip
    deal = client.post("/api/crm/deals", json={"contact_id": contact["id"], "value": 10000}).json()
    client.post("/api/crm/activities", json={"type": "meeting", "deal_id": deal["id"],
        "body": "Necesitan reducir el tiempo de apertura de cuentas"})  # fmt: skip
    return contact["id"], deal["id"]


def test_catalog_draft_totals_and_public_acceptance(client: TestClient) -> None:
    contact_id, deal_id = _setup(client)
    product = client.post("/api/products", json={"name": "Licencia anual", "unit_price": 1200,
        "description": "Plataforma completa", "unit": "usuario"}).json()  # fmt: skip
    assert client.post("/api/products", json={"name": "licencia anual"}).status_code == 409

    created = client.post(
        "/api/proposals", json={"deal_id": deal_id, "product_ids": [product["id"]]}
    )
    assert created.status_code == 201, created.text
    p = created.json()
    assert (
        p["number"] == 1 and p["status"] == "draft" and p["title"] == "Propuesta para Banco Andino"
    )
    assert p["items"][0]["name"] == "Licencia anual" and p["contact"]["full_name"] == "Laura Gómez"
    body = {s["key"]: s["body"] for s in p["sections"]}
    # El borrador usa datos reales: saludo, industria y lo que se habló en la reunión.
    assert body["resumen"].startswith("Hola, Laura:")
    assert "sector financiero" in body["necesidad"]
    assert "apertura de cuentas" in body["necesidad"]
    assert "Licencia anual" in body["solucion"]

    updated = client.patch(f"/api/proposals/{p['id']}", json={
        "items": [{"name": "Licencia anual", "quantity": 10, "unit_price": 1200, "discount": 10},
                  {"name": "Implementación", "quantity": 1, "unit_price": 3000}],
        "tax_rate": 19,
    }).json()  # fmt: skip
    assert updated["totals"] == {"subtotal": 13800.0, "discounts": 1200.0, "tax": 2622.0,
                                 "total": 16422.0}  # fmt: skip

    public_id = updated["public_id"]
    # Vista previa del equipo: no cuenta como apertura del cliente.
    client.get(f"/api/public/proposals/{public_id}", params={"vista_previa": True})
    assert client.get(f"/api/proposals/{p['id']}").json()["view_count"] == 0
    # Sin enviar no se puede aceptar.
    assert client.post(f"/api/public/proposals/{public_id}/decision",
                       json={"accept": True, "name": "Laura Gómez"}).status_code == 409  # fmt: skip

    sent = client.post(f"/api/proposals/{p['id']}/send").json()
    assert sent["status"] == "sent"
    deal = client.get(f"/api/crm/deals/{deal_id}").json()
    assert deal["stage"] == "Propuesta enviada"

    client.post("/api/auth/logout")  # el cliente no tiene sesión
    view = client.get(f"/api/public/proposals/{public_id}").json()
    assert view["issuer"] == "Lácteos Inventados" and view["totals"]["total"] == 16422.0
    assert "public_id" not in view and "id" not in view
    accepted = client.post(f"/api/public/proposals/{public_id}/decision",
                           json={"accept": True, "name": "Laura Gómez", "note": "Adelante"}).json()  # fmt: skip
    assert accepted["status"] == "accepted" and accepted["decided_by"] == "Laura Gómez"
    assert client.post(f"/api/public/proposals/{public_id}/decision",
                       json={"accept": False}).status_code == 409  # fmt: skip

    client.post("/api/auth/login", json={"email": "ana@lacteos.co", "password": "ClaveSegura2026"})
    final = client.get(f"/api/proposals/{p['id']}").json()
    assert final["view_count"] == 1 and final["status"] == "accepted"
    assert client.get(f"/api/crm/deals/{deal_id}").json()["status"] == "won"
    timeline = [
        a["body"] for a in client.get("/api/crm/timeline", params={"contacto": contact_id}).json()
    ]
    assert any("aceptó la propuesta" in t for t in timeline)
    assert any("abrió la propuesta" in t for t in timeline)
    # Respondida, ya no se edita: se duplica.
    assert client.patch(f"/api/proposals/{p['id']}", json={"title": "X"}).status_code == 409
    copy = client.post(f"/api/proposals/{p['id']}/duplicate").json()
    assert copy["number"] == 2 and copy["status"] == "draft" and copy["public_id"] != public_id


def test_proposals_are_isolated_and_unknown_links_fail(client: TestClient) -> None:
    _, deal_id = _setup(client)
    p = client.post("/api/proposals", json={"deal_id": deal_id}).json()
    # Sin catálogo, la línea toma el valor de la oportunidad.
    assert p["items"][0]["unit_price"] == 10000 and p["totals"]["total"] == 10000
    client.post("/api/auth/logout")
    register(client, "otro@otra.co", company="Otra")
    assert client.get(f"/api/proposals/{p['id']}").status_code == 404
    assert client.get("/api/proposals").json() == []
    assert client.get("/api/public/proposals/no-existe").status_code == 404


def test_brand_design_templates_and_language(client: TestClient) -> None:
    contact_id, deal_id = _setup(client)
    brand = client.get("/api/proposal-brand").json()
    assert len(brand["templates"]) == 5 and set(brand["languages"]) == {"es", "en", "pt"}
    assert brand["brand"]["legal_name"] == "Lácteos Inventados"
    saved = client.put("/api/proposal-brand", json={
        "primary": "#0f766e", "accent": "#f59e0b", "template": "moderna", "language": "en",
        "logo_url": "https://cdn.example.com/logo.png", "website": "lacteos.co",
    }).json()["brand"]  # fmt: skip
    assert saved["primary"] == "#0F766E" and saved["template"] == "moderna"
    assert client.put("/api/proposal-brand", json={"logo_url": "javascript:x"}).status_code == 400

    p = client.post("/api/proposals", json={"deal_id": deal_id}).json()
    assert p["design"]["template"] == "moderna" and p["design"]["language"] == "en"
    assert p["title"] == "Proposal for Banco Andino"
    assert p["sections"][0]["title"] == "Executive summary"
    assert p["design"]["executive"]["name"] == "Ana López"

    updated = client.patch(f"/api/proposals/{p['id']}", json={"design": {
        "template": "audaz", "language": "pt", "primary": "#111827",
        "executive": {"name": "Ana López", "title": "Gerente comercial", "phone": "+57 300"},
    }}).json()  # fmt: skip
    assert updated["look"]["template"] == "audaz" and updated["look"]["primary"] == "#111827"
    assert updated["look"]["accent"] == "#F59E0B"  # heredado de la marca
    regen = client.post(f"/api/proposals/{p['id']}/regenerate").json()
    assert regen["sections"][0]["title"] == "Resumo executivo"
    assert regen["title"] == "Proposta para Banco Andino"

    view = client.get(
        f"/api/public/proposals/{p['public_id']}", params={"vista_previa": True}
    ).json()
    assert view["look"]["executive"]["title"] == "Gerente comercial"
    assert view["look"]["logo_url"] == "https://cdn.example.com/logo.png"
    # La siguiente propuesta recuerda los datos del ejecutivo.
    nxt = client.post("/api/proposals", json={"contact_id": contact_id}).json()
    assert nxt["design"]["executive"]["title"] == "Gerente comercial"


def test_rich_catalog_with_image_and_payment_link(client: TestClient) -> None:
    _, deal_id = _setup(client)
    product = client.post("/api/products", json={
        "name": "Club Elite Persona", "unit_price": 990, "unit": "Por persona",
        "description": "<p><strong>Pase VIP Anual</strong></p><ul><li>Miami</li><li>México</li></ul>"
                       "<p onclick='x()'>Beneficios<script>alert(1)</script></p>",
        "image_url": "https://cdn.example.com/club.png",
        "payment_url": "https://pay.example.com/club",
    })  # fmt: skip
    assert product.status_code == 201, product.text
    body = product.json()
    assert "<strong>Pase VIP Anual</strong>" in body["description"]
    assert "script" not in body["description"] and "onclick" not in body["description"]
    bad = client.post("/api/products", json={"name": "Otro", "payment_url": "javascript:x"})
    assert bad.status_code == 400

    p = client.post("/api/proposals", json={"deal_id": deal_id,
                                            "product_ids": [body["id"]]}).json()  # fmt: skip
    item = p["items"][0]
    assert item["image_url"].endswith("club.png") and item["payment_url"].endswith("/club")
    solution = next(s["body"] for s in p["sections"] if s["key"] == "solucion")
    assert "Club Elite Persona: Pase VIP Anual" in solution and "<" not in solution
    view = client.get(f"/api/public/proposals/{p['public_id']}", params={"vista_previa": True})
    assert view.json()["items"][0]["payment_url"] == "https://pay.example.com/club"
