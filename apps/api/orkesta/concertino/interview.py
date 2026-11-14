# ruff: noqa: E501
"""Concertino aprende lo que vende la empresa en la misma conversación.

Si a la empresa le falta lo esencial de su producto (qué vende, a quién, qué problema resuelve
y qué resultado logra), Concertino lo pregunta en el chat, una pregunta a la vez, y guarda cada
respuesta en la memoria de la empresa (la ficha del producto). Las demás preguntas (diferencial,
caso de éxito, precio, competencia y objeciones) se pueden saltar. Al terminar pregunta qué
necesita la persona: una secuencia de correos, responder una objeción, preparar una reunión…

También se puede volver a entrenar: «actualizar mi producto».
"""

from __future__ import annotations

import re
import uuid
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from orkesta.concertino.models import ProductFact
from orkesta.engine.normalize.text import fold
from orkesta.platform.models import User

QUESTIONS: list[dict[str, str]] = [
    {"kind": "producto", "question": "Cuéntame qué vendes, con detalle: qué es, cómo funciona y qué incluye.",
     "example": "Una plataforma de prospección B2B: encuentra a quien decide, completa su correo y envía las campañas desde tu buzón."},
    {"kind": "publico", "question": "¿A quién se lo vendes? Sector, país, tamaño de empresa y el cargo que decide la compra.",
     "example": "Empresas de 50 a 500 empleados en Colombia y México; decide la gerencia comercial."},
    {"kind": "problema", "question": "¿Qué problema concreto le resuelves a ese cliente? ¿Qué le duele hoy?",
     "example": "Su equipo pierde horas buscando contactos y escribiendo correos uno por uno."},
    {"kind": "valor", "question": "¿Qué resultado logra el cliente cuando te compra? Si puedes, con un número.",
     "example": "Triplica las reuniones agendadas sin sumar personas al equipo."},
    {"kind": "diferencial", "question": "¿Qué te diferencia de las otras opciones que tiene tu cliente?",
     "example": "Datos de Latinoamérica verificados y soporte en español."},
    {"kind": "prueba", "question": "¿Qué caso de éxito puedo contar? Cliente o sector, y el resultado con un número.",
     "example": "Una empresa de software pasó de 4 a 12 reuniones al mes en 60 días."},
    {"kind": "precio", "question": "¿Cuál es la referencia de precio o el rango de inversión?",
     "example": "Desde 99 dólares al mes por usuario."},
    {"kind": "competidor", "question": "¿Contra quién compites y en qué eres distinto? (nombre y diferencia)",
     "example": "Apollo: nosotros tenemos mejores datos de Latinoamérica."},
    {"kind": "objecion", "question": "¿Qué objeciones escuchas más y cómo las respondes hoy?",
     "example": "«Es caro» → lo comparo con el costo de no tener reuniones."},
]  # fmt: skip
REQUIRED = ("producto", "publico", "problema", "valor")
KINDS = [q["kind"] for q in QUESTIONS]
_SKIP = re.compile(r"^(saltar|salta|siguiente|paso|despues|luego|no se|no lo se|omitir|ninguna?)\b")
_KEEP = re.compile(r"^(igual|mantener|mantenla|mantenlo|dejalo|dejala|asi esta bien|esta bien)\b")
_RESTART = re.compile(
    r"\b(actualiza|actualizar|cambia|cambiar|entrena|entrenar|ensena|ensenar|corrige|corregir)"
    r"(te)?\b.{0,25}\b(producto|lo que vendo|que vendo|lo que vendemos|mi empresa)\b"
)
NEEDS = ["Crea una secuencia de 3 correos", "Me dijo que es muy caro",
         "Preparar una reunión", "¿A qué sector le apunto?"]  # fmt: skip


def _facts(session: Session, tenant_id: uuid.UUID) -> dict[str, list[ProductFact]]:
    out: dict[str, list[ProductFact]] = {}
    for f in session.scalars(select(ProductFact).where(ProductFact.tenant_id == tenant_id)):
        out.setdefault(f.kind, []).append(f)
    return out


def _step(kind: str) -> dict[str, Any]:
    q = next(q for q in QUESTIONS if q["kind"] == kind)
    return {**q, "step": KINDS.index(kind) + 1, "total": len(KINDS),
            "required": kind in REQUIRED}  # fmt: skip


def pending(session: Session, tenant_id: uuid.UUID) -> dict[str, Any] | None:
    """La primera pregunta pendiente si a la empresa le falta lo esencial; si no, None."""
    have = _facts(session, tenant_id)
    if all(have.get(k) for k in REQUIRED):
        return None
    return _step(next(k for k in KINDS if not have.get(k)))


def wants_restart(question: str) -> bool:
    return bool(_RESTART.search(fold(question)))


def _reply(summary: str, step: dict[str, Any] | None, suggestions: list[str]) -> dict[str, Any]:
    return {"case_id": None, "topic": "producto", "topic_label": "", "objection": None,
            "objection_label": None, "formal": False, "summary": summary, "options": [],
            "tips": [], "sections": [], "next_step": "", "missing": [], "sources": [],
            "suggestions": suggestions, "interview": step}  # fmt: skip


def ask_step(step: dict[str, Any], *, intro: str = "", current: str = "") -> dict[str, Any]:
    text = (
        intro + " " if intro else ""
    ) + f"Pregunta {step['step']} de {step['total']}: {step['question']}"
    if current:
        text += f"\n\nHoy tengo guardado: «{current[:300]}». Escribe la nueva versión o «igual» para mantenerla."
    else:
        text += f"\n\nPor ejemplo: {step['example']}"
    chips = ["Saltar"] if not step["required"] else []
    if current:
        chips = ["Igual", *chips]
    return _reply(text, step, chips)


def start(session: Session, tenant_id: uuid.UUID, name: str, *, can_edit: bool) -> dict[str, Any]:
    """«Actualizar mi producto»: vuelve a pasar por todas las preguntas, mostrando lo guardado."""
    if not can_edit:
        return _reply("Lo que vende tu empresa lo actualiza la administración o la jefatura. "
                      "Si algo está desactualizado, avísales y lo corrijo con ellos.", None, NEEDS)  # fmt: skip
    have = _facts(session, tenant_id)
    first = _step(KINDS[0])
    current = next((f.title for f in have.get(KINDS[0], []) if f.status == "confirmado"), "")
    return ask_step(first, intro=f"Perfecto, {name}. Repasemos lo que vendes.", current=current)


def answer(session: Session, *, tenant_id: uuid.UUID, user: User, can_edit: bool, kind: str,
           text: str, name: str) -> dict[str, Any]:  # fmt: skip
    """Guarda la respuesta a la pregunta ``kind`` y devuelve la siguiente (o el cierre)."""
    have = _facts(session, tenant_id)
    existed = bool(have.get(kind))
    confirmed = [f for f in have.get(kind, []) if f.status == "confirmado"]
    folded = fold(text).strip(" .¡!¿?")
    step = _step(kind)
    if existed and confirmed and not can_edit:
        return start(session, tenant_id, name, can_edit=False)
    if _SKIP.match(folded) and not step["required"]:
        if not existed:
            session.add(ProductFact(tenant_id=tenant_id, created_by=user.id, kind=kind,
                                    title="(sin respuesta)", status="descartado", source="chat"))  # fmt: skip
    elif _KEEP.match(folded) and confirmed:
        pass
    elif len(folded) < 4:
        return ask_step(step, intro="Necesito un poco más de detalle para venderlo bien.")
    else:
        lines = [x.strip() for x in text.strip().splitlines() if x.strip()]
        title, detail = lines[0][:300], text.strip()[:4000]
        if confirmed:
            confirmed[0].title, confirmed[0].detail = title, detail
            confirmed[0].source = "chat"
        else:
            fact = ProductFact(tenant_id=tenant_id, created_by=user.id, kind=kind, title=title,
                               detail=detail, status="confirmado", source="chat")  # fmt: skip
            if kind == "objecion":
                fact.answer = detail
            session.add(fact)
    session.flush()
    have = _facts(session, tenant_id)
    later = KINDS[KINDS.index(kind) + 1 :]
    # Al actualizar se repasan todas; al aprender por primera vez, solo las que faltan.
    nxt = later[0] if (existed and later) else next((k for k in later if not have.get(k)), None)
    if nxt is not None:
        current = next((f.title for f in have.get(nxt, []) if f.status == "confirmado"), "")
        return ask_step(_step(nxt), intro="Anotado.", current=current)
    missing = [k for k in REQUIRED if not have.get(k)]
    if missing:
        return ask_step(_step(missing[0]), intro="Me falta esta para poder venderlo bien.")
    saved = [f for k in KINDS for f in have.get(k, []) if f.status == "confirmado"]
    summary = (f"Listo, {name}. Guardé en la memoria de tu empresa lo que vendes "
               f"({len(saved)} datos): {saved[0].title[:140]}… Ya puedo venderlo contigo.\n\n"
               "¿Qué necesitas hoy? Puedo crear una secuencia de correos, ayudarte a responder "
               "una objeción, preparar una reunión o una propuesta, o decirte a qué sector "
               "apuntar.")  # fmt: skip
    return _reply(summary, None, NEEDS)
