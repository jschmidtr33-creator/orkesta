"""Esquema MVP de ORKESTA: 16 tablas.

Los estados se guardan como texto con restricciones CHECK en lugar de tipos ENUM de PostgreSQL:
añadir un estado nuevo solo requiere cambiar la restricción, no recrear un tipo.
"""

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
    SmallInteger,
    String,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from orkesta.db.base import Base, IdMixin, OrgScopedMixin


def _in(column: str, values: tuple[str, ...]) -> str:
    quoted = ", ".join(f"'{v}'" for v in values)
    return f"{column} IN ({quoted})"


USER_ROLES = ("admin", "operator")
BATCH_STATUSES = (
    "draft",
    "importing",
    "imported",
    "queued",
    "running",
    "completed",
    "failed",
    "cancelled",
)
IMPORT_ROW_STATUSES = ("pending", "valid", "invalid", "duplicate")
DOMAIN_STATUSES = ("candidate", "probable", "confirmed", "rejected", "unknown")
CONFIDENCE_LEVELS = ("high", "medium", "low")
PATTERN_ORIGINS = ("observed", "learned")
EMAIL_LABELS = ("publicly_found", "inferred", "verified_by_provider")
JOB_STATUSES = ("pending", "running", "succeeded", "failed", "cancelled")
ML_TASKS = ("domain", "pattern")
ML_MODEL_STATUSES = ("candidate", "active", "retired")
LABEL_SOURCES = (
    "wikidata",
    "openstreetmap",
    "crawl",
    "user_confirmed",
    "user_rejected",
    "labeled_set",
)


# --- Organización y acceso -------------------------------------------------------------------


class Organization(IdMixin, Base):
    __tablename__ = "organizations"

    name: Mapped[str] = mapped_column(String(200), nullable=False)
    slug: Mapped[str] = mapped_column(String(80), nullable=False, unique=True)


class User(OrgScopedMixin, Base):
    __tablename__ = "users"
    __table_args__ = (
        CheckConstraint(_in("role", USER_ROLES), name="role"),
        Index("uq_users_email_lower", func.lower(text("email")), unique=True),
    )

    email: Mapped[str] = mapped_column(String(320), nullable=False)
    password_hash: Mapped[str] = mapped_column(String(255), nullable=False)
    role: Mapped[str] = mapped_column(
        String(20), nullable=False, default="operator", server_default="operator"
    )
    is_active: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True, server_default=text("true")
    )
    last_login_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


# --- Lotes e importación ---------------------------------------------------------------------


class EnrichmentBatch(OrgScopedMixin, Base):
    __tablename__ = "enrichment_batches"
    __table_args__ = (
        CheckConstraint(_in("status", BATCH_STATUSES), name="status"),
        CheckConstraint(
            "total_rows >= 0 AND processed_rows >= 0 AND failed_rows >= 0", name="counts"
        ),
        Index("ix_enrichment_batches_org_sha", "organization_id", "source_sha256"),
    )

    created_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL")
    )
    source_filename: Mapped[str] = mapped_column(String(255), nullable=False)
    source_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    source_format: Mapped[str] = mapped_column(
        String(10), nullable=False, default="csv", server_default="csv"
    )
    headers: Mapped[list[str]] = mapped_column(
        JSONB, nullable=False, server_default=text("'[]'::jsonb")
    )
    import_summary: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, server_default=text("'{}'::jsonb")
    )
    error_message: Mapped[str | None] = mapped_column(Text)
    column_mapping: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, server_default=text("'{}'::jsonb")
    )
    settings: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, server_default=text("'{}'::jsonb")
    )
    status: Mapped[str] = mapped_column(
        String(20), nullable=False, default="draft", server_default="draft"
    )
    total_rows: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default=text("0")
    )
    processed_rows: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default=text("0")
    )
    failed_rows: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default=text("0")
    )
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class ImportRow(OrgScopedMixin, Base):
    """Fila del archivo importado. ``raw`` es el original y nunca se modifica; los resultados
    de la validación y la normalización se guardan en columnas aparte."""

    __tablename__ = "import_rows"
    __table_args__ = (
        UniqueConstraint("batch_id", "row_number"),
        CheckConstraint(_in("status", IMPORT_ROW_STATUSES), name="status"),
        Index("ix_import_rows_batch_status", "batch_id", "status"),
    )

    batch_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("enrichment_batches.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    row_number: Mapped[int] = mapped_column(Integer, nullable=False)
    raw: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    status: Mapped[str] = mapped_column(
        String(20), nullable=False, default="pending", server_default="pending"
    )
    issues: Mapped[list[str]] = mapped_column(
        JSONB, nullable=False, server_default=text("'[]'::jsonb")
    )
    normalized: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    duplicate_of_row: Mapped[int | None] = mapped_column(Integer)
    contact_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("contacts.id", ondelete="SET NULL"), index=True
    )


# --- Empresas y contactos --------------------------------------------------------------------


class Company(OrgScopedMixin, Base):
    __tablename__ = "companies"
    __table_args__ = (
        UniqueConstraint(
            "organization_id",
            "name_normalized",
            "country_code",
            postgresql_nulls_not_distinct=True,
        ),
        CheckConstraint("country_code IS NULL OR length(country_code) = 2", name="country_code"),
    )

    name_original: Mapped[str] = mapped_column(String(300), nullable=False)
    name_normalized: Mapped[str] = mapped_column(String(300), nullable=False)
    country_code: Mapped[str | None] = mapped_column(String(2))
    city: Mapped[str | None] = mapped_column(String(120))


class Contact(OrgScopedMixin, Base):
    """Persona única por empresa: el mismo nombre normalizado en la misma empresa es el mismo
    contacto aunque llegue en varios lotes."""

    __tablename__ = "contacts"
    __table_args__ = (
        UniqueConstraint(
            "company_id",
            "first_name_normalized",
            "last_name_normalized",
            name="uq_contacts_company_person",
        ),
    )

    company_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("companies.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    first_name_original: Mapped[str] = mapped_column(String(200), nullable=False)
    last_name_original: Mapped[str] = mapped_column(String(200), nullable=False)
    first_name_normalized: Mapped[str] = mapped_column(String(200), nullable=False)
    last_name_normalized: Mapped[str] = mapped_column(String(200), nullable=False)
    title: Mapped[str | None] = mapped_column(String(300))


# --- Dominios, evidencias, patrones y correos ------------------------------------------------


class Domain(OrgScopedMixin, Base):
    __tablename__ = "domains"
    __table_args__ = (
        UniqueConstraint("company_id", "domain"),
        CheckConstraint(_in("status", DOMAIN_STATUSES), name="status"),
        CheckConstraint(
            "confidence_level IS NULL OR " + _in("confidence_level", CONFIDENCE_LEVELS),
            name="confidence_level",
        ),
        CheckConstraint("confidence_score BETWEEN 0 AND 100", name="confidence_score"),
        CheckConstraint("domain = lower(domain)", name="domain_lowercase"),
    )

    company_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("companies.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    domain: Mapped[str] = mapped_column(String(253), nullable=False)
    status: Mapped[str] = mapped_column(
        String(20), nullable=False, default="candidate", server_default="candidate"
    )
    confidence_score: Mapped[int] = mapped_column(
        SmallInteger, nullable=False, default=0, server_default=text("0")
    )
    confidence_level: Mapped[str | None] = mapped_column(String(10))
    reason_codes: Mapped[list[str]] = mapped_column(
        JSONB, nullable=False, server_default=text("'[]'::jsonb")
    )
    has_mx: Mapped[bool | None] = mapped_column(Boolean)
    mx_provider: Mapped[str | None] = mapped_column(String(80))
    discovery_method: Mapped[str] = mapped_column(String(40), nullable=False)
    model_version: Mapped[str | None] = mapped_column(String(40))
    last_checked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class EvidenceRecord(OrgScopedMixin, Base):
    __tablename__ = "evidence_records"

    domain_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("domains.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    source_type: Mapped[str] = mapped_column(String(40), nullable=False)
    url: Mapped[str] = mapped_column(Text, nullable=False)
    snippet: Mapped[str | None] = mapped_column(String(500))
    method: Mapped[str] = mapped_column(String(40), nullable=False)
    observed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class EmailPatternObservation(OrgScopedMixin, Base):
    __tablename__ = "email_pattern_observations"
    __table_args__ = (
        UniqueConstraint("domain_id", "pattern", "origin"),
        CheckConstraint(_in("origin", PATTERN_ORIGINS), name="origin"),
        CheckConstraint(_in("confidence_level", CONFIDENCE_LEVELS), name="confidence_level"),
        CheckConstraint("confidence_score BETWEEN 0 AND 100", name="confidence_score"),
        CheckConstraint("observation_count >= 0 AND exception_count >= 0", name="counts"),
    )

    domain_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("domains.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    pattern: Mapped[str] = mapped_column(String(40), nullable=False)
    origin: Mapped[str] = mapped_column(String(20), nullable=False)
    observation_count: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default=text("0")
    )
    exception_count: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default=text("0")
    )
    confidence_score: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    confidence_level: Mapped[str] = mapped_column(String(10), nullable=False)
    model_version: Mapped[str | None] = mapped_column(String(40))
    observed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class EmailCandidate(OrgScopedMixin, Base):
    __tablename__ = "email_candidates"
    __table_args__ = (
        UniqueConstraint("contact_id", "address"),
        UniqueConstraint("contact_id", "rank"),
        CheckConstraint("rank BETWEEN 0 AND 2", name="rank"),
        CheckConstraint(_in("label", EMAIL_LABELS), name="label"),
        CheckConstraint(_in("confidence_level", CONFIDENCE_LEVELS), name="confidence_level"),
        CheckConstraint("confidence_score BETWEEN 0 AND 100", name="confidence_score"),
        CheckConstraint("address = lower(address)", name="address_lowercase"),
    )

    contact_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("contacts.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    domain_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("domains.id", ondelete="SET NULL"), index=True
    )
    address: Mapped[str] = mapped_column(String(320), nullable=False)
    rank: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    label: Mapped[str] = mapped_column(String(30), nullable=False)
    pattern: Mapped[str | None] = mapped_column(String(40))
    confidence_score: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    confidence_level: Mapped[str] = mapped_column(String(10), nullable=False)
    reason_codes: Mapped[list[str]] = mapped_column(
        JSONB, nullable=False, server_default=text("'[]'::jsonb")
    )


# --- Cola de trabajos ------------------------------------------------------------------------


class EnrichmentJob(OrgScopedMixin, Base):
    """Tarea de la cola. El worker la reclama con ``SELECT ... FOR UPDATE SKIP LOCKED``."""

    __tablename__ = "enrichment_jobs"
    __table_args__ = (
        UniqueConstraint("batch_id", "idempotency_key"),
        CheckConstraint(_in("status", JOB_STATUSES), name="status"),
        CheckConstraint("attempts >= 0 AND max_attempts >= 1", name="attempts"),
        Index(
            "ix_enrichment_jobs_pending",
            "run_after",
            postgresql_where=text("status = 'pending'"),
        ),
        Index(
            "ix_enrichment_jobs_running",
            "locked_at",
            postgresql_where=text("status = 'running'"),
        ),
    )

    batch_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("enrichment_batches.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    company_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("companies.id", ondelete="SET NULL"), index=True
    )
    kind: Mapped[str] = mapped_column(String(40), nullable=False)
    idempotency_key: Mapped[str] = mapped_column(String(200), nullable=False)
    payload: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, server_default=text("'{}'::jsonb")
    )
    status: Mapped[str] = mapped_column(
        String(20), nullable=False, default="pending", server_default="pending"
    )
    attempts: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default=text("0")
    )
    max_attempts: Mapped[int] = mapped_column(
        Integer, nullable=False, default=3, server_default=text("3")
    )
    run_after: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    locked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    locked_by: Mapped[str | None] = mapped_column(String(120))
    last_error: Mapped[str | None] = mapped_column(Text)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class JobEvent(OrgScopedMixin, Base):
    __tablename__ = "job_events"

    job_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("enrichment_jobs.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    event_type: Mapped[str] = mapped_column(String(40), nullable=False)
    detail: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, server_default=text("'{}'::jsonb")
    )


# --- Machine learning ------------------------------------------------------------------------


class MlModel(OrgScopedMixin, Base):
    """Versión de un modelo entrenado.

    El artefacto se guarda en la base porque el disco de Railway es efímero.
    """

    __tablename__ = "ml_models"
    __table_args__ = (
        UniqueConstraint("organization_id", "task", "version"),
        CheckConstraint(_in("task", ML_TASKS), name="task"),
        CheckConstraint(_in("status", ML_MODEL_STATUSES), name="status"),
        Index(
            "uq_ml_models_one_active",
            "organization_id",
            "task",
            unique=True,
            postgresql_where=text("status = 'active'"),
        ),
    )

    task: Mapped[str] = mapped_column(String(20), nullable=False)
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[str] = mapped_column(
        String(20), nullable=False, default="candidate", server_default="candidate"
    )
    algorithm: Mapped[str] = mapped_column(String(60), nullable=False)
    features: Mapped[list[str]] = mapped_column(JSONB, nullable=False)
    metrics: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    dataset_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    artifact: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    trained_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class TrainingExample(OrgScopedMixin, Base):
    __tablename__ = "training_examples"
    __table_args__ = (
        CheckConstraint(_in("task", ML_TASKS), name="task"),
        CheckConstraint(_in("label_source", LABEL_SOURCES), name="label_source"),
        Index("ix_training_examples_org_task", "organization_id", "task"),
    )

    task: Mapped[str] = mapped_column(String(20), nullable=False)
    label_source: Mapped[str] = mapped_column(String(30), nullable=False)
    company_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("companies.id", ondelete="SET NULL"), index=True
    )
    domain_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("domains.id", ondelete="SET NULL"), index=True
    )
    features: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    label: Mapped[str] = mapped_column(String(60), nullable=False)


# --- Ajustes y auditoría ---------------------------------------------------------------------


class AppSetting(OrgScopedMixin, Base):
    __tablename__ = "app_settings"
    __table_args__ = (UniqueConstraint("organization_id", "key"),)

    key: Mapped[str] = mapped_column(String(120), nullable=False)
    value: Mapped[Any] = mapped_column(JSONB, nullable=False)


class AuditLog(OrgScopedMixin, Base):
    __tablename__ = "audit_logs"
    __table_args__ = (Index("ix_audit_logs_org_created", "organization_id", "created_at"),)

    actor_user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL")
    )
    action: Mapped[str] = mapped_column(String(80), nullable=False)
    entity_type: Mapped[str | None] = mapped_column(String(60))
    entity_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    detail: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, server_default=text("'{}'::jsonb")
    )
