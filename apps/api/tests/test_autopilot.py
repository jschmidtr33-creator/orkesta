"""Campaña automatizada: de una frase a una campaña en borrador lista para aprobar."""

from __future__ import annotations

import uuid
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from orkesta.campaigns import autopilot
from orkesta.core.db import set_context
from orkesta.core.jobs import run_once
from tests.conftest import register
from tests.test_concertino import _ficha, _pro


def test_parse_understands_the_brief() -> None:
    plan = autopilot.parse("Gerentes de compras de supermercados en Colombia, para 10 reuniones")
    assert plan["chips"] == ["Gerentes y jefes", "Compras", "Retail y consumo", "Colombia"]
    assert plan["target"] == 334 and plan["goal"] == 10
    assert "comercio_al_por_menor" in plan["filters"]["sectors"]
    many = autopilot.parse("directores de RRHH en empresas de más de 200 empleados en Latam")
    assert "Empresas de 201+ personas" in many["chips"] and len(many["filters"]["countries"]) > 4
    assert autopilot.parse("algo bonito")["missing"]


def test_autopilot_prepares_a_draft_campaign(
    client: TestClient, session_factory: sessionmaker[Session], monkeypatch: pytest.MonkeyPatch
) -> None:
    from orkesta.enrichment import agent as enrichment
    from orkesta.research import agent as research
    from orkesta.research.models import ResearchItem, ResearchRun
    from orkesta.validation import agent as validation

    register(client, "ana@lacteos.co")
    _pro(session_factory)
    _ficha(client)

    def fake_research(factory: Any, tenant_id: uuid.UUID, payload: dict) -> None:
        run_id = uuid.UUID(payload["run_id"])
        with factory.begin() as session:
            set_context(session, tenant_id=tenant_id)
            for i in range(5):
                session.add(ResearchItem(
                    tenant_id=tenant_id, run_id=run_id, kind="person", key=f"p{i}",
                    company_key=f"cadena{i}.co", score=90 - i, sources=[],
                    data={"first_name": f"Persona{i}", "last_name": "Prueba",
                          "job_title": "Gerente de compras", "company": f"Cadena {i}",
                          "email": f"persona{i}@cadena{i}.co", "country_code": "CO"},
                ))  # fmt: skip
            session.get(ResearchRun, run_id).status = "completed"  # type: ignore[union-attr]

    monkeypatch.setattr(research, "run_research", fake_research)
    monkeypatch.setattr(enrichment, "run_enrichment", lambda f, t, p: None)
    monkeypatch.setattr(validation, "run_validation", lambda f, t, p: None)
    preview = client.post("/api/campaigns/auto/preview",
                          json={"text": "Gerentes de compras de supermercados en Colombia, 5 contactos"})  # fmt: skip
    assert preview.json()["target"] == 10  # mínimo 10
    made = client.post("/api/campaigns/auto",
                       json={"text": "Gerentes de compras de supermercados en Colombia"})  # fmt: skip
    assert made.status_code == 201, made.text
    cid = made.json()["id"]
    assert made.json()["settings"]["auto"]["state"] == "preparing"
    while run_once(session_factory, "pruebas"):
        pass
    camp = client.get(f"/api/campaigns/{cid}").json()
    auto = camp["settings"]["auto"]
    assert auto["state"] == "ready", auto
    assert [s["state"] for s in auto["steps"]] == ["done"] * 6, auto
    assert auto["counts"]["contacts"] == 5 and camp["status"] == "draft"
    assert len(camp["steps"]) == 3 and "{{" in camp["steps"][0]["body"]
    assert camp["audience"]["quality"] == "confiables" and camp["audience"]["list_id"]
    # Editar los ajustes no borra el estado de la campaña automatizada.
    client.patch(f"/api/campaigns/{cid}", json={"settings": camp["settings"]})
    assert client.get(f"/api/campaigns/{cid}").json()["settings"]["auto"]["state"] == "ready"


def test_new_list_members_join_the_running_campaign(
    client: TestClient, session_factory: sessionmaker[Session], monkeypatch: pytest.MonkeyPatch
) -> None:
    """Campaña automatizada en marcha: lo que se suma a su lista entra solo a la secuencia."""
    from orkesta.campaigns.models import Campaign, Recipient

    test_autopilot_prepares_a_draft_campaign(client, session_factory, monkeypatch)
    cid = client.get("/api/campaigns").json()["items"][0]["id"]
    list_id = client.get(f"/api/campaigns/{cid}").json()["audience"]["list_id"]
    me = client.get("/api/auth/me").json()
    tenant_id = uuid.UUID(
        me["workspace"]["id"] if me.get("workspace") else me["workspaces"][0]["id"]
    )
    with session_factory.begin() as session:
        set_context(session, tenant_id=tenant_id)
        running = session.get(Campaign, uuid.UUID(cid))
        running.status = "active"  # type: ignore[union-attr]
        autopilot._LAST_SYNC.clear()
        assert autopilot.sync_list(session, running) == 5  # type: ignore[arg-type]  # como al lanzar
    nuevo = client.post("/api/contacts", json={"first_name": "Nueva", "last_name": "Persona",
                                               "company": "Cadena 9", "email": "nueva@cadena9.co"}).json()  # fmt: skip
    sin = client.post("/api/contacts", json={"first_name": "Sin", "last_name": "Correo",
                                             "company": "Cadena 8"}).json()  # fmt: skip
    r = client.post(f"/api/lists/{list_id}/members", json={"ids": [nuevo["id"], sin["id"]]})
    assert r.status_code == 200, r.text
    autopilot._LAST_SYNC.clear()
    with session_factory.begin() as session:
        set_context(session, tenant_id=tenant_id)
        from sqlalchemy import update

        from orkesta.enrichment.models import EnrichmentRun

        session.execute(update(EnrichmentRun).values(status="completed"))  # la falsa quedó en cola
        campaign = session.get(Campaign, uuid.UUID(cid))
        assert autopilot.sync_list(session, campaign) == 1  # type: ignore[arg-type]
        emails = set(
            session.scalars(select(Recipient.email).where(Recipient.campaign_id == campaign.id))
        )  # type: ignore[union-attr]
        assert "nueva@cadena9.co" in emails and len(emails) == 6
        assert campaign.settings["auto"]["enrich_asked"] == [sin["id"]]  # type: ignore[union-attr]
        assert autopilot.sync_list(session, campaign) == 0  # type: ignore[arg-type]  # no repite
