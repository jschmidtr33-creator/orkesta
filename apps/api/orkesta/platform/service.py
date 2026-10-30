"""Reglas de la plataforma: registro de empresas, sesiones, equipo, invitaciones y auditoría."""

from __future__ import annotations

import re
import unicodedata
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

from email_validator import EmailNotValidError, validate_email
from sqlalchemy import func, select, text, update
from sqlalchemy.orm import Session

from orkesta.core.config import get_settings
from orkesta.core.db import set_context
from orkesta.core.security import (
    AccessClaims,
    hash_password,
    issue_access_token,
    new_token,
    token_hash,
    validate_password,
    verify_password,
)
from orkesta.platform.models import (
    ROLES,
    AuditLog,
    AuthSession,
    DemoRequest,
    Invitation,
    LoginAttempt,
    Membership,
    Tenant,
    User,
)


class PlatformError(ValueError):
    """Error de negocio con mensaje apto para mostrar al usuario."""

    def __init__(self, message: str, status: int = 400) -> None:
        super().__init__(message)
        self.status = status


@dataclass(frozen=True)
class Tokens:
    access: str
    refresh: str
    claims: AccessClaims


def now() -> datetime:
    return datetime.now(UTC)


def normalize_email(value: str) -> str:
    try:
        return validate_email(value.strip(), check_deliverability=False).normalized.lower()
    except EmailNotValidError as exc:
        raise PlatformError("Escribe un correo válido.") from exc


def _slugify(name: str) -> str:
    ascii_name = unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode()
    slug = re.sub(r"[^a-z0-9]+", "-", ascii_name.lower()).strip("-")
    return (slug or "empresa")[:60]


def _unique_slug(session: Session, name: str) -> str:
    base = _slugify(name)
    slug, n = base, 1
    while session.scalar(select(Tenant.id).where(Tenant.slug == slug)) is not None:
        n += 1
        slug = f"{base}-{n}"
    return slug


# --- Auditoría -----------------------------------------------------------------------------


def audit(
    session: Session,
    *,
    tenant_id: uuid.UUID,
    actor_id: uuid.UUID | None,
    action: str,
    entity_type: str | None = None,
    entity_id: str | uuid.UUID | None = None,
    ip: str | None = None,
    **detail: Any,
) -> None:
    session.add(
        AuditLog(
            tenant_id=tenant_id,
            actor_user_id=actor_id,
            action=action,
            entity_type=entity_type,
            entity_id=str(entity_id) if entity_id else None,
            detail=detail,
            ip=ip,
        )
    )


# --- Empresas y usuarios -------------------------------------------------------------------


def create_user(
    session: Session,
    *,
    email: str,
    full_name: str,
    password: str | None = None,
    google_sub: str | None = None,
    platform_admin: bool = False,
) -> User:
    email = normalize_email(email)
    if session.scalar(select(User.id).where(User.email == email)) is not None:
        raise PlatformError("Ya existe una cuenta con ese correo. Inicia sesión.", 409)
    if password is not None:
        validate_password(password)
    user = User(
        email=email,
        full_name=full_name.strip()[:200],
        password_hash=hash_password(password) if password else None,
        google_sub=google_sub,
        is_platform_admin=platform_admin,
    )
    session.add(user)
    session.flush()
    return user


def create_tenant(session: Session, *, owner: User, name: str, ip: str | None = None) -> Tenant:
    name = name.strip()
    if len(name) < 2:
        raise PlatformError("Escribe el nombre de la empresa.")
    tenant = Tenant(name=name[:160], slug=_unique_slug(session, name))
    session.add(tenant)
    session.flush()
    set_context(session, tenant_id=tenant.id, user_id=owner.id)
    session.add(Membership(tenant_id=tenant.id, user_id=owner.id, role="admin"))
    owner.last_tenant_id = tenant.id
    audit(session, tenant_id=tenant.id, actor_id=owner.id, action="tenant.created", ip=ip)
    session.flush()
    return tenant


def register(
    session: Session, *, company: str, full_name: str, email: str, password: str, ip: str | None
) -> tuple[User, Tenant]:
    if len(full_name.strip()) < 2:
        raise PlatformError("Escribe tu nombre completo.")
    user = create_user(session, email=email, full_name=full_name, password=password)
    tenant = create_tenant(session, owner=user, name=company, ip=ip)
    return user, tenant


def active_memberships(session: Session, user_id: uuid.UUID) -> list[tuple[Membership, Tenant]]:
    set_context(session, user_id=user_id)
    rows = session.execute(
        select(Membership, Tenant)
        .join(Tenant, Tenant.id == Membership.tenant_id)
        .where(
            Membership.user_id == user_id,
            Membership.status == "active",
            Tenant.status == "active",
        )
        .order_by(Tenant.name)
    ).tuples()
    return list(rows)


def _pick_membership(
    session: Session, user: User, tenant_id: uuid.UUID | None = None
) -> Membership | None:
    memberships = [m for m, _ in active_memberships(session, user.id)]
    wanted = tenant_id or user.last_tenant_id
    for membership in memberships:
        if membership.tenant_id == wanted:
            return membership
    if tenant_id is not None:
        raise PlatformError("No perteneces a ese espacio de trabajo.", 403)
    return memberships[0] if memberships else None


# --- Sesiones ------------------------------------------------------------------------------


def start_session(
    session: Session,
    user: User,
    *,
    tenant_id: uuid.UUID | None = None,
    user_agent: str | None = None,
    ip: str | None = None,
) -> Tokens:
    membership = _pick_membership(session, user, tenant_id)
    claims = AccessClaims(
        user_id=user.id,
        tenant_id=membership.tenant_id if membership else None,
        role=membership.role if membership else None,
        platform_admin=user.is_platform_admin,
    )
    refresh = new_token()
    session.add(
        AuthSession(
            user_id=user.id,
            tenant_id=claims.tenant_id,
            token_hash=token_hash(refresh),
            expires_at=now() + timedelta(days=get_settings().refresh_token_days),
            user_agent=(user_agent or "")[:300] or None,
            ip=ip,
        )
    )
    if membership:
        user.last_tenant_id = membership.tenant_id
    user.last_login_at = now()
    session.flush()
    return Tokens(issue_access_token(claims), refresh, claims)


def _too_many_failures(session: Session, email: str) -> bool:
    settings = get_settings()
    since = now() - timedelta(minutes=settings.login_window_minutes)
    failures = session.scalar(
        select(func.count())
        .select_from(LoginAttempt)
        .where(
            LoginAttempt.email == email,
            LoginAttempt.succeeded.is_(False),
            LoginAttempt.created_at >= since,
        )
    )
    return (failures or 0) >= settings.login_max_failures


def login(
    session: Session, *, email: str, password: str, user_agent: str | None, ip: str | None
) -> Tokens:
    email = normalize_email(email)
    if _too_many_failures(session, email):
        raise PlatformError(
            "Demasiados intentos fallidos. Espera unos minutos y vuelve a intentarlo.", 429
        )
    user = session.scalar(select(User).where(User.email == email))
    ok = verify_password(password, user.password_hash if user else None)
    session.add(LoginAttempt(email=email, ip=ip, succeeded=bool(ok and user and user.is_active)))
    if not ok or user is None or not user.is_active:
        session.flush()
        raise PlatformError("Correo o contraseña incorrectos.", 401)
    return start_session(session, user, user_agent=user_agent, ip=ip)


def refresh_session(
    session: Session, refresh: str, *, tenant_id: uuid.UUID | None = None, ip: str | None = None
) -> Tokens:
    """Rota el token de refresco: el anterior queda revocado. Reutilizar uno revocado cierra
    todas las sesiones del usuario (posible robo)."""
    record = session.scalar(
        select(AuthSession).where(AuthSession.token_hash == token_hash(refresh)).with_for_update()
    )
    if record is None:
        raise PlatformError("La sesión expiró. Inicia sesión de nuevo.", 401)
    if record.revoked_at is not None:
        session.execute(
            update(AuthSession)
            .where(AuthSession.user_id == record.user_id, AuthSession.revoked_at.is_(None))
            .values(revoked_at=now())
        )
        session.flush()
        raise PlatformError("La sesión expiró. Inicia sesión de nuevo.", 401)
    if record.expires_at <= now():
        raise PlatformError("La sesión expiró. Inicia sesión de nuevo.", 401)
    user = session.get(User, record.user_id)
    if user is None or not user.is_active:
        raise PlatformError("La sesión expiró. Inicia sesión de nuevo.", 401)
    record.revoked_at = now()
    return start_session(
        session, user, tenant_id=tenant_id or record.tenant_id, user_agent=record.user_agent, ip=ip
    )


def end_session(session: Session, refresh: str | None) -> None:
    if not refresh:
        return
    session.execute(
        update(AuthSession)
        .where(AuthSession.token_hash == token_hash(refresh), AuthSession.revoked_at.is_(None))
        .values(revoked_at=now())
    )


# --- Equipo e invitaciones -----------------------------------------------------------------


def _check_role(role: str) -> str:
    if role not in ROLES:
        raise PlatformError("Rol no válido.")
    return role


def invite(
    session: Session,
    *,
    tenant_id: uuid.UUID,
    inviter_id: uuid.UUID,
    email: str,
    role: str,
    ip: str | None = None,
) -> tuple[Invitation, str]:
    email = normalize_email(email)
    role = _check_role(role)
    existing_user = session.scalar(select(User).where(User.email == email))
    if existing_user is not None:
        member = session.scalar(
            select(Membership).where(
                Membership.tenant_id == tenant_id, Membership.user_id == existing_user.id
            )
        )
        if member is not None and member.status == "active":
            raise PlatformError("Esa persona ya es parte del equipo.", 409)
    # Una invitación vigente por correo: se reemplaza por la nueva.
    session.execute(
        update(Invitation)
        .where(
            Invitation.tenant_id == tenant_id,
            Invitation.email == email,
            Invitation.accepted_at.is_(None),
            Invitation.revoked_at.is_(None),
        )
        .values(revoked_at=now())
    )
    token = new_token()
    invitation = Invitation(
        tenant_id=tenant_id,
        email=email,
        role=role,
        token_hash=token_hash(token),
        invited_by=inviter_id,
        expires_at=now() + timedelta(days=get_settings().invitation_days),
    )
    session.add(invitation)
    session.flush()
    audit(
        session, tenant_id=tenant_id, actor_id=inviter_id, action="member.invited",
        entity_type="invitation", entity_id=invitation.id, ip=ip, email=email, role=role,
    )  # fmt: skip
    return invitation, token


def find_invitation(session: Session, token: str) -> tuple[Invitation, Tenant]:
    session.execute(
        text("SELECT set_config('app.invitation_token', :h, true)"), {"h": token_hash(token)}
    )
    row = session.execute(
        select(Invitation, Tenant)
        .join(Tenant, Tenant.id == Invitation.tenant_id)
        .where(Invitation.token_hash == token_hash(token))
    ).first()
    if row is None:
        raise PlatformError("La invitación no existe.", 404)
    invitation, tenant = row._tuple()
    if invitation.revoked_at or invitation.accepted_at:
        raise PlatformError("Esta invitación ya se usó o fue anulada.", 410)
    if invitation.expires_at <= now():
        raise PlatformError("La invitación venció. Pide una nueva al administrador.", 410)
    return invitation, tenant


def accept_invitation(
    session: Session,
    *,
    token: str,
    user: User | None = None,
    full_name: str = "",
    password: str | None = None,
    ip: str | None = None,
) -> User:
    """Acepta con la cuenta actual (si el correo coincide) o crea la cuenta del invitado."""
    invitation, tenant = find_invitation(session, token)
    if user is None:
        user = session.scalar(select(User).where(User.email == invitation.email))
        if user is not None:
            raise PlatformError("Ya tienes cuenta: inicia sesión para aceptar la invitación.", 409)
        user = create_user(
            session, email=invitation.email, full_name=full_name, password=password or ""
        )
    elif user.email != invitation.email:
        raise PlatformError(
            f"La invitación es para {invitation.email}. Inicia sesión con ese correo.", 403
        )
    set_context(session, tenant_id=tenant.id, user_id=user.id)
    member = session.scalar(
        select(Membership).where(Membership.tenant_id == tenant.id, Membership.user_id == user.id)
    )
    if member is None:
        session.add(Membership(tenant_id=tenant.id, user_id=user.id, role=invitation.role))
    else:
        member.role, member.status = invitation.role, "active"
    invitation.accepted_at = now()
    user.last_tenant_id = tenant.id
    audit(
        session, tenant_id=tenant.id, actor_id=user.id, action="invitation.accepted",
        entity_type="invitation", entity_id=invitation.id, ip=ip,
    )  # fmt: skip
    session.flush()
    return user


def revoke_invitation(
    session: Session, *, tenant_id: uuid.UUID, actor_id: uuid.UUID, invitation_id: uuid.UUID
) -> None:
    invitation = session.get(Invitation, invitation_id)
    if invitation is None or invitation.tenant_id != tenant_id:
        raise PlatformError("Invitación no encontrada.", 404)
    if invitation.accepted_at is None and invitation.revoked_at is None:
        invitation.revoked_at = now()
        audit(
            session, tenant_id=tenant_id, actor_id=actor_id, action="invitation.revoked",
            entity_type="invitation", entity_id=invitation.id,
        )  # fmt: skip


def _active_admins(session: Session, tenant_id: uuid.UUID) -> int:
    return int(
        session.scalar(
            select(func.count())
            .select_from(Membership)
            .where(
                Membership.tenant_id == tenant_id,
                Membership.role == "admin",
                Membership.status == "active",
            )
        )
        or 0
    )


def update_member(
    session: Session,
    *,
    tenant_id: uuid.UUID,
    actor_id: uuid.UUID,
    membership_id: uuid.UUID,
    role: str | None = None,
    active: bool | None = None,
) -> Membership:
    member = session.get(Membership, membership_id)
    if member is None or member.tenant_id != tenant_id:
        raise PlatformError("Miembro no encontrado.", 404)
    was_admin = member.role == "admin" and member.status == "active"
    if role is not None:
        member.role = _check_role(role)
    if active is not None:
        member.status = "active" if active else "disabled"
    session.flush()
    if was_admin and _active_admins(session, tenant_id) == 0:
        raise PlatformError("El espacio de trabajo necesita al menos un administrador activo.")
    audit(
        session, tenant_id=tenant_id, actor_id=actor_id, action="member.updated",
        entity_type="membership", entity_id=member.id, role=member.role, status=member.status,
    )  # fmt: skip
    if member.status == "disabled":
        # Cierra sus sesiones en este espacio de trabajo.
        session.execute(
            update(AuthSession)
            .where(
                AuthSession.user_id == member.user_id,
                AuthSession.tenant_id == tenant_id,
                AuthSession.revoked_at.is_(None),
            )
            .values(revoked_at=now())
        )
    return member


# --- Sitio público -------------------------------------------------------------------------


def create_demo_request(
    session: Session,
    *,
    name: str,
    email: str,
    company: str,
    job_title: str,
    country: str,
    message: str,
    consent: bool,
    ip: str | None,
) -> DemoRequest:
    if not consent:
        raise PlatformError("Necesitamos tu autorización para contactarte.")
    if len(name.strip()) < 2 or len(company.strip()) < 2:
        raise PlatformError("Escribe tu nombre y el de tu empresa.")
    request = DemoRequest(
        name=name.strip()[:160],
        email=normalize_email(email),
        company=company.strip()[:160],
        job_title=job_title.strip()[:160],
        country=country.strip()[:80],
        message=message.strip()[:2000],
        consent=True,
        ip=ip,
    )
    session.add(request)
    session.flush()
    return request
