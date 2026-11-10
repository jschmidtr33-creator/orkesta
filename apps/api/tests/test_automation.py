"""Automatización: recetas, reglas propias, eventos de campañas/CRM/contactos, cadenas y aislamiento."""

from __future__ import annotations

from datetime import timedelta

import psycopg
import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session, sessionmaker

from orkesta.campaigns import service as campaigns
from orkesta.core.jobs import run_once
from tests.conftest import register
from tests.test_campaigns import FakeGoogle, _connect, _contacts, _rewind, google  # noqa: F401


def _jobs(factory: sessionmaker[Session]) -> None:
    while run_once(factory, "pruebas"):
        pass


def _stages(client: TestClient) -> dict[str, str]:
    return {s["name"]: s["id"] for s in client.get("/api/crm/stages").json()["stages"]}


def test_recipe_on_reply_creates_deal_and_single_task(
    client: TestClient, google: FakeGoogle, session_factory: sessionmaker[Session],  # noqa: F811
    database: tuple[str, str],
) -> None:  # fmt: skip
    register(client, "ana@lacteos.co")
    _connect(client)
    catalog = client.get("/api/automations").json()
    assert any(r["key"] == "respondio" for r in catalog["recipes"])
    assert client.post("/api/automations/recipes/respondio").status_code == 201
    assert client.get("/api/automations").json()["recipes"][0]["installed"] is True

    ids = _contacts(client)
    campaign = client.post("/api/campaigns", json={"name": "Una"}).json()
    client.patch(f"/api/campaigns/{campaign['id']}", json={
        "steps": [{"subject": "Hola", "body": "Mensaje de prueba largo", "delay_days": 0}],
        "audience": {"contact_ids": [ids[0]]},
    })  # fmt: skip
    client.post(f"/api/campaigns/{campaign['id']}/launch")
    assert campaigns.tick(session_factory) == 1
    google.reply("th-1", "Laura <laura@bancoandino.co>")
    _rewind(database)
    campaigns.tick(session_factory)
    _jobs(session_factory)

    items = client.get("/api/crm/deals").json()["deals"]
    assert len(items) == 1 and items[0]["stage"] == "Contactado"
    timeline = client.get("/api/crm/timeline", params={"contacto": ids[0]}).json()
    tasks = [a for a in timeline if a["type"] == "task"]
    assert len(tasks) == 1 and "Responder a Laura" in tasks[0]["body"]
    runs = client.get("/api/automations/runs").json()
    assert runs[0]["status"] == "ok" and runs[0]["contact"]["full_name"] == "Laura Gómez"


def test_custom_rules_contact_created_and_stage_chain_stops(
    client: TestClient, session_factory: sessionmaker[Session],
) -> None:  # fmt: skip
    register(client, "ana@lacteos.co")
    lst = client.post("/api/lists", json={"name": "Entrantes"}).json()
    rule = client.post("/api/automations", json={
        "trigger": "contact.created", "conditions": {"source": "manual"},
        "actions": [{"type": "add_to_list", "list_id": lst["id"]},
                    {"type": "create_task", "title": "Calificar a {{nombre}}", "due_hours": 2}],
    })  # fmt: skip
    assert rule.status_code == 201, rule.text
    contact = client.post("/api/contacts", json={"first_name": "Mario", "last_name": "Paz",
                                                 "company": "Retail Uno"}).json()  # fmt: skip
    _jobs(session_factory)
    members = client.get("/api/contacts", params={"lista": lst["id"]}).json()
    assert members["total"] == 1
    timeline = client.get("/api/crm/timeline", params={"contacto": contact["id"]}).json()
    assert any(a["body"] == "Calificar a Mario" for a in timeline)

    # Dos reglas que se disparan entre sí no quedan en bucle.
    st = _stages(client)
    a, b = st["Contactado"], st["Propuesta enviada"]
    for when, to in ((a, b), (b, a)):
        client.post("/api/automations", json={
            "trigger": "deal.stage_changed", "conditions": {"stage_id": when},
            "actions": [{"type": "move_deal", "stage_id": to}],
        })  # fmt: skip
    deal = client.post("/api/crm/deals", json={"contact_id": contact["id"]}).json()
    client.post(f"/api/crm/deals/{deal['id']}/move", json={"stage_id": a})
    _jobs(session_factory)
    moves = [x for x in client.get("/api/crm/timeline", params={"contacto": contact["id"]}).json()
             if x["type"] == "stage"]  # fmt: skip
    assert 2 <= len(moves) <= 4

    bad = client.post("/api/automations", json={"trigger": "nada", "actions": []})
    assert bad.status_code == 400
    register(client, "otro@otra.co", company="Otra")
    assert client.get("/api/automations").json()["items"] == []
    foreign = client.post("/api/automations", json={
        "trigger": "deal.stage_changed", "conditions": {"stage_id": a},
        "actions": [{"type": "create_task", "title": "x tarea"}]})  # fmt: skip
    assert foreign.status_code == 404


def test_finished_without_reply_waits_and_skips_if_replied(
    monkeypatch: pytest.MonkeyPatch,
    client: TestClient, google: FakeGoogle, session_factory: sessionmaker[Session],  # noqa: F811
    database: tuple[str, str],
) -> None:  # fmt: skip
    register(client, "ana@lacteos.co")
    _connect(client)
    client.post("/api/automations/recipes/nutrir")
    ids = _contacts(client)
    campaign = client.post("/api/campaigns", json={"name": "Corta"}).json()
    client.patch(f"/api/campaigns/{campaign['id']}", json={
        "steps": [{"subject": "Hola", "body": "Mensaje de prueba largo", "delay_days": 0}],
        "audience": {"contact_ids": ids[:2]},
    })  # fmt: skip
    client.post(f"/api/campaigns/{campaign['id']}/launch")
    monkeypatch.setattr(campaigns, "_gap", lambda *a: timedelta(0))
    campaigns.tick(session_factory)
    _rewind(database)
    campaigns.tick(session_factory)
    _jobs(session_factory)  # aún no: espera 3 días
    assert client.get("/api/automations/runs").json() == []
    google.reply("th-1", "Laura <laura@bancoandino.co>")
    _rewind(database)
    campaigns.tick(session_factory)
    with psycopg.connect(database[0], autocommit=True) as conn:
        conn.execute("UPDATE orkesta.jobs SET run_after = now() WHERE kind = 'automation.run'")
    _jobs(session_factory)
    runs = client.get("/api/automations/runs").json()
    assert sorted(r["status"] for r in runs) == ["ok", "skipped"]
    nutrir = next(x for x in client.get("/api/lists").json() if x["name"] == "Nutrir")
    assert nutrir["contacts"] == 1


def test_each_person_turns_a_rule_on_or_off_for_their_own_contacts(
    client: TestClient, session_factory: sessionmaker[Session],
) -> None:  # fmt: skip
    """Cada persona apaga o enciende una regla solo para sus contactos; para toda la empresa la
    activa la administración o la jefatura."""
    from tests.test_supervision import _join, _pro

    register(client, "ana@lacteos.co")
    _pro(session_factory)
    luis = _join(client, "luis@lacteos.co", "vendedor", "Luis")
    rule = client.post("/api/automations", json={
        "trigger": "contact.created",
        "actions": [{"type": "create_task", "title": "Calificar a {{nombre}}", "due_hours": 2}],
    }).json()  # fmt: skip
    # El vendedor no la apaga para todos, pero sí para sí mismo.
    assert luis.patch(f"/api/automations/{rule['id']}", json={"enabled": False}).status_code == 403
    off = luis.patch(f"/api/automations/{rule['id']}", json={"mine": False}).json()
    assert off["on_for_me"] is False and off["enabled"] is True
    assert client.get("/api/automations").json()["items"][0]["on_for_me"] is True

    his = luis.post("/api/contacts", json={"first_name": "Mario", "last_name": "Paz"}).json()
    hers = client.post("/api/contacts", json={"first_name": "Rosa", "last_name": "Díaz"}).json()
    _jobs(session_factory)
    his_tasks = luis.get("/api/crm/timeline", params={"contacto": his["id"]}).json()
    assert not any(a["body"] == "Calificar a Mario" for a in his_tasks)
    her_tasks = client.get("/api/crm/timeline", params={"contacto": hers["id"]}).json()
    assert any(a["body"] == "Calificar a Rosa" for a in her_tasks)

    # La vuelve a encender para sí: corre de nuevo sobre sus contactos.
    luis.patch(f"/api/automations/{rule['id']}", json={"mine": True})
    again = luis.post("/api/contacts", json={"first_name": "Iván", "last_name": "Ruiz"}).json()
    _jobs(session_factory)
    tasks = luis.get("/api/crm/timeline", params={"contacto": again["id"]}).json()
    assert any(a["body"] == "Calificar a Iván" for a in tasks)
