"""Modelos versionados de la IA propia y ejemplos etiquetados (correcciones de las personas)."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import (
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    LargeBinary,
    String,
    Text,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from orkesta.core.db import Base, IdMixin


class BrainModel(IdMixin, Base):
    """Una versión entrenada de un modelo. Solo una por tarea está activa (la que se usa)."""

    __tablename__ = "brain_models"
    __table_args__ = (Index("ix_brain_models_task_version", "task", "version", unique=True),)

    task: Mapped[str] = mapped_column(String(40), nullable=False)
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    algorithm: Mapped[str] = mapped_column(String(30), nullable=False)
    active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    trigger: Mapped[str] = mapped_column(String(20), nullable=False, default="manual")
    n_train: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    n_test: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    threshold: Mapped[float] = mapped_column(Float, nullable=False, default=0.6)
    metrics: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    baseline: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    data_hash: Mapped[str] = mapped_column(String(64), nullable=False, default="")
    duration_ms: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    blob: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    notes: Mapped[str] = mapped_column(Text, nullable=False, default="")


class BrainFeedback(IdMixin, Base):
    """Un ejemplo etiquetado por una persona (p. ej. corrigió la categoría de una respuesta).

    Es la materia prima para que los modelos aprendan de sus errores."""

    __tablename__ = "brain_feedback"
    __table_args__ = (Index("ix_brain_feedback_task_created", "task", "created_at"),)

    task: Mapped[str] = mapped_column(String(40), nullable=False)
    text: Mapped[str] = mapped_column(Text, nullable=False, default="")
    data: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    label: Mapped[str] = mapped_column(String(60), nullable=False)
    predicted: Mapped[str | None] = mapped_column(String(60))
    tenant_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("orkesta.tenants.id", ondelete="CASCADE")
    )
    user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("orkesta.users.id", ondelete="SET NULL")
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )
