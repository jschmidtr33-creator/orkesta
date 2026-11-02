"""Campañas: buzones, audiencia, lanzamiento, motor de envío, respuestas y seguimiento."""

from __future__ import annotations

import logging
import random
import secrets
import threading
import time
import uuid
from collections.abc import Sequence
from datetime import UTC, datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from sqlalchemy import delete, func, select, text, update
from sqlalchemy.orm import Session, sessionmaker

from orkesta.automation.events import emit
from orkesta.campaigns import gmail, replies, templating
from orkesta.campaigns.models import (
    Campaign,
    Mailbox,
    Message,
    Recipient,
    Suppression,
    TrackingToken,
)
from orkesta.core.db import set_context
from orkesta.crm.models import Deal
from orkesta.crm.service import log as crm_log
from orkesta.data.models import Contact, DataPoint, ListMember
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
    "corporate_mode": False,  # texto plano, sin medición: para bancos y corporativos
    "per_domain_daily": 3,  # máximo de correos al día a una misma empresa (0 = sin límite)
}
# Calentamiento del buzón: límite máximo por día de envío (día 1, día 2, …).
WARMUP = (20, 30, 45, 65, 90, 125, 170, 230, 310, 420, 560, 750, 1000, 1350, 1800, 2000)


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


def mailbox_dict(m: Mailbox, sent_today: int = 0, session: Session | None = None) -> dict[str, Any]:
    limit, day = effective_limit(session, m) if session is not None else (m.daily_limit, None)
    return {
        "effective_limit": limit,
        "warmup_day": day,
        "id": str(m.id),
        "email": m.email,
        "display_name": m.display_name,
        "status": m.status,
        "error": m.error,
        "daily_limit": m.daily_limit,
        "warmup": m.warmup,
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
        track_opens=bool(merged["track_opens"]) and not merged["corporate_mode"],
        track_clicks=bool(merged["track_clicks"]) and not merged["corporate_mode"],
        corporate_mode=bool(merged["corporate_mode"]),
        per_domain_daily=max(0, min(500, int(merged["per_domain_daily"] or 0))),
    )
    return merged


def send_days(session: Session, mailbox_id: uuid.UUID) -> int:
    """Días distintos en que el buzón ya envió correos (para el calentamiento)."""
    return int(
        session.scalar(
            select(func.count(func.distinct(func.date(Message.sent_at)))).where(
                Message.mailbox_id == mailbox_id, Message.sent_at < _day_start(_tz("UTC"))
            )
        )
        or 0
    )


def effective_limit(session: Session, mailbox: Mailbox) -> tuple[int, int | None]:
    """(límite de hoy, día de calentamiento o None si está desactivado)."""
    if not mailbox.warmup:
        return mailbox.daily_limit, None
    day = send_days(session, mailbox.id)
    cap = WARMUP[min(day, len(WARMUP) - 1)]
    return min(mailbox.daily_limit, cap), day + 1


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


_MX_CACHE: dict[str, tuple[datetime, list[str] | None]] = {}


def _mail_servers(domain: str) -> list[str] | None:
    """MX del dominio con caché de 6 horas (``[]`` = no recibe correo, ``None`` = no se sabe)."""
    from orkesta.research import memory

    hit = _MX_CACHE.get(domain)
    if hit and hit[0] > now() - timedelta(hours=6):
        return hit[1]
    hosts = (memory.MX_RESOLVER or memory._resolve_mx)(domain)
    _MX_CACHE[domain] = (now(), hosts)
    return hosts


def email_quality(
    session: Session, tenant_id: uuid.UUID, contacts: Sequence[Contact]
) -> dict[uuid.UUID, str]:
    """Calidad de cada correo antes de enviar (verificación previa, sin terceros):
    «confiable» (validado, publicado o con formato visto/confirmado), «comun» (estimado con el
    formato más común, sin evidencia) o «sin_correo» (el dominio no recibe correo)."""
    estimated = [c.id for c in contacts if c.email and c.email_status == "estimado"]
    confidence: dict[uuid.UUID, int] = {}
    if estimated:
        for cid, _value, conf in session.execute(
            select(DataPoint.contact_id, DataPoint.value, DataPoint.confidence).where(
                DataPoint.tenant_id == tenant_id, DataPoint.field == "email",
                DataPoint.contact_id.in_(estimated),
            )
        ).all():  # fmt: skip
            confidence[cid] = max(confidence.get(cid, 0), int(conf))
    domains = {c.email.rsplit("@", 1)[-1].lower() for c in contacts if c.email}
    no_mail = {d for d in list(domains)[:400] if _mail_servers(d) == []}
    out: dict[uuid.UUID, str] = {}
    for c in contacts:
        if not c.email:
            continue
        if c.email.rsplit("@", 1)[-1].lower() in no_mail:
            out[c.id] = "sin_correo"
        elif c.email_status == "estimado" and confidence.get(c.id, 0) < 40:
            out[c.id] = "comun"
        else:
            out[c.id] = "confiable"
    return out


def _eligible(
    session: Session, tenant_id: uuid.UUID, audience: dict[str, Any], contacts: Sequence[Contact]
) -> tuple[list[Contact], dict[str, int]]:
    suppressed = set(
        session.scalars(select(Suppression.email).where(Suppression.tenant_id == tenant_id))
    )
    include_estimated = bool(audience.get("include_estimated", True))
    trusted_only = audience.get("quality") == "confiables"
    quality = email_quality(session, tenant_id, contacts)
    counts = {"total": len(contacts), "eligible": 0, "no_email": 0, "invalid": 0,
              "suppressed": 0, "estimated": 0, "excluded_estimated": 0, "no_mail_domain": 0,
              "trusted": 0, "common": 0, "excluded_common": 0}  # fmt: skip
    keep: list[Contact] = []
    for c in contacts:
        q = quality.get(c.id, "")
        if not c.email:
            counts["no_email"] += 1
        elif c.email in suppressed:
            counts["suppressed"] += 1
        elif c.email_status == "invalido":
            counts["invalid"] += 1
        elif q == "sin_correo":
            counts["no_mail_domain"] += 1
        elif c.email_status == "estimado" and not include_estimated:
            counts["excluded_estimated"] += 1
        elif q == "comun" and trusted_only:
            counts["excluded_common"] += 1
        else:
            counts["eligible"] += 1
            counts["estimated"] += c.email_status == "estimado"
            counts["trusted" if q == "confiable" else "common"] += 1
            keep.append(c)
    return keep, counts


def audience_preview(
    session: Session, tenant_id: uuid.UUID, audience: dict[str, Any]
) -> dict[str, Any]:
    contacts = session.scalars(_audience_query(tenant_id, audience).limit(MAX_RECIPIENTS + 1)).all()
    keep, counts = _eligible(session, tenant_id, audience, contacts)
    sample = keep[0] if keep else None
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
            "blocked": by_status.get("blocked", 0),
            "unsubscribed": by_status.get("unsubscribed", 0),
            "open_rate": rate(opened),
            "click_rate": rate(clicked),
            "reply_rate": rate(replied),
            "bounce_rate": rate(by_status.get("bounced", 0)),
            "block_rate": rate(by_status.get("blocked", 0)),
            "replies": reply_breakdown(session, c),
        },
    }
    if full:
        data.update(steps=c.steps, settings=c.settings, audience=c.audience)
        data["learning"] = learning(session, c)
    return data


def reply_breakdown(session: Session, c: Campaign) -> dict[str, int]:
    rows = session.execute(
        select(Recipient.reply_category, func.count())
        .where(Recipient.campaign_id == c.id, Recipient.replied_at.is_not(None))
        .group_by(Recipient.reply_category)
    ).all()
    out = {(cat or "otro"): int(n) for cat, n in rows}
    out["deals"] = int(session.scalar(select(func.count()).where(
        Recipient.campaign_id == c.id, Recipient.deal_id.is_not(None))) or 0)  # fmt: skip
    return out


def _reproposed(session: Session, c: Campaign) -> list[uuid.UUID]:
    """Contactos cuyo correo estimado rebotó y que ya tienen un nuevo formato propuesto."""
    rows = session.execute(
        select(Contact.id)
        .join(Recipient, Recipient.contact_id == Contact.id)
        .where(
            Recipient.campaign_id == c.id,
            Recipient.status == "bounced",
            Contact.email.is_not(None),
            Contact.email != Recipient.email,
            Contact.email_status == "estimado",
        )
    ).all()
    return [r[0] for r in rows]


def learning(session: Session, c: Campaign) -> dict[str, int]:
    """Lo que ORKESTA aprendió de esta campaña para las próximas búsquedas."""
    confirmed = session.scalar(
        select(func.count())
        .select_from(Recipient)
        .join(Contact, Contact.id == Recipient.contact_id)
        .where(
            Recipient.campaign_id == c.id,
            Recipient.status == "replied",
            Contact.email_status == "valido",
        )
    )
    domains = session.scalar(
        select(func.count(func.distinct(func.split_part(Recipient.email, "@", 2)))).where(
            Recipient.campaign_id == c.id, Recipient.status.in_(("bounced", "replied"))
        )
    )
    return {
        "confirmed": int(confirmed or 0),
        "reproposed": len(_reproposed(session, c)),
        "domains_learned": int(domains or 0),
    }


def delete_campaign(session: Session, c: Campaign) -> None:
    """Borra la campaña con sus destinatarios y mensajes. Si está enviando, se detiene antes."""
    session.delete(c)


def retry_bounced(session: Session, c: Campaign, user: User) -> Campaign:
    """Nueva campaña (borrador) con la misma secuencia para los correos re-propuestos."""
    ids = _reproposed(session, c)
    if not ids:
        raise CampaignError("No hay correos re-propuestos para reintentar.", 409)
    name = f"{c.name} · reintento"[:160]
    retry = session.scalar(  # un solo borrador de reintento por campaña
        select(Campaign).where(
            Campaign.tenant_id == c.tenant_id, Campaign.name == name, Campaign.status == "draft"
        )
    ) or create_campaign(session, tenant_id=c.tenant_id, user=user, name=name)
    retry.steps = list(c.steps)
    retry.settings = dict(c.settings)
    retry.mailbox_id = c.mailbox_id or retry.mailbox_id
    retry.audience = {"contact_ids": [str(i) for i in ids], "include_estimated": True}
    return retry


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
            "quality": "confiables" if audience.get("quality") == "confiables" else "todos",
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
    contacts = session.scalars(
        _audience_query(campaign.tenant_id, campaign.audience).limit(MAX_RECIPIENTS)
    ).all()
    keep, _ = _eligible(session, campaign.tenant_id, campaign.audience, contacts)
    created = 0
    start = now()
    for c in keep:
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


def _gap(settings: dict[str, Any] | None = None, daily_limit: int = 80) -> timedelta:
    """Pausa aleatoria entre correos del mismo buzón: ritmo humano, no ráfagas, pero ajustada
    para que el límite diario del buzón quepa en el horario de envío (1.500 en 9 horas ≈ un
    correo cada ~20 s)."""
    settings = settings or {}
    hours = max(1, int(settings.get("end_hour", 17)) - int(settings.get("start_hour", 8)))
    average = hours * 3600 / max(1, daily_limit)
    average = max(15.0, min(125.0, average))
    low, high = average * 0.6, average * 1.4
    return timedelta(seconds=random.uniform(low, high))  # noqa: S311 - ritmo, no criptografía


def _learn(session: Session, recipient: Recipient, outcome: str) -> None:
    """IA propia: una respuesta confirma el formato de correo estimado; un rebote lo debilita."""
    from orkesta.research.extract import detect_pattern
    from orkesta.research.memory import feedback

    contact = session.get(Contact, recipient.contact_id)
    if contact is None or contact.email != recipient.email:
        return
    if contact.email_status == "estimado" or outcome == "replied":
        pattern = detect_pattern(contact.first_name or "", contact.last_name or "", recipient.email)
        if pattern:
            feedback(session, recipient.email, pattern, outcome)
    if outcome == "replied" and contact.email_status in ("estimado", "sin_validar", "desconocido"):
        contact.email_status = "valido"  # respondió: el correo existe
    if outcome == "bounced" and contact.email_status == "estimado":
        # Propone el siguiente formato más probable (nunca uno que ya rebotó en ese dominio).
        from orkesta.research.memory import next_email

        domain = recipient.email.rsplit("@", 1)[1].lower()
        new, _ = next_email(session, contact.first_name or "", contact.last_name or "", domain)
        if new and new != recipient.email:
            contact.email = new
            contact.email_status = "estimado"
            contact.email_check_reason = (
                f"{recipient.email} rebotó; se propone {new} (nuevo formato)"
            )


def blocked_domains(session: Session, tenant_id: uuid.UUID) -> set[str]:
    """Empresas cuyo filtro ya bloqueó un correo de este cliente: se les escribe en texto plano,
    sin medición y con calma."""
    rows = session.scalars(
        select(func.distinct(func.lower(func.split_part(Recipient.email, "@", 2)))).where(
            Recipient.tenant_id == tenant_id, Recipient.status == "blocked"
        )
    )
    return set(rows)


def _pick(
    session: Session, campaign: Campaign, mailbox: Mailbox, candidates: list[Recipient],
    tz: ZoneInfo,
) -> Recipient | None:  # fmt: skip
    """El siguiente destinatario respetando el máximo diario por empresa destinataria."""
    cap = int(campaign.settings.get("per_domain_daily", 3) or 0)
    if not candidates or cap <= 0:
        return candidates[0] if candidates else None
    domain = func.lower(func.split_part(Recipient.email, "@", 2))
    rows = session.execute(
        select(domain, func.count())
        .select_from(Message)
        .join(Recipient, Recipient.id == Message.recipient_id)
        .where(Message.mailbox_id == mailbox.id, Message.sent_at >= _day_start(tz))
        .group_by(domain)
    ).all()
    counts = {str(row[0]): int(row[1]) for row in rows}
    blocked = blocked_domains(session, campaign.tenant_id)
    for r in candidates:
        d = r.email.rsplit("@", 1)[-1].lower()
        limit = 1 if d in blocked else cap  # empresas que ya bloquearon: una al día
        if counts.get(d, 0) < limit:
            return r
    return None


def send_next(session: Session, campaign: Campaign, mailbox: Mailbox) -> bool:
    """Envía como máximo un correo de la campaña. ``True`` si envió."""
    tz = _tz(str(campaign.settings.get("timezone", "America/Bogota")))
    if sent_today(session, mailbox.id, tz) >= effective_limit(session, mailbox)[0]:
        return False
    from orkesta.admin.limits import emails_left_today

    if emails_left_today(session, campaign.tenant_id, tz) == 0:
        return False  # límite diario del plan: sigue mañana
    candidates = session.scalars(
        select(Recipient)
        .where(
            Recipient.campaign_id == campaign.id,
            Recipient.status.in_(("pending", "active")),
            Recipient.next_send_at <= now(),
        )
        .order_by(Recipient.next_send_at)
        .limit(40)
        .with_for_update(skip_locked=True)
    ).all()
    recipient = _pick(session, campaign, mailbox, list(candidates), tz)
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
    domain = recipient.email.rsplit("@", 1)[-1].lower()
    # Modo corporativo de la campaña, o automático para empresas que ya bloquearon un correo.
    plain = bool(campaign.settings.get("corporate_mode")) or domain in blocked_domains(
        session, campaign.tenant_id
    )
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
        track_opens=bool(campaign.settings.get("track_opens", True)) and not plain,
        track_clicks=bool(campaign.settings.get("track_clicks", True)) and not plain,
        plain=plain,
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
            Recipient.status.in_(("active", "completed"))
            # Rebotes anteriores a la distinción «no existe» / «bloqueado»: se revisan una vez.
            | ((Recipient.status == "bounced") & Recipient.error.is_(None)),
            (Recipient.checked_at.is_(None)) | (Recipient.checked_at < now() - CHECK_EVERY),
        )
        .order_by(Recipient.checked_at.nulls_first())
        .limit(limit)
    ).all()
    found = 0
    for recipient in due:
        recipient.checked_at = now()
        try:
            outcome, texts = gmail.thread_check(
                access_token(session, mailbox), recipient.thread_id or "", mailbox.email
            )
        except gmail.GmailError as exc:
            if exc.auth:
                mailbox.status, mailbox.error = "error", str(exc)[:300]
                return found
            continue
        if recipient.status == "bounced":  # rebote antiguo: reclasificar
            if outcome == "blocked":
                _undo_bounce(session, campaign, recipient)
            else:
                recipient.error = NOT_FOUND_NOTE if outcome == "bounced" else "Revisado"
            continue
        category, text = "", ""
        if outcome == "replied":
            category, text = _read_replies(texts)
            if category == "fuera_oficina":
                # Fuera de oficina sin el encabezado automático: la secuencia sigue.
                recipient.error = f"Fuera de oficina: {text[:200]}" if text else None
                continue
        if outcome in ("replied", "bounced"):
            _learn(session, recipient, outcome)
        if outcome == "replied" and recipient.status != "replied":
            recipient.status, recipient.replied_at, recipient.next_send_at = "replied", now(), None
            recipient.error = None
            handle_reply(session, campaign, recipient, category, text, mailbox.user_id)
            found += 1
        elif outcome == "blocked":
            # El filtro de la empresa rechazó el mensaje: la dirección puede existir. No se
            # castiga el formato ni se marca inválida; solo se detiene la secuencia.
            recipient.status, recipient.next_send_at = "blocked", None
            recipient.error = BLOCKED_NOTE
            found += 1
        elif outcome == "bounced":
            recipient.status, recipient.next_send_at = "bounced", None
            recipient.error = NOT_FOUND_NOTE
            suppress(session, campaign.tenant_id, recipient.email, "bounced")
            contact = session.get(Contact, recipient.contact_id)
            if contact is not None and contact.email == recipient.email:
                contact.email_status = "invalido"
                contact.email_check_reason = "Rebotó en una campaña"
            _emit(session, campaign, recipient, "campaign.bounced")
            found += 1
    return found


def _read_replies(texts: list[str]) -> tuple[str, str]:
    """Categoría de la respuesta más reciente que no sea un «fuera de oficina»."""
    import html

    last_ooo = ""
    for raw in reversed(texts):
        text = " ".join(html.unescape(raw).split())
        category, _confidence = replies.classify(text)
        if category != "fuera_oficina":
            return category, text
        last_ooo = last_ooo or text
    return ("fuera_oficina", last_ooo) if texts else ("otro", "")


def handle_reply(
    session: Session,
    campaign: Campaign,
    recipient: Recipient,
    category: str,
    text: str,
    owner_id: uuid.UUID | None = None,
    *,
    automatic: bool = True,
) -> None:
    """Actúa según lo que respondió la persona: oportunidad en el CRM, seguimiento o baja."""
    from orkesta.crm.service import DealInput, create_deal

    category = category if category in replies.CATEGORIES else "otro"
    label = replies.CATEGORIES[category]["label"]
    owner = owner_id or campaign.created_by
    recipient.reply_category = category
    if text:
        recipient.reply_snippet = text[:500]
    tenant = campaign.tenant_id
    contact = session.get(Contact, recipient.contact_id)
    name = contact.full_name if contact and contact.full_name else recipient.email
    quote = f": «{text[:240]}»" if text else ""
    if automatic:
        crm_log(session, tenant_id=tenant, kind="email", user_id=None,
                contact_id=recipient.contact_id,
                body=f"Respondió a la campaña «{campaign.name}» · {label}{quote}")  # fmt: skip
    if category in replies.POSITIVE and recipient.deal_id is None and owner is not None:
        existing = session.scalar(
            select(Deal.id)
            .where(
                Deal.tenant_id == tenant,
                Deal.contact_id == recipient.contact_id,
                Deal.status == "open",
            )
            .limit(1)
        )
        if existing is None:
            base = (contact.company_name or contact.full_name) if contact else recipient.email
            deal = create_deal(session, tenant_id=tenant, user_id=owner,
                               data=DealInput(title=f"{base} · {campaign.name}",
                                              contact_id=recipient.contact_id))  # fmt: skip
            existing = deal.id
        recipient.deal_id = existing
    if category == "baja":
        recipient.status = "unsubscribed"
        suppress(session, tenant, recipient.email, "unsubscribed")
    if automatic:
        notify(
            session, tenant_id=tenant, user_id=owner, kind="reply",
            title=f"{name} respondió a «{campaign.name}» · {label}",
            body=text[:200] or "Revisa tu Gmail para contestar.",
            link=f"/app/crm?oportunidad={recipient.deal_id}" if recipient.deal_id
            else f"/app/campanas/{campaign.id}",
        )  # fmt: skip
        if _emit(session, campaign, recipient, "campaign.replied"):
            return  # una automatización decide qué hacer (en vez de la tarea fija)
    task = {
        "reunion": (timedelta(hours=1), f"Agendar la reunión con {name}: pidió reunirse"),
        "interesado": (timedelta(hours=2), f"Responder a {name}: está interesado"),
        "mas_info": (timedelta(hours=2), f"Enviar la información que pidió {name}"),
        "referido": (timedelta(hours=4), f"Escribir a la persona que refirió {name}"),
        "no_ahora": (timedelta(days=60), f"Retomar contacto con {name} (pidió más adelante)"),
        "otro": (timedelta(hours=4), f"Responder a {name}"),
    }.get(category)
    if task is not None:
        crm_log(session, tenant_id=tenant, kind="task", user_id=owner,
                contact_id=recipient.contact_id, deal_id=recipient.deal_id,
                due_at=now() + task[0], body=f"{task[1]} (campaña «{campaign.name}»)")  # fmt: skip


BLOCKED_NOTE = "Bloqueado por el filtro de seguridad del destinatario (la dirección puede existir)"
NOT_FOUND_NOTE = "Dirección inexistente"


def _undo_bounce(session: Session, campaign: Campaign, recipient: Recipient) -> None:
    """Un «bloqueado» que se contó como rebote: se deshace lo aprendido por error."""
    from orkesta.research.extract import detect_pattern
    from orkesta.research.memory import unlearn_bounce

    recipient.status, recipient.error = "blocked", BLOCKED_NOTE
    session.execute(
        delete(Suppression).where(
            Suppression.tenant_id == campaign.tenant_id,
            Suppression.email == recipient.email,
            Suppression.reason == "bounced",
        )
    )
    contact = session.get(Contact, recipient.contact_id)
    if contact is None:
        return
    pattern = detect_pattern(contact.first_name or "", contact.last_name or "", recipient.email)
    unlearn_bounce(session, recipient.email, pattern or "")
    reason = contact.email_check_reason or ""
    if contact.email != recipient.email and reason.startswith(recipient.email):
        contact.email, contact.email_status = recipient.email, "estimado"
        contact.email_check_reason = BLOCKED_NOTE[:300]
    elif contact.email == recipient.email and contact.email_status == "invalido":
        contact.email_status, contact.email_check_reason = "estimado", BLOCKED_NOTE[:300]


AUTO_PAUSE_MIN = 20  # envíos antes de evaluar
AUTO_PAUSE_RATE = 0.15  # rebotes + bloqueos sobre contactados


def _guard(session: Session, campaign: Campaign, mailbox: Mailbox) -> None:
    """Protege la reputación del buzón: pausa la campaña si rebota o la bloquean demasiado."""
    if campaign.status != "active" or campaign.settings.get("auto_paused_at"):
        return
    contacted, failed = session.execute(
        select(
            func.count().filter(Recipient.sent_count > 0),
            func.count().filter(Recipient.status.in_(("bounced", "blocked"))),
        ).where(Recipient.campaign_id == campaign.id)
    ).one()
    if contacted < AUTO_PAUSE_MIN or failed / contacted < AUTO_PAUSE_RATE:
        return
    campaign.status = "paused"
    campaign.settings = {**campaign.settings, "auto_paused_at": now().isoformat()}
    share = round(100 * failed / contacted)
    notify(
        session, tenant_id=campaign.tenant_id, user_id=mailbox.user_id or campaign.created_by,
        kind="reply", title=f"Pausamos «{campaign.name}»: {share} % de rebotes",
        body="Para cuidar la reputación de tu correo. Revisa los rebotes antes de reanudar.",
        link=f"/app/campanas/{campaign.id}",
    )  # fmt: skip


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
                    limit = effective_limit(session, mailbox)[0]
                    mailbox.next_send_after = now() + _gap(campaign.settings, limit)
                    sent += 1
                if mailbox.status == "active":
                    check_replies(session, campaign, mailbox)
                    _guard(session, campaign, mailbox)
                _finish_if_done(session, campaign)
    return sent


class Scheduler(threading.Thread):
    def __init__(self, factory: sessionmaker[Session], every: float = 10.0):
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
                try:
                    from orkesta.research.directory import maybe_refresh

                    maybe_refresh(self.factory)
                except Exception:
                    logger.exception("directory_refresh_failed")

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
