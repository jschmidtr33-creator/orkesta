"""Supervisión del equipo: qué trabaja cada persona a cargo, con cifras reales del período.

La jefatura ve solo a las personas que tiene asignadas; la administración, a todo el equipo.
Las políticas de la base (``scope_*``) garantizan además que ninguna cifra incluya trabajo de
personas fuera de su alcance.
"""

from __future__ import annotations

import uuid
from collections import defaultdict
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from orkesta.campaigns.models import Campaign, Recipient
from orkesta.campaigns.replies import POSITIVE
from orkesta.core.db import current_scope
from orkesta.crm.models import Deal, Stage
from orkesta.platform.models import AuditLog, Membership, User
from orkesta.reports.service import Period, Scope, _between, _count, kpis, overview
from orkesta.research.models import ResearchRun

KEYS = (
    "contacts_added", "emails_sent", "contacted", "opened", "replied", "reply_rate", "bounced",
    "deals_created", "proposals_sent", "won", "lost", "win_rate", "won_value", "activities",
)  # fmt: skip


def members(session: Session, tenant_id: uuid.UUID, me: uuid.UUID) -> list[Any]:
    """Personas que esta jefatura o administración supervisa (nunca se incluye a sí misma)."""
    scope = current_scope(session)
    query = (
        select(User.id, User.full_name, User.email, User.job_title, User.last_login_at,
               User.avatar_type, User.avatar_version, Membership.role, Membership.status)
        .join(Membership, Membership.user_id == User.id)
        .where(Membership.tenant_id == tenant_id, User.id != me)
        .order_by(User.full_name, User.email)
    )  # fmt: skip
    if scope:
        visible = [uuid.UUID(u) for u in scope.split(",")]
        query = query.where(User.id.in_(visible))
    return list(session.execute(query).all())


def _avatar(row: Any) -> str | None:
    return f"/api/avatars/{row.id}?v={row.avatar_version}" if row.avatar_type else None


def person_metrics(
    session: Session, tenant_id: uuid.UUID, uid: uuid.UUID, p: Period
) -> dict[str, Any]:
    s = Scope(tenant_id, uid)
    k = kpis(session, s, p)
    positive = _count(session, s.recipients(select(func.count()).where(
        Recipient.reply_category.in_(POSITIVE), _between(Recipient.replied_at, p))))  # fmt: skip
    searches = _count(session, select(func.count()).select_from(ResearchRun).where(
        ResearchRun.tenant_id == tenant_id, ResearchRun.created_by == uid,
        _between(ResearchRun.created_at, p)))  # fmt: skip
    active = _count(session, select(func.count()).select_from(Campaign).where(
        Campaign.tenant_id == tenant_id, Campaign.created_by == uid,
        Campaign.status == "active"))  # fmt: skip
    open_value: dict[str, float] = defaultdict(float)
    open_deals = 0
    for currency, n, value in session.execute(
        select(Deal.currency, func.count(), func.coalesce(func.sum(Deal.value), 0))
        .where(Deal.tenant_id == tenant_id, Deal.owner_user_id == uid, Deal.status == "open")
        .group_by(Deal.currency)
    ).all():
        open_deals += int(n)
        open_value[currency] += round(float(value), 2)
    last = session.scalar(
        select(func.max(AuditLog.created_at)).where(
            AuditLog.tenant_id == tenant_id, AuditLog.actor_user_id == uid
        )
    )
    return {
        **{key: k[key] for key in KEYS},
        "positive": positive,
        "searches": searches,
        "active_campaigns": active,
        "open_deals": open_deals,
        "open_value": dict(open_value),
        "last_action_at": last.isoformat() if last else None,
    }


def team_overview(
    session: Session, tenant_id: uuid.UUID, me: uuid.UUID, p: Period
) -> dict[str, Any]:
    people = []
    totals: dict[str, Any] = defaultdict(int)
    money: dict[str, dict[str, float]] = {"won_value": defaultdict(float),
                                         "open_value": defaultdict(float)}  # fmt: skip
    for row in members(session, tenant_id, me):
        m = person_metrics(session, tenant_id, row.id, p)
        people.append({
            "user_id": str(row.id), "name": row.full_name or row.email, "email": row.email,
            "job_title": row.job_title or "", "avatar_url": _avatar(row), "role": row.role,
            "active": row.status == "active",
            "last_login_at": row.last_login_at.isoformat() if row.last_login_at else None,
            **m,
        })  # fmt: skip
        for key, value in m.items():
            if key in money:
                for cur, v in value.items():
                    money[key][cur] += v
            elif isinstance(value, int) and not key.endswith("_rate"):
                totals[key] += value
    totals["reply_rate"] = (
        round(100 * totals["replied"] / totals["contacted"], 1) if totals["contacted"] else None
    )
    return {
        "period": {"from": p.start.astimezone(p.tz).date().isoformat(), "days": p.days},
        "people": people,
        "totals": {**totals, **{k: dict(v) for k, v in money.items()}},
    }


def can_supervise(session: Session, tenant_id: uuid.UUID, me: uuid.UUID, uid: uuid.UUID) -> bool:
    return any(row.id == uid for row in members(session, tenant_id, me))


def person_detail(
    session: Session, tenant_id: uuid.UUID, uid: uuid.UUID, p: Period
) -> dict[str, Any]:
    from orkesta.platform.router import ACTION_LABELS

    user = session.get(User, uid)
    report = overview(session, tenant_id, p, uid)
    deals = session.execute(
        select(Deal.id, Deal.title, Deal.value, Deal.currency, Deal.updated_at, Stage.name)
        .join(Stage, Stage.id == Deal.stage_id)
        .where(Deal.tenant_id == tenant_id, Deal.owner_user_id == uid, Deal.status == "open")
        .order_by(Deal.updated_at.desc())
        .limit(50)
    ).all()
    runs = session.scalars(
        select(ResearchRun)
        .where(ResearchRun.tenant_id == tenant_id, ResearchRun.created_by == uid)
        .order_by(ResearchRun.created_at.desc())
        .limit(15)
    ).all()
    log = session.scalars(
        select(AuditLog)
        .where(AuditLog.tenant_id == tenant_id, AuditLog.actor_user_id == uid)
        .order_by(AuditLog.created_at.desc())
        .limit(40)
    ).all()
    return {
        "person": {
            "user_id": str(uid),
            "name": (user.full_name or user.email) if user else "—",
            "email": user.email if user else "",
            "job_title": (user.job_title or "") if user else "",
        },
        "metrics": person_metrics(session, tenant_id, uid, p),
        "report": report,
        "deals": [
            {
                "id": str(d.id),
                "title": d.title,
                "value": float(d.value),
                "currency": d.currency,
                "stage": d.name,
                "updated_at": d.updated_at.isoformat(),
            }
            for d in deals
        ],
        "searches": [
            {
                "id": str(r.id),
                "summary": r.summary or "Búsqueda",
                "status": r.status,
                "found": int((r.totals or {}).get("people", 0) or 0),
                "saved_name": r.saved_name,
                "at": r.created_at.isoformat(),
            }
            for r in runs
        ],
        "timeline": [
            {
                "id": str(a.id),
                "at": a.created_at.isoformat(),
                "action": a.action,
                "label": ACTION_LABELS.get(a.action, a.action),
                "detail": a.detail,
            }
            for a in log
        ],
    }
