"""Concertino: el asesor de ventas senior de ORKESTA (IA propia, sin terceros).

Dos tablas: la ficha viva del producto de cada empresa y los casos de asesoría (cada pregunta,
lo que Concertino sugirió, lo que hizo el vendedor y cómo terminó), de los que aprende.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, Index, Integer, String, Text, func
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from orkesta.core.db import Base, IdMixin

FACT_KINDS = ("producto", "publico", "problema", "valor", "prueba", "precio", "competidor",
              "diferencial", "objecion", "pregunta")  # fmt: skip
FACT_STATUSES = ("confirmado", "aprendido", "descartado")
ACTIONS = ("ninguna", "usada", "editada", "descartada")


def _in(column: str, values: tuple[str, ...]) -> str:
    return f"{column} IN ({', '.join(repr(v) for v in values)})"


class ProductFact(IdMixin, Base):
    """Un dato de la ficha del producto. Concertino solo afirma lo que está confirmado."""

    __tablename__ = "product_facts"
    __table_args__ = (
        CheckConstraint(_in("kind", FACT_KINDS), name="kind"),
        CheckConstraint(_in("status", FACT_STATUSES), name="status"),
        Index("ix_product_facts_tenant_kind", "tenant_id", "kind", "status"),
    )

    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False
    )
    created_by: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL")
    )
    kind: Mapped[str] = mapped_column(String(20), nullable=False)
    title: Mapped[str] = mapped_column(String(300), nullable=False)
    detail: Mapped[str] = mapped_column(Text, nullable=False, default="")
    # Para objeciones y preguntas: la mejor respuesta conocida.
    answer: Mapped[str] = mapped_column(Text, nullable=False, default="")
    status: Mapped[str] = mapped_column(String(12), nullable=False, default="confirmado")
    source: Mapped[str] = mapped_column(String(20), nullable=False, default="manual")
    evidence: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    meta: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )


class AdviceCase(IdMixin, Base):
    """Una consulta a Concertino. Es privada de quien preguntó; la empresa aprende de las cifras
    (qué enfoque se usó y si terminó en reunión), nunca de que otra persona lea la conversación."""

    __tablename__ = "advice_cases"
    __table_args__ = (
        CheckConstraint(_in("action", ACTIONS), name="action"),
        Index("ix_advice_cases_tenant_user", "tenant_id", "user_id", "created_at"),
        Index("ix_advice_cases_recipient", "recipient_id"),
    )

    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False
    )
    user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL")
    )
    topic: Mapped[str] = mapped_column(String(20), nullable=False)
    objection: Mapped[str | None] = mapped_column(String(20))
    question: Mapped[str] = mapped_column(Text, nullable=False, default="")
    context: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    shown: Mapped[list[str]] = mapped_column(JSONB, nullable=False, default=list)
    chosen: Mapped[str | None] = mapped_column(String(40))
    action: Mapped[str] = mapped_column(String(12), nullable=False, default="ninguna")
    final_text: Mapped[str] = mapped_column(Text, nullable=False, default="")
    recipient_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    outcome: Mapped[str | None] = mapped_column(String(20))
    outcome_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )
