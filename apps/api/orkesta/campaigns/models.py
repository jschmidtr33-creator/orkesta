"""Campañas de correo: buzones conectados, campañas por secuencia, destinatarios, mensajes,
seguimiento y lista de exclusión."""

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
    Integer,
    LargeBinary,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from orkesta.core.db import Base, IdMixin

RECIPIENT_STATUSES = (
    "pending", "active", "replied", "bounced", "blocked", "unsubscribed", "completed", "failed",
    "skipped",
)  # fmt: skip
CAMPAIGN_STATUSES = ("draft", "active", "paused", "completed")


def _in(column: str, values: tuple[str, ...]) -> str:
    return f"{column} IN ({', '.join(repr(v) for v in values)})"


def _tenant() -> Mapped[uuid.UUID]:
    return mapped_column(
        UUID(as_uuid=True), ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False
    )


class Mailbox(IdMixin, Base):
    """Buzón de Gmail/Google Workspace conectado por OAuth. Los tokens se guardan cifrados."""

    __tablename__ = "mailboxes"
    __table_args__ = (
        UniqueConstraint("tenant_id", "email"),
        CheckConstraint(_in("status", ("active", "error", "disconnected")), name="status"),
    )

    tenant_id: Mapped[uuid.UUID] = _tenant()
    user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL")
    )
    provider: Mapped[str] = mapped_column(String(20), nullable=False, default="gmail")
    email: Mapped[str] = mapped_column(String(320), nullable=False)
    display_name: Mapped[str] = mapped_column(String(160), nullable=False, default="")
    refresh_token: Mapped[str] = mapped_column(Text, nullable=False)
    access_token: Mapped[str | None] = mapped_column(Text)
    token_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="active")
    error: Mapped[str | None] = mapped_column(String(300))
    daily_limit: Mapped[int] = mapped_column(Integer, nullable=False, default=80)
    # Calentamiento: el límite real sube día a día desde el primer envío del buzón.
    warmup: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True, server_default="true"
    )
    signature: Mapped[str] = mapped_column(Text, nullable=False, default="")
    next_send_after: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class Campaign(IdMixin, Base):
    __tablename__ = "campaigns"
    __table_args__ = (
        CheckConstraint(_in("status", CAMPAIGN_STATUSES), name="status"),
        Index("ix_campaigns_tenant_status", "tenant_id", "status"),
    )

    tenant_id: Mapped[uuid.UUID] = _tenant()
    created_by: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL")
    )
    mailbox_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("mailboxes.id", ondelete="SET NULL")
    )
    name: Mapped[str] = mapped_column(String(160), nullable=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="draft")
    # [{"subject", "body", "delay_days"}]: el paso 1 abre el hilo; los demás responden en él.
    steps: Mapped[list[dict[str, Any]]] = mapped_column(JSONB, nullable=False, default=list)
    # {"timezone", "days": [1..7], "start_hour", "end_hour", "track_opens", "track_clicks"}
    settings: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    # {"list_id": ..., "filters": {...}, "include_estimated": bool}
    audience: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    launched_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )


class Recipient(IdMixin, Base):
    __tablename__ = "campaign_recipients"
    __table_args__ = (
        UniqueConstraint("campaign_id", "contact_id"),
        CheckConstraint(_in("status", RECIPIENT_STATUSES), name="status"),
        Index("ix_campaign_recipients_due", "tenant_id", "status", "next_send_at"),
    )

    tenant_id: Mapped[uuid.UUID] = _tenant()
    campaign_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("campaigns.id", ondelete="CASCADE"), nullable=False
    )
    contact_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("contacts.id", ondelete="CASCADE"), nullable=False
    )
    email: Mapped[str] = mapped_column(String(320), nullable=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="pending")
    step: Mapped[int] = mapped_column(Integer, nullable=False, default=0)  # próximo paso
    next_send_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    thread_id: Mapped[str | None] = mapped_column(String(100))
    first_rfc_id: Mapped[str | None] = mapped_column(String(300))
    last_rfc_id: Mapped[str | None] = mapped_column(String(300))
    first_subject: Mapped[str | None] = mapped_column(String(300))
    sent_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    opened_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    open_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    clicked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    click_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    replied_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    checked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    error: Mapped[str | None] = mapped_column(String(300))


class Message(IdMixin, Base):
    __tablename__ = "campaign_messages"
    __table_args__ = (Index("ix_campaign_messages_mailbox_sent", "mailbox_id", "sent_at"),)

    tenant_id: Mapped[uuid.UUID] = _tenant()
    campaign_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("campaigns.id", ondelete="CASCADE"), nullable=False
    )
    recipient_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("campaign_recipients.id", ondelete="CASCADE"),
        nullable=False,
    )  # fmt: skip
    mailbox_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("mailboxes.id", ondelete="SET NULL")
    )
    step: Mapped[int] = mapped_column(Integer, nullable=False)
    subject: Mapped[str] = mapped_column(String(300), nullable=False)
    gmail_id: Mapped[str | None] = mapped_column(String(100))
    thread_id: Mapped[str | None] = mapped_column(String(100))
    rfc_message_id: Mapped[str | None] = mapped_column(String(300))
    token: Mapped[str] = mapped_column(String(48), nullable=False, unique=True)
    sent_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    opened_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    open_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    click_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)


class TrackingToken(Base):
    """Índice global token → empresa (el píxel y los enlaces llegan sin sesión)."""

    __tablename__ = "tracking_tokens"

    token: Mapped[str] = mapped_column(String(48), primary_key=True)
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False
    )
    message_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("campaign_messages.id", ondelete="CASCADE"), nullable=False
    )


class Suppression(IdMixin, Base):
    """A quién nunca más se le escribe: bajas, rebotes o exclusiones manuales."""

    __tablename__ = "suppressions"
    __table_args__ = (
        UniqueConstraint("tenant_id", "email"),
        CheckConstraint(_in("reason", ("unsubscribed", "bounced", "manual")), name="reason"),
    )

    tenant_id: Mapped[uuid.UUID] = _tenant()
    email: Mapped[str] = mapped_column(String(320), nullable=False)
    reason: Mapped[str] = mapped_column(String(20), nullable=False)


class EmailAsset(IdMixin, Base):
    """Imágenes de correos y firmas. Se sirven sin sesión en /t/i/{id} (el id no se adivina),
    por eso la tabla es global como ``tracking_tokens`` y cada consulta filtra por empresa."""

    __tablename__ = "email_assets"
    __table_args__ = (Index("ix_email_assets_tenant", "tenant_id", "created_at"),)

    tenant_id: Mapped[uuid.UUID] = _tenant()
    content_type: Mapped[str] = mapped_column(String(40), nullable=False)
    name: Mapped[str] = mapped_column(String(200), nullable=False, default="")
    size: Mapped[int] = mapped_column(Integer, nullable=False)
    data: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
