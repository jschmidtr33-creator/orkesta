"""Campañas: buzones, audiencia, lanzamiento, motor de envío, respuestas y seguimiento."""

from __future__ import annotations

import logging
import random
import secrets
import threading
import time
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from sqlalchemy import func, select, text, update
from sqlalchemy.orm import Session, sessionmaker

from orkesta.automation.events import emit
from orkesta.campaigns import gmail, templating
from orkesta.campaigns.models import (
    Campaign,
    Mailbox,
    Message,
    Recipient,
    Suppression,
    TrackingToken,
)
from orkesta.core.db import set_context
from orkesta.crm.service import log as crm_log
from orkesta.data.models import Contact, ListMember
from orkesta.data.service import ContactFilters, _filtered
from orkesta.engine.normalize.location import COUNTRIES
from orkesta.notifications.service import notify
from orkesta.platform.models import Tenant, User

logger = logging.getLogger("orkesta.campaigns")
MAX_STEPS = 6
MAX_RECIPIENTS = 5000
CHECK_EVERY = timedelta(minutes=15)
REPLY_WINDOW = timedelta(days=7)
PREFETCH = timedelta(seconds=10)  # lecturas automáticas justo después del envío
DEFAULT_SETTINGS = {
    "timezone": "America/Bogota",
    "days": [1, 2, 3, 4, 5],
    "start_hour": 8,
    "end_hour": 17,
    "track_opens": True,
    "track_clicks": True,
}


class CampaignError(ValueError):
    def __init__(self, message: str, status: int = 400):
        super().__init__(message)
        self.status = status


def now() -> datetime:
    return datetime.now(UTC)


# --- Buzones --------------------------------------------------------------------------------


def save_mailbox(
    session: Session, *, tenant_id: uuid.UUID, user_id: uuid.UUID, connected: gmail.Connected
) -> Mailbox:
    mailbox = session.scalar(
        select(Mailbox).where(Mailbox.tenant_id == tenant_id, Mailbox.email == connected.email)
    )
    if mailbox is None:
        mailbox = Mailbox(tenant_id=tenant_id, email=connected.email, refresh_token="")
        session.add(mailbox)
    mailbox.user_id = user_id
    mailbox.display_name = connected.name or mailbox.display_name
    mailbox.refresh_token = gmail.encrypt(connected.refresh_token)
    mailbox.access_token = gmail.encrypt(connected.access_token)
    mailbox.token_expires_at = connected.expires_at
    mailbox.status, mailbox.error = "active", None
    session.flush()
    return mailbox


def mailbox_dict(m: Mailbox, sent_today: int = 0) -> dict[str, Any]:
    return {
        "id": str(m.id),
        "email": m.email,
        "display_name": m.display_name,
        "status": m.status,
        "error": m.error,
        "daily_limit": m.daily_limit,
        "signature": m.signature,
        "user_id": str(m.user_id) if m.user_id else None,
        "sent_today": sent_today,
    }


def access_token(session: Session, mailbox: Mailbox) -> str:
    if mailbox.access_token and mailbox.token_expires_at and mailbox.token_expires_at > now():
        return gmail.decrypt(mailbox.access_token)
    token, expires = gmail.refresh_access_token(gmail.decrypt(mailbox.refresh_token))
    mailbox.access_token, mailbox.token_expires_at = gmail.encrypt(token), expires
    return token


def _day_start(tz: ZoneInfo) -> datetime:
    local = now().astimezone(tz)
    return local.replace(hour=0, minute=0, second=0, microsecond=0).astimezone(UTC)


def sent_today(session: Session, mailbox_id: uuid.UUID, tz: ZoneInfo) -> int:
    return int(
        session.scalar(
            select(func.count()).where(
                Message.mailbox_id == mailbox_id, Message.sent_at >= _day_start(tz)
            )
        )
        or 0
    )


# --- Campañas -------------------------------------------------------------------------------


def _tz(name: str) -> ZoneInfo:
    try:
        return ZoneInfo(name)
    except (ZoneInfoNotFoundError, ValueError):
        return ZoneInfo("America/Bogota")


def clean_steps(steps: list[dict[str, Any]]) -> list[dict[str, Any]]:
    out = []
    for i, raw in enumerate(steps[:MAX_STEPS]):
        subject = " ".join(str(raw.get("subject", "")).split())[:250]
        body = str(raw.get("body", ""))[:100000]
        if templating.is_html(body):
            body = templating.clean_html(body)
        delay = int(raw.get("delay_days", 0 if i == 0 else 3) or 0)
        out.append({"subject": subject, "body": body, "delay_days": max(0, min(60, delay))})
    return out


def clean_settings(data: dict[str, Any]) -> dict[str, Any]:
    merged = {**DEFAULT_SETTINGS, **{k: v for k, v in data.items() if k in DEFAULT_SETTINGS}}
    merged["timezone"] = str(_tz(str(merged["timezone"])))
    days = sorted({int(d) for d in merged["days"] if 1 <= int(d) <= 7})
    merged["days"] = days or [1, 2, 3, 4, 5]
    start = max(0, min(23, int(merged["start_hour"])))
    end = max(start + 1, min(24, int(merged["end_hour"])))
    merged.update(
        start_hour=start,
        end_hour=end,
        track_opens=bool(merged["track_opens"]),
        track_clicks=bool(merged["track_clicks"]),
    )
    return merged


def get_campaign(session: Session, tenant_id: uuid.UUID, campaign_id: uuid.UUID) -> Campaign:
    campaign = session.get(Campaign, campaign_id)
    if campaign is None or campaign.tenant_id != tenant_id:
        raise CampaignError("Esa campaña no existe.", 404)
    return campaign


def _audience_query(tenant_id: uuid.UUID, audience: dict[str, Any]) -> Any:
    f = audience.get("filters") or {}
    query = _filtered(
        tenant_id,
        ContactFilters(
            q=f.get("q", ""),
            status=f.get("estado", ""),
            country=f.get("pais", ""),
            industry=f.get("industria", ""),
            email_status=f.get("correo", ""),
        ),
    )
    if audience.get("list_id"):
        query = query.where(
            Contact.id.in_(
                select(ListMember.contact_id).where(
                    ListMember.list_id == uuid.UUID(str(audience["list_id"]))
                )
            )
        )
    if audience.get("contact_ids"):
        query = query.where(Contact.id.in_([uuid.UUID(str(i)) for i in audience["contact_ids"]]))
    return query


def audience_preview(
    session: Session, tenant_id: uuid.UUID, audience: dict[str, Any]
) -> dict[str, Any]:
    contacts = session.scalars(_audience_query(tenant_id, audience).limit(MAX_RECIPIENTS + 1)).all()
    suppressed = set(
        session.scalars(select(Suppression.email).where(Suppression.tenant_id == tenant_id))
    )
    include_estimated = bool(audience.get("include_estimated", True))
    counts = {
        "total": len(contacts),
        "eligible": 0,
        "no_email": 0,
        "invalid": 0,
        "suppressed": 0,
        "estimated": 0,
        "excluded_estimated": 0,
    }
    sample = None
    for c in contacts:
        if not c.email:
            counts["no_email"] += 1
        elif c.email in suppressed:
            counts["suppressed"] += 1
        elif c.email_status == "invalido":
            counts["invalid"] += 1
        elif c.email_status == "estimado" and not include_estimated:
            counts["excluded_estimated"] += 1
        else:
            counts["eligible"] += 1
            counts["estimated"] += c.email_status == "estimado"
            sample = sample or c
    return {
        **counts,
        "sample": {"id": str(sample.id), "full_name": sample.full_name} if sample else None,
        "too_many": len(contacts) > MAX_RECIPIENTS,
    }


def _iso(value: datetime | None) -> str | None:
    return value.isoformat() if value else None


def campaign_dict(session: Session, c: Campaign, *, full: bool = True) -> dict[str, Any]:
    rows = session.execute(
        select(Recipient.status, func.count())
        .where(Recipient.campaign_id == c.id)
        .group_by(Recipient.status)
    ).all()
    by_status = {s: int(n) for s, n in rows}
    agg = session.execute(
        select(
            func.count().filter(Recipient.sent_count > 0),
            func.count().filter(Recipient.opened_at.is_not(None)),
            func.count().filter(Recipient.clicked_at.is_not(None)),
            func.coalesce(func.sum(Recipient.sent_count), 0),
        ).where(Recipient.campaign_id == c.id)
    ).one()
    contacted, opened, clicked, emails = (int(x or 0) for x in agg)
    replied = by_status.get("replied", 0)

    def rate(n: int) -> float | None:
        return round(100 * n / contacted, 1) if contacted else None

    mailbox = session.get(Mailbox, c.mailbox_id) if c.mailbox_id else None
    data: dict[str, Any] = {
        "id": str(c.id),
        "name": c.name,
        "status": c.status,
        "mailbox": {"id": str(mailbox.id), "email": mailbox.email, "status": mailbox.status}
        if mailbox
        else None,
        "launched_at": c.launched_at.isoformat() if c.launched_at else None,
        "next_window": _iso(next_window(c.settings)) if c.status == "active" else None,
        "created_at": c.created_at.isoformat(),
        "steps_count": len(c.steps),
        "stats": {
            "recipients": sum(by_status.values()),
            "by_status": by_status,
            "contacted": contacted,
            "emails_sent": emails,
            "opened": opened,
            "clicked": clicked,
            "replied": replied,
            "bounced": by_status.get("bounced", 0),
            "unsubscribed": by_status.get("unsubscribed", 0),
            "open_rate": rate(opened),
            "click_rate": rate(clicked),
            "reply_rate": rate(replied),
            "bounce_rate": rate(by_status.get("bounced", 0)),
        },
    }
    if full:
        data.update(steps=c.steps, settings=c.settings, audience=c.audience)
    return data


def create_campaign(session: Session, *, tenant_id: uuid.UUID, user: User, name: str) -> Campaign:
    clean = " ".join(name.split())[:160]
    if len(clean) < 2:
        raise CampaignError("Escribe un nombre para la campaña.")
    mailbox = session.scalar(
        select(Mailbox).where(
            Mailbox.tenant_id == tenant_id, Mailbox.user_id == user.id, Mailbox.status == "active"
        )
    )
    campaign = Campaign(
        tenant_id=tenant_id,
        created_by=user.id,
        name=clean,
        mailbox_id=mailbox.id if mailbox else None,
        steps=[
            {
                "subject": "{{nombre}}, una idea para {{empresa|tu equipo}}",
                "delay_days": 0,
                "body": "Hola, {{nombre|}}:\n\nEscribe aquí tu mensaje inicial: por qué le "
                "escribes, qué valor le aportas y una pregunta concreta.\n\n"
                "Saludos,\n{{remitente}}",
            },
            {
                "subject": "",
                "delay_days": 3,
                "body": "Hola de nuevo, {{nombre|}}. ¿Pudiste ver mi correo anterior? "
                "Con gusto te cuento en 15 minutos.\n\n{{remitente}}",
            },
            {
                "subject": "",
                "delay_days": 5,
                "body": "{{nombre|Hola}}, no quiero insistir de más. Si no es el momento, "
                "dime y no vuelvo a escribirte. Si conviene hablar con otra persona del equipo, "
                "¿me indicas con quién?\n\n{{remitente}}",
            },
        ],
        settings=dict(DEFAULT_SETTINGS),
        audience={"include_estimated": True},
    )
    session.add(campaign)
    session.flush()
    return campaign


def update_campaign(session: Session, campaign: Campaign, data: dict[str, Any]) -> Campaign:
    if "name" in data:
        name = " ".join(str(data["name"]).split())[:160]
        if len(name) < 2:
            raise CampaignError("Escribe un nombre para la campaña.")
        campaign.name = name
    if "steps" in data:
        if campaign.status != "draft" and len(data["steps"]) < len(campaign.steps):
            raise CampaignError("No puedes quitar pasos de una campaña ya lanzada.", 409)
        campaign.steps = clean_steps(list(data["steps"]))
    if "settings" in data:
        campaign.settings = clean_settings(dict(data["settings"]))
    if "audience" in data:
        if campaign.status != "draft":
            raise CampaignError("La audiencia se define antes de lanzar.", 409)
        audience = dict(data["audience"])
        campaign.audience = {
            "list_id": str(audience["list_id"]) if audience.get("list_id") else None,
            "filters": {
                k: str(v)[:200]
                for k, v in (audience.get("filters") or {}).items()
                if k in ("q", "estado", "pais", "industria", "correo") and v
            },
            "contact_ids": [str(i) for i in (audience.get("contact_ids") or [])][:MAX_RECIPIENTS],
            "include_estimated": bool(audience.get("include_estimated", True)),
        }
    if "mailbox_id" in data:
        mailbox = session.get(Mailbox, data["mailbox_id"]) if data["mailbox_id"] else None
        if data["mailbox_id"] and (mailbox is None or mailbox.tenant_id != campaign.tenant_id):
            raise CampaignError("Ese buzón no existe.", 404)
        campaign.mailbox_id = mailbox.id if mailbox else None
    return campaign


def validate_for_launch(session: Session, campaign: Campaign) -> list[str]:
    problems = []
    mailbox = session.get(Mailbox, campaign.mailbox_id) if campaign.mailbox_id else None
    if mailbox is None:
        problems.append("Elige el buzón desde el que se enviará.")
    elif mailbox.status != "active":
        problems.append("El buzón elegido necesita reconectarse.")
    if not campaign.steps:
        problems.append("Agrega al menos un correo a la secuencia.")
    else:
        if not campaign.steps[0]["subject"].strip():
            problems.append("El primer correo necesita asunto.")
        for i, step in enumerate(campaign.steps, start=1):
            if len(step["body"].strip()) < 10:
                problems.append(f"El correo {i} está vacío.")
            for part in (step["subject"], step["body"]):
                for name in templating._VAR.findall(part):
                    if name[0] not in templating.VARIABLES:
                        problems.append(f"La variable {{{{{name[0]}}}}} no existe (correo {i}).")
    return problems


def launch(session: Session, campaign: Campaign) -> int:
    if campaign.status != "draft":
        raise CampaignError("Esta campaña ya fue lanzada.", 409)
    problems = validate_for_launch(session, campaign)
    if problems:
        raise CampaignError(" ".join(problems))
    suppressed = set(
        session.scalars(
            select(Suppression.email).where(Suppression.tenant_id == campaign.tenant_id)
        )
    )
    include_estimated = bool(campaign.audience.get("include_estimated", True))
    contacts = session.scalars(
        _audience_query(campaign.tenant_id, campaign.audience).limit(MAX_RECIPIENTS)
    ).all()
    created = 0
    start = now()
    for c in contacts:
        if not c.email or c.email in suppressed or c.email_status == "invalido":
            continue
        if c.email_status == "estimado" and not include_estimated:
            continue
        session.add(
            Recipient(
                tenant_id=campaign.tenant_id,
                campaign_id=campaign.id,
                contact_id=c.id,
                email=c.email,
                next_send_at=start,
            )
        )
        created += 1
    if not created:
        raise CampaignError("La audiencia no tiene contactos con correo para enviar.")
    campaign.status, campaign.launched_at = "active", start
    session.flush()
    return created


def suppress(session: Session, tenant_id: uuid.UUID, email: str, reason: str) -> None:
    exists = session.scalar(
        select(Suppression.id).where(Suppression.tenant_id == tenant_id, Suppression.email == email)
    )
    if exists is None:
        session.add(Suppression(tenant_id=tenant_id, email=email.lower(), reason=reason))
    session.execute(
        update(Recipient)
        .where(
            Recipient.tenant_id == tenant_id,
            Recipient.email == email,
            Recipient.status.in_(("pending", "active")),
        )
        .values(status="unsubscribed" if reason == "unsubscribed" else "bounced", next_send_at=None)
    )


# --- Motor de envío -------------------------------------------------------------------------


def next_window(settings: dict[str, Any]) -> datetime | None:
    """Próximo inicio del horario de envío (``None`` si ya está dentro del horario)."""
    if _in_window(settings):
        return None
    tz = _tz(str(settings.get("timezone", "America/Bogota")))
    local = now().astimezone(tz)
    start, end = int(settings.get("start_hour", 8)), int(settings.get("end_hour", 17))
    days = settings.get("days", [1, 2, 3, 4, 5]) or [1, 2, 3, 4, 5]
    for offset in range(8):
        day = (local + timedelta(days=offset)).replace(minute=0, second=0, microsecond=0)
        if day.isoweekday() not in days:
            continue
        candidate = day.replace(hour=start)
        if offset == 0 and local.hour >= end:
            continue
        if candidate > local:
            return candidate
    return None


def _in_window(settings: dict[str, Any]) -> bool:
    local = now().astimezone(_tz(str(settings.get("timezone", "America/Bogota"))))
    if local.isoweekday() not in settings.get("days", [1, 2, 3, 4, 5]):
        return False
    return int(settings.get("start_hour", 8)) <= local.hour < int(settings.get("end_hour", 17))


def _gap() -> timedelta:
    """Pausa aleatoria entre correos del mismo buzón: ritmo humano, no ráfagas."""
    return timedelta(seconds=random.randint(70, 180))  # noqa: S311 - ritmo, no criptografía


def send_next(session: Session, campaign: Campaign, mailbox: Mailbox) -> bool:
    """Envía como máximo un correo de la campaña. ``True`` si envió."""
    tz = _tz(str(campaign.settings.get("timezone", "America/Bogota")))
    if sent_today(session, mailbox.id, tz) >= mailbox.daily_limit:
        return False
    recipient = session.scalar(
        select(Recipient)
        .where(
            Recipient.campaign_id == campaign.id,
            Recipient.status.in_(("pending", "active")),
            Recipient.next_send_at <= now(),
        )
        .order_by(Recipient.next_send_at)
        .limit(1)
        .with_for_update(skip_locked=True)
    )
    if recipient is None:
        return False
    contact = session.get(Contact, recipient.contact_id)
    suppressed = session.scalar(
        select(Suppression.id).where(
            Suppression.tenant_id == campaign.tenant_id, Suppression.email == recipient.email
        )
    )
    if contact is None or suppressed is not None or recipient.step >= len(campaign.steps):
        recipient.status = "skipped" if contact is None or suppressed else "completed"
        recipient.next_send_at = None
        return True
    step = campaign.steps[recipient.step]
    owner = session.get(User, mailbox.user_id) if mailbox.user_id else None
    sender = mailbox.display_name or (owner.full_name if owner else "") or mailbox.email
    country = COUNTRIES.get(contact.country_code or "", ("",))[0]
    values = templating.context(contact, sender, country)
    body, _ = templating.render(templating.as_html(step["body"]), values, escape=True)
    signature, _ = templating.render(templating.as_html(mailbox.signature), values, escape=True)
    if recipient.step == 0 or not recipient.first_subject:
        subject, _ = templating.render(step["subject"], values)
        thread_ref = None
    else:
        custom = step["subject"].strip()
        subject = (
            templating.render(custom, values)[0] if custom else f"Re: {recipient.first_subject}"
        )
        thread_ref = recipient.thread_id
    token = secrets.token_urlsafe(24)
    built = templating.build(
        sender_email=mailbox.email,
        sender_name=sender,
        to_email=recipient.email,
        to_name=contact.full_name,
        subject=subject or "(sin asunto)",
        body_html=body,
        signature_html=signature,
        token=token,
        track_opens=bool(campaign.settings.get("track_opens", True)),
        track_clicks=bool(campaign.settings.get("track_clicks", True)),
        in_reply_to=recipient.last_rfc_id if thread_ref else None,
        references=" ".join(x for x in (recipient.first_rfc_id, recipient.last_rfc_id) if x)
        if thread_ref
        else None,
    )
    try:
        token_value = access_token(session, mailbox)
        sent = gmail.send(token_value, built.message.as_bytes(), thread_ref)
    except gmail.GmailError as exc:
        if exc.auth:
            mailbox.status, mailbox.error = "error", str(exc)[:300]
        elif exc.recipient:
            recipient.status, recipient.error, recipient.next_send_at = (
                "failed",
                str(exc)[:300],
                None,
            )
        else:
            recipient.next_send_at = now() + timedelta(minutes=15)
            recipient.error = str(exc)[:300]
        logger.warning("campaign_send_failed %s %s", campaign.id, exc)
        return False
    rfc_id = gmail.message_rfc_id(token_value, str(sent.get("id", ""))) or built.rfc_id
    message = Message(
        tenant_id=campaign.tenant_id,
        campaign_id=campaign.id,
        recipient_id=recipient.id,
        mailbox_id=mailbox.id,
        step=recipient.step,
        subject=built.subject[:300],
        gmail_id=str(sent.get("id", ""))[:100],
        thread_id=str(sent.get("threadId", ""))[:100],
        rfc_message_id=rfc_id[:300],
        token=token,
        sent_at=now(),
    )
    session.add(message)
    session.flush()
    session.add(TrackingToken(token=token, tenant_id=campaign.tenant_id, message_id=message.id))
    recipient.thread_id = recipient.thread_id or message.thread_id
    recipient.first_rfc_id = recipient.first_rfc_id or rfc_id
    recipient.first_subject = recipient.first_subject or built.subject[:300]
    recipient.last_rfc_id = rfc_id
    recipient.sent_count += 1
    recipient.step += 1
    recipient.error = None
    recipient.status = "active"
    if recipient.step < len(campaign.steps):
        recipient.next_send_at = now() + timedelta(
            days=campaign.steps[recipient.step]["delay_days"]
        )
    else:
        recipient.next_send_at = None
        recipient.status = "completed" if campaign.steps else recipient.status
        _emit(session, campaign, recipient, "campaign.finished", recipient_id=recipient.id)
    crm_log(
        session,
        tenant_id=campaign.tenant_id,
        kind="email",
        user_id=mailbox.user_id,
        contact_id=contact.id,
        body=f"Campaña «{campaign.name}» · correo {message.step + 1}: {built.subject}",
    )
    if contact.status == "nuevo":
        contact.status = "en_proceso"
    return True


def check_replies(session: Session, campaign: Campaign, mailbox: Mailbox, limit: int = 10) -> int:
    due = session.scalars(
        select(Recipient)
        .where(
            Recipient.campaign_id == campaign.id,
            Recipient.thread_id.is_not(None),
            Recipient.status.in_(("active", "completed")),
            (Recipient.checked_at.is_(None)) | (Recipient.checked_at < now() - CHECK_EVERY),
        )
        .order_by(Recipient.checked_at.nulls_first())
        .limit(limit)
    ).all()
    found = 0
    for recipient in due:
        recipient.checked_at = now()
        try:
            outcome = gmail.thread_outcome(
                access_token(session, mailbox), recipient.thread_id or "", mailbox.email
            )
        except gmail.GmailError as exc:
            if exc.auth:
                mailbox.status, mailbox.error = "error", str(exc)[:300]
                return found
            continue
        if outcome == "replied" and recipient.status != "replied":
            recipient.status, recipient.replied_at, recipient.next_send_at = "replied", now(), None
            crm_log(
                session,
                tenant_id=campaign.tenant_id,
                kind="email",
                user_id=None,
                contact_id=recipient.contact_id,
                body=f"Respondió a la campaña «{campaign.name}». Revisa tu Gmail.",
            )
            notify(
                session, tenant_id=campaign.tenant_id,
                user_id=mailbox.user_id or campaign.created_by, kind="reply",
                title=f"{recipient.email} respondió a «{campaign.name}»",
                body="Revisa tu Gmail para contestar.", link=f"/app/campanas/{campaign.id}",
            )  # fmt: skip
            if _emit(session, campaign, recipient, "campaign.replied"):
                found += 1
                continue  # una automatización decide qué hacer (en vez de la tarea fija)
            crm_log(
                session,
                tenant_id=campaign.tenant_id,
                kind="task",
                user_id=campaign.created_by,
                contact_id=recipient.contact_id,
                due_at=now() + timedelta(hours=4),
                body=f"Responder a {recipient.email} (campaña «{campaign.name}»)",
            )
            found += 1
        elif outcome == "bounced":
            recipient.status, recipient.next_send_at = "bounced", None
            suppress(session, campaign.tenant_id, recipient.email, "bounced")
            contact = session.get(Contact, recipient.contact_id)
            if contact is not None and contact.email == recipient.email:
                contact.email_status = "invalido"
                contact.email_check_reason = "Rebotó en una campaña"
            _emit(session, campaign, recipient, "campaign.bounced")
            found += 1
    return found


def _finish_if_done(session: Session, campaign: Campaign) -> None:
    pending = session.scalar(
        select(func.count()).where(
            Recipient.campaign_id == campaign.id, Recipient.status.in_(("pending", "active"))
        )
    )
    if pending:
        return
    # Quienes ya recibieron toda la secuencia pueden responder todavía: se revisa una semana más.
    waiting = session.scalar(
        select(func.max(Message.sent_at)).where(
            Message.campaign_id == campaign.id,
            Message.recipient_id.in_(
                select(Recipient.id).where(
                    Recipient.campaign_id == campaign.id, Recipient.status == "completed"
                )
            ),
        )
    )
    if waiting is None or waiting < now() - REPLY_WINDOW:
        campaign.status = "completed"


def tick(factory: sessionmaker[Session]) -> int:
    """Una vuelta del motor: por cada empresa con campañas activas, un envío por buzón (si le
    toca) y revisión de respuestas. Un candado global evita que dos procesos envíen a la vez."""
    sent = 0
    with factory() as session:
        tenants = list(session.scalars(select(Tenant.id)))
    for tenant_id in tenants:
        with factory.begin() as session:
            got = session.scalar(
                text("SELECT pg_try_advisory_xact_lock(hashtext(:k))"),
                {"k": f"campaign-tick:{tenant_id}"},
            )
            if not got:
                continue
            set_context(session, tenant_id=tenant_id)
            campaigns = session.scalars(
                select(Campaign).where(Campaign.tenant_id == tenant_id, Campaign.status == "active")
            ).all()
            busy: set[uuid.UUID] = set()
            for campaign in campaigns:
                mailbox = session.get(Mailbox, campaign.mailbox_id) if campaign.mailbox_id else None
                if mailbox is None or mailbox.status != "active":
                    continue
                if (
                    mailbox.id not in busy
                    and _in_window(campaign.settings)
                    and (mailbox.next_send_after is None or mailbox.next_send_after <= now())
                    and send_next(session, campaign, mailbox)
                ):
                    busy.add(mailbox.id)
                    mailbox.next_send_after = now() + _gap()
                    sent += 1
                if mailbox.status == "active":
                    check_replies(session, campaign, mailbox)
                _finish_if_done(session, campaign)
    return sent


class Scheduler(threading.Thread):
    def __init__(self, factory: sessionmaker[Session], every: float = 30.0):
        super().__init__(name="orkesta-campaigns", daemon=True)
        self.factory, self.every = factory, every
        self.stop_event = threading.Event()

    def run(self) -> None:
        last_purge = 0.0
        while not self.stop_event.wait(self.every):
            try:
                tick(self.factory)
            except Exception:
                logger.exception("campaign_tick_failed")
            try:
                from orkesta.notifications.service import due_task_reminders

                due_task_reminders(self.factory)
            except Exception:
                logger.exception("reminders_failed")
            if time.monotonic() - last_purge > 600:
                last_purge = time.monotonic()
                try:
                    from orkesta.research.service import purge_all_unsaved

                    purge_all_unsaved(self.factory)
                except Exception:
                    logger.exception("research_purge_failed")

    def stop(self) -> None:
        self.stop_event.set()


# --- Seguimiento público --------------------------------------------------------------------


def _emit(session: Session, campaign: Campaign | None, recipient: Recipient, trigger: str,
          **extra: Any) -> int:  # fmt: skip
    campaign = campaign or session.get(Campaign, recipient.campaign_id)
    mailbox = (
        session.get(Mailbox, campaign.mailbox_id) if campaign and campaign.mailbox_id else None
    )
    owner = (mailbox.user_id if mailbox else None) or (campaign.created_by if campaign else None)
    return emit(session, recipient.tenant_id, trigger, contact_id=recipient.contact_id,
                campaign_id=recipient.campaign_id, user_id=owner, **extra)  # fmt: skip


def _by_token(session: Session, token: str) -> tuple[Message, Recipient] | None:
    link = session.get(TrackingToken, token[:48])
    if link is None:
        return None
    set_context(session, tenant_id=link.tenant_id)
    message = session.get(Message, link.message_id)
    recipient = session.get(Recipient, message.recipient_id) if message else None
    return (message, recipient) if message and recipient else None


def record_open(session: Session, token: str) -> None:
    found = _by_token(session, token)
    if found is None:
        return
    message, recipient = found
    if now() - message.sent_at < PREFETCH:
        return  # prelectura automática inmediata, no una apertura real
    message.open_count += 1
    message.opened_at = message.opened_at or now()
    recipient.open_count += 1
    recipient.opened_at = recipient.opened_at or now()
    _emit(session, None, recipient, "campaign.opened", open_count=recipient.open_count)


def record_click(session: Session, token: str) -> None:
    found = _by_token(session, token)
    if found is None:
        return
    message, recipient = found
    message.click_count += 1
    recipient.click_count += 1
    first = recipient.clicked_at is None
    recipient.clicked_at = recipient.clicked_at or now()
    recipient.opened_at = recipient.opened_at or now()
    if first:
        _emit(session, None, recipient, "campaign.clicked")


def unsubscribe(session: Session, token: str) -> str | None:
    found = _by_token(session, token)
    if found is None:
        return None
    _, recipient = found
    suppress(session, recipient.tenant_id, recipient.email, "unsubscribed")
    already = recipient.status == "unsubscribed"
    recipient.status, recipient.next_send_at = "unsubscribed", None
    if not already:
        _emit(session, None, recipient, "campaign.unsubscribed")
    crm_log(
        session,
        tenant_id=recipient.tenant_id,
        kind="system",
        user_id=None,
        contact_id=recipient.contact_id,
        body="Se dio de baja de los correos de campañas.",
    )
    return recipient.email
