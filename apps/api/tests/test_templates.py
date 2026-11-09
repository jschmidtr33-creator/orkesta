"""Plantillas de equipo: una persona guarda su campaña o propuesta y el resto puede usarla."""

from __future__ import annotations

from fastapi.testclient import TestClient

from tests.conftest import register
from tests.test_supervision import _join


def test_campaign_and_proposal_templates_are_shared_with_the_company(client: TestClient) -> None:
    register(client, "ana@lacteos.co")
    luis = _join(client, "luis@lacteos.co", "vendedor", "Luis")

    # Ana arma su campaña y la guarda como plantilla de equipo.
    campaign = client.post("/api/campaigns", json={"name": "Lanzamiento Q1"}).json()
    steps = [{"subject": "{{nombre}}, idea para {{empresa}}", "body": "Hola {{nombre}}",
              "delay_days": 0}, {"subject": "", "body": "¿Lo viste?", "delay_days": 4}]  # fmt: skip
    client.patch(f"/api/campaigns/{campaign['id']}", json={"steps": steps})
    # «Guardar como plantilla»: solo para quien la guarda.
    personal = client.post("/api/templates", json={
        "kind": "campaign", "source_id": campaign["id"], "name": "Solo mía"})  # fmt: skip
    assert personal.status_code == 201, personal.text
    assert luis.get("/api/templates", params={"tipo": "campaign"}).json() == []
    assert luis.post("/api/campaigns", json={"name": "X", "template_id": personal.json()["id"]}
                     ).status_code in (400, 404)  # fmt: skip
    # «Compartir con el equipo»: la ve toda la empresa.
    saved = client.post("/api/templates", json={
        "kind": "campaign", "source_id": campaign["id"], "name": "Secuencia retail",
        "description": "La que mejor responde", "shared": True})  # fmt: skip
    assert saved.status_code == 201, saved.text
    assert {t["name"]: t["shared"] for t in client.get(
        "/api/templates", params={"tipo": "campaign"}).json()} == {
        "Solo mía": False, "Secuencia retail": True}  # fmt: skip

    # Luis no ve la campaña de Ana, pero sí la plantilla, y crea la suya con ella.
    assert luis.get("/api/campaigns").json()["items"] == []
    listed = luis.get("/api/templates", params={"tipo": "campaign"}).json()
    assert [t["name"] for t in listed] == ["Secuencia retail"] and listed[0][
        "author"
    ] == "Ana López"
    assert listed[0]["can_manage"] is False and listed[0]["summary"] == "2 correos"
    mine = luis.post("/api/campaigns", json={"name": "Mi campaña",
                                             "template_id": saved.json()["id"]}).json()  # fmt: skip
    detail = luis.get(f"/api/campaigns/{mine['id']}").json()
    got = detail["steps"]
    assert [s["body"] for s in got] == ["Hola {{nombre}}", "¿Lo viste?"]
    used = {t["name"]: t["use_count"] for t in client.get("/api/templates",
                                                          params={"tipo": "campaign"}).json()}  # fmt: skip
    assert used["Secuencia retail"] == 1
    # Solo quien la creó (o la administración) la cambia o la borra.
    assert luis.delete(f"/api/templates/{saved.json()['id']}").status_code == 403

    # Propuestas: el contenido y los productos viajan; el cliente no.
    proposal = client.post("/api/proposals", json={}).json()
    client.patch(f"/api/proposals/{proposal['id']}", json={
        "items": [{"name": "Licencia anual", "quantity": 10, "unit_price": 1200}],
        "tax_rate": 19})  # fmt: skip
    ptpl = client.post("/api/templates", json={"kind": "proposal", "source_id": proposal["id"],
                                               "name": "Licencias estándar", "shared": True}).json()  # fmt: skip
    new = luis.post("/api/proposals", json={"template_id": ptpl["id"]}).json()
    assert [i["name"] for i in new["items"]] == ["Licencia anual"] and new["tax_rate"] in (
        19,
        "19.00",
        19.0,
    )
    assert client.delete(f"/api/templates/{ptpl['id']}").json() == {"ok": True}
    assert luis.get("/api/templates", params={"tipo": "proposal"}).json() == []
