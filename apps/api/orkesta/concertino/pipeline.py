# ruff: noqa: E501  (textos para el vendedor: líneas largas a propósito)
"""«¿Qué oportunidad priorizo esta semana?»: Concertino ordena las oportunidades abiertas de
la persona con los datos del CRM (etapa, valor, salud, fecha de cierre, tareas y propuestas) y
le dice qué hacer con cada una. Sin terceros y sin inventar nada: solo lo que hay en ORKESTA."""

from __future__ import annotations

import re
import uuid
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from orkesta.crm import intelligence
from orkesta.crm.models import Activity, Deal, Stage
from orkesta.engine.normalize.text import fold

_WANTS = re.compile(
    r"\b(prioriz\w*|"
    r"(que|cual|cuales|a que|en que|con que) (oportunidad|oportunidades|cuenta|cuentas|negocio|negocios|cliente|clientes|deal|deals)\b.*\b(priorizo|atiendo|trabajo|muevo|enfoco|empiezo|llamo|cierro|esta semana|hoy|primero)|"
    r"(a quien|a quienes) (llamo|escribo|le escribo|contacto) (hoy|esta semana|primero)|"
    r"(revisa|revisame|analiza|ordena|ordename) (mis|el) (oportunidades|negocios|pipeline|embudo|cuentas)|"
    r"como (va|esta|van) mi (pipeline|embudo)|"
    r"mis oportunidades)"
)

LIMIT = 5


def wants(question: str) -> bool:
    return bool(_WANTS.search(fold(question)))


def _money(value: Decimal | None, currency: str) -> str:
    if not value:
        return ""
    return f"{currency} {int(value):,}".replace(",", ".")


def _days(n: int) -> str:
    return "1 día" if n == 1 else f"{n} días"


def build(session: Session, tenant_id: uuid.UUID, user_id: uuid.UUID) -> dict[str, Any]:
    mine = list(session.scalars(
        select(Deal).where(Deal.tenant_id == tenant_id, Deal.status == "open",
                           Deal.owner_user_id == user_id)
    ))  # fmt: skip
    team = False
    if not mine:
        mine = list(session.scalars(
            select(Deal).where(Deal.tenant_id == tenant_id, Deal.status == "open")
        ))  # fmt: skip
        team = bool(mine)
    if not mine:
        return {"summary": "No tienes oportunidades abiertas en el CRM. Cuando alguien responda con interés o agendes una reunión, créala y te digo cuál mover primero.",
                "options": [], "tips": [], "sections": [], "next_step": "", "missing": [],
                "suggestions": ["Dame una estrategia para conseguir reuniones", "¿Cómo hago seguimiento sin molestar?"]}  # fmt: skip
    now = datetime.now(UTC)
    stages = {s.id: s for s in session.scalars(select(Stage).where(Stage.tenant_id == tenant_id))}
    health = intelligence.health(session, tenant_id, mine)
    ids = [d.id for d in mine]
    tasks: dict[uuid.UUID, Activity] = {}
    for row in session.scalars(
        select(Activity).where(Activity.tenant_id == tenant_id, Activity.deal_id.in_(ids),
                               Activity.type == "task", Activity.done_at.is_(None))
        .order_by(Activity.due_at.asc().nulls_last())
    ):  # fmt: skip
        if row.deal_id is not None:
            tasks.setdefault(row.deal_id, row)
    top_value = max((d.value or 0 for d in mine), default=0) or 1

    rows = []
    for d in mine:
        stage = stages.get(d.stage_id)
        prob = stage.probability if stage else 0
        h = health.get(d.id, {"score": 50, "label": "tibia", "reasons": []})
        score = prob * 0.45 + h["score"] * 0.3 + float(d.value or 0) / float(top_value) * 20
        closing = (d.expected_close - now.date()).days if d.expected_close else None
        if closing is not None and 0 <= closing <= 14:
            score += 15
        pending = tasks.get(d.id)
        if pending is not None and pending.due_at is not None and pending.due_at < now:
            score += 8  # una tarea vencida pide atención esta semana
        rows.append((score, d, stage, h, closing, pending))
    rows.sort(key=lambda r: r[0], reverse=True)

    def todo(d: Deal, h: dict[str, Any], closing: int | None, task: Activity | None) -> str:
        reasons = " ".join(h["reasons"])
        if task is not None and task.due_at is not None and task.due_at < now:
            late = (now - task.due_at).days
            return f"Tienes una tarea vencida hace {_days(max(1, late))}: «{task.body[:80]}». Hazla hoy o muévela con una fecha real."
        if "Aceptó la propuesta" in reasons:
            return "Aceptó la propuesta: envía el contrato o la orden de compra y agenda el inicio."
        if "Vio la propuesta" in reasons:
            return (
                "Vio la propuesta: llama hoy para resolver dudas y pregunta qué falta para firmar."
            )
        if closing is not None and closing < 0:
            return "La fecha estimada de cierre ya pasó: pregunta qué falta para decidir y actualiza la fecha."
        if "Sin actividad" in reasons:
            return "Se está enfriando: escríbele con un dato nuevo o un caso, y pregunta si sigue siendo prioridad."
        if task is not None:
            when = task.due_at.strftime("%d/%m") if task.due_at else "sin fecha"
            return f"Tu próxima tarea ({when}): «{task.body[:80]}»."
        if closing is not None and closing <= 14:
            return f"Cierra en {_days(closing)}: confirma quién firma y qué necesita para hacerlo."
        return "No tiene un siguiente paso: agenda una reunión o una tarea con fecha."

    items = []
    for i, (_score, d, stage, h, closing, task) in enumerate(rows[:LIMIT], start=1):
        facts = [x for x in (stage.name if stage else "", _money(d.value, d.currency),
                             f"cierra en {_days(closing)}" if closing is not None and 0 <= closing <= 30 else "")
                 if x]  # fmt: skip
        items.append(f"{i}. {d.title} ({' · '.join(facts)}). {todo(d, h, closing, task)}")
    sections = [{"title": "Esta semana, en este orden", "items": items}]
    cold = [f"{d.title}: {'; '.join(h['reasons'][:2]) or 'sin movimiento'}."
            for _s, d, _st, h, _c, _t in rows[LIMIT:] if h["label"] == "fria"][:5]  # fmt: skip
    if cold:
        sections.append({"title": "Se están enfriando", "items": cold})
    one_currency = len({d.currency for d in mine}) == 1
    total = sum((d.value or 0) for d in mine) if one_currency else 0
    who = "del equipo (no tienes oportunidades a tu nombre)" if team else "abiertas"
    summary = (f"Tienes {len(mine)} oportunidades {who}"
               + (f" por {_money(Decimal(total), mine[0].currency)}" if total else "")
               + ". Las ordené por probabilidad de etapa, valor, salud y fecha de cierre; empieza por la primera.")  # fmt: skip
    return {"summary": summary, "options": [], "tips": [], "sections": sections,
            "next_step": "Ábreme una oportunidad y pregúntame «¿cómo avanzo esta cuenta?» para el detalle.",
            "missing": [], "suggestions": []}  # fmt: skip
