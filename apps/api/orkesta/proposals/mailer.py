"""Envío de una propuesta al cliente desde el Gmail conectado del vendedor."""

from __future__ import annotations

import html
import re
import uuid
from email.message import EmailMessage
from email.utils import formataddr, make_msgid
from typing import Any

from sqlalchemy.orm import Session

from orkesta.campaigns import gmail
from orkesta.campaigns.models import Mailbox
from orkesta.campaigns.templating import as_html, html_to_text, render
from orkesta.crm.service import log as crm_log
from orkesta.platform.models import User
from orkesta.proposals.design import get_brand, resolved
from orkesta.proposals.models import Proposal
from orkesta.proposals.service import ProposalError, mark_sent, variable_values

_EMAIL = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
BUTTON = {"es": "Ver la propuesta", "en": "View the proposal", "pt": "Ver a proposta"}


def _on(hex_color: str) -> str:
    """Texto blanco u oscuro según qué se lea mejor sobre el color del botón."""
    n = int(hex_color.lstrip("#") or "0", 16)
    r, g, b = (n >> 16) & 255, (n >> 8) & 255, n & 255
    return "#0B1220" if (0.299 * r + 0.587 * g + 0.114 * b) > 170 else "#FFFFFF"


def _emails(raw: str | list[str]) -> list[str]:
    items = raw if isinstance(raw, list) else re.split(r"[,;\s]+", raw or "")
    out = []
    for item in items:
        email = item.strip().lower()
        if not email:
            continue
        if not _EMAIL.match(email):
            raise ProposalError(f"El correo «{email}» no es válido.")
        out.append(email)
    return out[:10]


def send_by_email(session: Session, proposal: Proposal, user: User, *, mailbox_id: uuid.UUID,
                  to: str | list[str], cc: str | list[str], subject: str, body: str,
                  link: str) -> dict[str, Any]:  # fmt: skip
    mailbox = session.get(Mailbox, mailbox_id)
    if mailbox is None or mailbox.tenant_id != proposal.tenant_id:
        raise ProposalError("Ese buzón no existe.", 404)
    if mailbox.status != "active":
        raise ProposalError("El buzón necesita reconectarse en Configuración → Integraciones.")
    recipients, copies = _emails(to), _emails(cc)
    if not recipients:
        raise ProposalError("Escribe el correo del cliente.")
    values = {**variable_values(session, proposal), "enlace_propuesta": link}
    subject_text = " ".join(render(subject, values)[0].split())[:250]
    if not subject_text:
        raise ProposalError("Escribe el asunto del correo.")
    body_html = render(as_html(body), values, escape=True)[0]
    look = resolved(get_brand(session, proposal.tenant_id), proposal.design or {})
    label = BUTTON.get(look["language"], BUTTON["es"])
    style = (
        f"background:{look['primary']};color:{_on(look['primary'])};text-decoration:none;"
        "padding:12px 22px;border-radius:10px;font-weight:bold;display:inline-block"
    )
    button = (
        f'<p style="margin:24px 0"><a href="{html.escape(link)}" style="{style}">{label} →</a></p>'
    )
    signature = as_html(mailbox.signature)
    content = (
        '<div style="font-family:Arial,Helvetica,sans-serif;font-size:14px;line-height:1.55;'
        f'color:#1f2937">{body_html}{button if link not in body_html else ""}'
        + (f'<div style="margin-top:16px">{signature}</div>' if signature else "")
        + "</div>"
    )
    owner = session.get(User, mailbox.user_id) if mailbox.user_id else None
    sender = mailbox.display_name or (owner.full_name if owner else "") or mailbox.email
    message = EmailMessage()
    domain = mailbox.email.split("@", 1)[1]
    message["Message-ID"] = make_msgid(domain=domain)
    message["From"] = formataddr((sender, mailbox.email))
    message["To"] = ", ".join(recipients)
    if copies:
        message["Cc"] = ", ".join(copies)
    message["Subject"] = subject_text
    text = html_to_text(content)
    if link not in text:
        text += f"\n\n{label}: {link}"
    message.set_content(text)
    message.add_alternative(content, subtype="html")
    from orkesta.campaigns.service import access_token  # import diferido: evita ciclos

    try:
        sent = gmail.send(access_token(session, mailbox), message.as_bytes())
    except gmail.GmailError as exc:
        if exc.auth:
            mailbox.status, mailbox.error = "error", str(exc)[:300]
        raise ProposalError(str(exc)) from exc
    mark_sent(session, proposal, user)
    crm_log(
        session, tenant_id=proposal.tenant_id, kind="email", user_id=user.id,
        contact_id=proposal.contact_id, deal_id=proposal.deal_id,
        body=f"Envió por correo la propuesta N.º {proposal.number} a {', '.join(recipients)}: "
             f"«{subject_text}»",
        meta={"gmail_thread": str(sent.get("threadId", "")), "mailbox": mailbox.email},
    )  # fmt: skip
    design = dict(proposal.design or {})
    design["last_email"] = {"to": recipients, "from": mailbox.email,
                            "thread_id": str(sent.get("threadId", ""))}  # fmt: skip
    proposal.design = design
    return {"to": recipients, "thread_id": str(sent.get("threadId", "")), "from": mailbox.email}
