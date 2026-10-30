"""Utilidades de texto compartidas por los normalizadores."""

from __future__ import annotations

import re
import unicodedata
from functools import lru_cache

_SPACES = re.compile(r"\s+")
# Caracteres de control y de formato invisibles (BOM, espacios de ancho cero, marcas de dirección).
_INVISIBLE = re.compile("[\\x00-\\x08\\x0b-\\x1f\\x7f\\u200b-\\u200f\\u2028-\\u202f\\u2060\\ufeff]")


def clean(value: str | None) -> str:
    """Quita invisibles, normaliza Unicode (NFC) y colapsa espacios. ``None`` → ``""``."""
    if value is None:
        return ""
    text = unicodedata.normalize("NFC", str(value))
    text = _INVISIBLE.sub("", text)
    text = text.replace(" ", " ")
    return _SPACES.sub(" ", text).strip()


def strip_accents(value: str) -> str:
    """Elimina tildes y diacríticos: "Muñoz Ávila" → "Munoz Avila"."""
    decomposed = unicodedata.normalize("NFKD", value)
    return "".join(ch for ch in decomposed if not unicodedata.combining(ch))


@lru_cache(maxsize=200_000)
def fold(value: str) -> str:
    """Forma de comparación: sin tildes, minúsculas, solo letras, dígitos y espacios simples."""
    text = strip_accents(clean(value)).lower()
    text = text.replace("&", " y ")
    text = re.sub(r"[^a-z0-9]+", " ", text)
    return _SPACES.sub(" ", text).strip()


def has_letters(value: str) -> bool:
    return any(ch.isalpha() for ch in value)
