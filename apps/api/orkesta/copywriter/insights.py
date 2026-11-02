"""El asistente de redacción aprende de las respuestas: compara los correos que la empresa ya
envió (incluidas las versiones de las pruebas A/B) y saca lecciones con números propios —
qué largo de asunto, qué personalización y qué cierre consiguen más respuestas — más sus
correos ganadores para reutilizarlos.
"""

from __future__ import annotations

import re
import time
import uuid
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from orkesta.campaigns.models import Campaign, Message, Recipient
from orkesta.campaigns.service import step_content
from orkesta.campaigns.templating import html_to_text

MIN_SENT = 15  # envíos mínimos para juzgar un correo
_VAR = re.compile(r"\{\{\s*([a-z_]+)")
_WORD = re.compile(r"[\wáéíóúñü]+", re.IGNORECASE)
_CACHE: dict[uuid.UUID, tuple[float, dict[str, Any]]] = {}


def _features(subject: str, body: str) -> dict[str, Any]:
    text = html_to_text(body or "")
    variables = set(_VAR.findall(f"{subject} {body}"))
    last = text.strip().rstrip()[-200:]
    return {
        "subject_words": len(_WORD.findall(subject)),
        "words": len(_WORD.findall(text)),
        "personalized": bool({"empresa", "cargo", "reto", "industria"} & variables),
        "question": "?" in last,
        "first_name": bool({"nombre", "nombre_completo"} & variables),
    }


LESSONS: list[tuple[str, str, str, Any]] = [
    # (clave, texto del grupo que cumple, texto del otro, condición)
    ("subject_short", "los asuntos cortos (5 palabras o menos)", "los asuntos más largos",
     lambda f: 0 < f["subject_words"] <= 5),
    ("personalized", "los correos que mencionan la empresa, el cargo o el reto",
     "los que no lo hacen", lambda f: f["personalized"]),
    ("short_body", "los correos breves (90 palabras o menos)", "los más largos",
     lambda f: f["words"] <= 90),
    ("question", "los que cierran con una pregunta", "los que no", lambda f: f["question"]),
]  # fmt: skip


def performance(session: Session, tenant_id: uuid.UUID) -> list[dict[str, Any]]:
    rows = session.execute(
        select(Message.campaign_id, Message.step, Message.variant,
               func.count(func.distinct(Message.recipient_id)),
               func.count(func.distinct(Message.recipient_id)).filter(
                   Recipient.replied_at.is_not(None), Recipient.replied_at >= Message.sent_at))
        .join(Recipient, Recipient.id == Message.recipient_id)
        .where(Message.tenant_id == tenant_id)
        .group_by(Message.campaign_id, Message.step, Message.variant)
    ).all()  # fmt: skip
    campaigns = {
        c.id: c
        for c in session.scalars(select(Campaign).where(Campaign.id.in_({r[0] for r in rows})))
    }
    out = []
    for campaign_id, step, variant, sent, replied in rows:
        campaign = campaigns.get(campaign_id)
        if campaign is None or step >= len(campaign.steps) or not sent:
            continue
        subject, body = step_content(campaign.steps[step], variant)
        out.append({
            "campaign_id": str(campaign_id), "campaign": campaign.name, "step": int(step),
            "variant": variant, "subject": subject, "body": body, "sent": int(sent),
            "replied": int(replied), "reply_rate": round(100 * replied / sent, 1),
            "features": _features(subject, body),
        })  # fmt: skip
    return out


def _pct(value: float) -> str:
    return f"{value:.1f}".replace(".", ",") + " %"


def lessons(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    judged = [i for i in items if i["sent"] >= MIN_SENT]
    out = []
    for key, yes_text, no_text, test in LESSONS:
        yes = [i for i in judged if test(i["features"])]
        no = [i for i in judged if not test(i["features"])]
        if len(yes) < 2 or len(no) < 2:
            continue
        sent_y, sent_n = sum(i["sent"] for i in yes), sum(i["sent"] for i in no)
        rate_y = 100 * sum(i["replied"] for i in yes) / sent_y
        rate_n = 100 * sum(i["replied"] for i in no) / sent_n
        if sent_y + sent_n < 60 or abs(rate_y - rate_n) < 0.5:
            continue
        better, worse = (yes_text, no_text) if rate_y > rate_n else (no_text, yes_text)
        hi, lo = max(rate_y, rate_n), min(rate_y, rate_n)
        out.append({
            "key": key, "good": rate_y > rate_n,
            "text": f"En tus campañas, {better} obtienen {_pct(hi)} de respuesta frente a "
                    f"{_pct(lo)} de {worse}.",
            "rate_yes": round(rate_y, 1), "rate_no": round(rate_n, 1),
        })  # fmt: skip
    return out


def insights(session: Session, tenant_id: uuid.UUID) -> dict[str, Any]:
    cached = _CACHE.get(tenant_id)
    if cached and time.monotonic() - cached[0] < 600:
        return cached[1]
    items = performance(session, tenant_id)
    top = sorted((i for i in items if i["sent"] >= MIN_SENT and i["step"] == 0),
                 key=lambda i: (-i["reply_rate"], -i["sent"]))[:3]  # fmt: skip
    data = {
        "emails_sent": sum(i["sent"] for i in items),
        "lessons": lessons(items),
        "top": [{k: v for k, v in i.items() if k != "features"} for i in top],
    }
    _CACHE[tenant_id] = (time.monotonic(), data)
    return data


def learned_checks(data: dict[str, Any], subject: str, body: str) -> list[dict[str, Any]]:
    """Revisiones que salen de lo aprendido: el correo actual frente a lo que mejor responde."""
    features = _features(subject, body)
    checks = []
    tests = {key: test for key, _, _, test in LESSONS}
    for lesson in data.get("lessons", []):
        meets = bool(tests[lesson["key"]](features))
        ok = meets == lesson["good"]
        checks.append({
            "id": f"aprendido_{lesson['key']}", "status": "ok" if ok else "warn",
            "label": "Sigue lo que mejor te funciona" if ok
            else "Tus campañas sugieren otro enfoque",
            "tip": lesson["text"], "weight": 10,
        })  # fmt: skip
    return checks
