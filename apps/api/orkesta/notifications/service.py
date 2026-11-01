"""Notificaciones: crear, listar y recordatorios de tareas vencidas (campana + correo)."""

from __future__ import annotations

import html
import logging
import uuid
from datetime import UTC, datetime, timedelta
from email.message import EmailMessage
from email.utils import formataddr
from typing import Any
from zoneinfo import ZoneInfo

from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from orkesta.core.config import get_settings
from orkesta.core.db import set_context
from orkesta.platform.models import Tenant, User

logger = logging.getLogger("orkesta.notifications")
LOOKBACK = timedelta(days=7)  # tareas vencidas hace más de una semana no generan aviso
BATCH = 50
TZ = ZoneInfo("America/Bogota")  # hora de los equipos en LATAM para el texto del aviso


def now() -> datetime:
    return datetime.now(UTC)


def notify(
    session: Session,
    *,
    tenant_id: uuid.UUID,
    user_id: uuid.UUID | None,
    kind: str,
    title: str,
    body: str = "",
    link: str = "",
) -> Any:
    from orkesta.notifications.models import Notification

    if user_id is None:
        return None
    row = Notification(
        tenant_id=tenant_id,
        user_id=user_id,
        kind=kind,
        title=title[:200],
        body=body[:2000],
        link=link[:300],
    )
    session.add(row)
    session.flush()
    return row


def notification_dict(n: Any) -> dict[str, Any]:
    return {
        "id": str(n.id),
        "kind": n.kind,
        "title": n.title,
        "body": n.body,
        "link": n.link,
        "read": n.read_at is not None,
        "created_at": n.created_at.isoformat(),
    }


def _send_email(
    session: Session, tenant_id: uuid.UUID, user: User, subject: str, text: str, link: str
) -> bool:
    """Correo al propio usuario desde su Gmail conectado (o el de su empresa)."""
    from orkesta.campaigns import gmail
    from orkesta.campaigns.models import Mailbox
    from orkesta.campaigns.service import access_token

    mailbox = session.scalar(
        select(Mailbox).where(
            Mailbox.tenant_id == tenant_id, Mailbox.status == "active", Mailbox.user_id == user.id
        )
    ) or session.scalar(
        select(Mailbox).where(Mailbox.tenant_id == tenant_id, Mailbox.status == "active")
    )
    if mailbox is None:
        return False
    base = get_settings().public_base_url.rstrip("/")
    url = f"{base}{link}" if base and link.startswith("/") else link
    message = EmailMessage()
    message["From"] = formataddr(("ORKESTA", mailbox.email))
    message["To"] = user.email
    message["Subject"] = subject
    message.set_content(f"{text}\n\n{url}\n\n— Recordatorio automático de ORKESTA")
    message.add_alternative(
        '<div style="font-family:Arial,Helvetica,sans-serif;font-size:14px;color:#1f2937">'
        f"<p>{html.escape(text).replace(chr(10), '<br>')}</p>"
        + (
            f'<p style="margin:20px 0"><a href="{html.escape(url)}" style="background:#4F46E5;'
            "color:#fff;text-decoration:none;padding:10px 18px;border-radius:10px;"
            'font-weight:bold;display:inline-block">Abrir en ORKESTA →</a></p>'
            if url
            else ""
        )
        + '<p style="font-size:12px;color:#94a3b8">Recordatorio automático de ORKESTA.</p></div>',
        subtype="html",
    )
    try:
        gmail.send(access_token(session, mailbox), message.as_bytes())
    except gmail.GmailError as exc:
        logger.warning("reminder_email_failed %s %s", user.id, exc)
        return False
    return True


def due_task_reminders(factory: sessionmaker[Session]) -> int:
    """Avisa (campana + correo) por cada tarea que acaba de vencer. Corre cada ~30 s."""
    from orkesta.crm.models import Activity
    from orkesta.data.models import Contact

    sent = 0
    with factory() as session:
        tenants = list(session.scalars(select(Tenant.id)))
    for tenant_id in tenants:
        with factory.begin() as session:
            set_context(session, tenant_id=tenant_id)
            tasks = session.scalars(
                select(Activity)
                .where(
                    Activity.tenant_id == tenant_id,
                    Activity.type == "task",
                    Activity.done_at.is_(None),
                    Activity.reminded_at.is_(None),
                    Activity.due_at <= now(),
                    Activity.due_at > now() - LOOKBACK,
                )
                .order_by(Activity.due_at)
                .limit(BATCH)
                .with_for_update(skip_locked=True)
            ).all()
            for task in tasks:
                task.reminded_at = now()
                user = session.get(User, task.user_id) if task.user_id else None
                if user is None:
                    continue
                contact = session.get(Contact, task.contact_id) if task.contact_id else None
                who = f" · {contact.full_name}" if contact else ""
                link = "/app/crm?vista=tareas"
                due = task.due_at.astimezone(TZ).strftime("%d/%m %H:%M") if task.due_at else ""
                n = notify(
                    session,
                    tenant_id=tenant_id,
                    user_id=user.id,
                    kind="task_due",
                    title=f"Tarea vencida: {task.body[:150]}",
                    body=f"Vencía {due}{who}",
                    link=link,
                )
                text = (
                    f"Hola {user.full_name or ''}:\n\nTu tarea «{task.body}» venció"
                    f"{' (' + contact.full_name + ')' if contact else ''}."
                )
                if _send_email(
                    session, tenant_id, user, f"Recordatorio: {task.body[:120]}", text, link
                ):
                    n.emailed_at = now()
                sent += 1
    return sent
