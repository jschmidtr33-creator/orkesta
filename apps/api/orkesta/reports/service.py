"""Reportes: embudo comercial, actividad en el tiempo, campañas, vendedores y pipeline.

Todas las cifras salen de los datos reales del espacio de trabajo en el período elegido;
nada se estima. El período anterior (misma duración) sirve para comparar.
"""

from __future__ import annotations

import uuid
from collections import defaultdict
from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from sqlalchemy import Select, and_, distinct, func, or_, select
from sqlalchemy.orm import Session

from orkesta.campaigns.models import Campaign, Mailbox, Message, Recipient
from orkesta.crm.models import Activity, Deal, Stage
from orkesta.data.models import Contact
from orkesta.platform.models import Membership, User
from orkesta.proposals.models import Proposal

MAX_DAYS = 366


@dataclass
class Period:
    start: datetime
    end: datetime  # exclusivo
    tz: ZoneInfo

    @property
    def days(self) -> int:
        return max(1, round((self.end - self.start).total_seconds() / 86400))

    def previous(self) -> Period:
        span = self.end - self.start
        return Period(self.start - span, self.start, self.tz)


def period(desde: date | None, hasta: date | None, tz_name: str) -> Period:
    try:
        tz = ZoneInfo(tz_name)
    except Exception:
        tz = ZoneInfo("America/Bogota")
    today = datetime.now(tz).date()
    end_day = min(hasta or today, today)
    start_day = desde or end_day - timedelta(days=29)
    if start_day > end_day:
        start_day, end_day = end_day, start_day
    start_day = max(start_day, end_day - timedelta(days=MAX_DAYS - 1))
    start = datetime.combine(start_day, time.min, tz).astimezone(UTC)
    end = datetime.combine(end_day + timedelta(days=1), time.min, tz).astimezone(UTC)
    return Period(start, end, tz)


def _between(column: Any, p: Period) -> Any:
    return and_(column >= p.start, column < p.end)


def _count(session: Session, query: Select[Any]) -> int:
    return int(session.scalar(query) or 0)


class Scope:
    """Filtros comunes: empresa y, si se elige, un vendedor."""

    def __init__(self, tenant_id: uuid.UUID, user_id: uuid.UUID | None):
        self.tenant_id, self.user_id = tenant_id, user_id

    def mailboxes(self) -> Any:
        return select(Mailbox.id).where(
            Mailbox.tenant_id == self.tenant_id, Mailbox.user_id == self.user_id
        )

    def messages(self, query: Select[Any]) -> Select[Any]:
        query = query.where(Message.tenant_id == self.tenant_id)
        return query.where(Message.mailbox_id.in_(self.mailboxes())) if self.user_id else query

    def recipients(self, query: Select[Any]) -> Select[Any]:
        query = query.where(Recipient.tenant_id == self.tenant_id)
        if self.user_id:
            query = query.where(
                Recipient.campaign_id.in_(
                    select(Campaign.id).where(
                        or_(Campaign.mailbox_id.in_(self.mailboxes()),
                            Campaign.created_by == self.user_id)
                    )
                )
            )  # fmt: skip
        return query

    def contacts(self, query: Select[Any]) -> Select[Any]:
        query = query.where(Contact.tenant_id == self.tenant_id)
        return query.where(Contact.owner_user_id == self.user_id) if self.user_id else query

    def deals(self, query: Select[Any]) -> Select[Any]:
        query = query.where(Deal.tenant_id == self.tenant_id)
        return query.where(Deal.owner_user_id == self.user_id) if self.user_id else query

    def proposals(self, query: Select[Any]) -> Select[Any]:
        query = query.where(Proposal.tenant_id == self.tenant_id)
        return query.where(Proposal.created_by == self.user_id) if self.user_id else query

    def activities(self, query: Select[Any]) -> Select[Any]:
        query = query.where(Activity.tenant_id == self.tenant_id)
        return query.where(Activity.user_id == self.user_id) if self.user_id else query


def _money(rows: Any) -> dict[str, float]:
    return {str(c): round(float(v or 0), 2) for c, v in rows if v}


def kpis(session: Session, s: Scope, p: Period) -> dict[str, Any]:
    contacted = _count(session, s.messages(
        select(func.count(distinct(Message.recipient_id))).where(
            Message.step == 0, _between(Message.sent_at, p))))  # fmt: skip
    replied = _count(session, s.recipients(
        select(func.count()).where(_between(Recipient.replied_at, p))))  # fmt: skip
    won = _count(session, s.deals(select(func.count()).where(
        Deal.status == "won", _between(Deal.closed_at, p))))  # fmt: skip
    lost = _count(session, s.deals(select(func.count()).where(
        Deal.status == "lost", _between(Deal.closed_at, p))))  # fmt: skip
    won_value = session.execute(s.deals(
        select(Deal.currency, func.sum(Deal.value))
        .where(Deal.status == "won", _between(Deal.closed_at, p)).group_by(Deal.currency)
    )).all()  # fmt: skip
    return {
        "contacts_added": _count(session, s.contacts(
            select(func.count()).where(_between(Contact.created_at, p)))),
        "emails_sent": _count(session, s.messages(
            select(func.count()).where(_between(Message.sent_at, p)))),
        "contacted": contacted,
        "opened": _count(session, s.recipients(
            select(func.count()).where(_between(Recipient.opened_at, p)))),
        "replied": replied,
        "reply_rate": round(100 * replied / contacted, 1) if contacted else None,
        "bounced": _count(session, s.recipients(select(func.count()).where(
            Recipient.status == "bounced", _between(Recipient.checked_at, p)))),
        "deals_created": _count(session, s.deals(
            select(func.count()).where(_between(Deal.created_at, p)))),
        "proposals_sent": _count(session, s.proposals(
            select(func.count()).where(_between(Proposal.sent_at, p)))),
        "won": won,
        "lost": lost,
        "win_rate": round(100 * won / (won + lost), 1) if won + lost else None,
        "won_value": _money(won_value),
        "activities": _count(session, s.activities(select(func.count()).where(
            Activity.type.in_(("call", "meeting", "note", "email")),
            _between(Activity.created_at, p)))),
    }  # fmt: skip


def _bucket(p: Period) -> str:
    return "day" if p.days <= 62 else "week"


def series(session: Session, s: Scope, p: Period) -> dict[str, Any]:
    unit = _bucket(p)
    tzname = str(p.tz)

    def grouped(query: Select[Any], column: Any) -> dict[str, int]:
        key = func.date_trunc(unit, func.timezone(tzname, column))
        rows = session.execute(
            query.add_columns(key.label("k")).where(_between(column, p)).group_by("k")
        ).all()
        return {r.k.date().isoformat(): int(r[0]) for r in rows}

    sent = grouped(s.messages(select(func.count())), Message.sent_at)
    replies = grouped(s.recipients(select(func.count())), Recipient.replied_at)
    contacts = grouped(s.contacts(select(func.count())), Contact.created_at)
    won = grouped(s.deals(select(func.count()).where(Deal.status == "won")), Deal.closed_at)
    # Todas las fechas del período, aunque no haya actividad (ceros explícitos).
    first = p.start.astimezone(p.tz).date()
    last = (p.end - timedelta(seconds=1)).astimezone(p.tz).date()
    if unit == "week":
        first -= timedelta(days=first.weekday())
    step = timedelta(days=1 if unit == "day" else 7)
    points, cursor = [], first
    while cursor <= last:
        k = cursor.isoformat()
        points.append({"date": k, "emails_sent": sent.get(k, 0), "replies": replies.get(k, 0),
                       "contacts_added": contacts.get(k, 0), "won": won.get(k, 0)})  # fmt: skip
        cursor += step
    return {"unit": unit, "points": points}


def by_campaign(session: Session, s: Scope, p: Period) -> list[dict[str, Any]]:
    sent = dict(session.execute(s.messages(
        select(Message.campaign_id, func.count()).where(_between(Message.sent_at, p))
        .group_by(Message.campaign_id))).tuples().all())  # fmt: skip
    if not sent:
        return []
    contacted = dict(session.execute(s.messages(
        select(Message.campaign_id, func.count(distinct(Message.recipient_id)))
        .where(Message.step == 0, _between(Message.sent_at, p))
        .group_by(Message.campaign_id))).tuples().all())  # fmt: skip

    def per(column: Any, *extra: Any) -> dict[uuid.UUID, int]:
        return dict(session.execute(s.recipients(
            select(Recipient.campaign_id, func.count()).where(_between(column, p), *extra)
            .group_by(Recipient.campaign_id))).tuples().all())  # fmt: skip

    opened, clicked = per(Recipient.opened_at), per(Recipient.clicked_at)
    replied = per(Recipient.replied_at)
    bounced = per(Recipient.checked_at, Recipient.status == "bounced")
    campaigns = session.scalars(select(Campaign).where(Campaign.id.in_(sent.keys()))).all()
    rows = []
    for c in campaigns:
        base = contacted.get(c.id, 0)

        def rate(n: int, base: int = base) -> float | None:
            return round(100 * n / base, 1) if base else None

        rows.append({
            "id": str(c.id), "name": c.name, "status": c.status, "emails_sent": sent[c.id],
            "contacted": base, "opened": opened.get(c.id, 0), "clicked": clicked.get(c.id, 0),
            "replied": replied.get(c.id, 0), "bounced": bounced.get(c.id, 0),
            "open_rate": rate(opened.get(c.id, 0)), "click_rate": rate(clicked.get(c.id, 0)),
            "reply_rate": rate(replied.get(c.id, 0)),
        })  # fmt: skip
    return sorted(rows, key=lambda r: (-r["replied"], -r["emails_sent"]))


def by_user(session: Session, tenant_id: uuid.UUID, p: Period,
            only: uuid.UUID | None = None) -> list[dict[str, Any]]:  # fmt: skip
    members = session.execute(
        select(User.id, User.full_name, User.email, Membership.role)
        .join(Membership, Membership.user_id == User.id)
        .where(Membership.tenant_id == tenant_id, Membership.status == "active")
        .order_by(User.full_name)
    ).all()
    rows = []
    for uid, name, email, role in members:
        if only and uid != only:
            continue
        k = kpis(session, Scope(tenant_id, uid), p)
        rows.append({"user_id": str(uid), "name": name or email, "email": email, "role": role,
                     **{key: k[key] for key in ("contacts_added", "emails_sent", "contacted",
                        "replied", "reply_rate", "deals_created", "proposals_sent", "won",
                        "win_rate", "won_value", "activities")}})  # fmt: skip
    return rows


def pipeline(session: Session, s: Scope) -> list[dict[str, Any]]:
    rows = session.execute(s.deals(
        select(Stage.id, Stage.name, Stage.position, Stage.probability, Deal.currency,
               func.count(Deal.id), func.coalesce(func.sum(Deal.value), 0))
        .join(Stage, Stage.id == Deal.stage_id)
        .where(Deal.status == "open")
        .group_by(Stage.id, Stage.name, Stage.position, Stage.probability, Deal.currency)
    )).all()  # fmt: skip
    stages: dict[uuid.UUID, dict[str, Any]] = {}
    for sid, name, pos, prob, cur, n, value in rows:
        st = stages.setdefault(sid, {"id": str(sid), "name": name, "position": pos,
                                     "probability": prob, "deals": 0, "value": {}})  # fmt: skip
        st["deals"] += int(n)
        st["value"][cur] = round(float(value), 2)
    return sorted(stages.values(), key=lambda x: x["position"])


def lost_reasons(session: Session, s: Scope, p: Period) -> list[dict[str, Any]]:
    reason = func.coalesce(func.nullif(func.trim(Deal.lost_reason), ""), "Sin motivo")
    rows = session.execute(s.deals(
        select(reason, func.count())
        .where(Deal.status == "lost", _between(Deal.closed_at, p))
        .group_by(reason).order_by(func.count().desc()).limit(8)
    )).all()  # fmt: skip
    return [{"reason": r, "count": int(n)} for r, n in rows]


def sources(session: Session, s: Scope, p: Period) -> list[dict[str, Any]]:
    rows = session.execute(s.contacts(
        select(Contact.source, func.count()).where(_between(Contact.created_at, p))
        .group_by(Contact.source).order_by(func.count().desc())
    )).all()  # fmt: skip
    return [{"source": src, "count": int(n)} for src, n in rows]


def overview(session: Session, tenant_id: uuid.UUID, p: Period,
             user_id: uuid.UUID | None) -> dict[str, Any]:  # fmt: skip
    s = Scope(tenant_id, user_id)
    current, previous = kpis(session, s, p), kpis(session, s, p.previous())
    funnel = [
        {"key": "contacts_added", "label": "Contactos nuevos", "value": current["contacts_added"]},
        {"key": "contacted", "label": "Contactados por campaña", "value": current["contacted"]},
        {"key": "opened", "label": "Abrieron", "value": current["opened"]},
        {"key": "replied", "label": "Respondieron", "value": current["replied"]},
        {"key": "deals_created", "label": "Oportunidades creadas",
         "value": current["deals_created"]},
        {"key": "proposals_sent", "label": "Propuestas enviadas",
         "value": current["proposals_sent"]},
        {"key": "won", "label": "Negocios ganados", "value": current["won"]},
    ]  # fmt: skip
    totals: dict[str, float] = defaultdict(float)
    for row in pipeline(session, s):
        for cur, v in row["value"].items():
            totals[cur] += v
    return {
        "period": {
            "from": p.start.astimezone(p.tz).date().isoformat(),
            "to": (p.end - timedelta(seconds=1)).astimezone(p.tz).date().isoformat(),
            "days": p.days,
            "previous_from": p.previous().start.astimezone(p.tz).date().isoformat(),
        },
        "kpis": current,
        "previous": previous,
        "funnel": funnel,
        "series": series(session, s, p),
        "campaigns": by_campaign(session, s, p),
        "team": by_user(session, tenant_id, p, only=user_id),
        "pipeline": pipeline(session, s),
        "pipeline_total": dict(totals),
        "lost_reasons": lost_reasons(session, s, p),
        "sources": sources(session, s, p),
    }
