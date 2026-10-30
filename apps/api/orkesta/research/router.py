"""API de Prospección con IA: opciones de filtros, corridas, resultados y guardado."""

from __future__ import annotations

import uuid
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field

from orkesta.core.web import Db, Principal, RateLimiter, client_ip, require_roles
from orkesta.data.service import DataError
from orkesta.platform.service import audit
from orkesta.research import service as svc

router = APIRouter(prefix="/api/research")
Member = Annotated[Principal, Depends(require_roles("admin", "supervisor", "vendedor"))]
run_limiter = RateLimiter(limit=30, window_seconds=3600)


def _fail(exc: DataError) -> HTTPException:
    return HTTPException(exc.status, str(exc))


class FiltersIn(BaseModel):
    country: str = Field(max_length=2)
    industries: list[str] = Field(default_factory=list, max_length=30)
    sizes: list[str] = Field(default_factory=list, max_length=10)
    levels: list[str] = Field(default_factory=list, max_length=10)
    areas: list[str] = Field(default_factory=list, max_length=20)
    city: str = Field(default="", max_length=80)
    keywords: str = Field(default="", max_length=80)
    companies: list[str] = Field(default_factory=list, max_length=25)
    include_base: bool = True
    limit: int = 25


class SaveIn(BaseModel):
    ids: list[uuid.UUID] = Field(max_length=1000)
    list_id: uuid.UUID | None = None
    new_list: str = Field(default="", max_length=120)


@router.get("/options")
def options(_: Member) -> dict[str, Any]:
    return svc.options()


@router.post("/runs", status_code=201)
def create_run(body: FiltersIn, request: Request, principal: Member, db: Db) -> dict[str, Any]:
    run_limiter.check(f"research:{principal.user.id}")
    try:
        run = svc.create_run(
            db,
            tenant_id=principal.tenant_id,
            user_id=principal.user.id,
            filters=body.model_dump(),
        )
    except DataError as exc:
        raise _fail(exc) from exc
    audit(
        db, tenant_id=principal.tenant_id, actor_id=principal.user.id, action="research.started",
        entity_type="research_run", entity_id=run.id, ip=client_ip(request),
    )  # fmt: skip
    return svc.run_dict(run)


@router.get("/runs")
def runs(principal: Member, db: Db) -> list[dict[str, Any]]:
    return [svc.run_dict(r) for r in svc.recent_runs(db, principal.tenant_id)]


@router.get("/runs/{run_id}")
def run_detail(run_id: uuid.UUID, principal: Member, db: Db) -> dict[str, Any]:
    try:
        run = svc.get_run(db, principal.tenant_id, run_id)
    except DataError as exc:
        raise _fail(exc) from exc
    return {
        **svc.run_dict(run),
        "companies": svc.items(db, principal.tenant_id, run_id, "company"),
        "people": svc.items(db, principal.tenant_id, run_id, "person"),
    }


@router.post("/runs/{run_id}/cancel")
def cancel(run_id: uuid.UUID, principal: Member, db: Db) -> dict[str, Any]:
    try:
        run = svc.get_run(db, principal.tenant_id, run_id)
    except DataError as exc:
        raise _fail(exc) from exc
    if run.status in ("queued", "running"):
        run.status = "canceled"
    return svc.run_dict(run)


@router.post("/runs/{run_id}/save")
def save(
    run_id: uuid.UUID, body: SaveIn, request: Request, principal: Member, db: Db
) -> dict[str, Any]:
    if not body.ids:
        raise HTTPException(400, "Selecciona al menos un decisor.")
    try:
        result = svc.save_people(
            db,
            tenant_id=principal.tenant_id,
            user_id=principal.user.id,
            run_id=run_id,
            item_ids=body.ids,
            list_id=body.list_id,
            new_list=body.new_list,
        )
    except DataError as exc:
        db.rollback()
        raise _fail(exc) from exc
    audit(
        db, tenant_id=principal.tenant_id, actor_id=principal.user.id, action="research.saved",
        entity_type="research_run", entity_id=run_id, ip=client_ip(request),
        count=result["created"] + result["merged"],
    )  # fmt: skip
    return result
