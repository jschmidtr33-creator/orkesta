"""Las capacidades de la IA propia que aprenden y la medición de cómo funcionan hoy.

Cada capacidad muestra en el panel «Cerebro» con qué método trabaja (reglas o modelo), cuántos
datos tiene para aprender y, cuando ya tiene modelo, su acierto frente a las reglas.
"""

from __future__ import annotations

import uuid
from collections.abc import Callable
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from orkesta.brain.registry import Task, register
from orkesta.core.db import set_context


def _pct(part: int, total: int) -> str:
    return f"{round(100 * part / total)} %" if total else "—"


def _num(value: int) -> str:
    return f"{value:,}".replace(",", ".")


def per_tenant(session: Session, fn: Callable[[uuid.UUID], dict[str, int]]) -> dict[str, int]:
    """Suma cifras de todas las empresas (cada tabla está protegida por RLS)."""
    from orkesta.platform.models import Tenant

    totals: dict[str, int] = {}
    for tenant_id in session.scalars(select(Tenant.id)).all():
        set_context(session, tenant_id=tenant_id)
        for key, value in fn(tenant_id).items():
            totals[key] = totals.get(key, 0) + value
    set_context(session)
    return totals


def _titles_status(session: Session) -> list[dict[str, Any]]:
    from orkesta.research.models import PersonIndex

    with_title = PersonIndex.title != ""
    total = int(session.scalar(select(func.count()).where(with_title)) or 0)
    count = select(func.count()).where(with_title)
    area = int(session.scalar(count.where(PersonIndex.area.is_not(None))) or 0)
    level = int(session.scalar(count.where(PersonIndex.level.is_not(None))) or 0)
    return [
        {"label": "Cargos en el índice", "value": _num(total)},
        {
            "label": "Con área identificada",
            "value": _pct(area, total),
            "hint": f"{_num(area)} cargos",
        },
        {
            "label": "Con nivel identificado",
            "value": _pct(level, total),
            "hint": f"{_num(level)} cargos",
        },
    ]


def _replies_status(session: Session) -> list[dict[str, Any]]:
    from orkesta.campaigns.models import Recipient

    def count(tenant_id: uuid.UUID) -> dict[str, int]:
        base = select(func.count()).select_from(Recipient).where(Recipient.tenant_id == tenant_id)
        return {
            "replied": int(session.scalar(base.where(Recipient.replied_at.is_not(None))) or 0),
            "classified": int(
                session.scalar(base.where(Recipient.reply_category.is_not(None))) or 0
            ),
        }

    t = per_tenant(session, count)
    return [
        {"label": "Respuestas recibidas", "value": _num(t.get("replied", 0))},
        {"label": "Clasificadas", "value": _num(t.get("classified", 0))},
    ]


def _patterns_status(session: Session) -> list[dict[str, Any]]:
    from orkesta.research.models import PatternStat

    def domains(*conditions: Any) -> int:
        query = select(func.count(func.distinct(PatternStat.domain))).where(*conditions)
        return int(session.scalar(query) or 0)

    total = domains()
    confirmed = domains(PatternStat.confirmed > 0)
    bounced = domains(PatternStat.bounced > 0)
    return [
        {"label": "Empresas con formato aprendido", "value": _num(total)},
        {"label": "Confirmados con respuestas o bases", "value": _pct(confirmed, total),
         "hint": f"{_num(confirmed)} dominios"},
        {"label": "Con algún rebote", "value": _pct(bounced, total),
         "hint": f"{_num(bounced)} dominios"},
    ]  # fmt: skip


def _outcomes_status(session: Session) -> list[dict[str, Any]]:
    from orkesta.campaigns.models import Recipient
    from orkesta.campaigns.replies import POSITIVE

    def count(tenant_id: uuid.UUID) -> dict[str, int]:
        base = select(func.count()).select_from(Recipient).where(Recipient.tenant_id == tenant_id)
        return {
            "contacted": int(session.scalar(base.where(Recipient.sent_count > 0)) or 0),
            "positive": int(
                session.scalar(base.where(Recipient.reply_category.in_(POSITIVE))) or 0
            ),
            "deals": int(session.scalar(base.where(Recipient.deal_id.is_not(None))) or 0),
        }

    t = per_tenant(session, count)
    return [
        {"label": "Personas contactadas", "value": _num(t.get("contacted", 0))},
        {"label": "Respuestas positivas", "value": _num(t.get("positive", 0)),
         "hint": _pct(t.get("positive", 0), t.get("contacted", 0)) + " de las contactadas"},
        {"label": "Oportunidades creadas", "value": _num(t.get("deals", 0))},
    ]  # fmt: skip


def _copy_status(session: Session) -> list[dict[str, Any]]:
    from orkesta.campaigns.models import Message, Recipient

    def count(tenant_id: uuid.UUID) -> dict[str, int]:
        sent = select(func.count()).select_from(Message).where(Message.tenant_id == tenant_id)
        replied = select(func.count()).select_from(Recipient).where(
            Recipient.tenant_id == tenant_id, Recipient.replied_at.is_not(None))  # fmt: skip
        return {
            "sent": int(session.scalar(sent) or 0),
            "replied": int(session.scalar(replied) or 0),
        }

    t = per_tenant(session, count)
    return [
        {"label": "Correos enviados (todas las empresas)", "value": _num(t.get("sent", 0))},
        {"label": "Con respuesta", "value": _num(t.get("replied", 0)),
         "hint": _pct(t.get("replied", 0), t.get("sent", 0)) + " de los enviados"},
    ]  # fmt: skip


register(Task(
    name="cargos", label="Cargos → nivel y área",
    description="Entiende cualquier cargo (con errores de tipeo, en inglés o nuevos) y lo ubica "
                "en su nivel y su área para buscar, filtrar y puntuar.",
    step=2, status=_titles_status,
))  # fmt: skip
register(Task(
    name="respuestas", label="Clasificador de respuestas",
    description="Lee cada respuesta a una campaña y decide qué quiso decir la persona; aprende de "
                "cada corrección y pregunta cuando duda.",
    step=3, status=_replies_status, method="Listas de frases",
))  # fmt: skip
register(Task(
    name="formato_correo", label="Formato de correo de empresas nuevas",
    description="Predice cómo arma los correos una empresa que ORKESTA nunca vio, por país, "
                "sector, "
                "tamaño y empresas parecidas.",
    step=4, status=_patterns_status, method="El formato más común del país",
))  # fmt: skip
register(Task(
    name="puntaje", label="Puntaje que aprende de resultados",
    description="Aprende en cada empresa qué perfiles responden y compran, y explica cada puntaje.",
    step=5, status=_outcomes_status, method="Parecido con la búsqueda",
))  # fmt: skip
register(Task(
    name="redaccion", label="Redacción que aprende entre todos",
    description="Ordena aperturas, estructuras y cierres por tasa de respuesta real en todas las "
                "empresas (solo cifras, nunca contenidos) y estima la probabilidad de respuesta.",
    step=6, status=_copy_status, method="Plantillas y pruebas A/B por empresa",
))  # fmt: skip
