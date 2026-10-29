"""Cuentas de envío, campañas y su motor de envío en el worker.

El envío avanza con un trabajo ``campaign_tick`` por campaña que se reprograma a sí mismo:

1. Revisa respuestas y rebotes de los destinatarios ya contactados (deja de escribirles).
2. Si está dentro del horario y de los límites diarios (por cuenta y por campaña), envía el
   siguiente correo vencido. Los seguimientos van en el mismo hilo que el primer correo.
3. Se reprograma con una pausa al azar (1-3 minutos) o para el inicio del próximo horario.
"""

from __future__ import annotations

import random
import uuid
from datetime import datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from itsdangerous import BadSignature, URLSafeSerializer
from sqlalchemy import func, select, update
from sqlalchemy.orm import Session

from orkesta.campaigns.history import excluded_by_history, record_contact
from orkesta.campaigns.transports import (
    Transport,
    TransportFactory,
    Watched,
    default_transport,
)
from orkesta.config import get_settings
from orkesta.db.models import (
    Campaign,
    CampaignMessage,
    CampaignRecipient,
    MailAccount,
    Prospect,
    ProspectList,
    ProspectListMember,
    Suppression,
)
from orkesta.engine.mail.message import build_mail, to_mime, unknown_variables
from orkesta.engine.mail.smtp import MailTransportError, SmtpSettings, check_smtp
from orkesta.secrets_box import encrypt
from orkesta.worker.queue import enqueue_job

TICK_JOB_KIND = "campaign_tick"
MAX_STEPS = 3
SPACING_SECONDS = (60, 180)  # pausa al azar entre correos de una misma cuenta
WATCH_DAYS = 14  # se vigilan respuestas hasta 14 días después del último envío
DEFAULT_WEEKDAYS = [0, 1, 2, 3, 4]


class CampaignError(ValueError):
    """Configuración o acción de campaña inválida."""


# --- Cuentas ---------------------------------------------------------------------------------


def create_smtp_account(
    session: Session,
    *,
    organization_id: uuid.UUID,
    email: str,
    from_name: str | None,
    smtp_host: str,
    smtp_port: int,
    smtp_security: str,
    username: str,
    password: str,
    imap_host: str | None,
    imap_port: int | None,
    daily_limit: int,
    verify: bool = True,
) -> MailAccount:
    email = email.strip().lower()
    if "@" not in email or not smtp_host or not password:
        raise CampaignError("Completa el correo, el servidor SMTP y la contraseña.")
    if smtp_security not in ("ssl", "starttls"):
        raise CampaignError("Seguridad SMTP inválida (SSL o STARTTLS).")
    settings = SmtpSettings(
        smtp_host.strip(), smtp_port, smtp_security, username or email, password
    )
    if verify:
        check_smtp(settings)  # lanza MailTransportError con un mensaje claro
    account = _upsert_account(session, organization_id, email)
    account.provider = "smtp"
    account.from_name = (from_name or "").strip()[:120] or None
    account.smtp_host, account.smtp_port, account.smtp_security = (
        settings.host, smtp_port, smtp_security,
    )  # fmt: skip
    account.imap_host = (imap_host or "").strip() or None
    account.imap_port = imap_port if account.imap_host else None
    account.username = settings.username
    account.secret = encrypt(password)
    account.daily_limit = max(1, min(daily_limit, 2000))
    account.active = True
    account.last_error = None
    account.verified_at = session.scalar(select(func.now())) if verify else None
    session.flush()
    return account


def save_gmail_account(
    session: Session, *, organization_id: uuid.UUID, email: str, refresh_token: str,
    from_name: str | None = None,
) -> MailAccount:  # fmt: skip
    account = _upsert_account(session, organization_id, email.strip().lower())
    account.provider = "gmail"
    account.from_name = (from_name or account.from_name or "").strip()[:120] or None
    account.secret = encrypt(refresh_token)
    account.smtp_host = account.smtp_port = account.smtp_security = None
    account.imap_host = account.imap_port = account.username = None
    if account.daily_limit is None:
        account.daily_limit = get_settings().mail_default_daily_limit
    account.active = True
    account.last_error = None
    account.verified_at = session.scalar(select(func.now()))
    session.flush()
    return account


def _upsert_account(session: Session, organization_id: uuid.UUID, email: str) -> MailAccount:
    account = session.scalar(
        select(MailAccount).where(
            MailAccount.organization_id == organization_id, MailAccount.email == email
        )
    )
    if account is None:
        account = MailAccount(
            organization_id=organization_id,
            email=email,
            provider="smtp",
            daily_limit=get_settings().mail_default_daily_limit,
        )
        session.add(account)
    return account


def get_account(session: Session, organization_id: uuid.UUID, account_id: uuid.UUID) -> MailAccount:
    account = session.get(MailAccount, account_id)
    if account is None or account.organization_id != organization_id:
        raise CampaignError("La cuenta de envío no existe.")
    return account


# --- Campañas --------------------------------------------------------------------------------


def validate_steps(steps: list[dict[str, Any]]) -> list[dict[str, Any]]:
    clean: list[dict[str, Any]] = []
    for index, step in enumerate(steps[:MAX_STEPS]):
        subject = str(step.get("subject") or "").strip()
        body = str(step.get("body") or "").strip()
        if not body:
            continue
        if index == 0 and not subject:
            raise CampaignError("El correo 1 necesita asunto.")
        unknown = unknown_variables(subject + " " + body)
        if unknown:
            raise CampaignError(
                "Variables no reconocidas: "
                + ", ".join(sorted("{{" + u + "}}" for u in unknown))
                + ". Usa {{nombre}}, {{apellido}}, {{empresa}} o {{cargo}}."
            )
        delay = 0 if index == 0 else max(1, min(int(step.get("delay_days") or 3), 60))
        clean.append({"subject": subject[:300], "body": body[:20000], "delay_days": delay})
    if not clean:
        raise CampaignError("La campaña necesita al menos el correo 1 con asunto y texto.")
    return clean


def create_campaign(
    session: Session,
    *,
    organization_id: uuid.UUID,
    user_id: uuid.UUID | None,
    name: str,
    list_id: uuid.UUID,
    account_id: uuid.UUID,
    steps: list[dict[str, Any]],
    signature_html: str | None = None,
    signature_text: str | None = None,
    daily_limit: int | None = None,
    timezone: str = "America/Bogota",
    window_start_hour: int = 8,
    window_end_hour: int = 18,
    weekdays: list[int] | None = None,
    track_opens: bool = False,
    exclude_recent_days: int = 0,
    exclude_replied: bool = False,
) -> Campaign:
    target = session.get(ProspectList, list_id)
    if target is None or target.organization_id != organization_id:
        raise CampaignError("La lista no existe.")
    account = get_account(session, organization_id, account_id)
    try:
        ZoneInfo(timezone)
    except Exception as exc:  # zona horaria inválida
        raise CampaignError("Zona horaria inválida.") from exc
    if not 0 <= window_start_hour < window_end_hour <= 24:
        raise CampaignError("El horario de envío no es válido.")
    days = sorted({d for d in (weekdays or DEFAULT_WEEKDAYS) if 0 <= d <= 6})
    if not days:
        raise CampaignError("Elige al menos un día de envío.")
    campaign = Campaign(
        organization_id=organization_id,
        user_id=user_id,
        name=" ".join(name.split())[:120] or target.name,
        list_id=target.id,
        account_id=account.id,
        status="draft",
        steps=validate_steps(steps),
        signature_html=(signature_html or "").strip() or None,
        signature_text=(signature_text or "").strip() or None,
        daily_limit=max(1, min(daily_limit or account.daily_limit, account.daily_limit)),
        timezone=timezone,
        window_start_hour=window_start_hour,
        window_end_hour=window_end_hour,
        weekdays=days,
        track_opens=track_opens,
        exclude_recent_days=max(0, min(exclude_recent_days, 730)),
        exclude_replied=exclude_replied,
    )
    session.add(campaign)
    session.flush()
    return campaign


def get_campaign(session: Session, organization_id: uuid.UUID, campaign_id: uuid.UUID) -> Campaign:
    campaign = session.get(Campaign, campaign_id)
    if campaign is None or campaign.organization_id != organization_id:
        raise CampaignError("La campaña no existe.")
    return campaign


def suppressed_emails(session: Session, organization_id: uuid.UUID) -> set[str]:
    return set(
        session.scalars(
            select(Suppression.email).where(Suppression.organization_id == organization_id)
        )
    )


def start_campaign(session: Session, campaign: Campaign) -> dict[str, int]:
    """Congela destinatarios y variables desde la lista y encola el envío."""
    if campaign.status not in ("draft", "paused"):
        raise CampaignError("La campaña ya está en marcha o terminó.")
    if get_settings().is_deployed and not get_settings().public_base_url:
        raise CampaignError(
            "Falta PUBLIC_BASE_URL: el enlace de baja de un clic es obligatorio para enviar."
        )
    summary = {"destinatarios": 0, "excluidos": 0, "por_historial": 0, "sin_correo": 0}
    if campaign.status == "draft":
        excluded = suppressed_emails(session, campaign.organization_id)
        recent = excluded_by_history(
            session,
            campaign.organization_id,
            recent_days=campaign.exclude_recent_days,
            exclude_replied=campaign.exclude_replied,
        )
        existing = set(
            session.scalars(
                select(CampaignRecipient.email).where(CampaignRecipient.campaign_id == campaign.id)
            )
        )
        now = session.scalar(select(func.now()))
        rows = session.execute(
            select(ProspectListMember, Prospect)
            .join(Prospect, Prospect.id == ProspectListMember.prospect_id)
            .where(ProspectListMember.list_id == campaign.list_id)
        )
        for member, prospect in rows:
            email = (member.email or "").lower()
            if not email:
                summary["sin_correo"] += 1
                continue
            if email in excluded or email in existing:
                summary["excluidos"] += 1
                continue
            if email in recent:
                summary["por_historial"] += 1
                continue
            existing.add(email)
            session.add(
                CampaignRecipient(
                    organization_id=campaign.organization_id,
                    campaign_id=campaign.id,
                    prospect_id=prospect.id,
                    email=email,
                    variables={
                        "nombre": prospect.first_name.split()[0].title()
                        if prospect.first_name
                        else "",
                        "apellido": prospect.last_name,
                        "empresa": prospect.company,
                        "cargo": prospect.title or "",
                    },
                    status="pending",
                    next_step=0,
                    next_send_at=now,
                )
            )
            summary["destinatarios"] += 1
        if summary["destinatarios"] == 0:
            raise CampaignError("Ninguna persona de la lista tiene correo enviable.")
        campaign.started_at = now
    campaign.status = "running"
    session.flush()
    _schedule_tick(session, campaign, delay_seconds=0)
    return summary


def pause_campaign(session: Session, campaign: Campaign) -> None:
    if campaign.status == "running":
        campaign.status = "paused"


def _schedule_tick(session: Session, campaign: Campaign, delay_seconds: float) -> None:
    now = session.scalar(select(func.now()))
    assert now is not None  # noqa: S101 - func.now() siempre devuelve valor
    job = enqueue_job(
        session,
        organization_id=campaign.organization_id,
        batch_id=None,
        kind=TICK_JOB_KIND,
        idempotency_key=f"tick:{campaign.id}:{uuid.uuid4().hex}",
        payload={"campaign_id": str(campaign.id)},
        max_attempts=3,
    )
    job.run_after = now + timedelta(seconds=delay_seconds)


# --- Baja de un clic -------------------------------------------------------------------------


def _serializer() -> URLSafeSerializer:
    settings = get_settings()
    return URLSafeSerializer(settings.secret_key or "orkesta-desarrollo", salt="baja-de-campana")


def unsubscribe_token(organization_id: uuid.UUID, email: str) -> str:
    return _serializer().dumps({"o": str(organization_id), "e": email.lower()})


def unsubscribe_url(organization_id: uuid.UUID, email: str) -> str | None:
    base = get_settings().public_base_url.rstrip("/")
    return f"{base}/baja/{unsubscribe_token(organization_id, email)}" if base else None


def apply_unsubscribe(session: Session, token: str) -> str:
    """Registra la baja del token. Devuelve el correo dado de baja."""
    try:
        data = _serializer().loads(token)
        organization_id, email = uuid.UUID(data["o"]), str(data["e"]).lower()
    except (BadSignature, KeyError, ValueError, TypeError) as exc:
        raise CampaignError("El enlace de baja no es válido.") from exc
    suppress(session, organization_id, email, "unsubscribed")
    session.execute(
        update(CampaignRecipient)
        .where(
            CampaignRecipient.organization_id == organization_id,
            CampaignRecipient.email == email,
            CampaignRecipient.status.in_(("pending", "active", "completed")),
        )
        .values(status="unsubscribed", next_send_at=None)
    )
    return email


def suppress(
    session: Session, organization_id: uuid.UUID, email: str, reason: str, note: str | None = None
) -> None:
    from sqlalchemy.dialects.postgresql import insert as pg_insert

    statement = pg_insert(Suppression).values(
        id=uuid.uuid4(), organization_id=organization_id, email=email.lower(), reason=reason,
        note=note,
    )  # fmt: skip
    session.execute(
        statement.on_conflict_do_nothing(constraint="uq_suppressions_organization_id_email")
    )


# --- Motor de envío --------------------------------------------------------------------------


def _within_window(campaign: Campaign, now: datetime) -> tuple[bool, datetime]:
    """¿Se puede enviar ahora? Si no, ¿cuándo empieza el próximo horario?"""
    zone = ZoneInfo(campaign.timezone)
    local = now.astimezone(zone)
    if (
        local.weekday() in campaign.weekdays
        and campaign.window_start_hour <= local.hour < campaign.window_end_hour
    ):
        return True, now
    for days_ahead in range(0, 8):
        day = (local + timedelta(days=days_ahead)).replace(
            hour=campaign.window_start_hour, minute=0, second=0, microsecond=0
        )
        if day > local and day.weekday() in campaign.weekdays:
            return False, day.astimezone(now.tzinfo)
    return False, now + timedelta(hours=1)


def _sent_since(session: Session, column: Any, value: uuid.UUID, since: datetime) -> int:
    return int(
        session.scalar(
            select(func.count())
            .select_from(CampaignMessage)
            .where(column == value, CampaignMessage.sent_at >= since)
        )
        or 0
    )


def _check_events(
    session: Session, campaign: Campaign, transport: Transport, now: datetime
) -> None:
    recipients = list(
        session.scalars(
            select(CampaignRecipient).where(
                CampaignRecipient.campaign_id == campaign.id,
                CampaignRecipient.status.in_(("active", "completed")),
                CampaignRecipient.first_message_id.is_not(None),
            )
        )
    )
    if not recipients:
        return
    since = now - timedelta(days=WATCH_DAYS)
    messages: dict[uuid.UUID, set[str]] = {}
    for recipient_id, message_id, sent_at in session.execute(
        select(
            CampaignMessage.recipient_id, CampaignMessage.message_id, CampaignMessage.sent_at
        ).where(CampaignMessage.recipient_id.in_([r.id for r in recipients]))
    ):
        if sent_at >= since:
            messages.setdefault(recipient_id, set()).add(message_id)
    watched = [
        Watched(str(r.id), r.email, r.thread_id, frozenset(messages.get(r.id, set())))
        for r in recipients
        if r.id in messages
    ]
    events = transport.events(watched)
    for recipient in recipients:
        key = str(recipient.id)
        if key in events.bounced:
            recipient.status, recipient.next_send_at = "bounced", None
            suppress(session, campaign.organization_id, recipient.email, "bounced")
        elif key in events.replied:
            recipient.status, recipient.next_send_at, recipient.replied_at = "replied", None, now
            record_contact(session, campaign.organization_id, recipient.email, now, replied=True)


def _send_next(
    session: Session, campaign: Campaign, account: MailAccount, transport: Transport, now: datetime
) -> bool:
    recipient = session.scalar(
        select(CampaignRecipient)
        .where(
            CampaignRecipient.campaign_id == campaign.id,
            CampaignRecipient.status.in_(("pending", "active")),
            CampaignRecipient.next_send_at <= now,
        )
        .order_by(CampaignRecipient.next_send_at)
        .limit(1)
        .with_for_update(skip_locked=True)
    )
    if recipient is None:
        return False
    if recipient.email in suppressed_emails(session, campaign.organization_id):
        recipient.status, recipient.next_send_at = "skipped", None
        return True
    step = campaign.steps[recipient.next_step]
    first_subject = campaign.steps[0]["subject"]
    is_followup = recipient.next_step > 0
    mail = build_mail(
        to=recipient.email,
        # Los seguimientos responden en el hilo: mismo asunto con "Re:" salvo que traigan uno.
        subject_template=step["subject"] or first_subject,
        body_template=step["body"],
        variables=recipient.variables,
        signature_text=campaign.signature_text,
        signature_html=campaign.signature_html,
        sender_email=account.email,
        in_reply_to=recipient.last_message_id if is_followup else None,
        unsubscribe_url=unsubscribe_url(campaign.organization_id, recipient.email),
    )
    try:
        sent = transport.send(to_mime(mail, account.email, account.from_name), recipient.thread_id)
    except MailTransportError as exc:
        recipient.last_error = str(exc)[:1000]
        account.last_error = str(exc)[:1000]
        raise
    session.add(
        CampaignMessage(
            organization_id=campaign.organization_id,
            recipient_id=recipient.id,
            account_id=account.id,
            step=recipient.next_step,
            subject=mail.subject[:300],
            message_id=mail.message_id,
            provider_id=sent.provider_id,
            sent_at=now,
        )
    )
    record_contact(session, campaign.organization_id, recipient.email, now)
    recipient.first_message_id = recipient.first_message_id or mail.message_id
    recipient.last_message_id = mail.message_id
    recipient.thread_id = recipient.thread_id or sent.thread_id
    recipient.last_error = None
    recipient.next_step += 1
    if recipient.next_step < len(campaign.steps):
        recipient.status = "active"
        recipient.next_send_at = now + timedelta(
            days=campaign.steps[recipient.next_step]["delay_days"]
        )
    else:
        recipient.status = "completed"
        recipient.next_send_at = None
    return True


def run_tick(
    session: Session,
    campaign_id: uuid.UUID,
    transport_factory: TransportFactory = default_transport,
) -> str:
    """Un paso del motor para una campaña. Devuelve qué hizo (para registros y pruebas)."""
    campaign = session.get(Campaign, campaign_id, with_for_update=True)
    if campaign is None or campaign.status != "running":
        return "detenida"
    account = session.get(MailAccount, campaign.account_id)
    if account is None or not account.active:
        campaign.status = "paused"
        return "cuenta inactiva"
    now = session.scalar(select(func.now()))
    assert now is not None  # noqa: S101 - func.now() siempre devuelve valor
    transport = transport_factory(account)
    try:
        _check_events(session, campaign, transport, now)
    except MailTransportError as exc:
        account.last_error = str(exc)[:1000]

    pending = int(
        session.scalar(
            select(func.count())
            .select_from(CampaignRecipient)
            .where(
                CampaignRecipient.campaign_id == campaign.id,
                CampaignRecipient.status.in_(("pending", "active")),
            )
        )
        or 0
    )
    if pending == 0:
        campaign.status, campaign.finished_at = "completed", now
        return "terminada"

    open_now, next_window = _within_window(campaign, now)
    if not open_now:
        _schedule_tick(session, campaign, (next_window - now).total_seconds())
        return "fuera de horario"
    day_start = now - timedelta(hours=24)
    if (
        _sent_since(session, CampaignMessage.account_id, account.id, day_start)
        >= account.daily_limit
    ):
        _schedule_tick(session, campaign, 3600)
        return "límite de la cuenta"
    campaign_sent = int(
        session.scalar(
            select(func.count())
            .select_from(CampaignMessage)
            .join(CampaignRecipient, CampaignRecipient.id == CampaignMessage.recipient_id)
            .where(
                CampaignRecipient.campaign_id == campaign.id, CampaignMessage.sent_at >= day_start
            )
        )
        or 0
    )
    if campaign_sent >= campaign.daily_limit:
        _schedule_tick(session, campaign, 3600)
        return "límite de la campaña"
    try:
        sent = _send_next(session, campaign, account, transport, now)
    except MailTransportError:
        _schedule_tick(session, campaign, 600)  # reintento en 10 minutos
        return "error de envío"
    if not sent:
        # Nada vencido ahora (seguimientos más adelante): revisar dentro de una hora.
        _schedule_tick(session, campaign, 3600)
        return "en espera"
    _schedule_tick(session, campaign, random.uniform(*SPACING_SECONDS))  # noqa: S311
    return "enviado"


def campaign_stats(session: Session, campaign_id: uuid.UUID) -> dict[str, int]:
    rows = session.execute(
        select(CampaignRecipient.status, func.count())
        .where(CampaignRecipient.campaign_id == campaign_id)
        .group_by(CampaignRecipient.status)
    )
    stats = {status: int(count) for status, count in rows}
    stats["enviados"] = int(
        session.scalar(
            select(func.count())
            .select_from(CampaignMessage)
            .join(CampaignRecipient, CampaignRecipient.id == CampaignMessage.recipient_id)
            .where(CampaignRecipient.campaign_id == campaign_id)
        )
        or 0
    )
    return stats
