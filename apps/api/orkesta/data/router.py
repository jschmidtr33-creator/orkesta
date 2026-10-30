"""API de datos comerciales: contactos, importaciones, listas y exportación."""

from __future__ import annotations

import math
import uuid
from datetime import UTC, datetime
from typing import Annotated, Any
from urllib.parse import quote

from fastapi import APIRouter, Depends, File, HTTPException, Query, Request, Response, UploadFile
from pydantic import BaseModel, Field

from orkesta.core.web import Db, Principal, client_ip, require_roles
from orkesta.data import service as svc
from orkesta.data.models import CONTACT_STATUSES, STATUS_LABELS, ImportJob
from orkesta.engine.files.contacts import MAX_BYTES
from orkesta.engine.normalize.location import COUNTRIES
from orkesta.engine.taxonomy.industries import INDUSTRY_LABELS
from orkesta.platform.service import audit

router = APIRouter(prefix="/api")
Member = Annotated[Principal, Depends(require_roles("admin", "supervisor", "vendedor"))]
Manager = Annotated[Principal, Depends(require_roles("admin", "supervisor"))]
PAGE_SIZES = (25, 50, 100)


def _fail(exc: svc.DataError) -> HTTPException:
    return HTTPException(exc.status, str(exc))


def _filters(
    q: str = "",
    estado: str = "",
    pais: str = "",
    industria: str = "",
    empresa: uuid.UUID | None = None,
    lista: uuid.UUID | None = None,
) -> svc.ContactFilters:
    return svc.ContactFilters(
        q=q[:200], status=estado, country=pais[:2], industry=industria[:40],
        company_id=empresa, list_id=lista,
    )  # fmt: skip


Filters = Annotated[svc.ContactFilters, Depends(_filters)]


class ContactIn(BaseModel):
    first_name: str = Field(default="", max_length=200)
    last_name: str = Field(default="", max_length=200)
    job_title: str = Field(default="", max_length=300)
    company: str = Field(default="", max_length=300)
    email: str = Field(default="", max_length=320)
    phone: str = Field(default="", max_length=40)
    city: str = Field(default="", max_length=120)
    country: str = Field(default="", max_length=80)
    industry: str = Field(default="", max_length=200)


class IdsIn(BaseModel):
    ids: list[uuid.UUID] = Field(default_factory=list, max_length=5000)


class ConfirmIn(BaseModel):
    mapping: dict[str, dict[str, int]]
    list_id: uuid.UUID | None = None
    new_list: str = Field(default="", max_length=120)


class ListIn(BaseModel):
    name: str = Field(max_length=120)
    description: str = Field(default="", max_length=500)


class MembersIn(BaseModel):
    ids: list[uuid.UUID] | None = Field(default=None, max_length=5000)
    # Alternativa: todos los contactos que cumplen estos filtros.
    q: str = ""
    estado: str = ""
    pais: str = ""
    industria: str = ""


# --- Metadatos -----------------------------------------------------------------------------


@router.get("/contacts/meta")
def meta(_: Member) -> dict[str, Any]:
    return {
        "statuses": [{"value": s, "label": STATUS_LABELS[s]} for s in CONTACT_STATUSES],
        "industries": [{"value": k, "label": v} for k, v in INDUSTRY_LABELS.items()],
        "countries": sorted(
            ({"value": k, "label": v[0]} for k, v in COUNTRIES.items()), key=lambda c: c["label"]
        ),
        "mappable": [{"value": k, "label": v} for k, v in svc.MAPPABLE.items()],
        "max_mb": MAX_BYTES // (1024 * 1024),
    }


# --- Contactos -----------------------------------------------------------------------------


@router.get("/contacts")
def list_contacts(
    principal: Member,
    db: Db,
    filters: Filters,
    pagina: Annotated[int, Query(ge=1)] = 1,
    tamano: int = 50,
) -> dict[str, Any]:
    size = tamano if tamano in PAGE_SIZES else 50
    items, total = svc.search_contacts(db, principal.tenant_id, filters, page=pagina, size=size)
    return {
        "items": [svc.contact_dict(c) for c in items],
        "total": total,
        "page": pagina,
        "pages": max(1, math.ceil(total / size)),
        "size": size,
        "counts": svc.status_counts(db, principal.tenant_id, filters),
    }


@router.post("/contacts", status_code=201)
def create_contact(body: ContactIn, request: Request, principal: Member, db: Db) -> dict[str, Any]:
    try:
        contact = svc.create_contact(
            db,
            tenant_id=principal.tenant_id,
            user=principal.user,
            data=svc.ContactInput(**body.model_dump()),
        )
    except svc.DataError as exc:
        raise _fail(exc) from exc
    audit(
        db, tenant_id=principal.tenant_id, actor_id=principal.user.id, action="contact.created",
        entity_type="contact", entity_id=contact.id, ip=client_ip(request),
    )  # fmt: skip
    return svc.contact_detail(db, contact)


@router.get("/contacts/export")
def export(principal: Member, db: Db, filters: Filters, request: Request) -> Response:
    content = svc.export_csv(db, principal.tenant_id, filters)
    audit(
        db, tenant_id=principal.tenant_id, actor_id=principal.user.id, action="contacts.exported",
        entity_type="contact", ip=client_ip(request),
    )  # fmt: skip
    name = f"ORKESTA_contactos_{datetime.now(UTC):%Y%m%d}.csv"
    return Response(
        content,
        media_type="text/csv; charset=utf-8",
        headers={
            "Content-Disposition": (
                f"attachment; filename=\"{name}\"; filename*=UTF-8''{quote(name)}"
            )
        },
    )


@router.get("/contacts/{contact_id}")
def get_contact(contact_id: uuid.UUID, principal: Member, db: Db) -> dict[str, Any]:
    try:
        contact = svc.get_contact(db, principal.tenant_id, contact_id)
    except svc.DataError as exc:
        raise _fail(exc) from exc
    return svc.contact_detail(db, contact)


@router.patch("/contacts/{contact_id}")
def update_contact(
    contact_id: uuid.UUID, body: ContactIn, request: Request, principal: Member, db: Db
) -> dict[str, Any]:
    try:
        contact = svc.get_contact(db, principal.tenant_id, contact_id)
        if principal.role == "vendedor" and contact.owner_user_id != principal.user.id:
            raise svc.DataError("Solo puedes editar los contactos que creaste o importaste.", 403)
        svc.update_contact(
            db, contact=contact, user=principal.user, data=svc.ContactInput(**body.model_dump())
        )
    except svc.DataError as exc:
        raise _fail(exc) from exc
    audit(
        db, tenant_id=principal.tenant_id, actor_id=principal.user.id, action="contact.updated",
        entity_type="contact", entity_id=contact_id, ip=client_ip(request),
    )  # fmt: skip
    return svc.contact_detail(db, contact)


@router.post("/contacts/delete")
def delete_contacts(body: IdsIn, request: Request, principal: Manager, db: Db) -> dict[str, int]:
    deleted = svc.delete_contacts(db, principal.tenant_id, body.ids)
    audit(
        db, tenant_id=principal.tenant_id, actor_id=principal.user.id, action="contacts.deleted",
        entity_type="contact", ip=client_ip(request), count=deleted,
    )  # fmt: skip
    return {"deleted": deleted}


# --- Importaciones -------------------------------------------------------------------------


def _import_dict(record: ImportJob) -> dict[str, Any]:
    return {
        "id": str(record.id),
        "filename": record.filename,
        "status": record.status,
        "sheets": record.sheets if record.status == "preview" else None,
        "mapping": record.mapping,
        "totals": record.totals,
        "error": record.error,
        "created_at": record.created_at.isoformat(),
        "finished_at": record.finished_at.isoformat() if record.finished_at else None,
    }


@router.post("/imports", status_code=201)
async def upload(principal: Member, db: Db, file: Annotated[UploadFile, File()]) -> dict[str, Any]:
    content = await file.read(MAX_BYTES + 1)
    try:
        record = svc.create_import(
            db, tenant_id=principal.tenant_id, user_id=principal.user.id,
            filename=file.filename or "base.csv", content=content,
        )  # fmt: skip
    except svc.DataError as exc:
        raise _fail(exc) from exc
    return _import_dict(record)


@router.post("/imports/{import_id}/confirm")
def confirm(import_id: uuid.UUID, body: ConfirmIn, principal: Member, db: Db) -> dict[str, Any]:
    try:
        record = svc.confirm_import(
            db, tenant_id=principal.tenant_id, user_id=principal.user.id, import_id=import_id,
            mapping=body.mapping, list_id=body.list_id, new_list_name=body.new_list,
        )  # fmt: skip
    except svc.DataError as exc:
        db.rollback()
        raise _fail(exc) from exc
    return _import_dict(record)


@router.get("/imports")
def imports(principal: Member, db: Db) -> list[dict[str, Any]]:
    return [_import_dict(r) for r in svc.recent_imports(db, principal.tenant_id)]


@router.get("/imports/{import_id}")
def import_status(import_id: uuid.UUID, principal: Member, db: Db) -> dict[str, Any]:
    try:
        return _import_dict(svc.get_import(db, principal.tenant_id, import_id))
    except svc.DataError as exc:
        raise _fail(exc) from exc


@router.delete("/imports/{import_id}")
def cancel_import(import_id: uuid.UUID, principal: Member, db: Db) -> dict[str, bool]:
    try:
        record = svc.get_import(db, principal.tenant_id, import_id)
    except svc.DataError as exc:
        raise _fail(exc) from exc
    if record.status == "preview":
        record.status, record.content = "canceled", None
    return {"ok": True}


# --- Listas --------------------------------------------------------------------------------


@router.get("/lists")
def lists(principal: Member, db: Db) -> list[dict[str, Any]]:
    return svc.lists_with_counts(db, principal.tenant_id)


@router.post("/lists", status_code=201)
def create_list(body: ListIn, principal: Member, db: Db) -> dict[str, str]:
    try:
        item = svc.create_list(
            db, tenant_id=principal.tenant_id, user_id=principal.user.id, name=body.name,
            description=body.description,
        )  # fmt: skip
    except svc.DataError as exc:
        raise _fail(exc) from exc
    return {"id": str(item.id), "name": item.name}


@router.patch("/lists/{list_id}")
def rename_list(list_id: uuid.UUID, body: ListIn, principal: Member, db: Db) -> dict[str, str]:
    try:
        item = svc.get_list(db, principal.tenant_id, list_id)
    except svc.DataError as exc:
        raise _fail(exc) from exc
    name = " ".join(body.name.split())[:120]
    if len(name) < 2:
        raise HTTPException(400, "Escribe un nombre para la lista.")
    item.name, item.description = name, body.description[:500]
    return {"id": str(item.id), "name": item.name}


@router.delete("/lists/{list_id}")
def delete_list(list_id: uuid.UUID, principal: Manager, db: Db) -> dict[str, bool]:
    try:
        item = svc.get_list(db, principal.tenant_id, list_id)
    except svc.DataError as exc:
        raise _fail(exc) from exc
    db.delete(item)  # los contactos no se borran, solo su pertenencia a la lista
    return {"ok": True}


@router.post("/lists/{list_id}/members")
def add_members(list_id: uuid.UUID, body: MembersIn, principal: Member, db: Db) -> dict[str, int]:
    filters = None
    if body.ids is None:
        filters = svc.ContactFilters(
            q=body.q, status=body.estado, country=body.pais, industry=body.industria
        )
    try:
        added = svc.add_to_list(
            db,
            tenant_id=principal.tenant_id,
            list_id=list_id,
            contact_ids=body.ids,
            filters=filters,
        )
    except svc.DataError as exc:
        raise _fail(exc) from exc
    return {"added": added}


@router.post("/lists/{list_id}/remove")
def remove_members(list_id: uuid.UUID, body: IdsIn, principal: Member, db: Db) -> dict[str, int]:
    try:
        removed = svc.remove_from_list(db, principal.tenant_id, list_id, body.ids)
    except svc.DataError as exc:
        raise _fail(exc) from exc
    return {"removed": removed}
