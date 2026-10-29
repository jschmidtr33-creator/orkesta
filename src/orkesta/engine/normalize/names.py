"""Normalización de nombres de personas, con convenciones hispanas.

- Nombres compuestos: "María José" → nombre principal "maria", completo "mariajose".
- Dos apellidos: el primero es el principal (convención LATAM); el segundo se conserva.
- Partículas: "de la Cruz" forma un solo apellido; se generan las variantes con y sin partícula.
- Se retiran tratamientos y credenciales ("Ing.", "Dra.", "MBA"), apodos entre paréntesis y emojis.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from orkesta.engine.normalize.text import clean, fold, has_letters

PARTICLES = frozenset(
    {
        "de",
        "del",
        "la",
        "las",
        "los",
        "y",
        "e",
        "i",
        "da",
        "das",
        "do",
        "dos",
        "di",
        "du",
        "van",
        "von",
        "der",
        "den",
        "le",
        "lo",
        "san",
        "santa",
        "mac",
        "st",
    }
)

# Tratamientos y credenciales que se retiran del nombre (comparados en forma plegada).
HONORIFICS = frozenset(
    {
        "sr",
        "sra",
        "srta",
        "dr",
        "dra",
        "ing",
        "inga",
        "lic",
        "licda",
        "arq",
        "abg",
        "abog",
        "mtro",
        "mtra",
        "prof",
        "phd",
        "mba",
        "msc",
        "cpa",
        "pmp",
        "mr",
        "mrs",
        "ms",
        "miss",
        "eng",
        "esq",
        "jr",
        "sr.",
        "ii",
        "iii",
        "cfa",
        "ceo",
        "cfo",
        "cto",
        "dds",
        "md",
    }
)

_PARENTHESES = re.compile(r"\([^)]*\)|\[[^\]]*\]|\"[^\"]*\"|“[^”]*”")
# Todo lo que no sea letra, marca diacrítica, espacio, guion o apóstrofo.
_NOT_NAME_CHAR = re.compile(r"[^\w\s'’\-]|[\d_]", re.UNICODE)
_AFTER_COMMA_CREDENTIALS = re.compile(r",.*$")


@dataclass(frozen=True)
class PersonName:
    first_original: str
    last_original: str
    first_display: str
    last_display: str
    # Formas plegadas (sin tildes, minúsculas) usadas para deduplicar.
    first_normalized: str
    last_normalized: str
    # Piezas para construir correos en la Etapa 3.
    first_primary: str
    first_compact: str
    last_primary: str
    last_primary_core: str
    last_second: str | None
    removed: tuple[str, ...] = field(default_factory=tuple)

    @property
    def is_valid(self) -> bool:
        return bool(self.first_primary) and bool(self.last_primary_core)


def _strip_extras(value: str) -> tuple[str, list[str]]:
    removed: list[str] = []
    text = clean(value)
    if _PARENTHESES.search(text):
        removed.extend(m.group(0) for m in _PARENTHESES.finditer(text))
        text = _PARENTHESES.sub(" ", text)
    # "Juan Pérez, MBA" → credenciales tras la coma.
    match = _AFTER_COMMA_CREDENTIALS.search(text)
    if match:
        removed.append(match.group(0).lstrip(", "))
        text = text[: match.start()]
    cleaned = _NOT_NAME_CHAR.sub(" ", text)
    if cleaned != text:
        removed.append("símbolos")
    tokens = [t.strip("-'’") for t in cleaned.split()]
    kept = []
    for token in tokens:
        if not token:
            continue
        if fold(token).replace(" ", "") in HONORIFICS:
            removed.append(token)
            continue
        kept.append(token)
    return " ".join(kept), removed


def _display_case(tokens: list[str]) -> str:
    """Capitaliza respetando partículas en minúscula y guiones: "PÉREZ-GÓMEZ DE LA CRUZ"."""
    out = []
    for index, token in enumerate(tokens):
        lower = token.lower()
        if index > 0 and fold(token) in PARTICLES:
            out.append(lower)
            continue
        parts = re.split(r"([-'’])", lower)
        out.append("".join(p[:1].upper() + p[1:] if p not in "-'’" else p for p in parts))
    return " ".join(out)


def _compact(value: str) -> str:
    return fold(value).replace(" ", "")


def _split_surnames(tokens: list[str]) -> tuple[list[str], list[str]]:
    """Separa el primer apellido (con sus partículas) del resto.

    "de la Cruz Pérez" → (["de", "la", "Cruz"], ["Pérez"]); "Pérez Gómez" → (["Pérez"], ["Gómez"]).
    """
    first: list[str] = []
    index = 0
    while index < len(tokens) and fold(tokens[index]) in PARTICLES:
        first.append(tokens[index])
        index += 1
    if index < len(tokens):
        first.append(tokens[index])
        index += 1
    return first, tokens[index:]


def normalize_person(first: str | None, last: str | None) -> PersonName:
    first_original, last_original = clean(first), clean(last)
    first_text, removed_first = _strip_extras(first_original)
    last_text, removed_last = _strip_extras(last_original)

    first_tokens = first_text.split()
    last_tokens = last_text.split()

    first_display = _display_case(first_tokens)
    last_display = _display_case(last_tokens)

    primary_tokens, second_tokens = _split_surnames(last_tokens)
    core_tokens = [t for t in primary_tokens if fold(t) not in PARTICLES]

    first_names = [t for t in first_tokens if fold(t) not in PARTICLES]
    return PersonName(
        first_original=first_original,
        last_original=last_original,
        first_display=first_display,
        last_display=last_display,
        first_normalized=fold(first_text),
        last_normalized=fold(last_text),
        first_primary=_compact(first_names[0]) if first_names else "",
        first_compact=_compact(" ".join(first_names)),
        last_primary=_compact(" ".join(primary_tokens)),
        last_primary_core=_compact(" ".join(core_tokens)),
        last_second=_compact(" ".join(second_tokens)) or None,
        removed=tuple(removed_first + removed_last),
    )


def looks_like_name(value: str) -> bool:
    """Un nombre válido tiene letras y no parece un correo ni una URL."""
    text = clean(value)
    return has_letters(text) and "@" not in text and "://" not in text
