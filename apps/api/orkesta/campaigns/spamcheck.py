# ruff: noqa: E501  (consejos largos para el usuario)
"""Revisor de spam antes de lanzar: lo que miran los filtros de Gmail, Outlook y los corporativos.

Revisa cada correo de la secuencia (palabras gatillo, enlaces, acortadores, imágenes, mayúsculas,
signos de exclamación, largo, asunto y personalización) y devuelve un puntaje de 0 a 100 (100 =
limpio) con lo que conviene cambiar. No consulta servicios externos ni gasta créditos.
"""

from __future__ import annotations

import html
import re
from typing import Any

from orkesta.engine.normalize.text import fold

# Palabras y frases que los filtros asocian con correo masivo o promocional.
TRIGGERS = (
    "gratis", "gratuito", "gratuita", "100%", "100 %", "garantizado", "garantizada", "oferta",
    "promocion", "descuento", "urgente", "ultima oportunidad", "gana dinero", "ganar dinero",
    "sin costo", "sin compromiso", "haz clic", "haga clic", "click aqui", "clic aqui", "compra ahora",
    "compre ahora", "oferta limitada", "por tiempo limitado", "solo hoy", "felicidades", "ganador",
    "premio", "dinero facil", "ingresos extra", "sin riesgo", "mejor precio", "precio increible",
    "free", "guaranteed", "act now", "limited time", "click here", "buy now", "winner",
    "risk free", "no cost", "special promotion", "$$$",
)  # fmt: skip
_TRIGGER_RE = re.compile(
    r"(?<![a-z0-9])(" + "|".join(re.escape(t) for t in TRIGGERS) + r")(?![a-z0-9])"
)
_SHORTENERS = re.compile(
    r"\b(bit\.ly|tinyurl\.com|goo\.gl|t\.co|ow\.ly|cutt\.ly|rebrand\.ly|is\.gd|buff\.ly|shorturl\.at)/",
    re.I,
)
_URL = re.compile(r"https?://[^\s<>\"')]+", re.I)
_TAG = re.compile(r"<[^>]+>")
_VAR = re.compile(r"\{\{\s*([a-z_]+)")
_SPIN = re.compile(r"\[\[[^\]]*\|[^\]]*\]\]")


def _text(body: str) -> str:
    body = re.sub(r"<br\s*/?>|</p>", "\n", body or "", flags=re.I)
    return html.unescape(_TAG.sub(" ", body))


def check_email(subject: str, body: str, *, first: bool) -> dict[str, Any]:
    """Puntaje (0-100) e issues de un correo. ``first``: es el primer correo de la secuencia."""
    issues: list[dict[str, Any]] = []

    def add(points: int, text: str, tip: str) -> None:
        issues.append({"points": points, "text": text, "tip": tip})

    plain = _text(body)
    folded_body = fold(plain).lower()
    folded_subject = fold(subject or "").lower()
    found = sorted({m.group(1) for m in _TRIGGER_RE.finditer(f"{folded_subject} {folded_body}")})
    if found:
        add(
            min(30, 8 * len(found)),
            f"Palabras que los filtros asocian con publicidad: {', '.join(found[:6])}.",
            "Cámbialas por lenguaje de conversación: en vez de «oferta» o «gratis», habla del problema y del resultado.",
        )
    links = _URL.findall(body or "")
    if _SHORTENERS.search(body or ""):
        add(
            20,
            "Usa un acortador de enlaces (bit.ly u otro).",
            "Los acortadores son la señal favorita de los filtros: pon el enlace completo de tu sitio.",
        )
    if len(set(links)) > 2:
        add(
            15,
            f"Tiene {len(set(links))} enlaces.",
            "Deja como máximo uno; en un correo en frío lo ideal es ninguno.",
        )
    elif first and links:
        add(
            5,
            "El primer correo lleva un enlace.",
            "En el primer contacto es mejor no poner enlaces: pide una respuesta, no un clic.",
        )
    if re.search(r"<img\b", body or "", re.I):
        add(
            15 if first else 8,
            "Tiene imágenes.",
            "Los correos con imágenes parecen boletines. Usa solo texto, sobre todo en el primero.",
        )
    caps = [w for w in re.findall(r"\b[A-ZÁÉÍÓÚÑ]{4,}\b", plain) if w not in ("ORKESTA",)]
    if len(caps) >= 3:
        add(
            10,
            "Muchas palabras en MAYÚSCULAS.",
            "Escribe como en una conversación: las mayúsculas se leen como gritos y como publicidad.",
        )
    if subject and subject.upper() == subject and re.search(r"[A-Z]{4,}", subject):
        add(
            15,
            "El asunto está todo en mayúsculas.",
            "Usa un asunto corto en minúsculas, como lo escribiría una persona.",
        )
    if (subject or "").count("!") >= 1:
        add(
            8,
            "El asunto lleva signos de exclamación.",
            "Quítalos: un asunto tranquilo se abre más y pasa mejor los filtros.",
        )
    if plain.count("!") > 2:
        add(5, "Muchos signos de exclamación en el texto.", "Usa uno como máximo.")
    words = len(plain.split())
    if words > 180:
        add(
            10,
            f"Es largo ({words} palabras).",
            "Un correo en frío funciona mejor con 50 a 120 palabras: problema, valor y una pregunta.",
        )
    elif 0 < words < 20:
        add(
            5,
            f"Es muy corto ({words} palabras).",
            "Agrega por qué le escribes a esa persona y una pregunta concreta.",
        )
    if len(subject or "") > 60:
        add(5, "El asunto es largo.", "Apunta a 2 a 6 palabras.")
    if first and re.match(r"^\s*(re|fw|fwd)\s*:", subject or "", re.I):
        add(
            15,
            "El asunto finge ser una respuesta («RE:»).",
            "Es engañoso y los filtros lo castigan: usa un asunto honesto.",
        )
    if first and not _VAR.search(f"{subject} {body}"):
        add(
            10,
            "No está personalizado.",
            "Incluye al menos el nombre o la empresa ({{nombre}}, {{empresa}}): correos idénticos a muchas personas se marcan como masivos.",
        )
    if re.search(r"[$€]\s?\d|\d\s?%", plain) and first:
        add(
            5,
            "Menciona precios o porcentajes en el primer correo.",
            "Guarda el precio para la conversación; en el primero habla del problema.",
        )
    score = max(0, 100 - sum(int(i["points"]) for i in issues))
    level = "bien" if score >= 85 else "revisar" if score >= 65 else "riesgo"
    return {
        "score": score,
        "level": level,
        "issues": issues,
        "varied": bool(_SPIN.search(f"{subject} {body}")),
    }


def check_steps(steps: list[dict[str, Any]]) -> dict[str, Any]:
    """Revisa todos los correos de la secuencia (y sus variantes A/B)."""
    out = []
    for i, step in enumerate(steps or []):
        versions = [("A", step.get("subject", ""), step.get("body", ""))]
        b = step.get("variant_b") or {}
        if b:
            versions.append(
                (
                    "B",
                    b.get("subject") or step.get("subject", ""),
                    b.get("body") or step.get("body", ""),
                )
            )
        for name, subject, body in versions:
            result = check_email(subject, body, first=i == 0)
            out.append({"step": i + 1, "variant": name if len(versions) > 1 else "", **result})
    score = min((r["score"] for r in out), default=100)
    level = "bien" if score >= 85 else "revisar" if score >= 65 else "riesgo"
    tips = []
    if out and not any(r["varied"] for r in out):
        tips.append(
            "Agrega variaciones con [[Hola|Buen día|Qué tal]]: cada persona recibe una versión distinta y los filtros no ven cientos de correos idénticos."
        )
    return {"score": score, "level": level, "emails": out, "tips": tips}
