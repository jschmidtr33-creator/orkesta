"""Panel de administración de ORKESTA: configuración global y bitácora del equipo interno."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import DateTime, ForeignKey, Index, String, func
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from orkesta.core.db import Base, IdMixin


class PlatformSetting(Base):
    """Valores globales (sin empresa): permisos por rol, registro, mantenimiento, anuncio…"""

    __tablename__ = "platform_settings"

    key: Mapped[str] = mapped_column(String(60), primary_key=True)
    value: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )


class AdminAudit(IdMixin, Base):
    __tablename__ = "admin_audit"
    __table_args__ = (Index("ix_admin_audit_created", "created_at"),)

    actor_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL")
    )
    action: Mapped[str] = mapped_column(String(60), nullable=False)
    target: Mapped[str] = mapped_column(String(200), nullable=False, default="")
    detail: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
