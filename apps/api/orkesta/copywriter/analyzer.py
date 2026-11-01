# ruff: noqa: E501  (textos de redacción: líneas largas a propósito)
"""Revisor de correos en frío: puntúa asunto y cuerpo con buenas prácticas de prospección
(longitud, personalización, una sola llamada a la acción, foco en el cliente, palabras que
activan filtros de spam, clichés y consistencia del trato)."""

from __future__ import annotations

import re
from typing import Any

from orkesta.campaigns.templating import html_to_text
from orkesta.copywriter.knowledge import CLICHES, SPAM_WORDS
from orkesta.engine.normalize.text import fold

_VAR = re.compile(r"\{\{\s*([a-z_]+)[^}]*\}\}")
_LINK = re.compile(r"https?://|<a\s", re.IGNORECASE)
_WORD = re.compile(r"[\wáéíóúñü]+", re.IGNORECASE)


def _check(cid: str, status: str, label: str, tip: str = "", weight: int = 10) -> dict[str, Any]:
    return {"id": cid, "status": status, "label": label, "tip": tip, "weight": weight}


def analyze(subject: str, body_html: str, *, step: int = 0, language: str = "es") -> dict[str, Any]:
    es = language != "en"
    text = html_to_text(body_html or "")
    plain = _VAR.sub(lambda m: m.group(1), text)
    words = _WORD.findall(plain)
    n = len(words)
    sentences = [s for s in re.split(r"[.!?]+\s", plain) if _WORD.search(s)]
    folded = fold(plain)
    checks: list[dict[str, Any]] = []

    # Asunto
    if step == 0 or subject.strip():
        sw = len(_WORD.findall(subject))
        if not subject.strip():
            checks.append(_check("asunto", "bad", "Falta el asunto" if es else "Missing subject",
                                 "Escribe un asunto de 2 a 7 palabras." if es else "2-7 words.", 15))  # fmt: skip
        elif sw > 9 or len(subject) > 60:
            checks.append(_check("asunto", "warn", "Asunto largo" if es else "Long subject",
                                 "Los asuntos de 2 a 7 palabras se abren más." if es else
                                 "2-7 word subjects get more opens.", 10))  # fmt: skip
        else:
            checks.append(
                _check(
                    "asunto",
                    "ok",
                    "Asunto de buena longitud" if es else "Good subject length",
                    weight=10,
                )
            )
        letters = [c for c in subject if c.isalpha()]
        if letters and sum(c.isupper() for c in letters) / len(letters) > 0.6:
            checks.append(_check("mayusculas", "bad", "Asunto en mayúsculas" if es else "All caps subject",
                                 "Las mayúsculas parecen publicidad y activan filtros." if es else
                                 "Caps look like ads and trigger filters.", 10))  # fmt: skip
        if "!" in subject:
            checks.append(_check("exclamacion_asunto", "warn", "Exclamación en el asunto" if es else
                                 "Exclamation in subject", "Quítala: suena a promoción." if es else
                                 "Remove it: it reads as promotional.", 5))  # fmt: skip

    # Longitud del cuerpo
    low, high = (50, 125) if step == 0 else (25, 90)
    if n < low * 0.6:
        checks.append(_check("longitud", "warn", f"Muy corto ({n} palabras)" if es else f"Too short ({n} words)",
                             "Explica por qué escribes y qué valor aportas." if es else
                             "Say why you're writing and the value.", 10))  # fmt: skip
    elif n > high:
        checks.append(_check("longitud", "warn" if n < high * 1.6 else "bad",
                             f"Largo ({n} palabras)" if es else f"Long ({n} words)",
                             f"Apunta a {low}-{high} palabras: se lee en el celular en 20 segundos." if es else
                             f"Aim for {low}-{high} words.", 15))  # fmt: skip
    else:
        checks.append(
            _check(
                "longitud",
                "ok",
                f"Buena longitud ({n} palabras)" if es else f"Good length ({n} words)",
                weight=15,
            )
        )

    # Personalización
    variables = set(_VAR.findall((subject or "") + " " + (body_html or "")))
    if {"nombre", "nombre_completo"} & variables and (
        {"empresa", "cargo", "reto", "industria"} & variables
    ):
        checks.append(
            _check("personalizacion", "ok", "Personalizado" if es else "Personalized", weight=15)
        )
    elif variables:
        checks.append(_check("personalizacion", "warn", "Poca personalización" if es else "Light personalization",
                             "Menciona su empresa, cargo o reto ({{empresa}}, {{cargo}}, {{reto}})." if es else
                             "Mention company, title or challenge.", 15))  # fmt: skip
    else:
        checks.append(_check("personalizacion", "bad", "Sin personalización" if es else "Not personalized",
                             "Usa al menos {{nombre}} y {{empresa}}." if es else "Use at least name and company.", 15))  # fmt: skip

    # Llamada a la acción
    questions = plain.count("?")
    if questions == 0:
        checks.append(_check("cta", "bad", "Sin llamada a la acción" if es else "No call to action",
                             "Cierra con una pregunta concreta y fácil de responder." if es else
                             "End with one concrete, easy question.", 15))  # fmt: skip
    elif questions > 2:
        checks.append(_check("cta", "warn", f"{questions} preguntas" if es else f"{questions} questions",
                             "Deja una sola pregunta principal para facilitar la respuesta." if es else
                             "Keep a single main question.", 10))  # fmt: skip
    else:
        checks.append(
            _check(
                "cta",
                "ok",
                "Una llamada a la acción clara" if es else "Clear call to action",
                weight=15,
            )
        )

    # Foco en el cliente
    if es:
        we = len(
            re.findall(r"\b(?:nosotros|nuestr[oa]s?|somos|nuestra empresa|mi empresa)\b", folded)
        )
        you = len(re.findall(r"\b(?:tu|tus|te|ti|usted|su|sus|le|contigo|tienes|puedes)\b", folded))
    else:
        we = len(re.findall(r"\b(?:we|our|us|i)\b", folded))
        you = len(re.findall(r"\b(?:you|your|yours)\b", folded))
    if we > you and we >= 2:
        checks.append(_check("foco", "warn", "Habla más de ti que del cliente" if es else "Too much about you",
                             "Cambia frases sobre tu empresa por lo que gana el cliente." if es else
                             "Reframe around what they gain.", 10))  # fmt: skip
    else:
        checks.append(
            _check("foco", "ok", "Enfocado en el cliente" if es else "Customer-focused", weight=10)
        )

    # Spam y clichés
    lang = "es" if es else "en"
    raw = (subject + " " + plain).lower()
    spam = [
        w
        for w in SPAM_WORDS[lang]
        if (w.lower() in raw if re.search(r"[^\w\s]", w) else fold(w) in fold(raw))
    ]
    if spam:
        checks.append(_check("spam", "bad" if len(spam) > 2 else "warn",
                             ("Palabras de spam: " if es else "Spam words: ") + ", ".join(spam[:4]),
                             "Reemplázalas: aumentan la probabilidad de caer en spam." if es else
                             "Replace them to avoid spam folders.", 10))  # fmt: skip
    else:
        checks.append(
            _check("spam", "ok", "Sin palabras de spam" if es else "No spam words", weight=10)
        )
    cliches = [c for c in CLICHES[lang] if fold(c) in folded]
    if cliches:
        checks.append(_check("cliches", "warn", ("Frases trilladas: " if es else "Clichés: ") + ", ".join(cliches[:3]),
                             "Empieza directo con algo relevante para la persona." if es else
                             "Open with something relevant to them.", 5))  # fmt: skip

    # Legibilidad, enlaces y trato
    avg = n / max(1, len(sentences))
    if avg > 24:
        checks.append(_check("frases", "warn", "Frases largas" if es else "Long sentences",
                             "Divide las frases de más de 20 palabras." if es else "Split sentences over 20 words.", 5))  # fmt: skip
    links = len(_LINK.findall(body_html or ""))
    if links > 2 or (step == 0 and links > 1):
        checks.append(_check("enlaces", "warn", f"{links} enlaces" if es else f"{links} links",
                             "En el primer correo, máximo un enlace: mejora la entrega." if es else
                             "Max one link in the first email.", 5))  # fmt: skip
    if plain.count("!") > 1:
        checks.append(_check("exclamaciones", "warn", "Muchas exclamaciones" if es else "Too many exclamations",
                             "Usa máximo una." if es else "Use one at most.", 5))  # fmt: skip
    if es:
        tu = len(re.findall(r"\b(?:tu|tus|te|ti|tienes|puedes|quieres|contigo)\b", folded))
        usted = len(re.findall(r"\b(?:usted|ustedes|le parece|podria usted)\b", folded))
        if tu and usted:
            checks.append(
                _check(
                    "trato",
                    "warn",
                    "Mezcla tú y usted",
                    "Elige un solo trato en todo el correo.",
                    5,
                )
            )

    total = sum(c["weight"] for c in checks)
    earned = sum(c["weight"] * {"ok": 1.0, "warn": 0.5, "bad": 0.0}[c["status"]] for c in checks)
    score = round(100 * earned / total) if total else 0
    return {"score": score, "words": n, "checks": checks}
