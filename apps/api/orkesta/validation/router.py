"""API de validación de correos."""

from __future__ import annotations

import uuid
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field
from sqlalchemy import func, select

from orkesta.core.jobs import enqueue
from orkesta.core.web import Db, Principal, RateLimiter, client_ip, require_roles
from orkesta.data.models import Contact
from orkesta.platform.service import audit
from orkesta.validation.agent import VALIDATION_JOB
from orkesta.validation.checks import STATUS_LABELS
from orkesta.validation.models import ValidationRun

router = APIRouter(prefix="/api/validation")
Member = Annotated[Principal, Depends(require_roles("admin", "supervisor", "vendedor"))]
limiter = RateLimiter(limit=40, window_seconds=3600)


class ScopeIn(BaseModel):
    ids: list[uuid.UUID] = Field(default_factory=list, max_length=5000)
    filters: dict[str, str] = Field(default_factory=dict)
    find_missing: bool = False


def _dict(run: ValidationRun) -> dict[str, Any]:
    return {
        "id": str(run.id),
        "status": run.status,
        "summary": run.summary,
        "find_missing": bool(run.scope.get("find_missing")),
        "progress": run.progress,
        "totals": run.totals,
        "error": run.error,
        "created_at": run.created_at.isoformat(),
        "finished_at": run.finished_at.isoformat() if run.finished_at else None,
    }


def _get(db: Db, tenant_id: uuid.UUID, run_id: uuid.UUID) -> ValidationRun:
    run = db.scalar(
        select(ValidationRun).where(
            ValidationRun.tenant_id == tenant_id, ValidationRun.id == run_id
        )
    )
    if run is None:
        raise HTTPException(404, "Esa validación no existe.")
    return run


@router.get("/summary")
def summary(principal: Member, db: Db) -> dict[str, Any]:
    rows = db.execute(
        select(Contact.email_status, func.count())
        .where(Contact.tenant_id == principal.tenant_id, Contact.email.is_not(None))
        .group_by(Contact.email_status)
    ).all()
    counts = {status: int(n) for status, n in rows}
    without = db.scalar(
        select(func.count()).where(
            Contact.tenant_id == principal.tenant_id, Contact.email.is_(None)
        )
    )
    return {
        "counts": counts,
        "without_email": int(without or 0),
        "labels": STATUS_LABELS,
    }


@router.post("/runs", status_code=201)
def create(body: ScopeIn, request: Request, principal: Member, db: Db) -> dict[str, Any]:
    limiter.check(f"validation:{principal.user.id}")
    active = db.scalar(
        select(func.count()).where(
            ValidationRun.tenant_id == principal.tenant_id,
            ValidationRun.status.in_(("queued", "running")),
        )
    )
    if active:
        raise HTTPException(429, "Ya hay una validación en curso. Espera a que termine.")
    allowed = {"q", "estado", "pais", "industria", "lista", "correo"}
    filters = {k: v[:200] for k, v in body.filters.items() if k in allowed and v}
    scope: dict[str, Any] = {"find_missing": body.find_missing}
    if body.ids:
        scope["ids"] = [str(i) for i in body.ids]
        summary = f"{len(body.ids)} contactos seleccionados"
    else:
        scope["filters"] = filters
        summary = "Contactos filtrados" if filters else "Toda la base"
    if body.find_missing:
        summary += " · buscar correos faltantes"
    run = ValidationRun(
        tenant_id=principal.tenant_id,
        created_by=principal.user.id,
        scope=scope,
        summary=summary,
        progress={"done": 0, "total": 0, "log": []},
    )
    db.add(run)
    db.flush()
    enqueue(
        db, tenant_id=principal.tenant_id, kind=VALIDATION_JOB,
        payload={"run_id": str(run.id)}, max_attempts=1,
    )  # fmt: skip
    audit(
        db, tenant_id=principal.tenant_id, actor_id=principal.user.id,
        action="validation.started", entity_type="validation_run", entity_id=run.id,
        ip=client_ip(request),
    )  # fmt: skip
    return _dict(run)


@router.get("/runs")
def runs(principal: Member, db: Db) -> list[dict[str, Any]]:
    rows = db.scalars(
        select(ValidationRun)
        .where(ValidationRun.tenant_id == principal.tenant_id)
        .order_by(ValidationRun.created_at.desc())
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
