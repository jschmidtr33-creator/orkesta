"""Inteligencia comercial propia del CRM (sin terceros).

- Salud de cada oportunidad (0-100): etapa, actividad reciente, tareas vencidas, respuestas,
  propuesta vista o aceptada y antigüedad frente al ciclo de venta real de la empresa.
- Pronóstico de cierre por mes, con probabilidades que aprende de las oportunidades cerradas.
- Atribución: qué campañas generaron oportunidades, ventas e ingresos.
"""

from __future__ import annotations

import uuid
from collections import defaultdict
from datetime import UTC, date, datetime, timedelta
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from orkesta.campaigns.models import Campaign, Recipient
from orkesta.crm.models import Activity, Deal, Stage


def _now() -> datetime:
    return datetime.now(UTC)


def _avg_cycle_days(session: Session, tenant_id: uuid.UUID) -> float | None:
    rows = session.execute(
        select(Deal.created_at, Deal.closed_at).where(
            Deal.tenant_id == tenant_id, Deal.status == "won", Deal.closed_at.is_not(None)
        )
    ).all()
    days = [(c - o).total_seconds() / 86400 for o, c in rows if c and o]
    return sum(days) / len(days) if len(days) >= 3 else None


def health(
    session: Session, tenant_id: uuid.UUID, deals: list[Deal]
) -> dict[uuid.UUID, dict[str, Any]]:
    """Salud de las oportunidades abiertas: «caliente», «tibia» o «fría», con sus razones."""
    from orkesta.proposals.models import Proposal

    open_deals = [d for d in deals if d.status == "open"]
    if not open_deals:
        return {}
    ids = [d.id for d in open_deals]
    contacts = [d.contact_id for d in open_deals if d.contact_id]
    last_activity = dict(session.execute(
        select(Activity.deal_id, func.max(Activity.created_at))
        .where(Activity.tenant_id == tenant_id, Activity.deal_id.in_(ids),
               Activity.type != "system")
        .group_by(Activity.deal_id)
    ).tuples().all())  # fmt: skip
    contact_activity = dict(session.execute(
        select(Activity.contact_id, func.max(Activity.created_at))
        .where(Activity.tenant_id == tenant_id, Activity.contact_id.in_(contacts),
               Activity.type.in_(("note", "call", "meeting", "email")))
        .group_by(Activity.contact_id)
    ).tuples().all()) if contacts else {}  # fmt: skip
    overdue = dict(session.execute(
        select(Activity.deal_id, func.count())
        .where(Activity.tenant_id == tenant_id, Activity.deal_id.in_(ids), Activity.type == "task",
               Activity.done_at.is_(None), Activity.due_at < _now())
        .group_by(Activity.deal_id)
    ).tuples().all())  # fmt: skip
    replied = set(session.scalars(
        select(Recipient.contact_id).where(Recipient.tenant_id == tenant_id,
                                           Recipient.contact_id.in_(contacts),
                                           Recipient.replied_at.is_not(None))
    )) if contacts else set()  # fmt: skip
    proposals: dict[uuid.UUID, str] = {}
    for deal_id, status in session.execute(
        select(Proposal.deal_id, Proposal.status).where(Proposal.tenant_id == tenant_id,
                                                         Proposal.deal_id.in_(ids))
    ).all():  # fmt: skip
        rank = {"accepted": 3, "viewed": 2, "sent": 1}
        if rank.get(status, 0) > rank.get(proposals.get(deal_id, ""), 0):
            proposals[deal_id] = status
    stages = {s.id: s for s in session.scalars(select(Stage).where(Stage.tenant_id == tenant_id))}
    cycle = _avg_cycle_days(session, tenant_id)
    out: dict[uuid.UUID, dict[str, Any]] = {}
    now = _now()
    for d in open_deals:
        stage = stages.get(d.stage_id)
        score = 20 + int((stage.probability if stage else 0) * 0.3)
        reasons: list[str] = []
        seen = [t for t in (last_activity.get(d.id), contact_activity.get(d.contact_id))
                if t is not None]  # fmt: skip
        last = max(seen) if seen else d.created_at
        idle = (now - last).days
        if idle <= 7:
            score += 20
            reasons.append("Actividad en los últimos 7 días")
        elif idle <= 21:
            score += 8
        else:
            score -= 15
            reasons.append(f"Sin actividad hace {idle} días")
        if overdue.get(d.id):
            score -= 15
            reasons.append(f"{overdue[d.id]} tarea(s) vencida(s)")
        if d.contact_id in replied:
            score += 12
            reasons.append("Respondió a una campaña")
        status = proposals.get(d.id)
        if status == "accepted":
            score += 25
            reasons.append("Aceptó la propuesta")
        elif status == "viewed":
            score += 15
            reasons.append("Vio la propuesta")
        elif status == "sent":
            score += 5
            reasons.append("Propuesta enviada, aún sin abrir")
        if d.value and d.value > 0:
            score += 5
        else:
            reasons.append("Sin valor estimado")
        age = (now - d.created_at).days
        if cycle and age > cycle * 1.5:
            score -= 10
            reasons.append(f"Lleva {age} días; tus ventas cierran en {round(cycle)} en promedio")
        if d.expected_close and d.expected_close < now.date():
            score -= 10
            reasons.append("La fecha estimada de cierre ya pasó")
        score = max(0, min(100, score))
        label = "caliente" if score >= 70 else "tibia" if score >= 45 else "fria"
        out[d.id] = {"score": score, "label": label, "reasons": reasons}
    return out


def learned_probabilities(
    session: Session, tenant_id: uuid.UUID
) -> dict[uuid.UUID, dict[str, Any]]:
    """Probabilidad de ganar desde cada etapa, aprendida de las oportunidades cerradas: de las
    que pasaron por la etapa, cuántas terminaron ganadas. Se mezcla con la configurada."""
    stages = list(session.scalars(select(Stage).where(Stage.tenant_id == tenant_id)))
    closed = {
        d.id: d.status
        for d in session.scalars(
            select(Deal).where(Deal.tenant_id == tenant_id, Deal.status.in_(("won", "lost")))
        )
    }
    passed: dict[str, set[uuid.UUID]] = defaultdict(set)
    if closed:
        for deal_id, meta in session.execute(
            select(Activity.deal_id, Activity.meta).where(
                Activity.tenant_id == tenant_id, Activity.type == "stage",
                Activity.deal_id.in_(list(closed)))
        ).all():  # fmt: skip
            for key in ("from", "to"):
                if meta and meta.get(key):
                    passed[str(meta[key])].add(deal_id)
    out: dict[uuid.UUID, dict[str, Any]] = {}
    for s in stages:
        configured = s.probability
        deals = passed.get(str(s.id), set())
        sample = len(deals)
        if s.kind != "open" or sample < 5:
            out[s.id] = {"probability": configured, "learned": None, "sample": sample}
            continue
        won = sum(1 for d in deals if closed.get(d) == "won")
        learned = round(100 * won / sample)
        weight = min(1.0, sample / 30)
        out[s.id] = {"probability": round(configured * (1 - weight) + learned * weight),
                     "learned": learned, "sample": sample}  # fmt: skip
    return out


def forecast(session: Session, tenant_id: uuid.UUID) -> dict[str, Any]:
    """Pronóstico de los próximos meses: ponderado, comprometido (≥70 %) y mejor caso."""
    probs = learned_probabilities(session, tenant_id)
    deals = list(session.scalars(select(Deal).where(Deal.tenant_id == tenant_id,
                                                    Deal.status == "open")))  # fmt: skip
    hp = health(session, tenant_id, deals)
    today = date.today()
    months = []
    for i in range(3):
        y, m = today.year + (today.month - 1 + i) // 12, (today.month - 1 + i) % 12 + 1
        months.append(f"{y}-{m:02d}")
    buckets: dict[str, dict[str, dict[str, float]]] = {}
    for d in deals:
        p = probs.get(d.stage_id, {}).get("probability", 0)
        h = hp.get(d.id, {}).get("score", 50)
        prob = max(0.0, min(1.0, p / 100 * (0.7 + 0.6 * h / 100)))  # la salud ajusta ±30 %
        if d.expected_close is None:
            key = "sin_fecha"
        else:
            month = d.expected_close.strftime("%Y-%m")
            key = "vencidas" if month < months[0] else (month if month in months else "despues")
        cur = buckets.setdefault(key, {}).setdefault(d.currency, {
            "weighted": 0.0, "commit": 0.0, "best": 0.0, "deals": 0})  # fmt: skip
        value = float(d.value or 0)
        cur["weighted"] += value * prob
        cur["best"] += value
        cur["deals"] += 1
        if prob >= 0.7:
            cur["commit"] += value
    order = ["vencidas", *months, "despues", "sin_fecha"]
    return {
        "periods": [
            {
                "key": k,
                "totals": {
                    c: {**v, "weighted": round(v["weighted"], 2)} for c, v in buckets[k].items()
                },
            }
            for k in order
            if k in buckets
        ],
        "stages": {str(k): v for k, v in probs.items()},
        "won_last_90": _won_since(session, tenant_id, 90),
    }


def _won_since(session: Session, tenant_id: uuid.UUID, days: int) -> dict[str, float]:
    rows = session.execute(
        select(Deal.currency, func.coalesce(func.sum(Deal.value), 0)).where(
            Deal.tenant_id == tenant_id, Deal.status == "won",
            Deal.closed_at >= _now() - timedelta(days=days))
        .group_by(Deal.currency)
    ).all()  # fmt: skip
    return {c: float(v) for c, v in rows}


def attribution(session: Session, tenant_id: uuid.UUID) -> list[dict[str, Any]]:
    """Oportunidades, ventas e ingresos que generó cada campaña: la oportunidad nació de una
    respuesta a la campaña, o el contacto respondió a la campaña antes de crearse."""
    deals = {d.id: d for d in session.scalars(select(Deal).where(Deal.tenant_id == tenant_id))}
    if not deals:
        return []
    by_contact: dict[uuid.UUID, list[Deal]] = defaultdict(list)
    for d in deals.values():
        if d.contact_id:
            by_contact[d.contact_id].append(d)
    credited: dict[uuid.UUID, uuid.UUID] = {}  # deal → campaña
    rows = session.execute(
        select(Recipient.campaign_id, Recipient.contact_id, Recipient.deal_id, Recipient.replied_at)
        .where(Recipient.tenant_id == tenant_id, Recipient.replied_at.is_not(None))
        .order_by(Recipient.replied_at)
    ).all()  # fmt: skip
    for campaign_id, contact_id, deal_id, replied_at in rows:
        if deal_id and deal_id in deals:
            credited.setdefault(deal_id, campaign_id)
        for d in by_contact.get(contact_id, []):
            if d.created_at >= replied_at - timedelta(days=1):
                credited.setdefault(d.id, campaign_id)
    found = session.execute(
        select(Campaign.id, Campaign.name, Campaign.created_by)
        .where(Campaign.id.in_(set(credited.values())))
    ).all() if credited else []  # fmt: skip
    names = {r[0]: r[1] for r in found}
    owners = {r[0]: str(r[2]) if r[2] else None for r in found}
    stats: dict[uuid.UUID, dict[str, Any]] = {}
    for deal_id, campaign_id in credited.items():
        d = deals[deal_id]
        row = stats.setdefault(campaign_id, {
            "campaign_id": str(campaign_id), "campaign": names.get(campaign_id, "—"),
            "owner_id": owners.get(campaign_id),
            "deals": 0, "won": 0, "lost": 0, "won_value": defaultdict(float),
            "pipeline": defaultdict(float)})  # fmt: skip
        row["deals"] += 1
        if d.status == "won":
            row["won"] += 1
            row["won_value"][d.currency] += float(d.value or 0)
        elif d.status == "lost":
            row["lost"] += 1
        else:
            row["pipeline"][d.currency] += float(d.value or 0)
    out = [{**r, "won_value": dict(r["won_value"]), "pipeline": dict(r["pipeline"])}
           for r in stats.values()]  # fmt: skip
    return sorted(out, key=lambda r: (-sum(r["won_value"].values()), -r["deals"]))
