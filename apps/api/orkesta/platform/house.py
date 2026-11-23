"""Solicitudes de la landing → el ORKESTA de ORKESTA.

Cada solicitud del formulario de contacto:

1. queda como contacto y oportunidad en la empresa de ORKESTA dentro de la app (la que se llama
   ORKESTA y tiene como administradora a la dueña de la plataforma), a nombre de esa persona;
2. le avisa dentro de la app y por correo (``DEMO_NOTIFY_EMAIL``, por defecto
   jenn@orkestaapp.com) desde el buzón del sistema.

Nada de esto puede hacer fallar el formulario: si algo no está configurado, solo se registra.
"""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass
from html import escape
from typing import Any

from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from orkesta.core.db import set_context

logger = logging.getLogger(__name__)


@dataclass
class House:
    tenant_id: uuid.UUID
    user_id: uuid.UUID


def find(session: Session) -> House | None:
    """La empresa ORKESTA y su administradora (la dueña de la plataforma)."""
    from orkesta.platform.models import Membership, Tenant, User

    admins = session.scalars(
        select(User.id).where(User.is_platform_admin.is_(True), User.is_active.is_(True))
        .order_by(User.created_at)
    ).all()  # fmt: skip
    for user_id in admins:
        # Las membresías se ven por persona (RLS): se mira cada administradora de la plataforma.
        set_context(session, user_id=user_id)
        tenant_id = session.scalar(
            select(Tenant.id)
            .join(Membership, Membership.tenant_id == Tenant.id)
            .where(
                Membership.user_id == user_id, Membership.role == "admin",
                Membership.status == "active",
                or_(func.lower(Tenant.slug).like("orkesta%"), func.lower(Tenant.name) == "orkesta"),
            )
            .order_by(Tenant.created_at)
            .limit(1)
        )  # fmt: skip
        if tenant_id is not None:
            return House(tenant_id, user_id)
    return None


TOPICS = {
    "demo": "Demo",
    "pregunta": "Consulta",
    "precios": "Cotización",
    "alianza": "Alianza",
}


def receive(session: Session, request: Any, topic: str = "demo", base_url: str = "") -> None:
    """Lleva la solicitud al CRM de ORKESTA y avisa. Nunca lanza errores."""
    label = TOPICS.get(topic, "Demo")
    deal_link = ""
    try:
        with session.begin_nested():
            deal_link = _to_crm(session, request, label, base_url)
    except Exception:
        logger.exception("house_crm_failed")
    try:
        _email(session, request, label, deal_link)
    except Exception:
        logger.exception("house_email_failed")


def _to_crm(session: Session, req: Any, label: str, base_url: str) -> str:
    from orkesta.crm import service as crm
    from orkesta.data import service as data
    from orkesta.data.models import Contact
    from orkesta.notifications.service import notify
    from orkesta.platform.models import User

    house = find(session)
    if house is None:
        logger.warning("house_tenant_missing")
        return ""
    set_context(session, tenant_id=house.tenant_id, user_id=house.user_id)
    user = session.get(User, house.user_id)
    first, _, last = req.name.strip().partition(" ")
    contact = session.scalar(
        select(Contact).where(
            Contact.tenant_id == house.tenant_id,
            Contact.owner_user_id == house.user_id,
            Contact.email == req.email,
        )
    )
    if contact is None:
        try:
            contact = data.create_contact(
                session, tenant_id=house.tenant_id, user=user,
                data=data.ContactInput(first_name=first, last_name=last, job_title=req.job_title,
                                       company=req.company, email=req.email, country=req.country),
            )  # fmt: skip
        except data.DataError:
            contact = None
    deal = crm.create_deal(
        session, tenant_id=house.tenant_id, user_id=house.user_id,
        data=crm.DealInput(title=f"{label} · {req.company}"[:200],
                           contact_id=contact.id if contact else None),
    )  # fmt: skip
    lines = [f"{label}: solicitud desde el formulario de orkestaapp.com."]
    if req.job_title:
        lines.append(f"Cargo: {req.job_title}")
    if req.country:
        lines.append(f"País: {req.country}")
    if req.message:
        lines.append(f"Qué quiere resolver: {req.message}")
    crm.add_activity(
        session, tenant_id=house.tenant_id, user_id=house.user_id, kind="note",
        body="\n".join(lines), contact_id=deal.contact_id, deal_id=deal.id, due_at=None,
    )  # fmt: skip
    notify(
        session, tenant_id=house.tenant_id, user_id=house.user_id, kind="demo_request",
        title=f"Nueva solicitud ({label.lower()}): {req.company}",
        body=f"{req.name} · {req.job_title or 'sin cargo'}", link=f"/crm?oportunidad={deal.id}",
    )  # fmt: skip
    return f"{base_url.rstrip('/')}/app/crm?oportunidad={deal.id}" if base_url else ""


def _email(session: Session, req: Any, label: str, deal_link: str) -> None:
    from orkesta.core import system_mail
    from orkesta.core.config import get_settings

    to = get_settings().demo_notify_email.strip()
    if not to:
        return
    rows = [("Motivo", label), ("Nombre", req.name), ("Correo", req.email),
            ("Empresa", req.company), ("Cargo", req.job_title), ("País", req.country),
            ("Mensaje", req.message)]  # fmt: skip
    text = "Nueva solicitud desde orkestaapp.com:\n\n" + "\n".join(
        f"{k}: {v}" for k, v in rows if v
    )
    if deal_link:
        text += f"\n\nOportunidad en ORKESTA: {deal_link}"
    html = (
        "<p>Nueva solicitud desde orkestaapp.com:</p><table>"
        + "".join(
            f"<tr><td style='padding:2px 12px 2px 0;color:#6B7280'>{k}</td>"
            f"<td>{escape(str(v)).replace(chr(10), '<br>')}</td></tr>"
            for k, v in rows
            if v
        )
        + "</table>"
    )
    if deal_link:
        html += f'<p><a href="{escape(deal_link)}">Ver la oportunidad en ORKESTA →</a></p>'
    subject = f"Nueva solicitud ({label.lower()}): {req.company}"
    sent = system_mail.send(session, to=to, subject=subject, text=text, html=html)
    if not sent:
        logger.warning("house_email_not_sent")
