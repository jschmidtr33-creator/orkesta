"""Tablas de la plataforma: empresas, usuarios, membresías, invitaciones, sesiones, auditoría y
solicitudes de demo.

Las tablas con ``tenant_id`` están protegidas por Row-Level Security (ver la migración inicial).
``users``, ``sessions``, ``login_attempts`` y ``demo_requests`` son globales: un usuario puede
pertenecer a varias empresas.
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
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from orkesta.core.db import Base, IdMixin

ROLES = ("admin", "supervisor", "vendedor")
ROLE_LABELS = {"admin": "Administrador", "supervisor": "Supervisor", "vendedor": "Vendedor"}


def _in(column: str, values: tuple[str, ...]) -> str:
    return f"{column} IN ({', '.join(repr(v) for v in values)})"


class Tenant(IdMixin, Base):
    __tablename__ = "tenants"

    name: Mapped[str] = mapped_column(String(160), nullable=False)
    slug: Mapped[str] = mapped_column(String(80), nullable=False, unique=True)
    plan: Mapped[str] = mapped_column(String(30), nullable=False, default="inicial")
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="active")


class User(IdMixin, Base):
    __tablename__ = "users"
    __table_args__ = (CheckConstraint("email = lower(email)", name="email_lowercase"),)

    email: Mapped[str] = mapped_column(String(320), nullable=False, unique=True)
    full_name: Mapped[str] = mapped_column(String(200), nullable=False, default="")
    password_hash: Mapped[str | None] = mapped_column(String(255))
    google_sub: Mapped[str | None] = mapped_column(String(64), unique=True)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    is_platform_admin: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    # Rol en el panel de administración de ORKESTA: owner (creadora), admin, soporte, lectura.
    platform_role: Mapped[str | None] = mapped_column(String(20))
    last_tenant_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    last_login_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class Membership(IdMixin, Base):
    __tablename__ = "memberships"
    __table_args__ = (
        UniqueConstraint("tenant_id", "user_id"),
        CheckConstraint(_in("role", ROLES), name="role"),
        CheckConstraint(_in("status", ("active", "disabled")), name="status"),
    )

    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    role: Mapped[str] = mapped_column(String(20), nullable=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="active")


class Invitation(IdMixin, Base):
    __tablename__ = "invitations"
    __table_args__ = (
        CheckConstraint(_in("role", ROLES), name="role"),
        Index("ix_invitations_token_hash", "token_hash", unique=True),
    )

    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False
    )
    email: Mapped[str] = mapped_column(String(320), nullable=False)
    role: Mapped[str] = mapped_column(String(20), nullable=False)
    token_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    invited_by: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL")
    )
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    accepted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class AuthSession(IdMixin, Base):
    """Sesión con token de refresco rotativo (se guarda solo su hash)."""

    __tablename__ = "sessions"
    __table_args__ = (Index("ix_sessions_token_hash", "token_hash", unique=True),)

    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    tenant_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("tenants.id", ondelete="CASCADE")
    )
    token_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    user_agent: Mapped[str | None] = mapped_column(String(300))
    ip: Mapped[str | None] = mapped_column(String(64))


class LoginAttempt(IdMixin, Base):
    __tablename__ = "login_attempts"
    __table_args__ = (Index("ix_login_attempts_email_created", "email", "created_at"),)

    email: Mapped[str] = mapped_column(String(320), nullable=False)
    ip: Mapped[str | None] = mapped_column(String(64))
    succeeded: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)


class AuditLog(IdMixin, Base):
    __tablename__ = "audit_logs"
    __table_args__ = (Index("ix_audit_logs_tenant_created", "tenant_id", "created_at"),)

    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False
    )
    actor_user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL")
    )
    action: Mapped[str] = mapped_column(String(80), nullable=False)
    entity_type: Mapped[str | None] = mapped_column(String(60))
    entity_id: Mapped[str | None] = mapped_column(String(64))
    detail: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    ip: Mapped[str | None] = mapped_column(String(64))


class DemoRequest(IdMixin, Base):
    """Solicitud de demo desde el sitio público (dato de la plataforma, no de una empresa)."""

    __tablename__ = "demo_requests"

    name: Mapped[str] = mapped_column(String(160), nullable=False)
    email: Mapped[str] = mapped_column(String(320), nullable=False)
    company: Mapped[str] = mapped_column(String(160), nullable=False)
    job_title: Mapped[str] = mapped_column(String(160), nullable=False, default="")
    country: Mapped[str] = mapped_column(String(80), nullable=False, default="")
    message: Mapped[str] = mapped_column(Text, nullable=False, default="")
    consent: Mapped[bool] = mapped_column(Boolean, nullable=False)
    ip: Mapped[str | None] = mapped_column(String(64))
