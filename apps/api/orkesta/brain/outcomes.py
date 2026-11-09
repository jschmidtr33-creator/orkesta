"""Puntaje que aprende de resultados (IA propia, paso 5).

Aprende, dentro de cada empresa y solo con sus datos, qué perfiles responden bien o terminan en
oportunidad (nivel, área, sector y país de la persona contactada) y lo suma al puntaje por
parecido con la búsqueda, explicando en palabras simples por qué sube o baja.

Privacidad: cada rasgo lleva el identificador de la empresa delante, así que lo que aprende una
empresa nunca mueve el puntaje de otra. El modelo solo guarda conteos por código (nivel, área,
sector, país), nunca nombres, correos ni textos.
"""

from __future__ import annotations

import logging
import math
import uuid

from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from orkesta.brain import ml

logger = logging.getLogger(__name__)
TASK = "puntaje"
YES, NO = "si", "no"
FINISHED = ("completed", "replied", "unsubscribed")
MIN_EVIDENCE = 8  # personas con ese rasgo para mencionarlo como motivo
WEIGHT = 0.5  # cuánto pesa lo aprendido frente al parecido con la búsqueda

LEVELS = {
    "c_level": "la alta dirección", "duenio_socio": "dueños y socios", "vp": "vicepresidencias",
    "director": "directores", "gerente": "gerentes", "jefe": "jefaturas",
    "profesional": "profesionales", "analista": "analistas", "asistente": "asistentes",
}  # fmt: skip


def features(tenant_id: uuid.UUID | str, *, level: str | None, area: str | None,
             industry: str | None, country: str | None) -> ml.Features:  # fmt: skip
    t = str(tenant_id)
    x: ml.Features = {f"{t}|base": 1.0}
    for name, value in (("nivel", level), ("area", area), ("sector", industry),
                        ("pais", (country or "").upper() or None)):  # fmt: skip
        if value:
            x[f"{t}|{name}:{value}"] = 1.0
    if level and area:
        x[f"{t}|perfil:{level}+{area}"] = 1.0
    return x


def _tenant_examples(session: Session, tenant_id: uuid.UUID) -> list[ml.Example]:
    from orkesta.campaigns.models import Recipient
    from orkesta.campaigns.replies import POSITIVE
    from orkesta.data.models import Contact

    positive = or_(Recipient.reply_category.in_(POSITIVE), Recipient.deal_id.is_not(None))
    rows = session.execute(
        select(Recipient.id, Recipient.contact_id, positive.label("ok"), Contact.level_code,
               Contact.area_code, Contact.industry_code, Contact.country_code)
        .join(Contact, Contact.id == Recipient.contact_id)
        .where(Recipient.tenant_id == tenant_id, Recipient.sent_count > 0,
               or_(positive, Recipient.status.in_(FINISHED)))
    ).all()  # fmt: skip
    out = []
    for r in rows:
        x = features(tenant_id, level=r.level_code, area=r.area_code, industry=r.industry_code,
                     country=r.country_code)  # fmt: skip
        # La misma persona en dos campañas cae siempre del mismo lado de la partición.
        out.append(ml.Example(f"c:{r.contact_id}", x, YES if r.ok else NO, r.level_code))
    return out


def build(session: Session) -> list[ml.Example]:
    from orkesta.core.db import set_context
    from orkesta.platform.models import Tenant

    examples: list[ml.Example] = []
    for tenant_id in session.scalars(select(Tenant.id)).all():
        set_context(session, tenant_id=tenant_id)
        examples += _tenant_examples(session, tenant_id)
    set_context(session)
    return examples


def make() -> ml.NaiveBayes:
    return ml.NaiveBayes(alpha=1.0, min_count=1)


DECISION_MAKERS = ("c_level", "duenio_socio", "vp", "director", "gerente")


def baseline(session: Session, test: list[ml.Example]) -> ml.Metrics:
    """La regla anterior: el parecido con la búsqueda da más puntos a quien decide, así que
    «responderá» si es gerente o más."""
    pairs = [(ex.y, YES if ex.raw in DECISION_MAKERS else NO, 1.0) for ex in test]
    return ml.score(pairs)


def _contributions(model: ml.NaiveBayes, x: ml.Features) -> dict[str, float]:
    """Cuánto empuja cada rasgo hacia «responde» (log de razón de probabilidades)."""
    if YES not in model.loglik or NO not in model.loglik:
        return {}
    out = {}
    for feat in x:
        if feat.endswith("|base"):
            continue
        yes = model.loglik[YES].get(feat)
        no = model.loglik[NO].get(feat)
        if yes is None and no is None:
            continue
        out[feat] = (yes if yes is not None else model.unseen[YES]) - (
            no if no is not None else model.unseen[NO])  # fmt: skip
    return out


def _describe(feat: str) -> str:
    _, _, part = feat.partition("|")
    kind, _, value = part.partition(":")
    if kind == "nivel":
        return LEVELS.get(value, value.replace("_", " "))
    if kind == "area":
        return f"el área de {value.replace('_', ' ')}"
    if kind == "sector":
        return f"el sector {value.replace('_', ' ')}"
    if kind == "pais":
        return f"contactos de {value}"
    if kind == "perfil":
        level, _, area = value.partition("+")
        return f"{LEVELS.get(level, level)} de {area.replace('_', ' ')}"
    return value


class Scorer:
    """El modelo activo listo para puntuar en una empresa (o nada si aún no hay modelo)."""

    def __init__(self, model: ml.NaiveBayes | None, tenant_id: uuid.UUID | str,
                 counts: dict[str, int] | None = None) -> None:  # fmt: skip
        self.model, self.tenant_id, self.counts = model, str(tenant_id), counts or {}

    @classmethod
    def load(cls, session: Session, tenant_id: uuid.UUID) -> Scorer:
        from orkesta.brain import registry

        try:
            loaded = registry.active(session, TASK)
        except Exception:
            logger.exception("brain_outcomes_load")
            loaded = None
        model = loaded[0] if loaded else None
        if not isinstance(model, ml.NaiveBayes) or f"{tenant_id}|base" not in model._vocab:
            return cls(None, tenant_id)  # la empresa aún no tiene resultados aprendidos
        counts = {ex_key: n for ex_key, n in _feature_counts(session, tenant_id).items()}
        return cls(model, tenant_id, counts)

    def adjust(self, score: int, reasons: list[str], *, level: str | None, area: str | None,
               industry: str | None, country: str | None) -> tuple[int, list[str]]:  # fmt: skip
        if self.model is None:
            return score, reasons
        x = features(self.tenant_id, level=level, area=area, industry=industry, country=country)
        contrib = {f: v for f, v in _contributions(self.model, x).items()
                   if self.counts.get(f, 0) >= MIN_EVIDENCE}  # fmt: skip
        if not contrib:
            return score, reasons
        total = max(-3.0, min(3.0, sum(contrib.values())))
        learned = 50 + 50 * math.tanh(total / 1.5)
        final = round((1 - WEIGHT) * score + WEIGHT * learned)
        best = max(contrib, key=contrib.__getitem__)
        worst = min(contrib, key=contrib.__getitem__)
        extra = []
        if contrib[best] > 0.25:
            extra.append(
                f"En tu empresa responden más {_describe(best)} (×{math.exp(contrib[best]):.1f})"
            )
        if contrib[worst] < -0.25:
            extra.append(f"En tu empresa responden menos {_describe(worst)}")
        return max(0, min(100, final)), reasons + extra


def _feature_counts(session: Session, tenant_id: uuid.UUID) -> dict[str, int]:
    counts: dict[str, int] = {}
    for ex in _tenant_examples(session, tenant_id):
        for feat in ex.x:
            counts[feat] = counts.get(feat, 0) + 1
    return counts
