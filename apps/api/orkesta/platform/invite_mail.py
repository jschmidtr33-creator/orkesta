"""Correo de invitación al equipo: sale del buzón conectado de quien invita.

Así llega con su nombre y desde un correo que la persona invitada reconoce (menos spam que un
remitente genérico). Si quien invita no tiene buzón conectado, no se envía nada y la app muestra
el enlace para compartirlo a mano.
"""

from __future__ import annotations

import html
import uuid
from email.message import EmailMessage
from email.utils import formataddr, make_msgid

from sqlalchemy import select
from sqlalchemy.orm import Session

from orkesta.core.config import get_settings
from orkesta.core.db import set_context
from orkesta.platform.models import ROLE_HELP, ROLE_LABELS, Invitation, Tenant, User

NO_MAILBOX = (
    "No se envió el correo: conecta tu buzón en Configuración → Integraciones. "
    "Mientras tanto, comparte el enlace."
)


def render(
    inviter: User, tenant: Tenant, invitation: Invitation, link: str
) -> tuple[str, str, str]:
    who = inviter.full_name or inviter.email
    role = ROLE_LABELS.get(invitation.role, invitation.role)
    days = get_settings().invitation_days
    subject = f"{who} te invitó a {tenant.name} en ORKESTA"
    text = (
        f"Hola,\n\n{who} te invitó a unirte a {tenant.name} en ORKESTA como {role}.\n"
        f"{ROLE_HELP.get(invitation.role, '')}\n\n"
        f"Acepta la invitación aquí: {link}\n\n"
        f"El enlace es personal y vence en {days} días.\n"
        "Si ya usas ORKESTA con tu propio espacio, todo tu trabajo pasa a la empresa al aceptar.\n"
    )
    e = html.escape
    body = f"""<!doctype html><html lang="es"><body style="margin:0;background:#F4F6FB;
font-family:Arial,Helvetica,sans-serif;color:#0B1020">
<table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="padding:32px 12px">
<tr><td align="center">
<table role="presentation" width="100%" cellpadding="0" cellspacing="0"
 style="max-width:520px;background:#ffffff;border-radius:16px;padding:32px">
<tr><td>
<p style="margin:0 0 20px;letter-spacing:.3em;font-size:12px;color:#4F46E5">ORKESTA</p>
<h1 style="margin:0 0 12px;font-size:22px">Únete a {e(tenant.name)}</h1>
<p style="margin:0 0 8px;font-size:15px;line-height:1.5;color:#374151">
<strong>{e(who)}</strong> te invitó a trabajar en ORKESTA como <strong>{e(role)}</strong>.</p>
<p style="margin:0 0 24px;font-size:14px;line-height:1.5;color:#6B7280">
{e(ROLE_HELP.get(invitation.role, ""))}</p>
<a href="{e(link)}" style="display:inline-block;background:#2563EB;color:#ffffff;
text-decoration:none;font-weight:bold;font-size:15px;padding:12px 22px;border-radius:10px">
Aceptar invitación</a>
<p style="margin:24px 0 0;font-size:12.5px;line-height:1.5;color:#9CA3AF">
El enlace es personal y vence en {days} días. Si ya usas ORKESTA con tu propio espacio, todo tu
trabajo pasa a {e(tenant.name)} al aceptar.<br>¿No esperabas este correo? Puedes ignorarlo.</p>
</td></tr></table></td></tr></table></body></html>"""
    return subject, body, text


def send(
    session: Session, *, tenant_id: uuid.UUID, inviter: User, invitation: Invitation, link: str
) -> str | None:
    """Envía la invitación. Devuelve ``None`` si salió, o el motivo por el que no se envió."""
    from orkesta.campaigns.models import Mailbox
    from orkesta.campaigns.service import deliver

    set_context(session, tenant_id=tenant_id, user_id=inviter.id)
    mailbox = session.scalar(
        select(Mailbox)
        .where(Mailbox.tenant_id == tenant_id, Mailbox.user_id == inviter.id,
               Mailbox.status == "active")
        .order_by(Mailbox.created_at).limit(1)
    )  # fmt: skip
    tenant = session.get(Tenant, tenant_id)
    if mailbox is None or tenant is None:
        return NO_MAILBOX
    subject, body, text = render(inviter, tenant, invitation, link)
    msg = EmailMessage()
    msg["From"] = formataddr((inviter.full_name or "ORKESTA", mailbox.email))
    msg["To"] = invitation.email
    msg["Subject"] = subject
    rfc_id = make_msgid(domain=mailbox.email.rsplit("@", 1)[-1])
    msg["Message-ID"] = rfc_id
    msg.set_content(text)
    msg.add_alternative(body, subtype="html")
    try:
        deliver(session, mailbox, msg.as_bytes(), None, invitation.email, rfc_id)
    except Exception as exc:  # el buzón puede estar vencido: la invitación igual queda creada
        reason = str(exc)[:140]
        return f"No se pudo enviar desde {mailbox.email} ({reason}). Comparte el enlace."
    return None
