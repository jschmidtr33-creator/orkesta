"""Resumen diario por correo (motor propio): tareas de hoy y vencidas, novedades de las últimas
24 horas, oportunidades frías y pronóstico del mes. Se envía desde el buzón que la persona
conectó a ORKESTA hacia su propio correo (sin servicios de correo de terceros)."""

from __future__ import annotations

import html
import logging
import uuid
from datetime import UTC, datetime, timedelta
from email.message import EmailMessage
from email.utils import formataddr, make_msgid
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from orkesta.platform.models import Membership, User

logger = logging.getLogger(__name__)
SEND_FROM_HOUR, SEND_UNTIL_HOUR = 7, 10


class DigestError(Exception):
    pass


def _tz(user: User) -> ZoneInfo:
    try:
        return ZoneInfo(user.timezone or "America/Bogota")
    except ZoneInfoNotFoundError:
        return ZoneInfo("America/Bogota")


def build(session: Session, tenant_id: uuid.UUID, user: User) -> dict[str, Any]:
    from orkesta.crm.intelligence import forecast, health
    from orkesta.crm.models import Activity, Deal
    from orkesta.notifications.models import Notification

    tz = _tz(user)
    local_now = datetime.now(tz)
    end_today = local_now.replace(hour=23, minute=59, second=59).astimezone(UTC)
    tasks = session.scalars(
        select(Activity).where(Activity.tenant_id == tenant_id, Activity.type == "task",
                               Activity.user_id == user.id, Activity.done_at.is_(None),
                               Activity.due_at <= end_today)
        .order_by(Activity.due_at).limit(15)
    ).all()  # fmt: skip
    now = datetime.now(UTC)
    news = session.scalars(
        select(Notification).where(Notification.tenant_id == tenant_id,
                                   Notification.user_id == user.id,
                                   Notification.created_at >= now - timedelta(hours=24))
        .order_by(Notification.created_at.desc()).limit(10)
    ).all()  # fmt: skip
    mine = list(session.scalars(select(Deal).where(Deal.tenant_id == tenant_id,
                                                   Deal.owner_user_id == user.id,
                                                   Deal.status == "open")))  # fmt: skip
    scores = health(session, tenant_id, mine)
    cold = sorted((d for d in mine if scores.get(d.id, {}).get("label") == "fria"),
                  key=lambda d: scores[d.id]["score"])[:5]  # fmt: skip
    month = local_now.strftime("%Y-%m")
    fc = forecast(session, tenant_id)
    this_month: dict[str, Any] = next((p["totals"] for p in fc["periods"] if p["key"] == month), {})
    return {
        "date": local_now,
        "overdue": [t for t in tasks if t.due_at and t.due_at < now],
        "today": [t for t in tasks if t.due_at and t.due_at >= now],
        "news": news,
        "cold": [(d, scores[d.id]) for d in cold],
        "forecast": {c: v["weighted"] for c, v in this_month.items()},
    }


def _money(values: dict[str, float]) -> str:
    return " · ".join(f"{v:,.0f} {c}".replace(",", ".") for c, v in values.items()) or "—"


def render(data: dict[str, Any], user: User, base_url: str) -> tuple[str, str, str]:
    first = (user.full_name or user.email).split()[0]
    n_tasks = len(data["overdue"]) + len(data["today"])
    subject = f"Tu día en ORKESTA · {n_tasks} tarea(s) · {len(data['news'])} novedad(es)"
    link = (base_url.rstrip("/") + "/app/") if base_url else "/app/"

    def items(rows: list[str]) -> str:
        if not rows:
            return '<p style="color:#6B7280;margin:4px 0 0">Nada por aquí.</p>'
        return "<ul style='padding-left:18px;margin:6px 0 0'>" + "".join(
            f"<li style='margin:4px 0'>{r}</li>" for r in rows) + "</ul>"  # fmt: skip

    def esc(text: str) -> str:
        return html.escape(text or "")

    sections = [
        ("Tareas vencidas", [esc(t.body) for t in data["overdue"]]),
        ("Para hoy", [esc(t.body) for t in data["today"]]),
        (
            "Novedades de las últimas 24 horas",
            [
                f"<strong>{esc(n.title)}</strong>" + (f" — {esc(n.body[:120])}" if n.body else "")
                for n in data["news"]
            ],
        ),
        (
            "Oportunidades que se están enfriando",
            [f"{esc(d.title)} — {esc(', '.join(h['reasons'][:2]))}" for d, h in data["cold"]],
        ),
    ]
    body = "".join(
        f"<h3 style='font-size:15px;margin:22px 0 0;color:#0B1220'>{title}</h3>{items(rows)}"
        for title, rows in sections
    )
    html_doc = f"""<div style="font-family:Arial,Helvetica,sans-serif;max-width:560px;margin:auto;color:#1F2937;font-size:14px;line-height:1.5">
<div style="height:6px;border-radius:6px;background:linear-gradient(90deg,#2563EB,#8B5CF6)"></div>
<h2 style="font-size:20px;margin:18px 0 4px;color:#0B1220">Buenos días, {esc(first)}</h2>
<p style="margin:0;color:#6B7280">Pronóstico ponderado de este mes: <strong style="color:#0B1220">{_money(data["forecast"])}</strong></p>
{body}
<p style="margin:26px 0"><a href="{link}" style="background:#2563EB;color:#fff;padding:10px 18px;border-radius:10px;text-decoration:none;font-weight:bold">Abrir ORKESTA</a></p>
<p style="font-size:12px;color:#9CA3AF">Recibes este resumen porque lo tienes activo en Configuración → Mi perfil.</p></div>"""  # noqa: E501
    text_lines = [f"Buenos días, {first}.", f"Pronóstico del mes: {_money(data['forecast'])}", ""]
    for title, rows in sections:
        text_lines.append(title + ":")
        text_lines += [f"- {html.unescape(r.replace('<strong>', '').replace('</strong>', ''))}"
                       for r in rows] or ["- Nada por aquí."]  # fmt: skip
        text_lines.append("")
    text_lines.append(link)
    return subject, html_doc, "\n".join(text_lines)


def send(session: Session, tenant_id: uuid.UUID, user: User) -> str:
    """Envía el resumen ahora desde el buzón de la persona. Devuelve el correo de destino."""
    from orkesta.campaigns.gmail import GmailError
    from orkesta.campaigns.models import Mailbox
    from orkesta.campaigns.service import deliver
    from orkesta.core.config import get_settings
    from orkesta.core.db import set_context

    set_context(session, tenant_id=tenant_id, user_id=user.id)
    mailbox = session.scalar(select(Mailbox).where(Mailbox.tenant_id == tenant_id,
                                                   Mailbox.user_id == user.id,
                                                   Mailbox.status == "active")
                             .order_by(Mailbox.created_at).limit(1))  # fmt: skip
    if mailbox is None:
        raise DigestError("Conecta tu buzón (Gmail, Microsoft 365 o SMTP) en Configuración → "
                          "Integraciones para recibir el resumen diario.")  # fmt: skip
    subject, html_doc, text = render(build(session, tenant_id, user), user,
                                     get_settings().public_base_url)  # fmt: skip
    msg = EmailMessage()
    msg["From"] = formataddr(("ORKESTA", mailbox.email))
    msg["To"] = user.email
    msg["Subject"] = subject
    rfc_id = make_msgid(domain=mailbox.email.rsplit("@", 1)[-1])
    msg["Message-ID"] = rfc_id
    msg["Auto-Submitted"] = "auto-generated"
    msg.set_content(text)
    msg.add_alternative(html_doc, subtype="html")
    try:
        deliver(session, mailbox, msg.as_bytes(), None, user.email, rfc_id)
    except GmailError as exc:
        raise DigestError(str(exc)) from exc
    return user.email


def send_due(factory: sessionmaker[Session]) -> int:
    """Lunes a viernes, entre las 7 y las 10 de la mañana de cada persona, una vez al día."""
    sent = 0
    with factory() as session:
        users = session.scalars(select(User).where(User.digest_enabled.is_(True),
                                                   User.is_active.is_(True))).all()  # fmt: skip
        candidates = []
        for user in users:
            local = datetime.now(_tz(user))
            if local.weekday() >= 5 or not SEND_FROM_HOUR <= local.hour < SEND_UNTIL_HOUR:
                continue
            if user.digest_last_sent == local.date():
                continue
            candidates.append((user.id, local.date()))
    for user_id, today in candidates:
        with factory.begin() as session:
            row = session.get(User, user_id)
            if row is None:
                continue
            active = select(Membership.tenant_id).where(
                Membership.user_id == row.id, Membership.status == "active"
            )
            tenant_id = row.last_tenant_id or session.scalar(active.limit(1))
            row.digest_last_sent = today
            if tenant_id is None:
                continue
            try:
                send(session, tenant_id, row)
                sent += 1
            except (DigestError, Exception):
                logger.info("digest_skipped", extra={"user": str(user_id)})
    return sent
