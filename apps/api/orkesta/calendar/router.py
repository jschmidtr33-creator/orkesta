"""Calendario de cada persona: su Google Calendar dentro de ORKESTA."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select

from orkesta.calendar import service as svc
from orkesta.calendar.models import CalendarEvent
from orkesta.campaigns import gmail
from orkesta.core.web import Db, Principal, require_roles
from orkesta.data.models import Contact

router = APIRouter(prefix="/api/calendar", tags=["calendario"])
Member = Annotated[Principal, Depends(require_roles("admin", "supervisor", "vendedor"))]


def _fail(exc: svc.CalendarServiceError) -> HTTPException:
    return HTTPException(exc.status, str(exc))


class AttendeeIn(BaseModel):
    email: str = Field(max_length=320)
    name: str = Field(default="", max_length=160)


class EventIn(BaseModel):
    title: str | None = Field(default=None, max_length=300)
    description: str | None = Field(default=None, max_length=20000)
    location: str | None = Field(default=None, max_length=500)
    start_at: str | None = None
    end_at: str | None = None
    all_day: bool | None = None
    contact_id: uuid.UUID | None = None
    attendees: list[AttendeeIn] | None = Field(default=None, max_length=50)
    meet: bool = False
    notify: bool = True


@router.get("/status")
def status(principal: Member, db: Db) -> dict[str, Any]:
    mailbox = svc.calendar_mailbox(db, principal.tenant_id, principal.user.id)
    return {
        "available": gmail.enabled(),
        "connected": mailbox is not None,
        "email": mailbox.email if mailbox else None,
        "synced_at": mailbox.calendar_synced_at.isoformat()
        if mailbox and mailbox.calendar_synced_at
        else None,
        "error": mailbox.calendar_error if mailbox else None,
    }


@router.post("/sync")
def sync_now(principal: Member, db: Db) -> dict[str, Any]:
    mailbox = svc.calendar_mailbox(db, principal.tenant_id, principal.user.id)
    if mailbox is None:
        raise HTTPException(409, "Conecta tu calendario de Google primero.")
    count = svc.sync_mailbox(db, mailbox)
    return {"ok": mailbox.calendar_error is None, "events": count, "error": mailbox.calendar_error}


@router.get("/events")
def events(
    principal: Member, db: Db, desde: str = "", hasta: str = "", contacto: uuid.UUID | None = None
) -> list[dict[str, Any]]:
    now = datetime.now(UTC)
    try:
        start = datetime.fromisoformat(desde) if desde else now - timedelta(days=7)
        end = datetime.fromisoformat(hasta) if hasta else now + timedelta(days=60)
    except ValueError as exc:
        raise HTTPException(422, "Fechas no válidas.") from exc
    start = start if start.tzinfo else start.replace(tzinfo=UTC)
    end = end if end.tzinfo else end.replace(tzinfo=UTC)
    query = (
        select(CalendarEvent, Contact)
        .outerjoin(Contact, Contact.id == CalendarEvent.contact_id)
        .where(
            CalendarEvent.tenant_id == principal.tenant_id,
            CalendarEvent.user_id == principal.user.id,
            CalendarEvent.status != "cancelled",
            CalendarEvent.end_at >= start,
            CalendarEvent.start_at <= end,
        )
        .order_by(CalendarEvent.start_at)
        .limit(1000)
    )
    if contacto:
        query = query.where(CalendarEvent.contact_id == contacto)
    return [svc.event_dict(e, c) for e, c in db.execute(query).all()]


@router.post("/events", status_code=201)
def create(body: EventIn, principal: Member, db: Db) -> dict[str, Any]:
    try:
        event = svc.create_event(
            db,
            tenant_id=principal.tenant_id,
            user_id=principal.user.id,
            data=body.model_dump(exclude_none=True),
        )
    except svc.CalendarServiceError as exc:
        raise _fail(exc) from exc
    contact = db.get(Contact, event.contact_id) if event.contact_id else None
    return svc.event_dict(event, contact)


@router.patch("/events/{event_id}")
def update(event_id: uuid.UUID, body: EventIn, principal: Member, db: Db) -> dict[str, Any]:
    try:
        event = svc.own_event(db, principal.tenant_id, principal.user.id, event_id)
        svc.update_event(db, event, body.model_dump(exclude_unset=True))
    except svc.CalendarServiceError as exc:
        raise _fail(exc) from exc
    contact = db.get(Contact, event.contact_id) if event.contact_id else None
    return svc.event_dict(event, contact)


@router.delete("/events/{event_id}")
def remove(event_id: uuid.UUID, principal: Member, db: Db, avisar: bool = True) -> dict[str, bool]:
    try:
        event = svc.own_event(db, principal.tenant_id, principal.user.id, event_id)
        svc.delete_event(db, event, avisar)
    except svc.CalendarServiceError as exc:
        raise _fail(exc) from exc
    return {"ok": True}
