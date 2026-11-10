"""Eventos del calendario de cada persona (espejo de su Google Calendar)."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    String,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from orkesta.core.db import Base, IdMixin


class CalendarEvent(IdMixin, Base):
    __tablename__ = "calendar_events"
    __table_args__ = (
        CheckConstraint("status IN ('confirmed', 'tentative', 'cancelled')", name="status"),
        CheckConstraint("source IN ('google', 'orkesta')", name="source"),
        UniqueConstraint("user_id", "google_id", name="uq_calendar_events_user_google"),
        Index("ix_calendar_events_user_start", "tenant_id", "user_id", "start_at"),
    )

    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    mailbox_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("mailboxes.id", ondelete="SET NULL")
    )
    google_id: Mapped[str | None] = mapped_column(String(1024))
    title: Mapped[str] = mapped_column(String(300), nullable=False, default="")
    description: Mapped[str] = mapped_column(Text, nullable=False, default="")
    location: Mapped[str] = mapped_column(String(500), nullable=False, default="")
    start_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    end_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    all_day: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    attendees: Mapped[list[dict[str, Any]]] = mapped_column(
        JSONB, nullable=False, default=list, server_default=text("'[]'::jsonb")
    )
    meet_url: Mapped[str] = mapped_column(String(500), nullable=False, default="")
    html_link: Mapped[str] = mapped_column(String(1000), nullable=False, default="")
    status: Mapped[str] = mapped_column(String(12), nullable=False, default="confirmed")
    source: Mapped[str] = mapped_column(String(10), nullable=False, default="google")
    contact_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("contacts.id", ondelete="SET NULL")
    )
    deal_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("deals.id", ondelete="SET NULL")
    )
    campaign_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("campaigns.id", ondelete="SET NULL")
    )
