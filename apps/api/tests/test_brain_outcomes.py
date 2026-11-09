"""Puntaje que aprende de resultados: cada empresa aprende de sus respuestas y lo explica, sin
mover el puntaje de otras empresas."""

from __future__ import annotations

import uuid

from fastapi.testclient import TestClient
from sqlalchemy import update
from sqlalchemy.orm import Session, sessionmaker

from orkesta.brain import capabilities, registry
from orkesta.brain.outcomes import Scorer
from orkesta.campaigns.models import Recipient
from orkesta.core.db import set_context
from orkesta.data.models import Contact
from tests.conftest import register


def test_score_learns_from_replies_privately(
    client: TestClient, session_factory: sessionmaker[Session]
) -> None:
    assert capabilities
    me = register(client, "ana@lacteos.co")
    campaign = client.post("/api/campaigns", json={"name": "Prueba"}).json()
    profiles = [("gerente", "compras", True)] * 40 + [("gerente", "ventas", False)] * 40
    ids = []
    for i, _ in enumerate(profiles):
        r = client.post("/api/contacts", json={"first_name": f"P{i}", "last_name": "Prueba",
                        "company": f"Empresa {i}", "email": f"p{i}@empresa{i}.co"})  # fmt: skip
        ids.append(r.json()["id"])
    with session_factory.begin() as session:
        tenant_id = uuid.UUID(me["workspace"]["id"])
        set_context(session, tenant_id=tenant_id)
        for cid, (level, area, ok) in zip(ids, profiles, strict=True):
            session.execute(update(Contact).where(Contact.id == uuid.UUID(cid)).values(
                level_code=level, area_code=area, industry_code="alimentos", country_code="CO"))  # fmt: skip
            session.add(Recipient(
                tenant_id=tenant_id, campaign_id=uuid.UUID(campaign["id"]),
                contact_id=uuid.UUID(cid), email=f"x{cid}@e.co", sent_count=2,
                status="replied" if ok else "completed",
                reply_category="interesado" if ok else None))  # fmt: skip

    with session_factory.begin() as session:
        row = registry.train(session, "puntaje")
        assert row.active, (row.metrics, row.baseline)

    with session_factory() as session:
        set_context(session, tenant_id=tenant_id)
        scorer = Scorer.load(session, tenant_id)
        other = Scorer.load(session, uuid.uuid4())
    buyer = scorer.adjust(50, [], level="gerente", area="compras", industry="alimentos",
                          country="CO")  # fmt: skip
    seller = scorer.adjust(50, [], level="gerente", area="ventas", industry="alimentos",
                           country="CO")  # fmt: skip
    assert buyer[0] > 60 > 40 > seller[0]
    assert any("responden más" in r and "compras" in r for r in buyer[1])
    assert any("responden menos" in r for r in seller[1])
    # Otra empresa sin resultados propios conserva el puntaje por parecido.
    assert other.adjust(50, ["x"], level="gerente", area="compras", industry=None,
                        country=None) == (50, ["x"])  # fmt: skip
