"""Inteligencia del CRM: salud de cada oportunidad, pronóstico de cierre y probabilidad
aprendida de las ventas cerradas."""

from __future__ import annotations

from datetime import date, timedelta

import psycopg
from fastapi.testclient import TestClient

from tests.conftest import register


def test_health_and_forecast(client: TestClient, database: tuple[str, str]) -> None:
    register(client, "ana@lacteos.co")
    contact = client.post("/api/contacts", json={"first_name": "Laura", "last_name": "Gómez",
                                                 "company": "Banco Andino"}).json()  # fmt: skip
    soon = (date.today() + timedelta(days=5)).isoformat()
    hot = client.post("/api/crm/deals", json={"title": "Banco Andino", "value": 10000,
                                              "contact_id": contact["id"],
                                              "expected_close": soon}).json()  # fmt: skip
    cold = client.post("/api/crm/deals", json={"title": "Sin movimiento", "value": 0}).json()
    client.post("/api/crm/activities", json={"deal_id": hot["id"], "contact_id": contact["id"],
                                             "type": "call", "body": "Llamada"})  # fmt: skip
    with psycopg.connect(database[0], autocommit=True) as conn:
        conn.execute("UPDATE orkesta.deals SET created_at = now() - interval '40 days', "
                     "expected_close = current_date - 3 WHERE id = %s", (cold["id"],))  # fmt: skip
    deals = {d["title"]: d for d in client.get("/api/crm/deals").json()["deals"]}
    assert deals["Banco Andino"]["health"]["score"] > deals["Sin movimiento"]["health"]["score"]
    assert deals["Sin movimiento"]["health"]["label"] == "fria"
    assert any("fecha estimada" in r for r in deals["Sin movimiento"]["health"]["reasons"])

    forecast = client.get("/api/crm/forecast").json()
    keys = [p["key"] for p in forecast["periods"]]
    assert "vencidas" in keys and soon[:7] in keys
    month = next(p for p in forecast["periods"] if p["key"] == soon[:7])
    usd = month["totals"]["USD"]
    assert usd["best"] == 10000 and 0 < usd["weighted"] < 10000
