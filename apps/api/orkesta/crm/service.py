"""Reglas del CRM: etapas, oportunidades, actividades e indicadores."""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import UTC, date, datetime
from decimal import Decimal, InvalidOperation
from typing import Any

from sqlalchemy import func, select, text
from sqlalchemy.orm import Session

from orkesta.crm.models import (
    ACTIVITY_LABELS,
    CURRENCIES,
    DEFAULT_STAGES,
    Activity,
    Deal,
    Stage,
)
from orkesta.data.models import Company, Contact
from orkesta.platform.models import User


class CrmError(ValueError):
    def __init__(self, message: str, status: int = 400):
        super().__init__(message)
        self.status = status


def now() -> datetime:
    return datetime.now(UTC)


# --- Etapas ---------------------------------------------------------------------------------


def ensure_stages(session: Session, tenant_id: uuid.UUID) -> list[Stage]:
    def load() -> list[Stage]:
        return list(
            session.scalars(
                select(Stage).where(Stage.tenant_id == tenant_id).order_by(Stage.position)
            )
        )

    stages = load()
    if stages:
        return stages
    # Dos pestañas pueden pedir el pipeline a la vez: un candado por empresa evita duplicarlo.
    session.execute(
        text("SELECT pg_advisory_xact_lock(hashtext(:key))"), {"key": f"stages:{tenant_id}"}
    )
    stages = load()
    if stages:
        return stages
    for i, (name, kind, probability) in enumerate(DEFAULT_STAGES):
        session.add(
            Stage(tenant_id=tenant_id, name=name, kind=kind, position=i, probability=probability)
        )
    session.flush()
    return load()


def get_stage(session: Session, tenant_id: uuid.UUID, stage_id: uuid.UUID) -> Stage:
    stage = session.get(Stage, stage_id)
    if stage is None or stage.tenant_id != tenant_id:
        raise CrmError("Esa etapa no existe.", 404)
    return stage


def stages_summary(session: Session, tenant_id: uuid.UUID) -> list[dict[str, Any]]:
    stages = ensure_stages(session, tenant_id)
    rows = session.execute(
        select(Deal.stage_id, Deal.currency, func.count(), func.coalesce(func.sum(Deal.value), 0))
        .where(Deal.tenant_id == tenant_id)
        .group_by(Deal.stage_id, Deal.currency)
    ).all()
    counts: dict[uuid.UUID, int] = {}
    values: dict[uuid.UUID, dict[str, float]] = {}
    for stage_id, currency, n, total in rows:
        counts[stage_id] = counts.get(stage_id, 0) + int(n)
        values.setdefault(stage_id, {})[currency] = float(total)
    return [
        {
            "id": str(s.id),
            "name": s.name,
            "kind": s.kind,
            "position": s.position,
            "probability": s.probability,
            "deals": counts.get(s.id, 0),
            "value": values.get(s.id, {}),
        }
        for s in stages
    ]


def create_stage(
    session: Session, tenant_id: uuid.UUID, name: str, kind: str, probability: int
) -> Stage:
    stages = ensure_stages(session, tenant_id)
    name = " ".join(name.split())[:80]
    if len(name) < 2:
        raise CrmError("Escribe un nombre para la etapa.")
    if any(s.name.lower() == name.lower() for s in stages):
        raise CrmError("Ya existe una etapa con ese nombre.", 409)
    # Las etapas abiertas nuevas van antes de Ganado/Perdido.
    closed = [s for s in stages if s.kind != "open"]
    position = min((s.position for s in closed), default=len(stages))
    for s in stages:
        if s.position >= position:
            s.position += 1
    stage = Stage(
        tenant_id=tenant_id, name=name, kind=kind, position=position,
        probability=max(0, min(100, probability)),
    )  # fmt: skip
    session.add(stage)
    session.flush()
    return stage


def update_stage(
    session: Session,
    stage: Stage,
    *,
    name: str | None,
    probability: int | None,
) -> Stage:
    if name is not None:
        clean = " ".join(name.split())[:80]
        if len(clean) < 2:
            raise CrmError("Escribe un nombre para la etapa.")
        stage.name = clean
    if probability is not None:
        stage.probability = max(0, min(100, probability))
    return stage


def reorder_stages(session: Session, tenant_id: uuid.UUID, ids: list[uuid.UUID]) -> None:
    stages = {s.id: s for s in ensure_stages(session, tenant_id)}
    if set(ids) != set(stages):
        raise CrmError("La lista de etapas no coincide.")
    for i, stage_id in enumerate(ids):
        stages[stage_id].position = i


def delete_stage(session: Session, stage: Stage) -> None:
    used = session.scalar(select(func.count()).where(Deal.stage_id == stage.id))
    if used:
        raise CrmError("Mueve primero las oportunidades de esta etapa.", 409)
    same_kind = session.scalar(
        select(func.count()).where(Stage.tenant_id == stage.tenant_id, Stage.kind == stage.kind)
    )
    if (same_kind or 0) <= 1:
        raise CrmError("El pipeline necesita al menos una etapa de este tipo.", 409)
    session.delete(stage)


# --- Oportunidades --------------------------------------------------------------------------


@dataclass
class DealInput:
    title: str | None = None
    contact_id: uuid.UUID | None = None
    stage_id: uuid.UUID | None = None
    value: str | float | None = None
    currency: str | None = None
    expected_close: date | None = None
    owner_user_id: uuid.UUID | None = None
    lost_reason: str | None = None


def _money(value: str | float | None) -> Decimal:
    if value in (None, ""):
        return Decimal(0)
    try:
        amount = Decimal(str(value).replace(",", "")).quantize(Decimal("0.01"))
    except InvalidOperation as exc:
        raise CrmError("El valor debe ser un número.") from exc
    if amount < 0 or amount >= Decimal("1e12"):
        raise CrmError("El valor no es válido.")
    return amount


def get_deal(session: Session, tenant_id: uuid.UUID, deal_id: uuid.UUID) -> Deal:
    deal = session.get(Deal, deal_id)
    if deal is None or deal.tenant_id != tenant_id:
        raise CrmError("Esa oportunidad no existe.", 404)
    return deal


def log(
    session: Session,
    *,
    tenant_id: uuid.UUID,
    kind: str,
    body: str,
    user_id: uuid.UUID | None,
    contact_id: uuid.UUID | None = None,
    deal_id: uuid.UUID | None = None,
    due_at: datetime | None = None,
    meta: dict[str, Any] | None = None,
) -> Activity:
    activity = Activity(
        tenant_id=tenant_id, type=kind, body=body[:5000], user_id=user_id,
        contact_id=contact_id, deal_id=deal_id, due_at=due_at, meta=meta or {},
    )  # fmt: skip
    session.add(activity)
    session.flush()
    return activity


def _apply_stage(deal: Deal, stage: Stage) -> None:
    deal.stage_id = stage.id
    deal.status = stage.kind
    deal.closed_at = now() if stage.kind != "open" else None
    if stage.kind != "lost":
        deal.lost_reason = None


def create_deal(
    session: Session, *, tenant_id: uuid.UUID, user_id: uuid.UUID, data: DealInput
) -> Deal:
    stages = ensure_stages(session, tenant_id)
    contact = None
    if data.contact_id:
        contact = session.get(Contact, data.contact_id)
        if contact is None or contact.tenant_id != tenant_id:
            raise CrmError("Ese contacto no existe.", 404)
    stage = (
        get_stage(session, tenant_id, data.stage_id)
        if data.stage_id
        else next(s for s in stages if s.kind == "open")
    )
    title = " ".join((data.title or "").split())[:200]
    if not title:
        base = (contact.company_name or contact.full_name) if contact else ""
        title = f"Oportunidad · {base}" if base else "Nueva oportunidad"
    currency = (data.currency or "USD").upper()
    if currency not in CURRENCIES:
        raise CrmError("Moneda no soportada.")
    deal = Deal(
        tenant_id=tenant_id,
        title=title,
        contact_id=contact.id if contact else None,
        company_id=contact.company_id if contact else None,
        owner_user_id=data.owner_user_id or user_id,
        stage_id=stage.id,
        value=_money(data.value),
        currency=currency,
        expected_close=data.expected_close,
    )
    _apply_stage(deal, stage)
    session.add(deal)
    session.flush()
    if contact is not None and contact.status == "nuevo":
        contact.status = "en_proceso"
    log(
        session, tenant_id=tenant_id, kind="system", user_id=user_id, contact_id=deal.contact_id,
        deal_id=deal.id, body=f"Creó la oportunidad «{deal.title}» en «{stage.name}».",
    )  # fmt: skip
    return deal


def update_deal(session: Session, deal: Deal, *, user_id: uuid.UUID, data: DealInput) -> Deal:
    if data.title is not None:
        title = " ".join(data.title.split())[:200]
        if not title:
            raise CrmError("La oportunidad necesita un nombre.")
        deal.title = title
    if data.value is not None:
        deal.value = _money(data.value)
    if data.currency is not None:
        currency = data.currency.upper()
        if currency not in CURRENCIES:
            raise CrmError("Moneda no soportada.")
        deal.currency = currency
    if data.expected_close is not None:
        deal.expected_close = data.expected_close
    if data.owner_user_id is not None:
        deal.owner_user_id = data.owner_user_id
    if data.lost_reason is not None:
        deal.lost_reason = data.lost_reason.strip()[:300] or None
    if data.stage_id is not None and data.stage_id != deal.stage_id:
        move_deal(session, deal, stage_id=data.stage_id, user_id=user_id)
    return deal


def move_deal(
    session: Session,
    deal: Deal,
    *,
    stage_id: uuid.UUID,
    user_id: uuid.UUID | None,
    lost_reason: str = "",
) -> Deal:
    stage = get_stage(session, deal.tenant_id, stage_id)
    if stage.id == deal.stage_id:
        return deal
    previous = session.get(Stage, deal.stage_id)
    _apply_stage(deal, stage)
    if stage.kind == "lost" and lost_reason.strip():
        deal.lost_reason = lost_reason.strip()[:300]
    text = f"Movió «{deal.title}» de «{previous.name if previous else '—'}» a «{stage.name}»."
    if deal.lost_reason and stage.kind == "lost":
        text += f" Motivo: {deal.lost_reason}"
    log(
        session, tenant_id=deal.tenant_id, kind="stage", user_id=user_id,
        contact_id=deal.contact_id, deal_id=deal.id, body=text,
        meta={"from": str(previous.id) if previous else None, "to": str(stage.id)},
    )  # fmt: skip
    if stage.kind == "won" and deal.contact_id:
        contact = session.get(Contact, deal.contact_id)
        if contact is not None and contact.status in ("nuevo", "en_proceso"):
            contact.status = "validado" if contact.email_status == "valido" else contact.status
    return deal


def deal_dict(deal: Deal, contact: Contact | None, company: Company | None,
              owner: User | None, stage: Stage | None) -> dict[str, Any]:  # fmt: skip
    return {
        "id": str(deal.id),
        "title": deal.title,
        "status": deal.status,
        "value": float(deal.value),
        "currency": deal.currency,
        "expected_close": deal.expected_close.isoformat() if deal.expected_close else None,
        "lost_reason": deal.lost_reason,
        "stage_id": str(deal.stage_id),
        "stage": stage.name if stage else "",
        "probability": stage.probability if stage else 0,
        "contact": {
            "id": str(contact.id),
            "full_name": contact.full_name,
            "job_title": contact.job_title,
            "email": contact.email,
            "phone": contact.phone,
        }
        if contact
        else None,
        "company": company.name if company else (contact.company_name if contact else None),
        "owner": {"id": str(owner.id), "name": owner.full_name or owner.email} if owner else None,
        "created_at": deal.created_at.isoformat(),
        "updated_at": deal.updated_at.isoformat() if deal.updated_at else None,
        "closed_at": deal.closed_at.isoformat() if deal.closed_at else None,
    }


def list_deals(
    session: Session,
    tenant_id: uuid.UUID,
    *,
    q: str = "",
    owner_id: uuid.UUID | None = None,
    status: str = "",
    contact_id: uuid.UUID | None = None,
    limit: int = 1000,
) -> list[dict[str, Any]]:
    query = (
        select(Deal, Contact, Company, User, Stage)
        .outerjoin(Contact, Contact.id == Deal.contact_id)
        .outerjoin(Company, Company.id == Deal.company_id)
        .outerjoin(User, User.id == Deal.owner_user_id)
        .outerjoin(Stage, Stage.id == Deal.stage_id)
        .where(Deal.tenant_id == tenant_id)
        .order_by(Deal.updated_at.desc())
        .limit(limit)
    )
    if owner_id:
        query = query.where(Deal.owner_user_id == owner_id)
    if status in ("open", "won", "lost"):
        query = query.where(Deal.status == status)
    if contact_id:
        query = query.where(Deal.contact_id == contact_id)
    for word in q.lower().split()[:5]:
        like = f"%{word}%"
        query = query.where(
            func.lower(Deal.title).like(like)
            | func.lower(func.coalesce(Contact.full_name, "")).like(like)
            | func.lower(func.coalesce(Company.name, "")).like(like)
        )
    return [deal_dict(*row) for row in session.execute(query).tuples()]


def deal_detail(session: Session, deal: Deal) -> dict[str, Any]:
    contact = session.get(Contact, deal.contact_id) if deal.contact_id else None
    company = session.get(Company, deal.company_id) if deal.company_id else None
    owner = session.get(User, deal.owner_user_id) if deal.owner_user_id else None
    stage = session.get(Stage, deal.stage_id)
    return deal_dict(deal, contact, company, owner, stage)


# --- Actividades ----------------------------------------------------------------------------


def activity_dict(activity: Activity, user: User | None) -> dict[str, Any]:
    return {
        "id": str(activity.id),
        "type": activity.type,
        "label": ACTIVITY_LABELS.get(activity.type, activity.type),
        "body": activity.body,
        "contact_id": str(activity.contact_id) if activity.contact_id else None,
        "deal_id": str(activity.deal_id) if activity.deal_id else None,
        "due_at": activity.due_at.isoformat() if activity.due_at else None,
        "done_at": activity.done_at.isoformat() if activity.done_at else None,
        "user": {"id": str(user.id), "name": user.full_name or user.email} if user else None,
        "created_at": activity.created_at.isoformat(),
    }


def timeline(
    session: Session,
    tenant_id: uuid.UUID,
    *,
    contact_id: uuid.UUID | None = None,
    deal_id: uuid.UUID | None = None,
) -> list[dict[str, Any]]:
    query = (
        select(Activity, User)
        .outerjoin(User, User.id == Activity.user_id)
        .where(Activity.tenant_id == tenant_id)
        .order_by(Activity.created_at.desc())
        .limit(300)
    )
    if deal_id:
        query = query.where(Activity.deal_id == deal_id)
    elif contact_id:
        # La línea de tiempo del contacto incluye lo de sus oportunidades.
        deal_ids = select(Deal.id).where(Deal.contact_id == contact_id)
        query = query.where((Activity.contact_id == contact_id) | Activity.deal_id.in_(deal_ids))
    else:
        raise CrmError("Indica un contacto o una oportunidad.")
    return [activity_dict(a, u) for a, u in session.execute(query).tuples()]


def add_activity(
    session: Session,
    *,
    tenant_id: uuid.UUID,
    user_id: uuid.UUID,
    kind: str,
    body: str,
    contact_id: uuid.UUID | None,
    deal_id: uuid.UUID | None,
    due_at: datetime | None,
) -> Activity:
    if kind not in ("note", "call", "meeting", "email", "task"):
        raise CrmError("Tipo de actividad no válido.")
    if not body.strip():
        raise CrmError("Escribe el detalle de la actividad.")
    if kind == "task" and due_at is None:
        raise CrmError("Las tareas necesitan fecha de vencimiento.")
    deal = get_deal(session, tenant_id, deal_id) if deal_id else None
    if contact_id:
        contact = session.get(Contact, contact_id)
        if contact is None or contact.tenant_id != tenant_id:
            raise CrmError("Ese contacto no existe.", 404)
    elif deal is not None:
        contact_id = deal.contact_id
    if not contact_id and deal is None:
        raise CrmError("La actividad debe ser de un contacto o una oportunidad.")
    return log(
        session, tenant_id=tenant_id, kind=kind, body=body.strip(), user_id=user_id,
        contact_id=contact_id, deal_id=deal.id if deal else None, due_at=due_at,
    )  # fmt: skip


def get_activity(session: Session, tenant_id: uuid.UUID, activity_id: uuid.UUID) -> Activity:
    activity = session.get(Activity, activity_id)
    if activity is None or activity.tenant_id != tenant_id:
        raise CrmError("Esa actividad no existe.", 404)
    return activity


def tasks(
    session: Session, tenant_id: uuid.UUID, *, user_id: uuid.UUID | None, include_done: bool
) -> list[dict[str, Any]]:
    query = (
        select(Activity, User, Contact, Deal)
        .outerjoin(User, User.id == Activity.user_id)
        .outerjoin(Contact, Contact.id == Activity.contact_id)
        .outerjoin(Deal, Deal.id == Activity.deal_id)
        .where(Activity.tenant_id == tenant_id, Activity.type == "task")
        .order_by(Activity.done_at.is_not(None), Activity.due_at)
        .limit(300)
    )
    if user_id:
        query = query.where(Activity.user_id == user_id)
    if not include_done:
        query = query.where(Activity.done_at.is_(None))
    out = []
    for activity, user, contact, deal in session.execute(query).tuples():
        item = activity_dict(activity, user)
        item["contact_name"] = contact.full_name if contact else None
        item["deal_title"] = deal.title if deal else None
        out.append(item)
    return out


# --- Indicadores ----------------------------------------------------------------------------


def metrics(session: Session, tenant_id: uuid.UUID) -> dict[str, Any]:
    row = session.execute(
        select(
            func.count().filter(Deal.status == "open"),
            func.count().filter(Deal.status == "won"),
            func.count().filter(Deal.status == "lost"),
        ).where(Deal.tenant_id == tenant_id)
    ).one()
    open_count, won, lost = (int(x or 0) for x in row)
    pipeline = session.execute(
        select(
            Deal.currency,
            func.coalesce(func.sum(Deal.value), 0),
            func.coalesce(func.sum(Deal.value * Stage.probability / 100.0), 0),
        )
        .join(Stage, Stage.id == Deal.stage_id)
        .where(Deal.tenant_id == tenant_id, Deal.status == "open")
        .group_by(Deal.currency)
    ).all()
    won_value = session.execute(
        select(Deal.currency, func.coalesce(func.sum(Deal.value), 0))
        .where(Deal.tenant_id == tenant_id, Deal.status == "won")
        .group_by(Deal.currency)
    ).all()
    overdue = session.scalar(
        select(func.count()).where(
            Activity.tenant_id == tenant_id,
            Activity.type == "task",
            Activity.done_at.is_(None),
            Activity.due_at < now(),
        )
    )
    closed = won + lost
    return {
        "open": open_count,
        "won": won,
        "lost": lost,
        "conversion_rate": round(100 * won / closed, 1) if closed else None,
        "pipeline": {c: float(v) for c, v, _ in pipeline},
        "weighted": {c: round(float(w), 2) for c, _, w in pipeline},
        "won_value": {c: float(v) for c, v in won_value},
        "overdue_tasks": int(overdue or 0),
    }
