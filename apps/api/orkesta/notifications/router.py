"""API de notificaciones: /api/notifications."""

from __future__ import annotations

import uuid
from typing import Annotated, Any

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field
from sqlalchemy import func, select, update

from orkesta.core.web import Db, Principal, require_roles
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
