"""Reasignar cuentas: pasar contactos u oportunidades a otra persona del equipo.

Quién puede: quien es dueño de la cuenta, la administración, y la jefatura sobre las personas
que tiene a cargo. La cuenta se mueve con sus oportunidades abiertas y tareas pendientes de la
persona anterior, queda una nota en su línea de tiempo, se avisa a quien la recibe y se registra
en la bitácora.

El cambio de dueño se escribe sin el filtro por persona (la base no deja a nadie escribir una
fila a nombre de otra): por eso los permisos se comprueban aquí, antes, fila por fila.
"""

from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy import select, update
from sqlalchemy.orm import Session

from orkesta.core.db import unscoped
from orkesta.crm.models import Activity, Deal
from orkesta.data.models import Contact
from orkesta.platform.models import Membership, TeamAssignment, User

KINDS = ("contact", "deal")
MAX_ITEMS = 500


class ReassignError(ValueError):
    def __init__(self, message: str, status: int = 400):
        super().__init__(message)
        self.status = status


def _managed(session: Session, tenant_id: uuid.UUID, me: uuid.UUID, role: str | None) -> set[Any]:
    """Personas cuyas cuentas puede reasignar quien actúa (además de las propias)."""
    if role == "admin":
        return {"*"}
    if role == "supervisor":
        return set(session.scalars(select(TeamAssignment.member_id).where(
            TeamAssignment.tenant_id == tenant_id, TeamAssignment.manager_id == me)))  # fmt: skip
    return set()


def candidates(session: Session, tenant_id: uuid.UUID) -> list[dict[str, Any]]:
    """Personas activas de la empresa a quienes se puede pasar una cuenta."""
    rows = session.execute(
        select(User.id, User.full_name, User.email, User.job_title, Membership.role)
        .join(Membership, Membership.user_id == User.id)
        .where(Membership.tenant_id == tenant_id, Membership.status == "active")
        .order_by(User.full_name, User.email)
    ).all()
    return [
        {"user_id": str(r.id), "name": r.full_name or r.email, "email": r.email,
         "job_title": r.job_title or "", "role": r.role}
        for r in rows
    ]  # fmt: skip


def reassign(
    session: Session, *, tenant_id: uuid.UUID, actor: User, role: str | None, kind: str,
    ids: list[uuid.UUID], to_user_id: uuid.UUID, reason: str = "", with_related: bool = True,
) -> dict[str, Any]:  # fmt: skip
    from orkesta.notifications.service import notify
    from orkesta.platform.service import audit

    if kind not in KINDS:
        raise ReassignError("Tipo no válido.")
    ids = list(dict.fromkeys(ids))
    if not ids:
        raise ReassignError("Elige al menos una cuenta.")
    if len(ids) > MAX_ITEMS:
        raise ReassignError(f"Puedes reasignar hasta {MAX_ITEMS} a la vez.")
    target = session.execute(
        select(User).join(Membership, Membership.user_id == User.id).where(
            Membership.tenant_id == tenant_id, Membership.status == "active", User.id == to_user_id)
    ).scalar_one_or_none()  # fmt: skip
    if target is None:
        raise ReassignError("Esa persona no está activa en tu equipo.")
    reason = " ".join(reason.split())[:300]
    model: Any = Contact if kind == "contact" else Deal
    # Solo lo que quien actúa puede ver (el filtro por persona sigue activo en esta lectura).
    rows = session.execute(
        select(model.id, model.owner_user_id).where(model.tenant_id == tenant_id, model.id.in_(ids))
    ).all()
    if len(rows) != len(ids):
        raise ReassignError("Alguna de esas cuentas no existe o no está a tu alcance.", 404)
    managed = _managed(session, tenant_id, actor.id, role)
    for _, owner in rows:
        if owner not in (None, actor.id) and "*" not in managed and owner not in managed:
            raise ReassignError("Solo puedes reasignar tus cuentas o las de tu equipo.", 403)

    moved = {"contacts": 0, "deals": 0, "tasks": 0}
    previous = {row.id: row.owner_user_id for row in rows}
    with unscoped(session):
        for item_id, before in previous.items():
            if before == target.id:
                continue
            if kind == "contact":
                session.execute(update(Contact).where(Contact.id == item_id)
                                .values(owner_user_id=target.id))  # fmt: skip
                moved["contacts"] += 1
                deals = (
                    session.scalars(select(Deal.id).where(
                        Deal.tenant_id == tenant_id, Deal.contact_id == item_id,
                        Deal.status == "open", Deal.owner_user_id == before)).all()
                    if with_related else []
                )  # fmt: skip
            else:
                deals = [item_id]
            for deal_id in deals:
                session.execute(update(Deal).where(Deal.id == deal_id)
                                .values(owner_user_id=target.id))  # fmt: skip
                moved["deals"] += 1
            if with_related and before is not None:
                scope = (Activity.contact_id == item_id) if kind == "contact" else (
                    Activity.deal_id == item_id)  # fmt: skip
                result = session.execute(
                    update(Activity).where(
                        Activity.tenant_id == tenant_id, scope, Activity.user_id == before,
                        Activity.type == "task", Activity.done_at.is_(None))
                    .values(user_id=target.id)
                )  # fmt: skip
                moved["tasks"] += int(getattr(result, "rowcount", 0) or 0)
            note = f"Reasignada de {_name(session, before)} a {target.full_name or target.email}"
            session.add(Activity(
                tenant_id=tenant_id, type="system", user_id=None,
                contact_id=item_id if kind == "contact" else None,
                deal_id=item_id if kind == "deal" else None,
                body=note + (f". Motivo: {reason}" if reason else "."),
                meta={"reassigned": {"from": str(before) if before else None,
                                     "to": str(target.id), "by": str(actor.id)}},
            ))  # fmt: skip
        session.flush()
    total = moved["contacts"] if kind == "contact" else moved["deals"]
    if total and target.id != actor.id:
        what = "contacto" if kind == "contact" else "oportunidad"
        plural = "s" if total != 1 else ""
        if kind == "deal" and total != 1:
            what, plural = "oportunidades", ""
        notify(
            session, tenant_id=tenant_id, user_id=target.id, kind="reassigned",
            title=f"{actor.full_name or actor.email} te asignó {total} {what}{plural}",
            body=reason, link="/contactos" if kind == "contact" else "/crm",
        )  # fmt: skip
    audit(
        session, tenant_id=tenant_id, actor_id=actor.id, action=f"{kind}.reassigned",
        entity_type=kind, entity_id=ids[0] if len(ids) == 1 else None, to=str(target.id),
        count=total, reason=reason,
    )  # fmt: skip
    return {"moved": moved, "to": {"user_id": str(target.id),
                                   "name": target.full_name or target.email}}  # fmt: skip


def _name(session: Session, user_id: uuid.UUID | None) -> str:
    if user_id is None:
        return "sin responsable"
    user = session.get(User, user_id)
    return (user.full_name or user.email) if user else "una persona que ya no está"
