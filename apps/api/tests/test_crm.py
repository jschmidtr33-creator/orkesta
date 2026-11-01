"""Etapa CRM: pipeline, oportunidades, línea de tiempo, tareas, permisos e indicadores."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from fastapi.testclient import TestClient

from tests.conftest import register


def _contact(client: TestClient, **data: str) -> str:
    response = client.post("/api/contacts", json=data)
    assert response.status_code == 201, response.text
    return response.json()["id"]


def _stages(client: TestClient) -> dict[str, dict]:
    return {s["name"]: s for s in client.get("/api/crm/stages").json()["stages"]}


def test_default_pipeline_and_deal_lifecycle(client: TestClient) -> None:
    register(client, "ana@lacteos.co")
    stages = _stages(client)
    assert list(stages)[:2] == ["Nuevo lead", "Contactado"]
    assert stages["Ganado"]["kind"] == "won" and stages["Perdido"]["kind"] == "lost"

    cid = _contact(client, first_name="Laura", last_name="Gómez", company="Banco Andino",
                   email="laura@bancoandino.co")  # fmt: skip
    deal = client.post("/api/crm/deals", json={"contact_id": cid, "value": 12000.5,
                                               "currency": "usd"}).json()  # fmt: skip
    assert deal["title"] == "Oportunidad · Banco Andino"
    assert deal["stage"] == "Nuevo lead" and deal["status"] == "open"
    assert deal["company"] == "Banco Andino" and deal["value"] == 12000.5
    # El contacto pasa a «En proceso» al tener una oportunidad.
    assert client.get(f"/api/contacts/{cid}").json()["status"] == "en_proceso"

    moved = client.post(f"/api/crm/deals/{deal['id']}/move",
                        json={"stage_id": stages["Propuesta enviada"]["id"]}).json()  # fmt: skip
    assert moved["stage"] == "Propuesta enviada"

    client.post("/api/crm/activities", json={"type": "call", "deal_id": deal["id"],
                                             "body": "Llamada de descubrimiento"})  # fmt: skip
    due = (datetime.now(UTC) - timedelta(days=1)).isoformat()
    task = client.post("/api/crm/activities", json={"type": "task", "deal_id": deal["id"],
                                                    "body": "Enviar propuesta", "due_at": due}).json()  # fmt: skip
    detail = client.get(f"/api/crm/deals/{deal['id']}").json()
    kinds = [a["type"] for a in detail["timeline"]]
    assert kinds[:3] == ["task", "call", "stage"] and "system" in kinds
    # La línea de tiempo del contacto incluye lo de sus oportunidades.
    contact_timeline = client.get("/api/crm/timeline", params={"contacto": cid}).json()
    assert len(contact_timeline) == len(detail["timeline"])

    metrics = client.get("/api/crm/deals").json()["metrics"]
    assert metrics["open"] == 1 and metrics["overdue_tasks"] == 1
    assert metrics["pipeline"] == {"USD": 12000.5}
    assert metrics["weighted"] == {"USD": 7200.3}  # 60 % de probabilidad en «Propuesta enviada»
    assert client.get("/api/crm/tasks").json()[0]["body"] == "Enviar propuesta"
    client.post(f"/api/crm/activities/{task['id']}/done")
    assert client.get("/api/crm/tasks").json() == []

    lost = client.post(f"/api/crm/deals/{deal['id']}/move",
                       json={"stage_id": stages["Perdido"]["id"], "lost_reason": "Presupuesto"}).json()  # fmt: skip
    assert lost["status"] == "lost" and lost["lost_reason"] == "Presupuesto"
    second = client.post("/api/crm/deals", json={"title": "Renovación", "contact_id": cid,
                                                 "stage_id": stages["Ganado"]["id"]}).json()  # fmt: skip
    assert second["status"] == "won"
    summary = client.get("/api/dashboard/summary").json()["kpis"]
    assert summary["open_opportunities"] == 0 and summary["conversion_rate"] == 50.0


def test_stage_management_rules(client: TestClient) -> None:
    register(client, "ana@lacteos.co")
    stages = _stages(client)
    created = client.post("/api/crm/stages", json={"name": "Demo técnica", "probability": 50})
    names = [s["name"] for s in created.json()["stages"]]
    # Las etapas abiertas nuevas quedan antes de Ganado y Perdido.
    assert names.index("Demo técnica") < names.index("Ganado")
    assert client.post("/api/crm/stages", json={"name": "demo técnica"}).status_code == 409
    deal = client.post(
        "/api/crm/deals", json={"title": "X", "stage_id": stages["Contactado"]["id"]}
    ).json()
    assert client.delete(f"/api/crm/stages/{stages['Contactado']['id']}").status_code == 409
    assert client.delete(f"/api/crm/stages/{stages['Ganado']['id']}").status_code == 409
    client.delete(f"/api/crm/deals/{deal['id']}")
    assert client.delete(f"/api/crm/stages/{stages['Contactado']['id']}").status_code == 200
    renamed = client.patch(
        f"/api/crm/stages/{stages['Nuevo lead']['id']}", json={"name": "Prospecto"}
    )
    assert renamed.json()["stages"][0]["name"] == "Prospecto"


def test_sellers_only_edit_their_deals_and_tenants_are_isolated(client: TestClient) -> None:
    register(client, "ana@lacteos.co")
    deal = client.post("/api/crm/deals", json={"title": "Grande"}).json()
    link = client.post(
        "/api/team/invitations", json={"email": "vend@lacteos.co", "role": "vendedor"}
    ).json()["link"]
    token = link.rsplit("/", 1)[-1]
    client.post("/api/auth/logout")
    client.post(f"/api/invitations/{token}/accept",
                json={"full_name": "Vende Dor", "password": "ClaveSegura2026"})  # fmt: skip
    assert client.get("/api/crm/deals").json()["deals"][0]["title"] == "Grande"
    assert client.patch(f"/api/crm/deals/{deal['id']}", json={"value": 1}).status_code == 403
    mine = client.post("/api/crm/deals", json={"title": "Mía"}).json()
    assert client.patch(f"/api/crm/deals/{mine['id']}", json={"value": 5}).json()["value"] == 5
    assert client.post("/api/crm/stages", json={"name": "Nueva"}).status_code == 403

    client.post("/api/auth/logout")
    register(client, "otro@otra.co", company="Otra")
    assert client.get("/api/crm/deals").json()["deals"] == []
    assert client.get(f"/api/crm/deals/{deal['id']}").status_code == 404
