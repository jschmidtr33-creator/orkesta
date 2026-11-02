"""API de notificaciones: /api/notifications."""

from __future__ import annotations

import uuid
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field
from sqlalchemy import func, select, update

from orkesta.core.web import CurrentPrincipal, Db, Principal, require_roles
from orkesta.notifications.models import Notification
from orkesta.notifications.service import notification_dict, now

router = APIRouter(prefix="/api/notifications")
Member = Annotated[Principal, Depends(require_roles("admin", "supervisor", "vendedor"))]


class ReadIn(BaseModel):
    ids: list[uuid.UUID] = Field(default_factory=list, max_length=200)
    all: bool = False


@router.get("")
def notifications(principal: Member, db: Db) -> dict[str, Any]:
    mine = (Notification.tenant_id == principal.tenant_id,
            Notification.user_id == principal.user.id)  # fmt: skip
    rows = db.scalars(
        select(Notification).where(*mine).order_by(Notification.created_at.desc()).limit(40)
    ).all()
    unread = db.scalar(
        select(func.count()).select_from(Notification).where(*mine, Notification.read_at.is_(None))
    )
    return {"items": [notification_dict(n) for n in rows], "unread": int(unread or 0)}


@router.post("/read")
def mark_read(body: ReadIn, principal: Member, db: Db) -> dict[str, bool]:
    query = update(Notification).where(
        Notification.tenant_id == principal.tenant_id,
        Notification.user_id == principal.user.id,
        Notification.read_at.is_(None),
    )
    if not body.all:
        query = query.where(Notification.id.in_(body.ids))
    db.execute(query.values(read_at=now()))
    return {"ok": True}


# --- Notificaciones push (Web Push) --------------------------------------------------------

push_router = APIRouter(prefix="/api/push")


class SubscriptionIn(BaseModel):
    endpoint: str = Field(min_length=10, max_length=1000)
    p256dh: str = Field(min_length=20, max_length=200)
    auth: str = Field(min_length=8, max_length=100)


@push_router.get("/key")
def push_key(principal: CurrentPrincipal, db: Db) -> dict[str, str]:
    from orkesta.notifications.push import vapid_keys

    return {"key": vapid_keys(db)[1]}


@push_router.post("/subscribe", status_code=201)
def push_subscribe(
    body: SubscriptionIn, request: Request, principal: CurrentPrincipal, db: Db
) -> dict[str, bool]:
    from orkesta.platform.models import PushSubscription

    if not body.endpoint.startswith("https://"):
        raise HTTPException(422, "Suscripción no válida.")
    row = db.scalar(select(PushSubscription).where(PushSubscription.endpoint == body.endpoint))
    if row is None:
        row = PushSubscription(endpoint=body.endpoint, user_id=principal.user.id,
                               p256dh=body.p256dh, auth=body.auth)  # fmt: skip
        db.add(row)
    row.user_id, row.p256dh, row.auth = principal.user.id, body.p256dh, body.auth
    row.user_agent = request.headers.get("user-agent", "")[:300]
    return {"ok": True}


class EndpointIn(BaseModel):
    endpoint: str = Field(max_length=1000)


@push_router.post("/unsubscribe")
def push_unsubscribe(body: EndpointIn, principal: CurrentPrincipal, db: Db) -> dict[str, bool]:
    from orkesta.platform.models import PushSubscription

    mine = (PushSubscription.endpoint == body.endpoint) & (
        PushSubscription.user_id == principal.user.id
    )
    row = db.scalar(select(PushSubscription).where(mine))
    if row is not None:
        db.delete(row)
    return {"ok": True}


@push_router.post("/test")
def push_test(principal: CurrentPrincipal, db: Db) -> dict[str, int]:
    from orkesta.notifications.push import deliver

    message = {"title": "ORKESTA", "link": "/app/",
               "body": "Las notificaciones de este dispositivo están activas."}  # fmt: skip
    sent = deliver(db, principal.user.id, message)
    return {"sent": sent}
