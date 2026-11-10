# ruff: noqa: E501  (textos de conversación: líneas largas a propósito)
"""Capa conversacional de Concertino (IA propia, sin servicios externos).

Le da a Concertino trato de persona: saluda por su nombre a quien pregunta, entiende la charla
(saludos, gracias, «¿qué puedes hacer?»), responde «¿qué vendo?» con el catálogo de la ficha,
recuerda la pregunta anterior para seguir el hilo («¿y en Miami?») y propone cómo continuar.
"""

from __future__ import annotations

import re
from typing import Any

from orkesta.concertino import engine
from orkesta.concertino.models import ProductFact
from orkesta.engine.normalize.text import fold
from orkesta.platform.models import User

_GREET = re.compile(
    r"^(hola|holi|buenas|buen dia|buenos dias|buenas tardes|buenas noches|hey|que tal|saludos)\b"
)
_THANKS = re.compile(
    r"^(gracias|muchas gracias|mil gracias|genial|perfecto|excelente|listo|ok|okey|vale|super|buenisimo|de acuerdo)\b"
)
_BYE = re.compile(r"^(chao|adios|hasta luego|nos vemos|bye)\b")
_WHO = re.compile(
    r"\b(quien eres|que eres|como te llamas|que (puedes|sabes) hacer|en que (me )?(puedes )?ayudar|como funcionas|para que sirves|ayuda)\b"
)
_CATALOG = re.compile(
    r"\b(que (le )?(vendo|vendemos|vende|ofrezco|ofrecemos|ofrece)\b(?! (a|al|para) )|"
    r"(que|cuales) (son )?(los |mis |nuestros )?(productos|servicios|paquetes|opciones)|"
    r"catalogo|portafolio|que tenemos para (vender|ofrecer))"
)
_FOLLOW = re.compile(r"^(y|e|pero|tambien|entonces|ok y|y si|y para|y en|y el|y la|y los|y las)\b")

OPENERS = ("Claro, {n}.", "Perfecto, {n}.", "Listo, {n}.", "Va, {n}.", "Bien, {n}.")


_SAID = re.compile(
    r"\b(me (dice|dijo|respondio|contesto|escribio|puso)|dice que|dijo que|responde que|nos dijo|el cliente|la clienta|el prospecto)\b"
)


def is_question(text: str) -> bool:
    """Una duda del vendedor («¿tienen…?», «¿hay…?»), no lo que dijo un cliente."""
    folded = fold(text)
    return (
        "?" in text or folded.startswith(("hay ", "tienen ", "tenemos ", "se puede", "puedo "))
    ) and not _SAID.search(folded)


def user_name(user: User) -> str:
    """Nombre de pila de quien usa Concertino (o el de su correo si no lo cargó)."""
    name = (user.full_name or "").strip().split(" ")[0]
    if not name:
        name = re.split(r"[._\-+0-9]", (user.email or "").split("@")[0])[0]
    return name[:1].upper() + name[1:].lower() if name else ""


def opener(name: str, turn: int) -> str:
    if not name:
        return ""
    return f"Hola, {name}." if turn == 0 else OPENERS[(turn - 1) % len(OPENERS)].format(n=name)


def greet(summary: str, name: str, turn: int) -> str:
    head = opener(name, turn)
    if not head or not summary:
        return summary or head
    if summary.startswith(("Hola", name)):
        return summary
    return f"{head} {summary}"


def short_name(fact: ProductFact) -> str:
    """«Delegación Silver: 5 entradas…» → «Delegación Silver»."""
    title = fact.title.strip()
    head = re.split(r":| — | \(", title, maxsplit=1)[0].strip()
    return head if 3 <= len(head) <= 70 else title[:70].rsplit(" ", 1)[0] + "…"


def _faqs(ctx: engine.Context) -> list[ProductFact]:
    return list(ctx.facts.get("pregunta") or [])


def starters(ctx: engine.Context) -> list[str]:
    """Preguntas para empezar, sacadas de la ficha de la empresa."""
    if not ctx.facts:
        return ["¿Qué puedes hacer?", "Me dijo que es muy caro", "¿A qué sector apunto?"]
    out = ["¿Qué vendo?", "Preséntame el producto en 30 segundos"]
    out += [f.title for f in _faqs(ctx)[:2]]
    out.append("Me dijo que es muy caro")
    return out[:5]


def _reply(summary: str, *, suggestions: list[str] | None = None,
           sections: list[dict[str, Any]] | None = None) -> dict[str, Any]:  # fmt: skip
    return {"summary": summary, "options": [], "tips": [], "sections": sections or [],
            "next_step": "", "missing": [], "suggestions": suggestions or []}  # fmt: skip


def small_talk(question: str, ctx: engine.Context, name: str) -> dict[str, Any] | None:
    """Saludos, gracias, despedidas y «¿qué puedes hacer?». None si es una consulta de trabajo."""
    folded = fold(question).strip(" ?¿!¡.")
    words = len(folded.split())
    if _WHO.search(folded) and words <= 8:
        has = bool(ctx.facts)
        return _reply(
            f"Hola, {name}. Soy Concertino, la IA de ventas propia de ORKESTA: no uso servicios "
            "externos. Aprendo de la ficha de producto de tu empresa, de las respuestas de tus "
            "campañas y de lo que te funciona a ti. "
            + ("Ya conozco tu producto: pregúntame qué vender, precios, cómo responder una objeción, "
               "cómo hablarle a un cargo o cómo seguir una cuenta." if has else
               "Todavía no tengo la ficha de tu producto; cuando la carguen en la página Producto, "
               "respondo con ella."),
            suggestions=starters(ctx),
        )  # fmt: skip
    if words > 4:
        return None
    if _GREET.search(folded):
        return _reply(f"Hola, {name}. ¿En qué te ayudo hoy? Puedo responder dudas del producto, "
                      "ayudarte con una objeción o preparar tu próximo paso con una cuenta.",
                      suggestions=starters(ctx))  # fmt: skip
    if _THANKS.search(folded):
        return _reply(f"Con gusto, {name}. Aquí estoy cuando me necesites.",
                      suggestions=["¿Cómo hago el seguimiento si no responde?", "¿Qué vendo?"])  # fmt: skip
    if _BYE.search(folded):
        return _reply(f"Hasta luego, {name}. Que te vaya muy bien con tus cuentas.")
    return None


def catalog(question: str, ctx: engine.Context) -> dict[str, Any] | None:
    """«¿Qué vendo?» → el catálogo de la ficha en palabras simples."""
    if not _CATALOG.search(fold(question)):
        return None
    main = engine._best(ctx, "producto")
    if main is None:
        return _reply("Todavía no tengo la ficha de tu producto. Cárgala en la página Producto y "
                      "desde ese momento te respondo con ella.")  # fmt: skip
    offers: list[str] = []
    seen: set[str] = set()
    for kind in ("producto", "precio"):
        for fact in ctx.facts.get(kind) or []:
            if fact is main or fact.meta.get("industria"):
                continue
            label = short_name(fact)
            key = fold(label)[:25]
            if key in seen or fold(label).startswith(
                ("calendario", "los 7", "escalera", "precios por")
            ):
                continue
            seen.add(key)
            offers.append(label)
    text = f"Vendes {engine._clean(main.title)}."
    publico = engine._best(ctx, "publico")
    if publico:
        text += f" Tu cliente ideal: {engine._clean(publico.title)}."
    sections = [{"title": "Lo que puedes ofrecer", "items": offers[:12]}] if offers else []
    suggestions = ["Preséntame el producto en 30 segundos"]
    priced = [short_name(f) for f in ctx.facts.get("precio") or [] if not f.meta.get("industria")]
    suggestions += [f"Precio de {o}" for o in priced
                    if not fold(o).startswith(("escalera", "precios por"))][:2]  # fmt: skip
    suggestions.append("¿Qué le ofrezco según el cargo?")
    return _reply(text, sections=sections, suggestions=suggestions)


def follow_up(question: str, history: list[dict[str, Any]]) -> str:
    """Si la pregunta continúa la anterior («¿y en Miami?»), devuelve la anterior para usarla
    como contexto (pesa menos que la nueva); si no, cadena vacía."""
    if not history:
        return ""
    folded = fold(question).strip(" ?¿")
    last = str(history[-1].get("question") or "")
    short = len(folded.split()) <= 6
    if last and short and (_FOLLOW.search(folded) or len(engine._terms(question)) <= 1):
        return last
    return ""


def from_sheet(question: str, ctx: engine.Context) -> dict[str, Any] | None:
    """Una pregunta frecuente de la ficha que responde esta consulta (para sumarla a un consejo)."""
    best = max(_faqs(ctx), key=lambda f: engine._overlap(question, f), default=None)
    if best is None or not best.answer or engine._overlap(question, best) < 4:
        return None
    return {"title": "Según tu ficha", "items": [best.answer]}


def suggestions_for(topic: str, ctx: engine.Context, question: str, shown: list[str]) -> list[str]:
    """Cómo seguir la conversación según lo que se acaba de responder."""
    faq = _faqs(ctx)
    asked = fold(question)
    related = sorted(faq, key=lambda f: engine._overlap(question, f), reverse=True)
    pool: list[str] = []
    if topic in ("producto", "general"):
        pool += [f.title for f in related[:3] if fold(f.title) not in asked]
        pool.append("Preséntame el producto en 30 segundos")
    elif topic in ("objecion", "respuesta"):
        pool += ["¿Cómo hago el seguimiento si no responde?", "¿Qué le ofrezco según el cargo?"]
        pool += [f.title for f in related[:1]]
    elif topic in ("perfil", "cuenta"):
        pool += ["¿Qué le ofrezco según el cargo?", "Ayúdame a preparar la reunión"]
    elif topic == "campana":
        pool += ["¿A qué sector apunto?", "Me dijo que es muy caro"]
    else:
        pool += ["¿Qué vendo?", "Me dijo que es muy caro"]
    out: list[str] = []
    for s in pool:
        if s and fold(s) not in {fold(x) for x in out + shown} and fold(s) != asked:
            out.append(s)
    return out[:3]


def product_reply(advice: dict[str, Any], question: str, ctx: engine.Context) -> dict[str, Any]:
    """Convierte la lista de la ficha en una respuesta conversada: primero la respuesta, luego
    lo relacionado."""
    hits: list[ProductFact] = advice.pop("_hits", [])
    if not hits:
        return advice
    top = hits[0]
    if top.kind == "pregunta" and top.answer:
        text = top.answer
        extra = top.detail
    else:
        text = top.title[0].upper() + top.title[1:]
        text = text if text.endswith(".") else f"{text}."
        extra = top.answer or top.detail
    if extra:
        text += f" {extra}"
    related = [f"{engine.KIND_LABELS.get(f.kind, f.kind)}: {short_name(f) if f.kind != 'pregunta' else f.title}"
               for f in hits[1:4]]  # fmt: skip
    advice["summary"] = text
    advice["sections"] = [{"title": "También te puede servir", "items": related}] if related else []
    return advice


def fallback(question: str, ctx: engine.Context) -> dict[str, Any]:
    """No hay nada en la ficha: propone lo más cercano en vez de una respuesta genérica."""
    facts = [f for items in ctx.facts.values() for f in items]
    near = sorted(facts, key=lambda f: engine._overlap(question, f), reverse=True)
    close = [f.title if f.kind == "pregunta" else f"¿Qué es {short_name(f)}?"
             for f in near[:6] if engine._overlap(question, f) > 0][:3]  # fmt: skip
    if close:
        return _reply("No encontré eso tal cual en la ficha. ¿Te refieres a alguno de estos temas?",
                      suggestions=close)  # fmt: skip
    return _reply(
        "No tengo eso en la ficha todavía. Puedo ayudarte con dudas del producto, una objeción, "
        "cómo hablarle a un cargo, una cuenta, una campaña, una propuesta o una reunión. "
        "Cuéntame el caso con tus palabras.",
        suggestions=starters(ctx),
    )
