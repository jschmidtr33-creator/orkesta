"""API de Concertino: /api/concertino."""

from __future__ import annotations

import uuid
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from orkesta.concertino import service as svc
from orkesta.core.web import Db, Principal, require_roles

router = APIRouter(prefix="/api/concertino")
Member = Annotated[Principal, Depends(require_roles("admin", "supervisor", "vendedor"))]


def _fail(exc: svc.ConcertinoError) -> HTTPException:
    return HTTPException(exc.status, str(exc))


class AskIn(BaseModel):
    question: str = Field(default="", max_length=4000)
    mode: str | None = Field(default=None, max_length=20)
    contact_id: uuid.UUID | None = None
    deal_id: uuid.UUID | None = None
    recipient_id: uuid.UUID | None = None
    campaign_id: uuid.UUID | None = None
    trato: str = Field(default="auto", pattern="^(auto|tu|usted)$")


class FeedbackIn(BaseModel):
    action: str | None = Field(default=None, pattern="^(ninguna|usada|editada|descartada)$")
    chosen: str | None = Field(default=None, max_length=80)
    final_text: str | None = Field(default=None, max_length=8000)
    topic: str | None = Field(default=None, max_length=20)
    objection: str | None = Field(default=None, max_length=20)
    outcome: str | None = Field(default=None, pattern="^(reunion|interesado|sigue|no_avanzo)$")


class FactIn(BaseModel):
    kind: str | None = Field(default=None, max_length=20)
    title: str | None = Field(default=None, max_length=300)
    detail: str | None = Field(default=None, max_length=4000)
    answer: str | None = Field(default=None, max_length=4000)
    status: str | None = Field(default=None, max_length=12)
    meta: dict[str, Any] | None = None


class BulkIn(BaseModel):
    facts: list[dict[str, Any]] = Field(default_factory=list, max_length=300)
    plantillas: list[dict[str, Any]] = Field(default_factory=list, max_length=40)


class ImportIn(BaseModel):
    text: str = Field(max_length=60000)


@router.get("/info")
def info(principal: Member, db: Db) -> dict[str, Any]:
    from orkesta.concertino.playbook import OBJECTIONS, TOPICS

    return {
        "topics": [{"code": k, "label": v} for k, v in TOPICS.items()],
        "objections": [{"code": o.code, "label": o.label} for o in OBJECTIONS],
        "plan": svc.plan_features(db, principal.tenant_id),
        "can_curate": svc.can_curate(principal.role),
    }


@router.post("/ask")
def ask(body: AskIn, principal: Member, db: Db) -> dict[str, Any]:
    try:
        svc.learn_outcomes(db, principal.tenant_id, principal.user)
        return svc.ask(
            db, tenant_id=principal.tenant_id, user=principal.user, question=body.question,
            mode=body.mode, contact_id=body.contact_id, deal_id=body.deal_id,
            recipient_id=body.recipient_id, campaign_id=body.campaign_id, trato=body.trato,
        )  # fmt: skip
    except svc.ConcertinoError as exc:
        raise _fail(exc) from exc


@router.post("/cases/{case_id}")
def case_feedback(
    case_id: uuid.UUID, body: FeedbackIn, principal: Member, db: Db
) -> dict[str, Any]:
    try:
        return svc.feedback(db, tenant_id=principal.tenant_id, user=principal.user,
                            case_id=case_id, **body.model_dump(exclude_none=True))  # fmt: skip
    except svc.ConcertinoError as exc:
        raise _fail(exc) from exc


@router.get("/history")
def history(principal: Member, db: Db) -> list[dict[str, Any]]:
    return svc.history(db, principal.tenant_id, principal.user)


@router.get("/facts")
def facts(principal: Member, db: Db) -> dict[str, Any]:
    return {**svc.facts(db, principal.tenant_id), "learned": None,
            "can_curate": svc.can_curate(principal.role)}  # fmt: skip


@router.post("/facts/learn")
def learn_facts(principal: Member, db: Db) -> dict[str, Any]:
    """Aprende de las respuestas de las campañas (solo cuando alguien lo pide)."""
    learned = svc.learn_from_campaigns(db, principal.tenant_id)
    return {**svc.facts(db, principal.tenant_id), "learned": learned,
            "can_curate": svc.can_curate(principal.role)}  # fmt: skip


@router.post("/facts", status_code=201)
def create_fact(body: FactIn, principal: Member, db: Db) -> dict[str, Any]:
    try:
        fact = svc.save_fact(db, tenant_id=principal.tenant_id, user=principal.user,
                             role=principal.role, **body.model_dump())  # fmt: skip
    except svc.ConcertinoError as exc:
        raise _fail(exc) from exc
    return svc.fact_dict(fact)


@router.patch("/facts/{fact_id}")
def update_fact(fact_id: uuid.UUID, body: FactIn, principal: Member, db: Db) -> dict[str, Any]:
    data = body.model_dump(exclude_none=True)
    data.pop("kind", None)
    try:
        fact = svc.save_fact(db, tenant_id=principal.tenant_id, user=principal.user,
                             role=principal.role, fact_id=fact_id, **data)  # fmt: skip
    except svc.ConcertinoError as exc:
        raise _fail(exc) from exc
    return svc.fact_dict(fact)


@router.delete("/facts/{fact_id}")
def delete_fact(fact_id: uuid.UUID, principal: Member, db: Db) -> dict[str, bool]:
    try:
        svc.delete_fact(db, tenant_id=principal.tenant_id, role=principal.role, fact_id=fact_id)
    except svc.ConcertinoError as exc:
        raise _fail(exc) from exc
    return {"ok": True}


@router.post("/facts/import")
def import_facts(body: ImportIn, principal: Member, db: Db) -> dict[str, int]:
    added = svc.import_text(db, tenant_id=principal.tenant_id, user=principal.user,
                            role=principal.role, text=body.text)  # fmt: skip
    return {"added": added}


@router.post("/facts/bulk")
def bulk_facts(body: BulkIn, principal: Member, db: Db) -> dict[str, int]:
    try:
        return svc.bulk_import(db, tenant_id=principal.tenant_id, user=principal.user,
                               role=principal.role, data=body.model_dump())  # fmt: skip
    except svc.ConcertinoError as exc:
        raise _fail(exc) from exc


@router.get("/stats")
def stats(principal: Member, db: Db) -> dict[str, Any]:
    return svc.stats(db, principal.tenant_id)
