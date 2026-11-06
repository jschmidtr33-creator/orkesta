"""API de plantillas de equipo: /api/templates."""

from __future__ import annotations

import uuid
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from orkesta.core.web import Db, Principal, require_roles
from orkesta.library import service as svc

router = APIRouter(prefix="/api/templates")
Member = Annotated[Principal, Depends(require_roles("admin", "supervisor", "vendedor"))]


class SaveIn(BaseModel):
    kind: str = Field(pattern="^(campaign|proposal)$")
    source_id: uuid.UUID
    name: str = Field(max_length=120)
    description: str = Field(default="", max_length=300)


class PatchIn(BaseModel):
    name: str | None = Field(default=None, max_length=120)
    description: str | None = Field(default=None, max_length=300)


def _fail(exc: svc.TemplateError) -> HTTPException:
    return HTTPException(exc.status, str(exc))


@router.get("")
def templates(principal: Member, db: Db, tipo: str = "campaign") -> list[dict[str, Any]]:
    if tipo not in ("campaign", "proposal"):
        raise HTTPException(400, "Tipo de plantilla no válido.")
    return svc.listing(db, principal.tenant_id, tipo, principal.user, principal.role)


@router.post("", status_code=201)
def save(body: SaveIn, principal: Member, db: Db) -> dict[str, Any]:
    try:
        template = svc.save(
            db, tenant_id=principal.tenant_id, user=principal.user, kind=body.kind,
            source_id=body.source_id, name=body.name, description=body.description,
        )  # fmt: skip
    except svc.TemplateError as exc:
        raise _fail(exc) from exc
    return {"id": str(template.id), "name": template.name}


def _managed(db: Db, principal: Principal, template_id: uuid.UUID) -> Any:
    from orkesta.library.models import TeamTemplate

    template = db.get(TeamTemplate, template_id)
    if template is None or template.tenant_id != principal.tenant_id:
        raise HTTPException(404, "Esa plantilla ya no existe.")
    if not svc.can_manage(template, principal.user, principal.role):
        raise HTTPException(
            403, "Solo quien creó la plantilla o la administración puede cambiarla."
        )
    return template


@router.patch("/{template_id}")
def rename(template_id: uuid.UUID, body: PatchIn, principal: Member, db: Db) -> dict[str, Any]:
    template = _managed(db, principal, template_id)
    try:
        if body.name is not None:
            template.name = svc._clean_name(body.name)
    except svc.TemplateError as exc:
        raise _fail(exc) from exc
    if body.description is not None:
        template.description = " ".join(body.description.split())[:300]
    return {"id": str(template.id), "name": template.name}


@router.delete("/{template_id}")
def delete(template_id: uuid.UUID, principal: Member, db: Db) -> dict[str, bool]:
    db.delete(_managed(db, principal, template_id))
    return {"ok": True}
