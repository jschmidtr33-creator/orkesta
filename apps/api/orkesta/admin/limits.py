"""Límites del plan de cada empresa (los define la creadora en el panel de administración).

Un límite en 0 significa «sin límite». El espacio de trabajo del equipo de ORKESTA no tiene
límites.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from zoneinfo import ZoneInfo

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from orkesta.platform.models import Invitation, Membership, Tenant, User

PLAN_LABELS = {"inicial": "Inicial", "pro": "Pro", "empresa": "Empresa", "cortesia": "Cortesía"}


class LimitError(ValueError):
    status = 402

    def __init__(self, message: str):
        super().__init__(message)


def _staff_tenant(session: Session, tenant_id: uuid.UUID) -> bool:
    return bool(
        session.scalar(
            select(func.count())
            .select_from(Membership)
            .join(User, User.id == Membership.user_id)
            .where(Membership.tenant_id == tenant_id, User.platform_role.is_not(None))
        )
    )


def plan_limits(session: Session, tenant_id: uuid.UUID) -> tuple[str, dict[str, int]] | None:
    """(plan, límites) de la empresa, o ``None`` si no tiene límites."""
    from orkesta.admin.service import app_settings

    tenant = session.get(Tenant, tenant_id)
    if tenant is None or _staff_tenant(session, tenant_id):
        return None
    limits = app_settings(session)["limits"].get(tenant.plan) or {}
    return tenant.plan, {k: int(v or 0) for k, v in limits.items()}


def _upgrade(plan: str) -> str:
    return f"Tu plan {PLAN_LABELS.get(plan, plan)} llegó a su límite"


def contact_capacity(session: Session, tenant_id: uuid.UUID) -> int | None:
    """Cuántos contactos nuevos caben todavía (``None`` = sin límite)."""
    from orkesta.data.models import Contact

    found = plan_limits(session, tenant_id)
    if found is None or not found[1].get("contacts"):
        return None
    used = session.scalar(
        select(func.count()).select_from(Contact).where(Contact.tenant_id == tenant_id)
    )
    return max(0, found[1]["contacts"] - int(used or 0))


def check_contacts(session: Session, tenant_id: uuid.UUID, adding: int = 1) -> None:
    room = contact_capacity(session, tenant_id)
    if room is not None and room < adding:
        found = plan_limits(session, tenant_id)
        plan, limits = found if found else ("", {})
        raise LimitError(
            f"{_upgrade(plan)} de {limits.get('contacts', 0):,} contactos.".replace(",", ".")
            + " Pide a ORKESTA ampliar tu plan."
        )


def emails_left_today(session: Session, tenant_id: uuid.UUID, tz: ZoneInfo) -> int | None:
    """Correos de campaña que la empresa aún puede enviar hoy (``None`` = sin límite)."""
    from orkesta.campaigns.models import Message

    found = plan_limits(session, tenant_id)
    if found is None or not found[1].get("emails_per_day"):
        return None
    start = datetime.now(tz).replace(hour=0, minute=0, second=0, microsecond=0)
    sent = session.scalar(
        select(func.count())
        .select_from(Message)
        .where(Message.tenant_id == tenant_id, Message.sent_at >= start.astimezone(UTC))
    )
    return max(0, found[1]["emails_per_day"] - int(sent or 0))


def check_users(session: Session, tenant_id: uuid.UUID, *, pending_email: str = "") -> None:
    """Personas activas + invitaciones vigentes no pueden superar el límite del plan."""
    found = plan_limits(session, tenant_id)
    if found is None or not found[1].get("users"):
        return
    plan, limits = found
    members = session.scalar(
        select(func.count())
        .select_from(Membership)
        .where(Membership.tenant_id == tenant_id, Membership.status == "active")
    )
    invites = session.scalar(
        select(func.count())
        .select_from(Invitation)
        .where(
            Invitation.tenant_id == tenant_id,
            Invitation.accepted_at.is_(None),
            Invitation.revoked_at.is_(None),
            Invitation.expires_at > datetime.now(UTC),
            Invitation.email != pending_email,
        )
    )
    if int(members or 0) + int(invites or 0) >= limits["users"]:
        raise LimitError(
            f"{_upgrade(plan)} de {limits['users']} personas en el equipo."
            " Pide a ORKESTA ampliar tu plan."
        )
