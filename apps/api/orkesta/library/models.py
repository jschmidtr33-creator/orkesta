"""Plantillas de equipo: campañas y propuestas que toda la empresa puede reutilizar."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, Index, Integer, String, func
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from orkesta.core.db import Base, IdMixin

KINDS = ("campaign", "proposal")


class TeamTemplate(IdMixin, Base):
    """Copia del contenido (sin destinatarios ni cliente). La ven todas las personas de la
    empresa, aunque cada campaña y propuesta siga siendo privada de quien la crea."""

    __tablename__ = "team_templates"
    __table_args__ = (
        CheckConstraint("kind IN ('campaign', 'proposal')", name="kind"),
        Index("ix_team_templates_tenant_kind", "tenant_id", "kind"),
    )

    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False
    )
    created_by: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL")
    )
    kind: Mapped[str] = mapped_column(String(10), nullable=False)
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    description: Mapped[str] = mapped_column(String(300), nullable=False, default="")
    payload: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    use_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )
