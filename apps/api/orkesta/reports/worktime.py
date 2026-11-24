"""Tiempo de trabajo del equipo en ORKESTA (solo para jefaturas y administración).

Desde la migración 0055 se mide minuto a minuto mientras la app está abierta y visible
(``presence_minutes``): «conectado» es el tiempo con ORKESTA abierto y «activo», los minutos en
que la persona usó el teclado, el ratón o la pantalla.

Para los días anteriores a la medición se estima con lo que ya existía:

- las renovaciones de sesión (cada ~15 minutos mientras la app está abierta y en uso) y
- las acciones que hizo la persona (registro de auditoría, notas, tareas, cambios de etapa,
  campañas lanzadas y propuestas enviadas).

Esos días se marcan como «estimado».
"""

from __future__ import annotations

import uuid
from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from sqlalchemy import func, or_, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from orkesta.platform.models import AuditLog, AuthSession, PresenceMinute
from orkesta.platform.service import now

SECTIONS = {
    "": "Inicio", "inicio": "Inicio", "prospeccion": "Prospección", "contactos": "Contactos",
    "campanas": "Campañas", "crm": "CRM", "propuestas": "Propuestas",
    "calendario": "Calendario", "automatizacion": "Automatización", "reportes": "Reportes",
    "equipo": "Mi equipo", "configuracion": "Configuración", "concertino": "Concertino",
    "admin": "Administración",
}  # fmt: skip
ACTIVITY_LABELS = {
    "note": "agregó una nota", "call": "registró una llamada", "meeting": "registró una reunión",
    "task": "creó una tarea", "stage": "movió una oportunidad de etapa",
}  # fmt: skip
BLOCK_GAP = timedelta(minutes=5)  # una pausa más larga corta el bloque de trabajo
SESSION_SPAN = timedelta(minutes=15)  # vida del acceso: renovación ≈ uso continuo
ACTION_SPAN = timedelta(minutes=5)  # cada acción cuenta como unos minutos de trabajo


def section_of(path: str) -> str:
    part = path.strip("/").split("/")[0].split("?")[0][:30]
    return part if part in SECTIONS else ""


def record(
    session: Session, tenant_id: uuid.UUID, user_id: uuid.UUID, *, active: bool, section: str
) -> None:
    """Suma el minuto actual (idempotente: varias pestañas cuentan una vez)."""
    minute = now().replace(second=0, microsecond=0)
    stmt = pg_insert(PresenceMinute).values(
        tenant_id=tenant_id, user_id=user_id, at=minute, active=active, section=section
    )
    session.execute(stmt.on_conflict_do_update(
        index_elements=["tenant_id", "user_id", "at"],
        set_={"active": PresenceMinute.active | stmt.excluded.active,
              "section": stmt.excluded.section},
    ))  # fmt: skip


@dataclass
class Event:
    at: datetime
    label: str
    kind: str  # accion | correo


def _merge(spans: list[tuple[datetime, datetime]]) -> list[tuple[datetime, datetime]]:
    out: list[tuple[datetime, datetime]] = []
    for start, end in sorted(spans):
        if out and start <= out[-1][1]:
            out[-1] = (out[-1][0], max(out[-1][1], end))
        else:
            out.append((start, end))
    return out


def _minutes(spans: list[tuple[datetime, datetime]]) -> int:
    return round(sum((e - s).total_seconds() for s, e in spans) / 60)


def _clip(
    spans: list[tuple[datetime, datetime]], lo: datetime, hi: datetime
) -> list[tuple[datetime, datetime]]:
    return [(max(s, lo), min(e, hi)) for s, e in spans if e > lo and s < hi]


def _bounds(day: date, tz: ZoneInfo) -> tuple[datetime, datetime]:
    start = datetime.combine(day, time.min, tz)
    return start, start + timedelta(days=1)


def actions(
    session: Session, tenant_id: uuid.UUID, uid: uuid.UUID, start: datetime, end: datetime
) -> list[Event]:
    """Lo que hizo la persona (no lo que ORKESTA hizo solo por ella)."""
    from orkesta.campaigns.models import Campaign
    from orkesta.crm.models import Activity
    from orkesta.platform.router import ACTION_LABELS
    from orkesta.proposals.models import Proposal

    out: list[Event] = []
    for at, action in session.execute(
        select(AuditLog.created_at, AuditLog.action).where(
            AuditLog.tenant_id == tenant_id, AuditLog.actor_user_id == uid,
            AuditLog.created_at >= start, AuditLog.created_at < end)
    ).all():  # fmt: skip
        out.append(Event(at, ACTION_LABELS.get(action, action.replace(".", " ")), "accion"))
    for kind, created, done in session.execute(
        select(Activity.type, Activity.created_at, Activity.done_at).where(
            Activity.tenant_id == tenant_id, Activity.user_id == uid,
            Activity.type.in_(list(ACTIVITY_LABELS)),
            or_(Activity.created_at.between(start, end), Activity.done_at.between(start, end)))
    ).all():  # fmt: skip
        if start <= created < end:
            out.append(Event(created, ACTIVITY_LABELS[kind], "accion"))
        if done is not None and start <= done < end and kind == "task":
            out.append(Event(done, "completó una tarea", "accion"))
    for name, at in session.execute(
        select(Campaign.name, Campaign.launched_at).where(
            Campaign.tenant_id == tenant_id, Campaign.created_by == uid,
            Campaign.launched_at >= start, Campaign.launched_at < end)
    ).all():  # fmt: skip
        out.append(Event(at, f"lanzó la campaña «{name}»", "accion"))
    for title, at in session.execute(
        select(Proposal.title, Proposal.sent_at).where(
            Proposal.tenant_id == tenant_id, Proposal.created_by == uid,
            Proposal.sent_at >= start, Proposal.sent_at < end)
    ).all():  # fmt: skip
        out.append(Event(at, f"envió la propuesta «{title or 'sin título'}»", "accion"))
    return sorted(out, key=lambda e: e.at)


def _emails(
    session: Session, tenant_id: uuid.UUID, uid: uuid.UUID, start: datetime, end: datetime
) -> list[datetime]:
    from orkesta.campaigns.models import Campaign, Message

    return list(session.scalars(
        select(Message.created_at).join(Campaign, Campaign.id == Message.campaign_id).where(
            Message.tenant_id == tenant_id, Campaign.created_by == uid,
            Message.created_at >= start, Message.created_at < end)
    ))  # fmt: skip


def _presence(
    session: Session, tenant_id: uuid.UUID, uid: uuid.UUID, start: datetime, end: datetime
) -> list[tuple[datetime, bool, str]]:
    return [
        (r.at, r.active, r.section)
        for r in session.execute(
            select(PresenceMinute.at, PresenceMinute.active, PresenceMinute.section)
            .where(PresenceMinute.tenant_id == tenant_id, PresenceMinute.user_id == uid,
                   PresenceMinute.at >= start, PresenceMinute.at < end)
            .order_by(PresenceMinute.at)
        ).all()
    ]  # fmt: skip


def _session_spans(
    session: Session, tenant_id: uuid.UUID, uid: uuid.UUID, start: datetime, end: datetime
) -> list[tuple[datetime, datetime]]:
    """Cada renovación de sesión cubre hasta la siguiente si fue seguida (uso continuo)."""
    spans = []
    for created, revoked in session.execute(
        select(AuthSession.created_at, AuthSession.revoked_at).where(
            AuthSession.user_id == uid,
            or_(AuthSession.tenant_id == tenant_id, AuthSession.tenant_id.is_(None)),
            AuthSession.created_at >= start - SESSION_SPAN, AuthSession.created_at < end)
    ).all():  # fmt: skip
        stop = created + SESSION_SPAN
        if revoked is not None and created < revoked <= created + SESSION_SPAN + timedelta(
            minutes=5
        ):
            stop = revoked
        spans.append((created, stop))
    return spans


def _blocks(rows: list[tuple[datetime, bool, str]]) -> list[dict[str, Any]]:
    blocks: list[dict[str, Any]] = []
    for at, active, section in rows:
        if blocks and at - blocks[-1]["_last"] <= BLOCK_GAP:
            b = blocks[-1]
        else:
            b = {"start": at, "_last": at, "minutes": 0, "active": 0, "_sections": Counter()}
            blocks.append(b)
        b["_last"] = at
        b["minutes"] += 1
        b["active"] += int(active)
        b["_sections"][section] += 1
    return [
        {
            "start": b["start"].isoformat(),
            "end": (b["_last"] + timedelta(minutes=1)).isoformat(),
            "minutes": b["minutes"],
            "active_minutes": b["active"],
            "sections": [SECTIONS.get(s, s) for s, _ in b["_sections"].most_common(3)],
        }
        for b in blocks
    ]


def _estimated(
    sessions: list[tuple[datetime, datetime]], events: list[Event], lo: datetime, hi: datetime
) -> tuple[list[tuple[datetime, datetime]], list[tuple[datetime, datetime]]]:
    work = [(e.at - ACTION_SPAN / 2, e.at + ACTION_SPAN / 2) for e in events if e.kind == "accion"]
    active = _clip(_merge(work), lo, hi)
    connected = _clip(_merge(sessions + work), lo, hi)
    return connected, active


def days(
    session: Session, tenant_id: uuid.UUID, uid: uuid.UUID, first: date, last: date, tz: ZoneInfo
) -> dict[str, Any]:
    """Un resumen por día, del más reciente al más antiguo."""
    start, _ = _bounds(first, tz)
    _, end = _bounds(last, tz)
    presence = _presence(session, tenant_id, uid, start, end)
    events = actions(session, tenant_id, uid, start, end)
    emails = _emails(session, tenant_id, uid, start, end)
    sessions = _session_spans(session, tenant_id, uid, start, end)
    by_day: dict[date, list[tuple[datetime, bool, str]]] = defaultdict(list)
    for row in presence:
        by_day[row[0].astimezone(tz).date()].append(row)
    acts: dict[date, list[Event]] = defaultdict(list)
    for e in events:
        acts[e.at.astimezone(tz).date()].append(e)
    sent = Counter(at.astimezone(tz).date() for at in emails)
    out: list[dict[str, Any]] = []
    day = last
    while day >= first:
        lo, hi = _bounds(day, tz)
        rows = by_day.get(day, [])
        if rows:
            item = {
                "connected_minutes": len(rows), "active_minutes": sum(1 for r in rows if r[1]),
                "first": rows[0][0].isoformat(),
                "last": (rows[-1][0] + timedelta(minutes=1)).isoformat(), "measured": True,
            }  # fmt: skip
        else:
            # Sin medición no se sabe cuánto lo usó de verdad: solo se estima el tiempo conectado.
            connected, _active = _estimated(_clip(sessions, lo, hi), acts.get(day, []), lo, hi)
            item = {
                "connected_minutes": _minutes(connected), "active_minutes": None,
                "first": connected[0][0].isoformat() if connected else None,
                "last": connected[-1][1].isoformat() if connected else None, "measured": False,
            }  # fmt: skip
        out.append({"day": day.isoformat(), **item, "actions": len(acts.get(day, [])),
                    "emails_sent": sent.get(day, 0)})  # fmt: skip
        day -= timedelta(days=1)
    worked = [d for d in out if d["connected_minutes"] > 0]
    measured = [d for d in worked if d["measured"]]
    return {
        "days": out,
        "summary": {
            "days_worked": len(worked),
            "connected_minutes": sum(d["connected_minutes"] for d in out),
            "active_minutes": sum(d["active_minutes"] or 0 for d in out),
            "avg_connected": round(sum(d["connected_minutes"] for d in worked) / len(worked))
            if worked else 0,
            "avg_active": round(sum(d["active_minutes"] for d in measured) / len(measured))
            if measured else None,
            "measured_days": len(measured),
            "actions": sum(d["actions"] for d in out),
            "measured_since": _measured_since(session, tenant_id, uid),
        },
    }  # fmt: skip


def _measured_since(session: Session, tenant_id: uuid.UUID, uid: uuid.UUID) -> str | None:
    first = session.scalar(select(func.min(PresenceMinute.at)).where(
        PresenceMinute.tenant_id == tenant_id, PresenceMinute.user_id == uid))  # fmt: skip
    return first.isoformat() if first else None


def day_detail(
    session: Session, tenant_id: uuid.UUID, uid: uuid.UUID, day: date, tz: ZoneInfo
) -> dict[str, Any]:
    """El historial de un día: bloques de conexión, en qué secciones y qué hizo."""
    lo, hi = _bounds(day, tz)
    rows = _presence(session, tenant_id, uid, lo, hi)
    events = actions(session, tenant_id, uid, lo, hi)
    emails = _emails(session, tenant_id, uid, lo, hi)
    if rows:
        blocks = _blocks(rows)
        sections = Counter(SECTIONS.get(r[2], r[2]) for r in rows)
    else:
        spans = _clip(_session_spans(session, tenant_id, uid, lo, hi), lo, hi)
        connected, _active = _estimated(spans, events, lo, hi)
        blocks = [
            {"start": s.isoformat(), "end": e.isoformat(), "minutes": _minutes([(s, e)]),
             "active_minutes": None, "sections": []}
            for s, e in connected
        ]  # fmt: skip
        sections = Counter()
    per_hour = Counter(
        at.astimezone(tz).replace(minute=0, second=0, microsecond=0) for at in emails
    )
    timeline = [{"at": e.at.isoformat(), "label": e.label, "kind": e.kind} for e in events]
    timeline += [
        {"at": hour.isoformat(), "kind": "correo",
         "label": f"sus campañas enviaron {n} correo{'s' if n != 1 else ''}"}
        for hour, n in per_hour.items()
    ]  # fmt: skip
    timeline.sort(key=lambda x: x["at"])
    return {
        "day": day.isoformat(),
        "measured": bool(rows),
        "blocks": blocks,
        "sections": [{"name": k, "minutes": v} for k, v in sections.most_common()],
        "timeline": timeline,
    }


def team_today(
    session: Session, tenant_id: uuid.UUID, uids: list[uuid.UUID], first: date, last: date,
    tz: ZoneInfo,
) -> dict[str, dict[str, Any]]:  # fmt: skip
    """Para la tabla de Mi equipo: promedio diario del período y lo de hoy."""
    out = {}
    for uid in uids:
        data = days(session, tenant_id, uid, first, last, tz)
        today = data["days"][0] if data["days"] else {}
        out[str(uid)] = {
            "avg_connected": data["summary"]["avg_connected"],
            "avg_active": data["summary"]["avg_active"],
            "days_worked": data["summary"]["days_worked"],
            "today_connected": today.get("connected_minutes", 0),
            "today_active": today.get("active_minutes") or 0,
        }
    return out
