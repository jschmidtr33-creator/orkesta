"""Redacción que aprende entre todos (IA propia, paso 6).

Convierte cada correo enviado en rasgos de estilo abstractos (cómo abre, cómo cierra, largo,
cuántas preguntas, cómo es el asunto, en qué paso va) y aprende de todas las empresas cuáles
reciben más respuestas. Nunca guarda ni compara textos: solo esas categorías y sus cifras.

Con eso estima la probabilidad de respuesta de un correo mientras se escribe y sugiere el
cambio de estilo que más la sube. Los rankings públicos solo muestran estilos usados por varias
empresas, para que nadie pueda deducir los resultados de otra.
"""

from __future__ import annotations

import html
import logging
import re
import unicodedata
import uuid
from collections import defaultdict
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from orkesta.brain import ml

logger = logging.getLogger(__name__)
TASK = "redaccion"
YES, NO = "si", "no"
MAX_EXAMPLES = 60000
MIN_TENANTS = 3  # un estilo aparece en el ranking solo si lo usan al menos 3 empresas
MIN_SENDS = 30

_TOKEN = re.compile(r"\{\{[^}]*\}\}")
_TAGS = re.compile(r"<[^>]+>")
_MEETING = re.compile(r"reunion|llamada|agend|minutos|calendario|cafe|videollamada|conversar")
_GREETING = re.compile(r"^(hola|buen[oa]s?|estimad[oa]s?|saludos|querid[oa])\b")

GROUPS: dict[str, tuple[str, dict[str, str]]] = {
    "apertura": ("Cómo abre", {
        "saludo": "con un saludo", "nombre": "con el nombre de la persona",
        "pregunta": "con una pregunta", "empresa": "mencionando su empresa",
        "directo": "yendo directo al punto"}),
    "cierre": ("Cómo cierra", {
        "reunion": "proponiendo una reunión corta", "pregunta": "con una pregunta",
        "enlace": "con un enlace", "afirmacion": "con una afirmación"}),
    "largo": ("Largo", {"corto": "corto (menos de 60 palabras)",
                        "medio": "medio (60 a 130 palabras)", "largo": "largo (más de 130)"}),
    "preguntas": ("Preguntas", {"0": "sin preguntas", "1": "una sola pregunta",
                                "2+": "varias preguntas"}),
    "variables": ("Personalización", {"0": "sin variables", "1": "una variable",
                                      "2+": "dos o más variables"}),
    "asunto": ("Asunto", {"corto": "asunto corto (hasta 4 palabras)",
                          "medio": "asunto de 5 a 8 palabras", "largo": "asunto largo"}),
    "asunto_pregunta": ("Asunto con pregunta", {"si": "asunto en forma de pregunta",
                                                "no": "asunto sin pregunta"}),
}  # fmt: skip


def _plain(text: str) -> str:
    text = html.unescape(_TAGS.sub(" ", text.replace("<br", "\n<br").replace("</p>", "\n")))
    text = unicodedata.normalize("NFKD", text)
    return "".join(c for c in text if not unicodedata.combining(c)).lower()


def style(subject: str, body: str, step: int) -> dict[str, str]:
    """Rasgos de estilo de un correo (solo categorías, sin texto)."""
    tokens = len(_TOKEN.findall(subject + " " + body))
    text = _plain(_TOKEN.sub(" NOMBRE ", body))
    lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
    # La firma (última línea corta sin puntuación) no cuenta como cierre.
    while len(lines) > 1 and len(lines[-1].split()) <= 4 and not lines[-1].endswith("?"):
        lines.pop()
    words = len(" ".join(lines).split())
    first = lines[0] if lines else ""
    last = lines[-1] if lines else ""
    raw_first = _plain(body).splitlines()[0] if body.strip() else ""
    if first.rstrip().endswith("?"):
        opening = "pregunta"
    elif "{{nombre" in raw_first or "nombre" in first.split(",")[0].split():
        opening = "nombre"
    elif _GREETING.match(first):
        opening = "saludo"
    elif "{{empresa" in body.split("\n\n")[0].lower():
        opening = "empresa"
    else:
        opening = "directo"
    if _MEETING.search(last):
        closing = "reunion"
    elif last.endswith("?"):
        closing = "pregunta"
    elif "http" in last or "www." in last:
        closing = "enlace"
    else:
        closing = "afirmacion"
    questions = text.count("?")
    subject_words = len(_TOKEN.sub("X", subject).split())
    return {
        "apertura": opening,
        "cierre": closing,
        "largo": "corto" if words < 60 else "medio" if words <= 130 else "largo",
        "preguntas": "0" if questions == 0 else "1" if questions == 1 else "2+",
        "variables": "0" if tokens == 0 else "1" if tokens == 1 else "2+",
        "asunto": "corto" if subject_words <= 4 else "medio" if subject_words <= 8 else "largo",
        "asunto_pregunta": "si" if subject.strip().endswith("?") else "no",
        "paso": str(min(step + 1, 3)) if step >= 0 else "1",
    }


def features(st: dict[str, str]) -> ml.Features:
    x: ml.Features = {f"{k}:{v}": 1.0 for k, v in st.items()}
    x[f"paso_cierre:{st['paso']}|{st['cierre']}"] = 1.0
    return x


def _tenant_examples(session: Session, tenant_id: uuid.UUID) -> list[ml.Example]:
    from orkesta.campaigns.models import Campaign, Message, Recipient
    from orkesta.campaigns.service import step_content

    steps = {c.id: c.steps or [] for c in session.scalars(
        select(Campaign).where(Campaign.tenant_id == tenant_id))}  # fmt: skip
    rows = session.execute(
        select(Message.recipient_id, Message.campaign_id, Message.step, Message.variant,
               Message.sent_at, Recipient.replied_at)
        .join(Recipient, Recipient.id == Message.recipient_id)
        .where(Message.tenant_id == tenant_id)
        .order_by(Message.recipient_id, Message.sent_at)
    ).all()  # fmt: skip
    by_recipient: dict[Any, list[Any]] = defaultdict(list)
    for r in rows:
        by_recipient[r.recipient_id].append(r)
    cache: dict[tuple[Any, int, str], ml.Features | None] = {}
    out = []
    for recipient_id, sent in by_recipient.items():
        replied = sent[0].replied_at
        # La respuesta cuenta para el último correo enviado antes de ella; los anteriores no.
        before = [m for m in sent if replied is None or m.sent_at <= replied]
        for i, m in enumerate(before):
            key = (m.campaign_id, m.step, m.variant)
            if key not in cache:
                campaign_steps = steps.get(m.campaign_id) or []
                if m.step >= len(campaign_steps):
                    cache[key] = None
                else:
                    subject, body = step_content(campaign_steps[m.step], m.variant)
                    cache[key] = features(style(subject, body, m.step))
            x = cache[key]
            if x is None:
                continue
            ok = replied is not None and i == len(before) - 1
            out.append(ml.Example(f"r:{recipient_id}", x, YES if ok else NO, str(tenant_id)))
    return out


def build(session: Session) -> list[ml.Example]:
    from orkesta.core.db import set_context
    from orkesta.platform.models import Tenant

    examples: list[ml.Example] = []
    for tenant_id in session.scalars(select(Tenant.id)).all():
        set_context(session, tenant_id=tenant_id)
        examples += _tenant_examples(session, tenant_id)
        if len(examples) >= MAX_EXAMPLES:
            break
    set_context(session)
    return examples[:MAX_EXAMPLES]


def make() -> ml.NaiveBayes:
    return ml.NaiveBayes(alpha=1.0, min_count=1)


def baseline(session: Session, test: list[ml.Example]) -> ml.Metrics:
    """La regla anterior: cada empresa con sus plantillas y pruebas A/B, sin aprender de las
    demás. Sin información de estilo, lo más probable es la tasa propia de la empresa."""
    held = {ex.key for ex in test}
    rates: dict[str, list[int]] = defaultdict(lambda: [0, 0])
    for ex in build(session):
        if ex.key in held:
            continue
        rates[str(ex.raw)][0] += ex.y == YES
        rates[str(ex.raw)][1] += 1
    pairs = []
    for ex in test:
        yes, n = rates.get(str(ex.raw), [0, 0])
        pairs.append((ex.y, YES if n and yes / n > 0.5 else NO, 1.0))
    return ml.score(pairs)


def _contrib(model: ml.NaiveBayes, feat: str) -> float | None:
    if YES not in model.loglik or NO not in model.loglik or feat not in model._vocab:
        return None
    yes = model.loglik[YES].get(feat, model.unseen[YES])
    no = model.loglik[NO].get(feat, model.unseen[NO])
    return yes - no


def assess(session: Session, subject: str, body: str, step: int = 0) -> dict[str, Any] | None:
    """Probabilidad estimada de respuesta y el cambio de estilo que más la subiría (``None``
    si aún no hay modelo activo)."""
    from orkesta.brain import registry

    try:
        loaded = registry.active(session, TASK)
    except Exception:
        logger.exception("brain_copy_load")
        return None
    if not loaded or not isinstance(loaded[0], ml.NaiveBayes):
        return None
    model = loaded[0]
    st = style(subject, body, step)
    probability = model.predict_proba(features(st)).get(YES, 0.0)
    average = 1 / (1 + (2.718281828 ** (model.prior.get(NO, 0) - model.prior.get(YES, 0))))
    tips = []
    for group, (_title, labels) in GROUPS.items():
        current = _contrib(model, f"{group}:{st[group]}")
        if current is None:
            continue
        options = [(v, _contrib(model, f"{group}:{v}")) for v in labels if v != st[group]]
        scored = [(v, c) for v, c in options if c is not None]
        if not scored:
            continue
        value, best = max(scored, key=lambda vc: vc[1])
        gain = best - current
        if gain > 0.2:
            times = 2.718281828**gain
            tips.append((gain, f"Prueba {labels[value]} en lugar de {labels[st[group]]}: "
                               f"suele recibir más respuestas (×{times:.1f})."))  # fmt: skip
    tips.sort(reverse=True)
    return {
        "probability": round(probability, 3),
        "average": round(average, 3),
        "style": {g: GROUPS[g][1].get(st[g], st[g]) for g in GROUPS},
        "tips": [t for _, t in tips[:3]],
    }


def ranking(session: Session) -> list[dict[str, Any]]:
    """Tasa de respuesta por estilo en todas las empresas (solo estilos usados por varias)."""
    examples = build(session)
    stats: dict[str, dict[str, Any]] = defaultdict(lambda: {"n": 0, "yes": 0, "tenants": set()})
    for ex in examples:
        for feat in ex.x:
            group = feat.split(":", 1)[0]
            if group not in GROUPS:
                continue
            row = stats[feat]
            row["n"] += 1
            row["yes"] += ex.y == YES
            row["tenants"].add(ex.raw)
    out = []
    for group, (title, labels) in GROUPS.items():
        items = []
        for value, label in labels.items():
            found = stats.get(f"{group}:{value}")
            if not found or found["n"] < MIN_SENDS or len(found["tenants"]) < MIN_TENANTS:
                continue
            items.append({"value": value, "label": label, "sends": found["n"],
                          "reply_rate": round(found["yes"] / found["n"], 4)})  # fmt: skip
        if items:
            items.sort(key=lambda it: it["reply_rate"], reverse=True)
            out.append({"group": group, "title": title, "items": items})
    return out
