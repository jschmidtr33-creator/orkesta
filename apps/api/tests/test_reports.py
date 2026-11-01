"""Reportes: embudo, comparación con el período anterior, series, campañas, equipo y pipeline."""

from __future__ import annotations

from fastapi.testclient import TestClient
from sqlalchemy.orm import Session, sessionmaker

from tests.conftest import register
from tests.test_campaigns import FakeGoogle, _connect, _contacts, _flush, google  # noqa: F401


def test_overview_numbers(
    client: TestClient, google: FakeGoogle, session_factory: sessionmaker[Session],  # noqa: F811
) -> None:  # fmt: skip
    register(client, "ana@lacteos.co")
    _connect(client)
    ids = _contacts(client)
    campaign = client.post("/api/campaigns", json={"name": "Bancos"}).json()
    client.patch(f"/api/campaigns/{campaign['id']}", json={
        "steps": [{"subject": "Hola", "body": "Mensaje de prueba largo", "delay_days": 0}],
        "audience": {"contact_ids": ids[:2]},
    })  # fmt: skip
    client.post(f"/api/campaigns/{campaign['id']}/launch")
    from datetime import timedelta

    from orkesta.campaigns import service as svc

    original = svc._gap
    svc._gap = lambda: timedelta(0)
    try:
        _flush(session_factory)
    finally:
        svc._gap = original

    stages = {s["name"]: s["id"] for s in client.get("/api/crm/stages").json()["stages"]}
    won_stage = next(sid for name, sid in stages.items() if "Ganad" in name)
    lost_stage = next(sid for name, sid in stages.items() if "Perdid" in name)
    d1 = client.post("/api/crm/deals", json={"contact_id": ids[0], "value": 1000}).json()
    d2 = client.post("/api/crm/deals", json={"contact_id": ids[1], "value": 500}).json()
    client.post(f"/api/crm/deals/{d1['id']}/move", json={"stage_id": won_stage})
    client.post(f"/api/crm/deals/{d2['id']}/move",
                json={"stage_id": lost_stage, "lost_reason": "Presupuesto"})  # fmt: skip
    client.post("/api/crm/deals", json={"contact_id": ids[2], "value": 700})

    r = client.get("/api/reports/overview")
    assert r.status_code == 200, r.text
    data = r.json()
    k = data["kpis"]
    assert k["contacts_added"] == 4 and k["emails_sent"] >= 1 and k["contacted"] >= 1
    assert k["deals_created"] == 3 and k["won"] == 1 and k["lost"] == 1 and k["win_rate"] == 50.0
    assert k["won_value"] == {"USD": 1000.0}
    assert data["previous"]["contacts_added"] == 0
    assert data["funnel"][0]["key"] == "contacts_added"
    assert data["series"]["unit"] == "day" and len(data["series"]["points"]) == 30
    assert sum(p["contacts_added"] for p in data["series"]["points"]) == 4
    assert data["campaigns"][0]["name"] == "Bancos"
    assert data["team"][0]["deals_created"] == 3
    assert data["lost_reasons"] == [{"reason": "Presupuesto", "count": 1}]
    assert sum(s["deals"] for s in data["pipeline"]) == 1
    assert data["sources"][0]["source"] == "manual"

    long = client.get("/api/reports/overview",
                      params={"desde": "2026-01-01", "hasta": "2026-10-31"}).json()  # fmt: skip
    assert long["series"]["unit"] == "week"

    # Otra empresa no ve estas cifras.
    register(client, "otro@otra.co", company="Otra")
    other = client.get("/api/reports/overview").json()
    assert other["kpis"]["contacts_added"] == 0 and other["campaigns"] == []
