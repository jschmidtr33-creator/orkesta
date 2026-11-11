"""Correo del sistema de ORKESTA: recuperación de contraseña y avisos de la plataforma.

Sale del buzón que la dueña de ORKESTA elige en el panel (por ejemplo, el de hola@orkestaapp.com
ya conectado a ORKESTA), con sus mismos tokens de Gmail, Microsoft 365 o SMTP. Así no hacen falta
claves nuevas ni otro proveedor de correo.
"""

from __future__ import annotations

import logging
import uuid
from email.message import EmailMessage
from email.utils import formataddr, make_msgid
from typing import Any

from sqlalchemy.orm import Session

from orkesta.core.db import set_context

logger = logging.getLogger(__name__)
KEY = "system_mail"


def config(session: Session) -> dict[str, Any] | None:
    from orkesta.admin import service as admin

    data = admin.app_settings(session).get(KEY)
    return data if isinstance(data, dict) and data.get("mailbox_id") else None


def _mailbox(session: Session, data: dict[str, Any]) -> Any:
    from orkesta.campaigns.models import Mailbox
    from orkesta.core.db import unscoped

    set_context(session, tenant_id=uuid.UUID(data["tenant_id"]), user_id=uuid.UUID(data["user_id"]))
    with unscoped(session):
        mailbox = session.get(Mailbox, uuid.UUID(data["mailbox_id"]))
    return mailbox if mailbox is not None and mailbox.status == "active" else None


def send(session: Session, *, to: str, subject: str, text: str, html: str | None = None) -> bool:
    """Envía un correo del sistema. ``False`` si no hay buzón del sistema o el envío falla."""
    from orkesta.campaigns.service import deliver

    data = config(session)
    if data is None:
        return False
    mailbox = _mailbox(session, data)
    if mailbox is None:
        logger.warning("system_mail_mailbox_unavailable")
        return False
    msg = EmailMessage()
    msg["From"] = formataddr(("ORKESTA", mailbox.email))
    msg["To"] = to
    msg["Subject"] = subject
    rfc_id = make_msgid(domain=mailbox.email.rsplit("@", 1)[-1])
    msg["Message-ID"] = rfc_id
    msg.set_content(text)
    if html:
        msg.add_alternative(html, subtype="html")
    try:
        deliver(session, mailbox, msg.as_bytes(), None, to, rfc_id)
    except Exception:
        logger.exception("system_mail_failed")
        return False
    return True
