"""Automatización: reglas «cuando pasa X → hacer Y» y su historial de ejecuciones."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import Boolean, DateTime, ForeignKey, Index, Integer, String
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from orkesta.core.db import Base, IdMixin


def _tenant() -> Mapped[uuid.UUID]:
    return mapped_column(
        UUID(as_uuid=True), ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False
    )


class Rule(IdMixin, Base):
    __tablename__ = "automation_rules"
    __table_args__ = (
        Index("ix_automation_rules_tenant_trigger", "tenant_id", "trigger", "enabled"),
    )

    tenant_id: Mapped[uuid.UUID] = _tenant()
    created_by: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL")
    )
    name: Mapped[str] = mapped_column(String(160), nullable=False)
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    trigger: Mapped[str] = mapped_column(String(40), nullable=False)
    conditions: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    actions: Mapped[list[dict[str, Any]]] = mapped_column(JSONB, nullable=False, default=list)
    run_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    last_run_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class Run(IdMixin, Base):
    __tablename__ = "automation_runs"
    __table_args__ = (Index("ix_automation_runs_tenant_created", "tenant_id", "created_at"),)

    tenant_id: Mapped[uuid.UUID] = _tenant()
    rule_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("automation_rules.id", ondelete="CASCADE"), nullable=False
    )
    trigger: Mapped[str] = mapped_column(String(40), nullable=False)
    contact_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("contacts.id", ondelete="SET NULL")
    )
    status: Mapped[str] = mapped_column(String(10), nullable=False)  # ok | error | skipped
    results: Mapped[list[dict[str, Any]]] = mapped_column(JSONB, nullable=False, default=list)
