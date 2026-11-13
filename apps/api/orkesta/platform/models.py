"""Tablas de la plataforma: empresas, usuarios, membresías, invitaciones, sesiones, auditoría y
solicitudes de demo.

Las tablas con ``tenant_id`` están protegidas por Row-Level Security (ver la migración inicial).
``users``, ``sessions``, ``login_attempts`` y ``demo_requests`` son globales: un usuario puede
pertenecer a varias empresas.
"""

from __future__ import annotations

import uuid
from datetime import UTC, date, datetime
from typing import Any

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    Date,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    LargeBinary,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from orkesta.core.db import Base, IdMixin

ROLES = ("admin", "supervisor", "vendedor")
ROLE_LABELS = {"admin": "Administrador", "supervisor": "Jefatura", "vendedor": "Vendedor"}
ROLE_HELP = {
    "admin": "Configura la empresa y ve el trabajo de todo el equipo.",
    "supervisor": "Ve el trabajo de las personas que tiene a cargo, sin poder modificarlo. "
    "Su propio trabajo no lo ve su equipo.",
    "vendedor": "Ve y trabaja solo lo suyo: contactos, listas, búsquedas, campañas y pipeline.",
}


def _in(column: str, values: tuple[str, ...]) -> str:
    return f"{column} IN ({', '.join(repr(v) for v in values)})"


class Tenant(IdMixin, Base):
    __tablename__ = "tenants"

    name: Mapped[str] = mapped_column(String(160), nullable=False)
    slug: Mapped[str] = mapped_column(String(80), nullable=False, unique=True)
    plan: Mapped[str] = mapped_column(String(30), nullable=False, default="inicial")
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="active")
    # Demo para empresas: hasta cuándo dura (plan «demo»). Al vencer, el espacio queda en pausa.
    trial_ends_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    @property
    def trial_expired(self) -> bool:
        return (
            self.plan == "demo"
            and self.trial_ends_at is not None
            and (self.trial_ends_at <= datetime.now(UTC))
        )


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
    # Perfil
    job_title: Mapped[str | None] = mapped_column(String(160))
    phone: Mapped[str | None] = mapped_column(String(40))
    recovery_email: Mapped[str | None] = mapped_column(String(320))
    timezone: Mapped[str | None] = mapped_column(String(60))
    avatar: Mapped[bytes | None] = mapped_column(LargeBinary, deferred=True)
    avatar_type: Mapped[str | None] = mapped_column(String(30))
    avatar_version: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    # Doble verificación (TOTP): secreto cifrado, códigos de respaldo (huellas) y último paso.
    mfa_secret: Mapped[str | None] = mapped_column(Text)
    mfa_enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    mfa_recovery: Mapped[list[str]] = mapped_column(JSONB, nullable=False, default=list)
    mfa_last_step: Mapped[int | None] = mapped_column(BigInteger)
    # Resumen diario por correo
    digest_enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    digest_last_sent: Mapped[date | None] = mapped_column(Date)

    @property
    def avatar_url(self) -> str | None:
        return f"/api/avatars/{self.id}?v={self.avatar_version}" if self.avatar_type else None


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


class TeamAssignment(IdMixin, Base):
    """Una persona a cargo de una jefatura: la jefatura ve su trabajo, sin poder modificarlo."""

    __tablename__ = "team_assignments"
    __table_args__ = (
        UniqueConstraint("tenant_id", "manager_id", "member_id", name="uq_team_assignments_pair"),
        CheckConstraint("manager_id <> member_id", name="distinct"),
        Index("ix_team_assignments_manager", "tenant_id", "manager_id"),
    )

    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False
    )
    manager_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    member_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )


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


class PushSubscription(IdMixin, Base):
    """Navegador o celular donde la persona activó las notificaciones push (Web Push)."""

    __tablename__ = "push_subscriptions"

    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    endpoint: Mapped[str] = mapped_column(String(1000), nullable=False, unique=True)
    p256dh: Mapped[str] = mapped_column(String(200), nullable=False)
    auth: Mapped[str] = mapped_column(String(100), nullable=False)
    user_agent: Mapped[str] = mapped_column(String(300), nullable=False, default="")


class ApiToken(IdMixin, Base):
    """Clave personal de la extensión de Chrome. Se guarda solo su huella (SHA-256): la clave
    completa se muestra una vez al crearla. Sirve únicamente para los contactos de su dueño."""

    __tablename__ = "api_tokens"

    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    name: Mapped[str] = mapped_column(String(80), nullable=False, default="Extensión de Chrome")
    token_hash: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    prefix: Mapped[str] = mapped_column(String(16), nullable=False)
    last_used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
