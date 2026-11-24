"""API de reportes: /api/reports/overview."""

from __future__ import annotations

import uuid
from datetime import date, timedelta
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from orkesta.core.db import private_open
from orkesta.core.web import Db, Principal, require_roles
from orkesta.reports import service as svc
from orkesta.reports import supervision as team
from orkesta.reports import worktime

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
        data = team.team_overview(db, principal.tenant_id, principal.user.id, p)
        first, last = _days(p)
        times = worktime.team_today(
            db, principal.tenant_id, [uuid.UUID(x["user_id"]) for x in data["people"]],
            first, last, p.tz,
        )  # fmt: skip
        for person in data["people"]:
            person["worktime"] = times.get(person["user_id"])
        return data


@router.get("/team/{user_id}")
def team_member(
    user_id: uuid.UUID, principal: Leader, db: Db, desde: date | None = None,
    hasta: date | None = None, tz: str = "America/Bogota",
) -> dict[str, Any]:  # fmt: skip
    if not team.can_supervise(db, principal.tenant_id, principal.user.id, user_id):
        raise HTTPException(404, "Esa persona no está a tu cargo.")
    with private_open(db):
        return team.person_detail(db, principal.tenant_id, user_id, svc.period(desde, hasta, tz))


def _days(p: svc.Period) -> tuple[date, date]:
    return p.start.astimezone(p.tz).date(), (p.end - timedelta(microseconds=1)).astimezone(
        p.tz
    ).date()


class PresenceIn(BaseModel):
    path: str = Field(default="", max_length=200)
    active: bool = False


@router.post("/presence", status_code=204)
def presence(body: PresenceIn, principal: Member, db: Db) -> None:
    """Un minuto con ORKESTA abierto. Nadie ve su propia medición: solo su jefatura y la
    administración, en Mi equipo."""
    worktime.record(db, principal.tenant_id, principal.user.id, active=body.active,
                    section=worktime.section_of(body.path))  # fmt: skip


@router.get("/team/{user_id}/worktime")
def team_member_worktime(
    user_id: uuid.UUID, principal: Leader, db: Db, desde: date | None = None,
    hasta: date | None = None, tz: str = "America/Bogota",
) -> dict[str, Any]:  # fmt: skip
    """Tiempo en ORKESTA por día de una persona a cargo (solo jefatura y administración)."""
    if not team.can_supervise(db, principal.tenant_id, principal.user.id, user_id):
        raise HTTPException(404, "Esa persona no está a tu cargo.")
    p = svc.period(desde, hasta, tz)
    first, last = _days(p)
    with private_open(db):
        return worktime.days(db, principal.tenant_id, user_id, first, last, p.tz)


@router.get("/team/{user_id}/worktime/{day}")
def team_member_day(
    user_id: uuid.UUID, day: date, principal: Leader, db: Db, tz: str = "America/Bogota"
) -> dict[str, Any]:
    """Historial de un día: bloques de conexión, secciones y lo que hizo."""
    if not team.can_supervise(db, principal.tenant_id, principal.user.id, user_id):
        raise HTTPException(404, "Esa persona no está a tu cargo.")
    p = svc.period(day, day, tz)
    with private_open(db):
        return worktime.day_detail(db, principal.tenant_id, user_id, day, p.tz)
