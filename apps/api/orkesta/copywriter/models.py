"""Buyer personas de cada empresa para el asistente de redacción."""

from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy import ForeignKey, String, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from orkesta.core.db import Base, IdMixin


class BuyerPersona(IdMixin, Base):
    __tablename__ = "buyer_personas"
    __table_args__ = (UniqueConstraint("tenant_id", "name"),)

    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False
    )
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    # {"area", "seniority", "industries", "pains", "goals", "objections", "notes"}
    data: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
