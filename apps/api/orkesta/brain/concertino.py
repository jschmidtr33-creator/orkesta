# ruff: noqa: E501  (textos de venta: líneas largas a propósito)
"""Modelos propios de Concertino en el Cerebro: tema de la pregunta y tipo de objeción.

Arrancan con ejemplos semilla (frases típicas) y aprenden de cada corrección de los vendedores
(«no era precio, era que ya tienen proveedor»). Privacidad: de una corrección solo entran al
modelo las palabras que también usan otras empresas o que ya estaban en las semillas.
"""

from __future__ import annotations

from collections import defaultdict
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from orkesta.brain import ml
from orkesta.brain.models import BrainFeedback
from orkesta.concertino import engine
from orkesta.concertino import playbook as pb

FEEDBACK_WEIGHT = 3

OBJECTION_SEED: dict[str, tuple[str, ...]] = {
    "precio": ("me parece muy caro", "está fuera de nuestro presupuesto", "no tenemos presupuesto para esto",
               "el precio es muy alto", "es costoso para nosotros", "¿tienen algún descuento?",
               "no nos alcanza este año", "es mucha plata para lo que hace"),
    "prioridad": ("no es prioridad en este momento", "retomemos el próximo año", "ahora no es buen momento",
                  "estamos enfocados en otros proyectos", "escríbeme el otro trimestre", "por ahora no",
                  "estamos full de trabajo", "lo vemos más adelante"),
    "proveedor": ("ya tenemos un proveedor", "ya trabajamos con otra empresa para eso", "ya contamos con una herramienta",
                  "estamos contentos con nuestro proveedor actual", "ya lo resolvimos internamente",
                  "ya usamos otra plataforma"),
    "persona": ("no soy la persona indicada", "eso no es mi área", "habla con el gerente de compras",
                "el encargado de eso es otra persona", "no manejo ese tema", "escríbele a mi jefe de ti"),
    "info": ("envíame información", "mándame una presentación", "me compartes más detalles por correo",
             "envíame el brochure", "quisiera más información", "mándame algo para revisarlo"),
    "riesgo": ("parece complicado de implementar", "la integración con nuestro sistema será difícil",
               "mi equipo no lo va a usar", "migrar todo es mucho trabajo", "me preocupa el riesgo del cambio",
               "la curva de aprendizaje es alta"),
    "confianza": ("no los conozco", "¿qué clientes tienen?", "¿tienen casos de éxito?",
                  "necesito referencias antes", "¿quiénes son ustedes?", "no conocemos su empresa"),
    "competencia": ("otra empresa nos ofrece lo mismo más barato", "estamos cotizando con otros",
                    "estamos comparando opciones", "la competencia nos dio mejor precio", "tenemos otra propuesta"),
    "comite": ("lo tengo que consultar con mi jefe", "lo decide el comité", "tengo que hablarlo con mi socio",
               "lo llevo a la junta directiva", "debo revisarlo con el gerente general", "lo decidimos en equipo"),
    "silencio": ("no me responde", "me dejó en visto", "no ha contestado mis correos", "desapareció después de la reunión",
                 "no volvió a escribir", "lleva dos semanas sin respuesta"),
    "no_interes": ("no nos interesa", "no gracias", "no aplica para nosotros", "no lo necesitamos",
                   "no estamos interesados"),
}  # fmt: skip

TOPIC_SEED: dict[str, tuple[str, ...]] = {
    "objecion": ("me dijo que es muy caro", "me respondió que ya tienen proveedor", "cómo manejo la objeción de precio",
                 "dice que no es prioridad", "me dijo que lo tiene que consultar"),
    "perfil": ("cómo le hablo a un gerente de compras", "qué le importa a un CFO", "cómo le escribo a la directora de marketing",
               "cómo le vendo a un CEO de un banco", "qué buscan los gerentes de tecnología"),
    "cuenta": ("qué hago con esta oportunidad que se enfrió", "cómo avanzo esta cuenta", "la negociación está estancada",
               "cómo cierro este negocio", "en qué etapa debería estar esta oportunidad"),
    "campana": ("por qué mi campaña no tiene respuestas", "cómo mejoro la tasa de respuesta", "nadie abre mis correos",
                "qué asunto uso en la secuencia", "cuántos seguimientos hago"),
    "propuesta": ("cómo presento el precio en la propuesta", "qué debe tener mi propuesta", "cómo armo la cotización",
                  "cómo justifico la inversión"),
    "reunion": ("tengo reunión mañana con el gerente general", "cómo preparo la demo", "qué pregunto en la llamada",
                "prepárame para la reunión de descubrimiento"),
    "estrategia": ("a qué sector le apunto este mes", "qué industria me responde mejor", "cuál es mi mejor segmento",
                   "a quién debería prospectar"),
    "producto": ("cómo explico nuestro producto", "cuál es nuestra propuesta de valor", "qué nos diferencia",
                 "cómo presento lo que vendemos en 30 segundos"),
}  # fmt: skip


def make() -> ml.NaiveBayes:
    return ml.NaiveBayes(alpha=0.5, min_count=1)


def _feedback(session: Session, task: str, labels: set[str]) -> list[tuple[str, str, Any]]:
    rows = session.execute(
        select(BrainFeedback.text, BrainFeedback.label, BrainFeedback.tenant_id)
        .where(BrainFeedback.task == task, BrainFeedback.label.in_(labels))
        .order_by(BrainFeedback.created_at)
    ).all()  # fmt: skip
    latest: dict[str, tuple[str, str, Any]] = {}
    for text, label, tenant in rows:
        if text and text.strip():
            latest[ml.fold(text)] = (text, label, tenant)
    return list(latest.values())


def _build(session: Session, task: str, seed: dict[str, tuple[str, ...]]) -> list[ml.Example]:
    examples = [ml.Example(f"s:{ml.fold(t)}", ml.word_features(t), label, t)
                for label, texts in seed.items() for t in texts]  # fmt: skip
    vocabulary = {k for ex in examples for k in ex.x}
    corrections = _feedback(session, task, set(seed))
    tenants: dict[str, set[Any]] = defaultdict(set)
    for text, _, tenant in corrections:
        for token in ml.word_features(text):
            tenants[token].add(tenant)
    for text, label, _ in corrections:
        shared = {k: v for k, v in ml.word_features(text).items()
                  if k in vocabulary or len(tenants[k]) >= 2}  # fmt: skip
        if shared:
            examples += [ml.Example(f"f:{ml.fold(text)}", shared, label, text)] * FEEDBACK_WEIGHT
    return examples


def build_objections(session: Session) -> list[ml.Example]:
    return _build(session, engine.OBJECTION_TASK, OBJECTION_SEED)


def build_topics(session: Session) -> list[ml.Example]:
    return _build(session, engine.TOPIC_TASK, TOPIC_SEED)


def baseline_objections(session: Session, test: list[ml.Example]) -> ml.Metrics:
    return ml.score([(ex.y, engine.objection_rules(str(ex.raw))[0], 1.0) for ex in test])


def baseline_topics(session: Session, test: list[ml.Example]) -> ml.Metrics:
    def rule(text: str) -> str:
        objection, _ = engine.objection_rules(text)
        return engine.classify_topic(None, text, objection=objection, has={})

    return ml.score([(ex.y, rule(str(ex.raw)), 1.0) for ex in test])


def status(session: Session, task: str) -> list[dict[str, Any]]:
    from sqlalchemy import func

    corrections = int(session.scalar(select(func.count()).select_from(BrainFeedback)
                                     .where(BrainFeedback.task == task)) or 0)  # fmt: skip
    seed = OBJECTION_SEED if task == engine.OBJECTION_TASK else TOPIC_SEED
    return [
        {"label": "Ejemplos semilla", "value": str(sum(len(v) for v in seed.values()))},
        {"label": "Correcciones de vendedores", "value": str(corrections)},
        {
            "label": "Categorías",
            "value": str(len(pb.OBJECTIONS) if seed is OBJECTION_SEED else len(TOPIC_SEED)),
        },
    ]
