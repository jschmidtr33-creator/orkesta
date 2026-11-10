"""Calendario de cada persona, sincronizado con su Google Calendar en ambos sentidos.

- Desde Google: cada pocos minutos se traen los eventos nuevos o cambiados (token de
  sincronización de Google, sin volver a leer todo). Si un invitado es un contacto de la
  persona, el evento queda ligado; si además estaba en una de sus campañas, ORKESTA entiende
  que el lead agendó una reunión: detiene su secuencia, crea la oportunidad y avisa.
- Hacia Google: lo que se crea, cambia o borra en ORKESTA se hace primero en Google Calendar
  (si Google no lo acepta, no se guarda nada a medias).

Los eventos son privados: solo los ve su dueño (ni la administración).
"""

from __future__ import annotations

import logging
import re
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import or_, select
from sqlalchemy.orm import Session, sessionmaker

from orkesta.calendar import google
from orkesta.calendar.models import CalendarEvent
from orkesta.campaigns.models import Campaign, Mailbox, Recipient
from orkesta.core.db import set_context
from orkesta.crm.models import Deal
from orkesta.crm.service import log as crm_log
from orkesta.data.models import Contact
from orkesta.platform.models import Tenant

logger = logging.getLogger(__name__)
SYNC_EVERY = timedelta(minutes=5)
_TAGS = re.compile(r"<[^>]+>")


class CalendarServiceError(Exception):
    def __init__(self, message: str, status: int = 400):
        super().__init__(message)
        self.status = status


def now() -> datetime:
    return datetime.now(UTC)


def calendar_mailbox(session: Session, tenant_id: uuid.UUID, user_id: uuid.UUID) -> Mailbox | None:
    return session.scalar(
        select(Mailbox)
        .where(
            Mailbox.tenant_id == tenant_id,
            Mailbox.user_id == user_id,
            Mailbox.provider == "gmail",
            Mailbox.calendar_enabled.is_(True),
        )
        .order_by(Mailbox.created_at)
        .limit(1)
    )


def _token(session: Session, mailbox: Mailbox) -> str:
    from orkesta.campaigns.service import access_token

    try:
        return access_token(session, mailbox)
    except Exception as exc:
        raise CalendarServiceError(
            "Google revocó el permiso de tu cuenta; vuelve a conectar Gmail.", 409
        ) from exc


# --- Desde Google ---------------------------------------------------------------------------


def _attendees(item: dict[str, Any], own_email: str) -> list[dict[str, Any]]:
    out = []
    for a in item.get("attendees") or []:
        email = str(a.get("email") or "").lower()
        if not email or a.get("self") or email == own_email.lower() or a.get("resource"):
            continue
        out.append(
            {
                "email": email,
                "name": str(a.get("displayName") or ""),
                "status": str(a.get("responseStatus") or ""),
            }
        )
    return out


def _meet(item: dict[str, Any]) -> str:
    if item.get("hangoutLink"):
        return str(item["hangoutLink"])[:500]
    for entry in (item.get("conferenceData") or {}).get("entryPoints") or []:
        if entry.get("entryPointType") == "video" and entry.get("uri"):
            return str(entry["uri"])[:500]
    return ""


def _match_contact(session: Session, event: CalendarEvent) -> Contact | None:
    emails = [a["email"] for a in event.attendees if a.get("email")]
    if not emails:
        return None
    return session.scalar(
        select(Contact)
        .where(
            Contact.tenant_id == event.tenant_id,
            Contact.email.in_(emails),
            or_(Contact.owner_user_id == event.user_id, Contact.owner_user_id.is_(None)),
        )
        .order_by(Contact.owner_user_id.is_(None))
        .limit(1)
    )


def _when_text(event: CalendarEvent) -> str:
    return (
        event.start_at.strftime("%d-%m-%Y")
        if event.all_day
        else (event.start_at.strftime("%d-%m-%Y %H:%M UTC"))
    )


def _meeting_booked(session: Session, event: CalendarEvent, contact: Contact) -> None:
    """Un contacto de la persona quedó invitado a una reunión suya que viene de Google: si
    estaba en una de sus campañas, agendó una reunión."""
    from orkesta.campaigns.service import handle_reply

    row = session.execute(
        select(Recipient, Campaign)
        .join(Campaign, Campaign.id == Recipient.campaign_id)
        .where(
            Recipient.tenant_id == event.tenant_id,
            Recipient.contact_id == contact.id,
            Campaign.created_by == event.user_id,
        )
        .order_by(Recipient.sent_count.desc(), Campaign.created_at.desc())
        .limit(1)
    ).first()
    when = _when_text(event)
    if row is None:
        crm_log(
            session,
            tenant_id=event.tenant_id,
            kind="meeting",
            user_id=event.user_id,
            contact_id=contact.id,
            body=f"Reunión agendada: {event.title} · {when}",
        )
        return
    recipient, campaign = row
    event.campaign_id = campaign.id
    if recipient.status in ("pending", "active", "completed"):
        recipient.status, recipient.replied_at, recipient.next_send_at = "replied", now(), None
    if recipient.reply_category not in ("reunion",):
        handle_reply(
            session,
            campaign,
            recipient,
            "reunion",
            f"Agendó una reunión: {event.title} ({when})",
            event.user_id,
        )
    event.deal_id = recipient.deal_id


def upsert_from_google(session: Session, mailbox: Mailbox, item: dict[str, Any]) -> None:
    google_id = str(item.get("id") or "")
    if not google_id or mailbox.user_id is None:
        return
    event = session.scalar(
        select(CalendarEvent).where(
            CalendarEvent.user_id == mailbox.user_id, CalendarEvent.google_id == google_id
        )
    )
    if item.get("status") == "cancelled":
        if event is not None:
            event.status = "cancelled"
        return
    start, all_day = google.parse_time(item.get("start"))
    end, _ = google.parse_time(item.get("end"))
    if start is None:
        return
    new = event is None
    if event is None:
        event = CalendarEvent(
            tenant_id=mailbox.tenant_id,
            user_id=mailbox.user_id,
            mailbox_id=mailbox.id,
            google_id=google_id,
            source="google",
        )
        session.add(event)
    event.title = str(item.get("summary") or "(Sin título)")[:300]
    event.description = str(item.get("description") or "")[:20000]
    event.location = str(item.get("location") or "")[:500]
    event.start_at, event.end_at, event.all_day = start, end or start + timedelta(hours=1), all_day
    event.attendees = _attendees(item, mailbox.email)
    event.meet_url = _meet(item)
    event.html_link = str(item.get("htmlLink") or "")[:1000]
    event.status = "tentative" if item.get("status") == "tentative" else "confirmed"
    session.flush()
    if event.contact_id is None:
        contact = _match_contact(session, event)
        if contact is not None:
            event.contact_id = contact.id
            if new and event.source == "google" and event.end_at >= now() - timedelta(days=1):
                _meeting_booked(session, event, contact)


def sync_mailbox(session: Session, mailbox: Mailbox) -> int:
    """Trae los cambios de Google Calendar de un buzón. Devuelve cuántos eventos llegaron."""
    try:
        token = _token(session, mailbox)
        try:
            items, next_token = google.list_changes(token, mailbox.calendar_sync_token)
        except google.CalendarError as exc:
            if not exc.gone:
                raise
            mailbox.calendar_sync_token = None
            items, next_token = google.list_changes(token, None)
    except (google.CalendarError, CalendarServiceError) as exc:
        mailbox.calendar_error = str(exc)[:300]
        return 0
    for item in items:
        with session.begin_nested():
            upsert_from_google(session, mailbox, item)
    mailbox.calendar_sync_token = next_token or mailbox.calendar_sync_token
    mailbox.calendar_synced_at, mailbox.calendar_error = now(), None
    return len(items)


def sync_all(factory: sessionmaker[Session]) -> int:
    """Una vuelta para todas las empresas (la llama el programador cada pocos minutos)."""
    total = 0
    with factory() as session:
        tenants = list(session.scalars(select(Tenant.id).where(
            or_(Tenant.plan != "demo", Tenant.trial_ends_at.is_(None),
                Tenant.trial_ends_at > now()))))  # fmt: skip
    for tenant_id in tenants:
        with factory.begin() as session:
            set_context(session, tenant_id=tenant_id)
            due = now() - SYNC_EVERY
            mailboxes = session.scalars(
                select(Mailbox).where(
                    Mailbox.tenant_id == tenant_id,
                    Mailbox.calendar_enabled.is_(True),
                    Mailbox.status == "active",
                    or_(Mailbox.calendar_synced_at.is_(None), Mailbox.calendar_synced_at < due),
                )
            ).all()
            for mailbox in mailboxes:
                try:
                    total += sync_mailbox(session, mailbox)
                except Exception:
                    logger.exception("calendar_sync_failed %s", mailbox.id)
    return total


# --- Hacia Google ---------------------------------------------------------------------------


def _clean_attendees(
    session: Session, tenant_id: uuid.UUID, data: dict[str, Any], contact: Contact | None
) -> list[dict[str, Any]]:
    seen: set[str] = set()
    out: list[dict[str, Any]] = []
    raw = list(data.get("attendees") or [])
    if contact is not None and contact.email:
        raw.insert(0, {"email": contact.email, "name": contact.full_name or ""})
    for a in raw[:50]:
        email = str((a or {}).get("email") or "").strip().lower()
        if not re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", email) or email in seen:
            continue
        seen.add(email)
        out.append({"email": email, "name": str(a.get("name") or "")[:160], "status": ""})
    return out


def _times(data: dict[str, Any]) -> tuple[datetime, datetime, bool]:
    try:
        start = datetime.fromisoformat(str(data["start_at"]).replace("Z", "+00:00"))
        end = datetime.fromisoformat(str(data["end_at"]).replace("Z", "+00:00"))
    except (KeyError, ValueError) as exc:
        raise CalendarServiceError("Indica la fecha y la hora de inicio y de fin.") from exc
    if start.tzinfo is None or end.tzinfo is None:
        raise CalendarServiceError("La hora debe incluir la zona horaria.")
    if end <= start:
        raise CalendarServiceError("La reunión debe terminar después de empezar.")
    return start, end, bool(data.get("all_day"))


def _contact(
    session: Session, tenant_id: uuid.UUID, user_id: uuid.UUID, contact_id: Any
) -> Contact | None:
    if not contact_id:
        return None
    contact = session.get(Contact, uuid.UUID(str(contact_id)))
    if (
        contact is None
        or contact.tenant_id != tenant_id
        or (contact.owner_user_id not in (None, user_id))
    ):
        raise CalendarServiceError("Ese contacto no existe.", 404)
    return contact


def _apply(event: CalendarEvent, result: dict[str, Any]) -> None:
    event.google_id = str(result.get("id") or event.google_id or "") or None
    event.meet_url = _meet(result) or event.meet_url
    event.html_link = str(result.get("htmlLink") or event.html_link)[:1000]


def create_event(
    session: Session, *, tenant_id: uuid.UUID, user_id: uuid.UUID, data: dict[str, Any]
) -> CalendarEvent:
    mailbox = calendar_mailbox(session, tenant_id, user_id)
    if mailbox is None:
        raise CalendarServiceError(
            "Conecta tu calendario de Google para agendar desde ORKESTA.", 409
        )
    title = " ".join(str(data.get("title") or "").split())[:300]
    if not title:
        raise CalendarServiceError("Ponle un título a la reunión.")
    start, end, all_day = _times(data)
    contact = _contact(session, tenant_id, user_id, data.get("contact_id"))
    attendees = _clean_attendees(session, tenant_id, data, contact)
    description = str(data.get("description") or "")[:20000]
    location = str(data.get("location") or "")[:500]
    payload = google.body(
        title=title,
        description=description,
        location=location,
        start_at=start,
        end_at=end,
        all_day=all_day,
        attendees=attendees,
        meet=bool(data.get("meet")),
    )
    try:
        result = google.insert(_token(session, mailbox), payload, bool(data.get("notify", True)))
    except google.CalendarError as exc:
        raise CalendarServiceError(str(exc), 502) from exc
    deal_id = None
    if contact is not None:
        deal_id = session.scalar(
            select(Deal.id).where(Deal.contact_id == contact.id, Deal.status == "open").limit(1)
        )
    event = CalendarEvent(
        tenant_id=tenant_id,
        user_id=user_id,
        mailbox_id=mailbox.id,
        title=title,
        description=description,
        location=location,
        start_at=start,
        end_at=end,
        all_day=all_day,
        attendees=attendees,
        status="confirmed",
        source="orkesta",
        contact_id=contact.id if contact else None,
        deal_id=deal_id,
    )
    _apply(event, result)
    session.add(event)
    session.flush()
    if contact is not None:
        crm_log(
            session,
            tenant_id=tenant_id,
            kind="meeting",
            user_id=user_id,
            contact_id=contact.id,
            deal_id=deal_id,
            body=f"Reunión agendada: {title} · {_when_text(event)}",
        )
    return event


def own_event(
    session: Session, tenant_id: uuid.UUID, user_id: uuid.UUID, event_id: uuid.UUID
) -> CalendarEvent:
    event = session.get(CalendarEvent, event_id)
    if event is None or event.tenant_id != tenant_id or event.user_id != user_id:
        raise CalendarServiceError("Esa reunión no existe.", 404)
    return event


def update_event(session: Session, event: CalendarEvent, data: dict[str, Any]) -> CalendarEvent:
    mailbox = calendar_mailbox(session, event.tenant_id, event.user_id)
    if mailbox is None or not event.google_id:
        raise CalendarServiceError("Conecta tu calendario de Google para editar esta reunión.", 409)
    title = " ".join(str(data.get("title") or event.title).split())[:300]
    merged = {
        "start_at": data.get("start_at") or event.start_at.isoformat(),
        "end_at": data.get("end_at") or event.end_at.isoformat(),
        "all_day": data.get("all_day", event.all_day),
    }
    start, end, all_day = _times(merged)
    contact = _contact(
        session, event.tenant_id, event.user_id, data.get("contact_id", event.contact_id)
    )
    attendees = (
        _clean_attendees(session, event.tenant_id, data, contact)
        if "attendees" in data or "contact_id" in data
        else event.attendees
    )
    description = str(data.get("description", event.description))[:20000]
    location = str(data.get("location", event.location))[:500]
    payload = google.body(
        title=title,
        description=description,
        location=location,
        start_at=start,
        end_at=end,
        all_day=all_day,
        attendees=attendees,
        meet=bool(data.get("meet")) and not event.meet_url,
    )
    try:
        result = google.patch(
            _token(session, mailbox), event.google_id, payload, bool(data.get("notify", True))
        )
    except google.CalendarError as exc:
        raise CalendarServiceError(str(exc), 502) from exc
    event.title, event.description, event.location = title, description, location
    event.start_at, event.end_at, event.all_day = start, end, all_day
    event.attendees, event.contact_id = attendees, contact.id if contact else None
    _apply(event, result)
    return event


def delete_event(session: Session, event: CalendarEvent, notify: bool = True) -> None:
    mailbox = calendar_mailbox(session, event.tenant_id, event.user_id)
    if mailbox is not None and event.google_id:
        try:
            google.delete(_token(session, mailbox), event.google_id, notify)
        except google.CalendarError as exc:
            raise CalendarServiceError(str(exc), 502) from exc
    event.status = "cancelled"


def event_dict(event: CalendarEvent, contact: Contact | None = None) -> dict[str, Any]:
    return {
        "id": str(event.id),
        "title": event.title,
        "description": _TAGS.sub(" ", event.description)[:2000] if event.description else "",
        "location": event.location,
        "start_at": event.start_at.isoformat(),
        "end_at": event.end_at.isoformat(),
        "all_day": event.all_day,
        "attendees": event.attendees,
        "meet_url": event.meet_url,
        "html_link": event.html_link,
        "status": event.status,
        "source": event.source,
        "contact": {
            "id": str(contact.id),
            "full_name": contact.full_name,
            "company_name": contact.company_name,
        }
        if contact
        else None,
        "deal_id": str(event.deal_id) if event.deal_id else None,
        "campaign_id": str(event.campaign_id) if event.campaign_id else None,
    }
