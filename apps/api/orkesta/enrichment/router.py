"""API de enriquecimiento de contactos: /api/enrichment/…"""

from __future__ import annotations

import uuid
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field
from sqlalchemy import func, select

from orkesta.core.jobs import enqueue
from orkesta.core.web import Db, Principal, RateLimiter, client_ip, require_roles
from orkesta.enrichment.agent import ENRICH_JOB
from orkesta.enrichment.models import EnrichmentRun
from orkesta.platform.service import audit

router = APIRouter(prefix="/api/enrichment")
Member = Annotated[Principal, Depends(require_roles("admin", "supervisor", "vendedor"))]
limiter = RateLimiter(limit=30, window_seconds=3600)


class ScopeIn(BaseModel):
    ids: list[uuid.UUID] = Field(default_factory=list, max_length=2000)
    filters: dict[str, str] = Field(default_factory=dict)
    profiles: bool = True


def _dict(run: EnrichmentRun) -> dict[str, Any]:
    return {
        "id": str(run.id),
        "status": run.status,
        "summary": run.summary,
        "progress": run.progress,
        "totals": run.totals,
        "error": run.error,
        "created_at": run.created_at.isoformat(),
        "finished_at": run.finished_at.isoformat() if run.finished_at else None,
    }


def _get(db: Db, tenant_id: uuid.UUID, run_id: uuid.UUID) -> EnrichmentRun:
    run = db.scalar(
        select(EnrichmentRun).where(
            EnrichmentRun.tenant_id == tenant_id, EnrichmentRun.id == run_id
        )
    )
    if run is None:
        raise HTTPException(404, "Ese enriquecimiento no existe.")
    return run


@router.post("/runs", status_code=201)
def create(body: ScopeIn, request: Request, principal: Member, db: Db) -> dict[str, Any]:
    limiter.check(f"enrichment:{principal.user.id}")
    active = db.scalar(
        select(func.count()).where(
            EnrichmentRun.tenant_id == principal.tenant_id,
            EnrichmentRun.status.in_(("queued", "running")),
        )
    )
    if active:
        raise HTTPException(429, "Ya hay un enriquecimiento en curso. Espera a que termine.")
    allowed = {"q", "estado", "pais", "industria", "lista", "correo"}
    scope: dict[str, Any] = {"profiles": body.profiles}
    if body.ids:
        scope["ids"] = [str(i) for i in body.ids]
        summary = f"{len(body.ids)} contactos seleccionados"
    else:
        scope["filters"] = {k: v[:200] for k, v in body.filters.items() if k in allowed and v}
        summary = "Contactos filtrados" if scope["filters"] else "Toda la base"
    run = EnrichmentRun(
        tenant_id=principal.tenant_id, created_by=principal.user.id, scope=scope,
        summary=summary, progress={"done": 0, "total": 0, "log": []},
    )  # fmt: skip
    db.add(run)
    db.flush()
    enqueue(db, tenant_id=principal.tenant_id, kind=ENRICH_JOB,
            payload={"run_id": str(run.id)}, max_attempts=1)  # fmt: skip
    audit(
        db, tenant_id=principal.tenant_id, actor_id=principal.user.id,
        action="enrichment.started", entity_type="enrichment_run", entity_id=run.id,
        ip=client_ip(request),
    )  # fmt: skip
    return _dict(run)


@router.get("/runs")
def runs(principal: Member, db: Db) -> list[dict[str, Any]]:
    rows = db.scalars(
        select(EnrichmentRun)
        .where(EnrichmentRun.tenant_id == principal.tenant_id)
        .order_by(EnrichmentRun.created_at.desc())
        .limit(10)
    )
    return [_dict(r) for r in rows]


@router.get("/runs/{run_id}")
def detail(run_id: uuid.UUID, principal: Member, db: Db) -> dict[str, Any]:
    return _dict(_get(db, principal.tenant_id, run_id))


@router.post("/runs/{run_id}/cancel")
def cancel(run_id: uuid.UUID, principal: Member, db: Db) -> dict[str, Any]:
    run = _get(db, principal.tenant_id, run_id)
    if run.status in ("queued", "running"):
        run.status = "canceled"
    return _dict(run)
