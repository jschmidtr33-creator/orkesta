"""API de reportes: /api/reports/overview."""

from __future__ import annotations

import uuid
from datetime import date
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException

from orkesta.core.db import private_open
from orkesta.core.web import Db, Principal, require_roles
from orkesta.reports import service as svc
from orkesta.reports import supervision as team

router = APIRouter(prefix="/api/reports")
Member = Annotated[Principal, Depends(require_roles("admin", "supervisor", "vendedor"))]
Leader = Annotated[Principal, Depends(require_roles("admin", "supervisor"))]


@router.get("/overview")
def overview(
    principal: Member, db: Db, desde: date | None = None, hasta: date | None = None,
    vendedor: uuid.UUID | None = None, tz: str = "America/Bogota",
) -> dict[str, Any]:  # fmt: skip
    # Un vendedor solo ve sus propias cifras; la jefatura, las suyas y las de su equipo (la base
    # filtra el resto); la administración, todo.
    user_id = principal.user.id if principal.role == "vendedor" else vendedor
    with private_open(db):  # solo cifras: los contactos de cada persona siguen siendo privados
        data = svc.overview(db, principal.tenant_id, svc.period(desde, hasta, tz), user_id)
    data["scope"] = {"user_id": str(user_id) if user_id else None,
                     "can_filter": principal.role != "vendedor"}  # fmt: skip
    return data


@router.get("/team")
def team_overview(
    principal: Leader, db: Db, desde: date | None = None, hasta: date | None = None,
    tz: str = "America/Bogota",
) -> dict[str, Any]:  # fmt: skip
    """Mi equipo: cifras de cada persona a cargo (solo lectura)."""
    p = svc.period(desde, hasta, tz)
    with private_open(db):
        return team.team_overview(db, principal.tenant_id, principal.user.id, p)


@router.get("/team/{user_id}")
def team_member(
    user_id: uuid.UUID, principal: Leader, db: Db, desde: date | None = None,
    hasta: date | None = None, tz: str = "America/Bogota",
) -> dict[str, Any]:  # fmt: skip
    if not team.can_supervise(db, principal.tenant_id, principal.user.id, user_id):
        raise HTTPException(404, "Esa persona no está a tu cargo.")
    with private_open(db):
        return team.person_detail(db, principal.tenant_id, user_id, svc.period(desde, hasta, tz))
