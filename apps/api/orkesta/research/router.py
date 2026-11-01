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
diag_limiter = RateLimiter(limit=20, window_seconds=3600)


def _fail(exc: DataError) -> HTTPException:
    return HTTPException(exc.status, str(exc))


class FiltersIn(BaseModel):
    """Filtros de leads con la estructura de Sales Navigator."""

    country: str = Field(max_length=2)
    cities: list[str] = Field(default_factory=list, max_length=5)
    sectors: list[str] = Field(default_factory=list, max_length=60)
    exclude_sectors: list[str] = Field(default_factory=list, max_length=60)
    sizes: list[str] = Field(default_factory=list, max_length=10)
    seniority: list[str] = Field(default_factory=list, max_length=10)
    functions: list[str] = Field(default_factory=list, max_length=30)
    exclude_functions: list[str] = Field(default_factory=list, max_length=30)
    titles: list[str] = Field(default_factory=list, max_length=10)
    exclude_titles: list[str] = Field(default_factory=list, max_length=10)
    companies: list[str] = Field(default_factory=list, max_length=25)
    exclude_companies: list[str] = Field(default_factory=list, max_length=25)
    first_name: str = Field(default="", max_length=60)
    last_name: str = Field(default="", max_length=60)
    keywords: str = Field(default="", max_length=80)
    include_base: bool = True
    limit: int = 25
    # Compatibilidad con la versión anterior de los filtros.
    industries: list[str] = Field(default_factory=list, max_length=30)
    levels: list[str] = Field(default_factory=list, max_length=10)
    areas: list[str] = Field(default_factory=list, max_length=20)
    city: str = Field(default="", max_length=80)


class SaveSearchIn(BaseModel):
    name: str = Field(max_length=120)


class SaveIn(BaseModel):
    ids: list[uuid.UUID] = Field(max_length=1000)
    list_id: uuid.UUID | None = None
    new_list: str = Field(default="", max_length=120)


@router.get("/diagnostics")
def diagnostics(
    _: Annotated[Principal, Depends(require_roles("admin", "supervisor"))],
) -> dict[str, Any]:
    diag_limiter.check("diagnostics")
    from orkesta.research.diagnostics import run_all

    return {"checks": run_all()}


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
    """Búsquedas guardadas del usuario."""
    return [svc.run_dict(r) for r in svc.saved_runs(db, principal.tenant_id, principal.user.id)]


@router.post("/runs/{run_id}/saved")
def save_search(run_id: uuid.UUID, body: SaveSearchIn, principal: Member, db: Db) -> dict[str, Any]:
    try:
        return svc.run_dict(svc.save_search(db, principal.tenant_id, run_id, body.name))
    except DataError as exc:
        raise _fail(exc) from exc


@router.delete("/runs/{run_id}")
def forget_search(run_id: uuid.UUID, principal: Member, db: Db) -> dict[str, bool]:
    try:
        svc.forget_search(db, principal.tenant_id, run_id)
    except DataError as exc:
        raise _fail(exc) from exc
    return {"ok": True}


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


@router.post("/runs/{run_id}/remove")
def remove_items(run_id: uuid.UUID, body: SaveIn, principal: Member, db: Db) -> dict[str, int]:
    """Quita leads de los resultados de una investigación (no toca Contactos)."""
    try:
        removed = svc.remove_items(db, principal.tenant_id, run_id, body.ids)
    except DataError as exc:
        raise _fail(exc) from exc
    return {"removed": removed}


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
