"""API de propuestas: /api/proposals (equipo) y /api/public/proposals (cliente, sin sesión)."""

from __future__ import annotations

import uuid
from datetime import date
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field
from sqlalchemy import select

from orkesta.core.web import Db, Principal, RateLimiter, client_ip, require_roles
from orkesta.platform.service import audit
from orkesta.proposals import design
from orkesta.proposals import service as svc
from orkesta.proposals.models import Product

router = APIRouter()
Member = Annotated[Principal, Depends(require_roles("admin", "supervisor", "vendedor"))]
Manager = Annotated[Principal, Depends(require_roles("admin", "supervisor"))]
public_limiter = RateLimiter(limit=60, window_seconds=600)


def _fail(exc: svc.ProposalError) -> HTTPException:
    return HTTPException(exc.status, str(exc))


class ProductIn(BaseModel):
    name: str = Field(max_length=160)
    description: str = Field(default="", max_length=20000)
    image_url: str = Field(default="", max_length=500)
    payment_url: str = Field(default="", max_length=500)
    unit_price: float = Field(default=0, ge=0)
    currency: str = Field(default="USD", max_length=3)
    unit: str = Field(default="unidad", max_length=40)
    active: bool = True


class CreateIn(BaseModel):
    deal_id: uuid.UUID | None = None
    contact_id: uuid.UUID | None = None
    product_ids: list[uuid.UUID] = Field(default_factory=list, max_length=50)


class ItemIn(BaseModel):
    name: str = Field(max_length=200)
    description: str = Field(default="", max_length=20000)
    image_url: str = Field(default="", max_length=500)
    payment_url: str = Field(default="", max_length=500)
    quantity: float = Field(default=1, ge=0)
    unit: str = Field(default="", max_length=40)
    unit_price: float = Field(default=0, ge=0)
    discount: float = Field(default=0, ge=0, le=100)


class SectionIn(BaseModel):
    key: str = Field(default="", max_length=30)
    title: str = Field(max_length=120)
    body: str = Field(default="", max_length=10000)


class UpdateIn(BaseModel):
    title: str | None = Field(default=None, max_length=200)
    currency: str | None = Field(default=None, max_length=3)
    items: list[ItemIn] | None = Field(default=None, max_length=100)
    sections: list[SectionIn] | None = Field(default=None, max_length=20)
    tax_rate: float | None = Field(default=None, ge=0, le=100)
    valid_until: date | None = None
    design: dict[str, Any] | None = None


class DecisionIn(BaseModel):
    accept: bool
    name: str = Field(default="", max_length=160)
    note: str = Field(default="", max_length=500)


# --- Catálogo -------------------------------------------------------------------------------


@router.get("/api/products")
def products(principal: Member, db: Db) -> list[dict[str, Any]]:
    rows = db.scalars(
        select(Product).where(Product.tenant_id == principal.tenant_id).order_by(Product.name)
    )
    return [svc.product_dict(p) for p in rows]


@router.post("/api/products", status_code=201)
def create_product(body: ProductIn, principal: Manager, db: Db) -> dict[str, Any]:
    try:
        return svc.product_dict(svc.save_product(db, principal.tenant_id, body.model_dump()))
    except svc.ProposalError as exc:
        raise _fail(exc) from exc


@router.patch("/api/products/{product_id}")
def update_product(
    product_id: uuid.UUID, body: ProductIn, principal: Manager, db: Db
) -> dict[str, Any]:
    product = db.get(Product, product_id)
    if product is None or product.tenant_id != principal.tenant_id:
        raise HTTPException(404, "Ese producto no existe.")
    try:
        return svc.product_dict(
            svc.save_product(db, principal.tenant_id, body.model_dump(), product)
        )
    except svc.ProposalError as exc:
        raise _fail(exc) from exc


@router.delete("/api/products/{product_id}")
def delete_product(product_id: uuid.UUID, principal: Manager, db: Db) -> dict[str, bool]:
    product = db.get(Product, product_id)
    if product is None or product.tenant_id != principal.tenant_id:
        raise HTTPException(404, "Ese producto no existe.")
    db.delete(product)  # las propuestas guardan copia de cada línea: no se afectan
    return {"ok": True}


# --- Propuestas -----------------------------------------------------------------------------


@router.get("/api/proposal-brand")
def brand(principal: Member, db: Db) -> dict[str, Any]:
    return {
        "brand": design.get_brand(db, principal.tenant_id),
        "templates": design.TEMPLATES,
        "languages": design.LANGUAGES,
        "fonts": design.FONTS,
        "can_edit": principal.role in ("admin", "supervisor"),
    }


@router.put("/api/proposal-brand")
def save_brand(body: dict[str, Any], principal: Manager, db: Db) -> dict[str, Any]:
    try:
        return {"brand": design.save_brand(db, principal.tenant_id, body)}
    except design.DesignError as exc:
        raise HTTPException(400, str(exc)) from exc


@router.get("/api/proposals")
def proposals(
    principal: Member, db: Db, estado: str = "", oportunidad: uuid.UUID | None = None
) -> list[dict[str, Any]]:
    return svc.list_proposals(db, principal.tenant_id, status=estado, deal_id=oportunidad)


@router.post("/api/proposals", status_code=201)
def create(body: CreateIn, request: Request, principal: Member, db: Db) -> dict[str, Any]:
    try:
        proposal = svc.create_proposal(
            db, tenant_id=principal.tenant_id, user=principal.user, deal_id=body.deal_id,
            contact_id=body.contact_id, product_ids=body.product_ids,
        )  # fmt: skip
    except svc.ProposalError as exc:
        raise _fail(exc) from exc
    audit(
        db, tenant_id=principal.tenant_id, actor_id=principal.user.id, action="proposal.created",
        entity_type="proposal", entity_id=proposal.id, ip=client_ip(request),
    )  # fmt: skip
    return svc.proposal_dict(db, proposal)


def _load(db: Db, principal: Principal, proposal_id: uuid.UUID) -> Any:
    try:
        return svc.get_proposal(db, principal.tenant_id, proposal_id)
    except svc.ProposalError as exc:
        raise _fail(exc) from exc


@router.get("/api/proposals/{proposal_id}")
def detail(proposal_id: uuid.UUID, principal: Member, db: Db) -> dict[str, Any]:
    return svc.proposal_dict(db, _load(db, principal, proposal_id))


@router.patch("/api/proposals/{proposal_id}")
def update(proposal_id: uuid.UUID, body: UpdateIn, principal: Member, db: Db) -> dict[str, Any]:
    proposal = _load(db, principal, proposal_id)
    data = body.model_dump(exclude_unset=True)
    try:
        svc.update_proposal(db, proposal, data)
    except svc.ProposalError as exc:
        raise _fail(exc) from exc
    db.flush()
    return svc.proposal_dict(db, proposal)


@router.post("/api/proposals/{proposal_id}/regenerate")
def regenerate(proposal_id: uuid.UUID, principal: Member, db: Db) -> dict[str, Any]:
    proposal = _load(db, principal, proposal_id)
    if proposal.status in ("accepted", "rejected"):
        raise HTTPException(409, "La propuesta ya fue respondida.")
    svc.regenerate(db, proposal, principal.user)
    return svc.proposal_dict(db, proposal)


@router.post("/api/proposals/{proposal_id}/send")
def send(proposal_id: uuid.UUID, principal: Member, db: Db) -> dict[str, Any]:
    proposal = _load(db, principal, proposal_id)
    svc.mark_sent(db, proposal, principal.user)
    db.flush()
    return svc.proposal_dict(db, proposal)


@router.post("/api/proposals/{proposal_id}/duplicate", status_code=201)
def duplicate(proposal_id: uuid.UUID, principal: Member, db: Db) -> dict[str, Any]:
    copy = svc.duplicate(db, _load(db, principal, proposal_id), principal.user)
    return svc.proposal_dict(db, copy)


@router.delete("/api/proposals/{proposal_id}")
def delete(proposal_id: uuid.UUID, principal: Member, db: Db) -> dict[str, bool]:
    proposal = _load(db, principal, proposal_id)
    if principal.role == "vendedor" and proposal.created_by != principal.user.id:
        raise HTTPException(403, "Solo puedes eliminar tus propias propuestas.")
    db.delete(proposal)
    return {"ok": True}


# --- Enlace público (cliente) ---------------------------------------------------------------


@router.get("/api/public/proposals/{public_id}")
def public_view(
    public_id: str, request: Request, db: Db, vista_previa: bool = False
) -> dict[str, Any]:
    public_limiter.check(f"view:{client_ip(request)}")
    try:
        proposal = svc.public_load(db, public_id)
    except svc.ProposalError as exc:
        raise _fail(exc) from exc
    # La vista previa del equipo no cuenta como apertura del cliente.
    return svc.public_view(db, proposal, record=not vista_previa)


@router.post("/api/public/proposals/{public_id}/decision")
def public_decision(public_id: str, body: DecisionIn, request: Request, db: Db) -> dict[str, Any]:
    public_limiter.check(f"decision:{client_ip(request)}")
    try:
        proposal = svc.public_load(db, public_id)
        svc.decide(db, proposal, accept=body.accept, name=body.name, note=body.note)
    except svc.ProposalError as exc:
        db.rollback()
        raise _fail(exc) from exc
    db.flush()
    return svc.public_view(db, proposal, record=False)
