"""CRM: etapas del pipeline, oportunidades y la línea de tiempo de cada contacto.

Todas las tablas llevan ``tenant_id`` y están protegidas por Row-Level Security.
"""

from __future__ import annotations

import uuid
from datetime import date, datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import (
    CheckConstraint,
    Date,
    DateTime,
    ForeignKey,
    Index,
    Numeric,
    SmallInteger,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from orkesta.core.db import Base, IdMixin

STAGE_KINDS = ("open", "won", "lost")
DEAL_STATUSES = ("open", "won", "lost")
ACTIVITY_TYPES = ("note", "call", "meeting", "email", "task", "stage", "system")
ACTIVITY_LABELS = {
    "note": "Nota",
    "call": "Llamada",
    "meeting": "Reunión",
    "email": "Correo",
    "task": "Tarea",
    "stage": "Cambio de etapa",
    "system": "Sistema",
}
DEFAULT_STAGES = (
    ("Nuevo lead", "open", 10),
    ("Contactado", "open", 20),
    ("Reunión agendada", "open", 40),
    ("Propuesta enviada", "open", 60),
    ("Negociación", "open", 80),
    ("Ganado", "won", 100),
    ("Perdido", "lost", 0),
)
CURRENCIES = ("USD", "COP", "MXN", "PEN", "CLP", "CRC", "EUR")


def _in(column: str, values: tuple[str, ...]) -> str:
    return f"{column} IN ({', '.join(repr(v) for v in values)})"


def _tenant() -> Mapped[uuid.UUID]:
    return mapped_column(
        UUID(as_uuid=True), ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False
    )


class Stage(IdMixin, Base):
    __tablename__ = "pipeline_stages"
    __table_args__ = (
        CheckConstraint(_in("kind", STAGE_KINDS), name="kind"),
        CheckConstraint("probability BETWEEN 0 AND 100", name="probability"),
        Index("ix_pipeline_stages_tenant_position", "tenant_id", "position"),
        UniqueConstraint("tenant_id", "name"),
    )

    tenant_id: Mapped[uuid.UUID] = _tenant()
    name: Mapped[str] = mapped_column(String(80), nullable=False)
    kind: Mapped[str] = mapped_column(String(10), nullable=False, default="open")
    position: Mapped[int] = mapped_column(SmallInteger, nullable=False, default=0)
    probability: Mapped[int] = mapped_column(SmallInteger, nullable=False, default=0)


class Deal(IdMixin, Base):
    """Oportunidad comercial: un contacto, una empresa, una etapa y un valor."""

    __tablename__ = "deals"
    __table_args__ = (
        CheckConstraint(_in("status", DEAL_STATUSES), name="status"),
        CheckConstraint(_in("currency", CURRENCIES), name="currency"),
        CheckConstraint("value >= 0", name="value"),
        Index("ix_deals_tenant_stage", "tenant_id", "stage_id"),
        Index("ix_deals_tenant_contact", "tenant_id", "contact_id"),
    )

    tenant_id: Mapped[uuid.UUID] = _tenant()
    title: Mapped[str] = mapped_column(String(200), nullable=False)
    contact_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("contacts.id", ondelete="SET NULL")
    )
    company_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("companies.id", ondelete="SET NULL")
    )
    owner_user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL")
    )
    stage_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("pipeline_stages.id", ondelete="RESTRICT"), nullable=False
    )
    status: Mapped[str] = mapped_column(String(10), nullable=False, default="open")
    value: Mapped[Decimal] = mapped_column(Numeric(14, 2), nullable=False, default=0)
    currency: Mapped[str] = mapped_column(String(3), nullable=False, default="USD")
    expected_close: Mapped[date | None] = mapped_column(Date)
    lost_reason: Mapped[str | None] = mapped_column(String(300))
    closed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )


class Activity(IdMixin, Base):
    """Un hecho en la línea de tiempo: nota, llamada, reunión, tarea o cambio de etapa."""

    __tablename__ = "activities"
    __table_args__ = (
        CheckConstraint(_in("type", ACTIVITY_TYPES), name="type"),
        Index("ix_activities_tenant_contact", "tenant_id", "contact_id", "created_at"),
        Index("ix_activities_tenant_deal", "tenant_id", "deal_id", "created_at"),
        Index("ix_activities_tenant_open_tasks", "tenant_id", "type", "done_at", "due_at"),
    )

    tenant_id: Mapped[uuid.UUID] = _tenant()
    type: Mapped[str] = mapped_column(String(10), nullable=False)
    contact_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("contacts.id", ondelete="CASCADE")
    )
    deal_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("deals.id", ondelete="CASCADE")
    )
    user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL")
    )
    body: Mapped[str] = mapped_column(Text, nullable=False, default="")
    due_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    done_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    reminded_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    meta: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
