"""Propuestas comerciales: catálogo de productos y servicios, propuestas y su enlace público."""

from __future__ import annotations

import uuid
from datetime import date, datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    Date,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from orkesta.core.db import Base, IdMixin

STATUSES = ("draft", "sent", "viewed", "accepted", "rejected")
STATUS_LABELS = {
    "draft": "Borrador",
    "sent": "Enviada",
    "viewed": "Vista por el cliente",
    "accepted": "Aceptada",
    "rejected": "Rechazada",
}


def _tenant() -> Mapped[uuid.UUID]:
    return mapped_column(
        UUID(as_uuid=True), ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False
    )


class Product(IdMixin, Base):
    """Producto o servicio reutilizable en las propuestas."""

    __tablename__ = "products"
    __table_args__ = (
        UniqueConstraint("tenant_id", "name"),
        CheckConstraint("unit_price >= 0", name="unit_price"),
    )

    tenant_id: Mapped[uuid.UUID] = _tenant()
    name: Mapped[str] = mapped_column(String(160), nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False, default="")
    unit_price: Mapped[Decimal] = mapped_column(Numeric(14, 2), nullable=False, default=0)
    currency: Mapped[str] = mapped_column(String(3), nullable=False, default="USD")
    unit: Mapped[str] = mapped_column(String(40), nullable=False, default="unidad")
    active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)


class Proposal(IdMixin, Base):
    __tablename__ = "proposals"
    __table_args__ = (
        UniqueConstraint("tenant_id", "number"),
        CheckConstraint(
            "status IN ('draft', 'sent', 'viewed', 'accepted', 'rejected')", name="status"
        ),
        Index("ix_proposals_tenant_created", "tenant_id", "created_at"),
        Index("ix_proposals_tenant_deal", "tenant_id", "deal_id"),
    )

    tenant_id: Mapped[uuid.UUID] = _tenant()
    number: Mapped[int] = mapped_column(Integer, nullable=False)
    deal_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("deals.id", ondelete="SET NULL")
    )
    contact_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("contacts.id", ondelete="SET NULL")
    )
    created_by: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL")
    )
    title: Mapped[str] = mapped_column(String(200), nullable=False)
    status: Mapped[str] = mapped_column(String(10), nullable=False, default="draft")
    currency: Mapped[str] = mapped_column(String(3), nullable=False, default="USD")
    # [{"name", "description", "quantity", "unit_price", "discount"}]
    items: Mapped[list[dict[str, Any]]] = mapped_column(JSONB, nullable=False, default=list)
    # [{"key", "title", "body"}]
    sections: Mapped[list[dict[str, Any]]] = mapped_column(JSONB, nullable=False, default=list)
    tax_rate: Mapped[Decimal] = mapped_column(Numeric(5, 2), nullable=False, default=0)
    valid_until: Mapped[date | None] = mapped_column(Date)
    public_id: Mapped[str] = mapped_column(String(48), nullable=False, unique=True)
    sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    first_viewed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_viewed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    view_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    decided_by: Mapped[str | None] = mapped_column(String(160))
    decision_note: Mapped[str | None] = mapped_column(String(500))
    # Diseño de esta propuesta: plantilla, idioma, colores, portada y datos del ejecutivo.
    design: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, default=dict, server_default="{}"
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )


class ProposalLink(Base):
    """Índice global del enlace público → empresa (la tabla de propuestas está protegida por
    RLS y el cliente que abre el enlace no tiene sesión)."""

    __tablename__ = "proposal_links"

    public_id: Mapped[str] = mapped_column(String(48), primary_key=True)
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False
    )
    proposal_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("proposals.id", ondelete="CASCADE"), nullable=False
    )


class ProposalBrand(Base):
    """Identidad visual de la empresa para sus propuestas (una por espacio de trabajo)."""

    __tablename__ = "proposal_brands"

    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("tenants.id", ondelete="CASCADE"), primary_key=True
    )
    data: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )
