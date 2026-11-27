# ruff: noqa: E501
"""Corrector de ortografía de Concertino (propio, sin servicios externos).

Lo que la persona le enseña (su producto, sus respuestas) se guarda y se usa con buena ortografía:
- mayúsculas cruzadas al teclear rápido: «TEner» → «Tener» (las siglas como LATAM o B2B se respetan);
- tildes que faltan: «aplicacion» → «aplicación», «tambien» → «también», «compania» → «compañía».
  El diccionario (tildes.json) sale de las 100.000 palabras más usadas del español y solo trae
  palabras cuya forma sin tilde casi no se usa: «esta/está», «solo», «publico» o «mas» no se tocan
  porque las dos formas existen;
- espacios dobles, espacio antes de la coma, mayúscula al empezar cada oración.
"""

from __future__ import annotations

import json
import re
from functools import lru_cache
from pathlib import Path

_WORD = re.compile(r"[A-Za-zÁÉÍÓÚÜÑáéíóúüñ]+")
_TOKEN = re.compile(r"\S+")
_LINK = re.compile(r"@|://|^www\.|\.(com|co|mx|net|org|io|app|ai|es|cl|pe|ar)\b", re.I)
_CROSSED = re.compile(r"^[A-ZÁÉÍÓÚÑ]{2}[a-záéíóúüñ]{2,}$")
# Frecuentes en ventas cuya forma sin tilde es un verbo poco usado en este contexto
_EXTRA = {"exito": "éxito", "numero": "número", "numeros": "números", "credito": "crédito",
          "rapido": "rápido", "rapida": "rápida", "ultimo": "último", "ultima": "última",
          "publico": "público", "metrica": "métrica", "metricas": "métricas",
          "tecnica": "técnica", "tecnicas": "técnicas", "practica": "práctica",
          "practicas": "prácticas", "estrategico": "estratégico", "logistica": "logística",
          "telefonico": "telefónico", "automatico": "automático", "automatica": "automática"}  # fmt: skip


@lru_cache(maxsize=1)
def _accents() -> dict[str, str]:
    data: dict[str, str] = json.loads((Path(__file__).with_name("tildes.json")).read_text("utf-8"))
    data.update(_EXTRA)
    return data


def _case_like(model: str, word: str) -> str:
    if model.isupper() and len(model) > 1:
        return word.upper()
    if model[:1].isupper():
        return word[:1].upper() + word[1:]
    return word


def _word(m: re.Match[str], changes: list[tuple[str, str]]) -> str:
    w = m.group(0)
    fixed = w
    if _CROSSED.match(fixed):
        fixed = fixed[0] + fixed[1:].lower()
    if not (fixed.isupper() and len(fixed) > 1):  # siglas en mayúscula: se respetan
        accented = _accents().get(fixed.lower())
        if accented:
            fixed = _case_like(fixed, accented)
    if fixed != w:
        changes.append((w, fixed))
    return fixed


def corregir(text: str) -> tuple[str, list[tuple[str, str]]]:
    """(texto corregido, [(antes, después)…]) para poder contarle a la persona qué se corrigió."""
    if not text:
        return text, []
    changes: list[tuple[str, str]] = []
    # Correos, enlaces y dominios no se tocan
    out = _TOKEN.sub(
        lambda t: t.group(0)
        if _LINK.search(t.group(0))
        else _WORD.sub(lambda m: _word(m, changes), t.group(0)),
        text,
    )
    out = re.sub(r"\bentre si\b", "entre sí", out)
    out = re.sub(r"[ \t]{2,}", " ", out)
    out = re.sub(r" +([,.;:])", r"\1", out)
    out = re.sub(r"([,;])(?=[A-Za-zÁÉÍÓÚÑáéíóúñ])", r"\1 ", out)
    # Mayúscula al empezar cada oración (no después de abreviaturas cortas como «p. ej.»)
    out = re.sub(
        r"(^|[.!?]\s+|\n\s*)([a-záéíóúñ])(?![^\s]*[@/]|[^\s]*\.[a-z])",
        lambda m: m.group(1) + m.group(2).upper(),
        out,
    )
    return out, changes


def fix(text: str) -> str:
    return corregir(text)[0]


def resumen(changes: list[tuple[str, str]], limit: int = 4) -> str:
    """«aplicacion → aplicación, TEner → Tener» (sin repetir)."""
    seen: list[str] = []
    for a, b in changes:
        item = f"{a} → {b}"
        if item not in seen:
            seen.append(item)
    extra = f" y {len(seen) - limit} más" if len(seen) > limit else ""
    return ", ".join(seen[:limit]) + extra
