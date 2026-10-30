"""Datos comerciales: empresas, contactos, procedencia por campo, listas e importaciones.

Todas las tablas llevan ``tenant_id`` y están protegidas por Row-Level Security.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    LargeBinary,
    SmallInteger,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from orkesta.core.db import Base, IdMixin

CONTACT_STATUSES = ("nuevo", "en_proceso", "enriquecido", "validado")
STATUS_LABELS = {
    "nuevo": "Nuevo",
    "en_proceso": "En proceso",
    "enriquecido": "Enriquecido",
    "validado": "Validado",
}
SOURCE_TYPES = ("importacion", "manual", "enriquecimiento", "investigacion")
IMPORT_STATUSES = ("preview", "queued", "running", "completed", "failed", "canceled")
# Campos del contacto con procedencia registrada (los 8 que se conservan de las bases).
TRACKED_FIELDS = (
    "first_name", "last_name", "job_title", "company", "email", "phone", "city",
    "country_code", "industry",
)  # fmt: skip


def _in(column: str, values: tuple[str, ...]) -> str:
    return f"{column} IN ({', '.join(repr(v) for v in values)})"


def _tenant() -> Mapped[uuid.UUID]:
    return mapped_column(
        UUID(as_uuid=True), ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False
    )


class Company(IdMixin, Base):
    __tablename__ = "companies"
    __table_args__ = (UniqueConstraint("tenant_id", "match_key"),)

    tenant_id: Mapped[uuid.UUID] = _tenant()
    name: Mapped[str] = mapped_column(String(300), nullable=False)
    match_key: Mapped[str] = mapped_column(String(300), nullable=False)
    domain: Mapped[str | None] = mapped_column(String(253))
    industry: Mapped[str | None] = mapped_column(String(200))
    industry_code: Mapped[str | None] = mapped_column(String(40))
    city: Mapped[str | None] = mapped_column(String(120))
    country_code: Mapped[str | None] = mapped_column(String(2))


class Contact(IdMixin, Base):
    __tablename__ = "contacts"
    __table_args__ = (
        UniqueConstraint("tenant_id", "dedup_key"),
        CheckConstraint(_in("status", CONTACT_STATUSES), name="status"),
        CheckConstraint(_in("source", SOURCE_TYPES), name="source"),
        CheckConstraint("email IS NULL OR email = lower(email)", name="email_lowercase"),
        Index("ix_contacts_tenant_status", "tenant_id", "status"),
        Index("ix_contacts_tenant_company", "tenant_id", "company_id"),
        Index(
            "ix_contacts_search_trgm",
            "search_text",
            postgresql_using="gin",
            postgresql_ops={"search_text": "gin_trgm_ops"},
        ),
    )

    tenant_id: Mapped[uuid.UUID] = _tenant()
    company_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("companies.id", ondelete="SET NULL")
    )
    owner_user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL")
    )
    dedup_key: Mapped[str] = mapped_column(String(400), nullable=False)
    first_name: Mapped[str] = mapped_column(String(200), nullable=False, default="")
    last_name: Mapped[str] = mapped_column(String(200), nullable=False, default="")
    full_name: Mapped[str] = mapped_column(String(300), nullable=False)
    job_title: Mapped[str | None] = mapped_column(String(300))
    level_code: Mapped[str | None] = mapped_column(String(30))
    area_code: Mapped[str | None] = mapped_column(String(30))
    company_name: Mapped[str | None] = mapped_column(String(300))
    email: Mapped[str | None] = mapped_column(String(320))
    email_domain: Mapped[str | None] = mapped_column(String(253))
    phone: Mapped[str | None] = mapped_column(String(40))
    city: Mapped[str | None] = mapped_column(String(120))
    country_code: Mapped[str | None] = mapped_column(String(2))
    industry: Mapped[str | None] = mapped_column(String(200))
    industry_code: Mapped[str | None] = mapped_column(String(40))
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="nuevo")
    source: Mapped[str] = mapped_column(String(20), nullable=False)
    search_text: Mapped[str] = mapped_column(Text, nullable=False, default="")
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )


class DataPoint(IdMixin, Base):
    """De dónde salió cada dato de un contacto, cuándo y con qué confianza."""

    __tablename__ = "data_points"
    __table_args__ = (
        CheckConstraint(_in("source_type", SOURCE_TYPES), name="source_type"),
        Index("ix_data_points_contact_field", "contact_id", "field"),
    )

    tenant_id: Mapped[uuid.UUID] = _tenant()
    contact_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("contacts.id", ondelete="CASCADE"), nullable=False
    )
    field: Mapped[str] = mapped_column(String(40), nullable=False)
    value: Mapped[str] = mapped_column(Text, nullable=False)
    source_type: Mapped[str] = mapped_column(String(20), nullable=False)
    source_label: Mapped[str] = mapped_column(String(300), nullable=False)
    source_ref: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    confidence: Mapped[int] = mapped_column(SmallInteger, nullable=False, default=100)
    actor_user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL")
    )


class ContactList(IdMixin, Base):
    __tablename__ = "contact_lists"
    __table_args__ = (UniqueConstraint("tenant_id", "name"),)

    tenant_id: Mapped[uuid.UUID] = _tenant()
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    description: Mapped[str] = mapped_column(String(500), nullable=False, default="")
    created_by: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL")
    )


class ListMember(IdMixin, Base):
    __tablename__ = "list_members"
    __table_args__ = (UniqueConstraint("list_id", "contact_id"),)

    tenant_id: Mapped[uuid.UUID] = _tenant()
    list_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("contact_lists.id", ondelete="CASCADE"), nullable=False
    )
    contact_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("contacts.id", ondelete="CASCADE"), nullable=False,
        index=True,
    )  # fmt: skip


class ImportJob(IdMixin, Base):
    __tablename__ = "imports"
    __table_args__ = (CheckConstraint(_in("status", IMPORT_STATUSES), name="status"),)

    tenant_id: Mapped[uuid.UUID] = _tenant()
    created_by: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL")
    )
    filename: Mapped[str] = mapped_column(String(300), nullable=False)
    size_bytes: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="preview")
    # Hojas detectadas: nombre, encabezados, columnas sugeridas y filas de muestra.
    sheets: Mapped[list[dict[str, Any]]] = mapped_column(JSONB, nullable=False, default=list)
    mapping: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    list_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("contact_lists.id", ondelete="SET NULL")
    )
    totals: Mapped[dict[str, int]] = mapped_column(JSONB, nullable=False, default=dict)
    error: Mapped[str | None] = mapped_column(Text)
    # El archivo se guarda solo hasta procesarlo; después se borra.
    content: Mapped[bytes | None] = mapped_column(LargeBinary)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class Job(IdMixin, Base):
    """Cola de trabajos en PostgreSQL (FOR UPDATE SKIP LOCKED). Tabla global: el worker fija el
    contexto de la empresa de cada trabajo antes de ejecutarlo."""

    __tablename__ = "jobs"
    __table_args__ = (
        CheckConstraint(_in("status", ("pending", "running", "done", "failed")), name="status"),
        Index("ix_jobs_pending", "status", "run_after"),
    )

    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False
    )
    kind: Mapped[str] = mapped_column(String(60), nullable=False)
    payload: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="pending")
    attempts: Mapped[int] = mapped_column(SmallInteger, nullable=False, default=0)
    max_attempts: Mapped[int] = mapped_column(SmallInteger, nullable=False, default=3)
    run_after: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    locked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    locked_by: Mapped[str | None] = mapped_column(String(100))
    last_error: Mapped[str | None] = mapped_column(Text)
