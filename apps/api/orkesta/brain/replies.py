"""IA propia, paso 3: clasificador de respuestas que aprende de cada corrección y pregunta
cuando duda.

- Decide un ensamble de dos modelos propios (Naive Bayes y regresión logística) sobre palabras y
  fragmentos del texto. Si los dos coinciden, decide solo; si discrepan, pregunta a la persona.
- Las listas de frases siguen como red de seguridad: «darse de baja» y «fuera de oficina» se
  respetan siempre, y una respuesta positiva con señales de negativa se pregunta.
- Aprende de las correcciones de todas las empresas sin mezclar contenidos: de una corrección
  solo se usan palabras del vocabulario común (conjunto curado) o que aparecen en correcciones
  de al menos dos empresas distintas. Nombres, correos y detalles propios nunca entran.
"""

from __future__ import annotations

import uuid
from collections import defaultdict
from dataclasses import dataclass
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from orkesta.brain import ml
from orkesta.brain.data import replies_eval, replies_seed
from orkesta.brain.models import BrainFeedback
from orkesta.campaigns import replies as rules

TASK = "respuestas"
FEEDBACK_WEIGHT = 3
NEGATIVE = ("no_interesado", "no_ahora", "baja")
SAFE = ("baja", "fuera_oficina")  # la regla manda siempre


def features(text: str) -> ml.Features:
    body = rules.clean(text) or ml.fold(text)
    return ml.merge(ml.word_features(body), ml.text_features(body, chars=(3, 4), words=False))


def make() -> ml.Ensemble:
    return ml.Ensemble([ml.NaiveBayes(alpha=0.3, min_count=1),
                        ml.LogisticRegression(epochs=30, min_count=1)])  # fmt: skip


@dataclass
class Decision:
    category: str
    confidence: float
    sure: bool
    source: str  # "reglas" | "modelo" | "reglas y modelo"


def decide_with(model: ml.Ensemble | None, text: str) -> Decision:
    rule, rule_conf = rules.classify(text)
    if rule in SAFE:
        return Decision(rule, rule_conf / 100, True, "reglas")
    if model is None:
        return Decision(rule, rule_conf / 100, True, "reglas")  # sin modelo: como siempre
    x = features(text)
    votes = model.votes(x)
    labels = [label for label, _ in votes]
    confidence = max(model.predict_proba(x).values())
    if len(set(labels)) == 1:
        category, sure, source = labels[0], True, "modelo"
        if rule == category:
            source = "reglas y modelo"
    elif rule != "otro" and rule in labels:
        category, sure, source = rule, True, "reglas y modelo"
    else:
        category, sure, source = labels[0], False, "modelo"
    # Antes de crear una oportunidad, ninguna señal de negativa puede quedar sin revisar.
    if sure and category in rules.POSITIVE and rule in NEGATIVE:
        sure = False
    return Decision(category, confidence, sure, source)


def decide(session: Session, text: str) -> Decision:
    from orkesta.brain.registry import active

    loaded = active(session, TASK)
    return decide_with(loaded[0] if loaded else None, text)  # type: ignore[arg-type]


# --- Entrenamiento ----------------------------------------------------------------------------


def _feedback(session: Session) -> list[tuple[str, str, uuid.UUID | None]]:
    rows = session.execute(
        select(BrainFeedback.text, BrainFeedback.label, BrainFeedback.tenant_id)
        .where(BrainFeedback.task == TASK, BrainFeedback.label.in_(list(rules.CATEGORIES)))
        .order_by(BrainFeedback.created_at)
    ).all()  # fmt: skip
    latest: dict[str, tuple[str, str, uuid.UUID | None]] = {}
    for text, label, tenant in rows:
        if text and text.strip():
            latest[ml.fold(text)] = (text, label, tenant)  # la corrección más reciente manda
    return list(latest.values())


def build(session: Session) -> list[ml.Example]:
    seed = replies_seed.examples()
    examples = [ml.Example(f"s:{ml.fold(t)}", features(t), label, t) for t, label in seed]
    vocabulary = {k for ex in examples for k in ex.x}
    corrections = _feedback(session)
    tenants_by_token: dict[str, set[Any]] = defaultdict(set)
    for text, _, tenant in corrections:
        for token in features(text):
            tenants_by_token[token].add(tenant)
    for text, label, _ in corrections:
        shared = {k: v for k, v in features(text).items()
                  if k in vocabulary or len(tenants_by_token[k]) >= 2}  # fmt: skip
        if shared:
            examples += [ml.Example(f"f:{ml.fold(text)}", shared, label, text)] * FEEDBACK_WEIGHT
    return examples


def predictor(model: Any, threshold: float, ex: ml.Example) -> tuple[str | None, float]:
    decision = decide_with(model, str(ex.raw))
    return decision.category, decision.confidence


def baseline(session: Session, test: list[ml.Example]) -> ml.Metrics:
    return ml.score([(ex.y, rules.classify(str(ex.raw))[0], 1.0) for ex in test])


def extras(session: Session, model: Any, threshold: float) -> dict[str, Any]:
    """Medición en el conjunto independiente (nunca usado para entrenar)."""
    total = len(replies_eval.EVAL)
    by_rules = sum(1 for t, label in replies_eval.EVAL if rules.classify(t)[0] == label)
    decided = right = 0
    for text, label in replies_eval.EVAL:
        decision = decide_with(model, text)
        if decision.sure:
            decided += 1
            right += decision.category == label
    return {
        "eval_n": total,
        "eval_rules": round(by_rules / total, 4),
        "eval_model": round(right / decided, 4) if decided else 0.0,
        "eval_decides": round(decided / total, 4),
    }
