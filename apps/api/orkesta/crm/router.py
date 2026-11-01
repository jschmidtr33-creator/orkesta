"""API del CRM: /api/crm/…"""

from __future__ import annotations

import uuid
from datetime import date, datetime
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field

from orkesta.core.web import Db, Principal, client_ip, require_roles
from orkesta.crm import service as svc
from orkesta.crm.models import CURRENCIES, Deal
from orkesta.platform.service import audit

router = APIRouter(prefix="/api/crm")
Member = Annotated[Principal, Depends(require_roles("admin", "supervisor", "vendedor"))]
Manager = Annotated[Principal, Depends(require_roles("admin", "supervisor"))]


def _fail(exc: svc.CrmError) -> HTTPException:
    return HTTPException(exc.status, str(exc))


def _can_edit(principal: Principal, deal: Deal) -> None:
    if principal.role == "vendedor" and deal.owner_user_id != principal.user.id:
        raise HTTPException(403, "Solo puedes modificar tus propias oportunidades.")


class StageIn(BaseModel):
    name: str = Field(max_length=80)
    probability: int = Field(default=50, ge=0, le=100)


class StagePatch(BaseModel):
    name: str | None = Field(default=None, max_length=80)
    probability: int | None = Field(default=None, ge=0, le=100)


class OrderIn(BaseModel):
    ids: list[uuid.UUID] = Field(max_length=50)


class DealIn(BaseModel):
    title: str | None = Field(default=None, max_length=200)
    contact_id: uuid.UUID | None = None
    stage_id: uuid.UUID | None = None
    value: float | None = Field(default=None, ge=0)
    currency: str | None = Field(default=None, max_length=3)
    expected_close: date | None = None
    owner_user_id: uuid.UUID | None = None
    lost_reason: str | None = Field(default=None, max_length=300)


class MoveIn(BaseModel):
    stage_id: uuid.UUID
    lost_reason: str = Field(default="", max_length=300)


class ActivityIn(BaseModel):
    type: str = Field(max_length=10)
    body: str = Field(max_length=5000)
    contact_id: uuid.UUID | None = None
    deal_id: uuid.UUID | None = None
    due_at: datetime | None = None


# --- Etapas ---------------------------------------------------------------------------------


@router.get("/stages")
def stages(principal: Member, db: Db) -> dict[str, Any]:
    return {"stages": svc.stages_summary(db, principal.tenant_id), "currencies": CURRENCIES}


@router.post("/stages", status_code=201)
def create_stage(body: StageIn, principal: Manager, db: Db) -> dict[str, Any]:
    try:
        svc.create_stage(db, principal.tenant_id, body.name, "open", body.probability)
    except svc.CrmError as exc:
        raise _fail(exc) from exc
    return {"stages": svc.stages_summary(db, principal.tenant_id)}


@router.patch("/stages/{stage_id}")
def update_stage(
    stage_id: uuid.UUID, body: StagePatch, principal: Manager, db: Db
) -> dict[str, Any]:
    try:
        stage = svc.get_stage(db, principal.tenant_id, stage_id)
        svc.update_stage(db, stage, name=body.name, probability=body.probability)
    except svc.CrmError as exc:
        raise _fail(exc) from exc
    return {"stages": svc.stages_summary(db, principal.tenant_id)}


@router.post("/stages/order")
def reorder(body: OrderIn, principal: Manager, db: Db) -> dict[str, Any]:
    try:
        svc.reorder_stages(db, principal.tenant_id, body.ids)
    except svc.CrmError as exc:
        raise _fail(exc) from exc
    return {"stages": svc.stages_summary(db, principal.tenant_id)}


@router.delete("/stages/{stage_id}")
def delete_stage(stage_id: uuid.UUID, principal: Manager, db: Db) -> dict[str, Any]:
    try:
        svc.delete_stage(db, svc.get_stage(db, principal.tenant_id, stage_id))
    except svc.CrmError as exc:
        raise _fail(exc) from exc
    db.flush()
    return {"stages": svc.stages_summary(db, principal.tenant_id)}


# --- Oportunidades --------------------------------------------------------------------------


@router.get("/deals")
def deals(
    principal: Member,
    db: Db,
    q: str = "",
    estado: str = "",
    responsable: uuid.UUID | None = None,
    contacto: uuid.UUID | None = None,
) -> dict[str, Any]:
    svc.ensure_stages(db, principal.tenant_id)
    return {
        "deals": svc.list_deals(
            db, principal.tenant_id, q=q[:100], status=estado, owner_id=responsable,
            contact_id=contacto,
        ),
        "metrics": svc.metrics(db, principal.tenant_id),
    }  # fmt: skip


@router.post("/deals", status_code=201)
def create_deal(body: DealIn, request: Request, principal: Member, db: Db) -> dict[str, Any]:
    try:
        deal = svc.create_deal(
            db, tenant_id=principal.tenant_id, user_id=principal.user.id,
            data=svc.DealInput(**body.model_dump()),
        )  # fmt: skip
    except svc.CrmError as exc:
        raise _fail(exc) from exc
    audit(
        db, tenant_id=principal.tenant_id, actor_id=principal.user.id, action="deal.created",
        entity_type="deal", entity_id=deal.id, ip=client_ip(request),
    )  # fmt: skip
    return svc.deal_detail(db, deal)


@router.get("/deals/{deal_id}")
def deal(deal_id: uuid.UUID, principal: Member, db: Db) -> dict[str, Any]:
    try:
        item = svc.get_deal(db, principal.tenant_id, deal_id)
        return {
            **svc.deal_detail(db, item),
            "timeline": svc.timeline(db, principal.tenant_id, deal_id=item.id),
        }
    except svc.CrmError as exc:
        raise _fail(exc) from exc


@router.patch("/deals/{deal_id}")
def update_deal(deal_id: uuid.UUID, body: DealIn, principal: Member, db: Db) -> dict[str, Any]:
    try:
        item = svc.get_deal(db, principal.tenant_id, deal_id)
        _can_edit(principal, item)
        svc.update_deal(
            db, item, user_id=principal.user.id,
            data=svc.DealInput(**body.model_dump(exclude_unset=True)),
        )  # fmt: skip
    except svc.CrmError as exc:
        raise _fail(exc) from exc
    db.flush()
    return svc.deal_detail(db, item)


@router.post("/deals/{deal_id}/move")
def move(deal_id: uuid.UUID, body: MoveIn, principal: Member, db: Db) -> dict[str, Any]:
    try:
        item = svc.get_deal(db, principal.tenant_id, deal_id)
        _can_edit(principal, item)
        svc.move_deal(
            db, item, stage_id=body.stage_id, user_id=principal.user.id,
            lost_reason=body.lost_reason,
        )  # fmt: skip
    except svc.CrmError as exc:
        raise _fail(exc) from exc
    db.flush()
    return svc.deal_detail(db, item)


@router.delete("/deals/{deal_id}")
def delete_deal(deal_id: uuid.UUID, request: Request, principal: Member, db: Db) -> dict[str, bool]:
    try:
        item = svc.get_deal(db, principal.tenant_id, deal_id)
    except svc.CrmError as exc:
        raise _fail(exc) from exc
    _can_edit(principal, item)
    db.delete(item)
    audit(
        db, tenant_id=principal.tenant_id, actor_id=principal.user.id, action="deal.deleted",
        entity_type="deal", entity_id=deal_id, ip=client_ip(request),
    )  # fmt: skip
    return {"ok": True}


# --- Actividades y tareas -------------------------------------------------------------------


@router.get("/timeline")
def timeline(
    principal: Member,
    db: Db,
    contacto: uuid.UUID | None = None,
    oportunidad: uuid.UUID | None = None,
) -> list[dict[str, Any]]:
    try:
        return svc.timeline(db, principal.tenant_id, contact_id=contacto, deal_id=oportunidad)
    except svc.CrmError as exc:
        raise _fail(exc) from exc


@router.post("/activities", status_code=201)
def add_activity(body: ActivityIn, principal: Member, db: Db) -> dict[str, Any]:
    try:
        activity = svc.add_activity(
            db, tenant_id=principal.tenant_id, user_id=principal.user.id, kind=body.type,
            body=body.body, contact_id=body.contact_id, deal_id=body.deal_id, due_at=body.due_at,
        )  # fmt: skip
    except svc.CrmError as exc:
        raise _fail(exc) from exc
    return svc.activity_dict(activity, principal.user)


@router.post("/activities/{activity_id}/done")
def complete(activity_id: uuid.UUID, principal: Member, db: Db) -> dict[str, Any]:
    try:
        activity = svc.get_activity(db, principal.tenant_id, activity_id)
    except svc.CrmError as exc:
        raise _fail(exc) from exc
    if activity.type != "task":
        raise HTTPException(400, "Solo las tareas se pueden completar.")
    activity.done_at = None if activity.done_at else svc.now()
    return svc.activity_dict(activity, principal.user)


@router.delete("/activities/{activity_id}")
def delete_activity(activity_id: uuid.UUID, principal: Member, db: Db) -> dict[str, bool]:
    try:
        activity = svc.get_activity(db, principal.tenant_id, activity_id)
    except svc.CrmError as exc:
        raise _fail(exc) from exc
    if activity.type in ("stage", "system"):
        raise HTTPException(400, "Los registros automáticos no se pueden borrar.")
    if principal.role == "vendedor" and activity.user_id != principal.user.id:
        raise HTTPException(403, "Solo puedes borrar tus propias actividades.")
    db.delete(activity)
    return {"ok": True}


@router.get("/tasks")
def my_tasks(principal: Member, db: Db, todas: bool = False, equipo: bool = False) -> list[Any]:
    user_id = None if equipo and principal.role != "vendedor" else principal.user.id
    return svc.tasks(db, principal.tenant_id, user_id=user_id, include_done=todas)
