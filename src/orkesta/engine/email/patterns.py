"""Catálogo de patrones de correo corporativo: construir y reconocer la parte local.

Un patrón describe cómo una empresa arma la parte local a partir del nombre de la persona:
``first.last`` → ``juan.perez``; ``flast`` → ``jperez``; ``flastl2`` → ``jperezg`` (inicial,
primer apellido e inicial del segundo, frecuente en LATAM).

Los nombres compuestos y las partículas generan variantes ("María José de la Torre" puede ser
``maria.delatorre``, ``mariajose.torre``…): al reconocer se prueban todas; al construir se usa la
forma principal, que es la más frecuente.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass

from orkesta.engine.normalize.names import PersonName

_ALNUM = re.compile(r"[^a-z0-9]")


@dataclass(frozen=True)
class NameParts:
    first: str
    last: str
    last2: str | None = None


def _clean(value: str | None) -> str:
    return _ALNUM.sub("", value or "")


# Cada patrón devuelve la parte local o None si faltan piezas (p. ej. segundo apellido).
PATTERNS: dict[str, Callable[[NameParts], str | None]] = {
    "first.last": lambda p: f"{p.first}.{p.last}",
    "flast": lambda p: f"{p.first[0]}{p.last}",
    "first": lambda p: p.first,
    "firstlast": lambda p: f"{p.first}{p.last}",
    "first_last": lambda p: f"{p.first}_{p.last}",
    "f.last": lambda p: f"{p.first[0]}.{p.last}",
    "first-last": lambda p: f"{p.first}-{p.last}",
    "flastl2": lambda p: f"{p.first[0]}{p.last}{p.last2[0]}" if p.last2 else None,
    "first.last.last2": lambda p: f"{p.first}.{p.last}.{p.last2}" if p.last2 else None,
    "first.lastl2": lambda p: f"{p.first}.{p.last}{p.last2[0]}" if p.last2 else None,
    "firstl": lambda p: f"{p.first}{p.last[0]}",
    "first.l": lambda p: f"{p.first}.{p.last[0]}",
    "last.first": lambda p: f"{p.last}.{p.first}",
    "lastfirst": lambda p: f"{p.last}{p.first}",
    "lastf": lambda p: f"{p.last}{p.first[0]}",
    "last": lambda p: p.last,
}

PATTERN_LABELS: dict[str, str] = {
    "first.last": "nombre.apellido",
    "flast": "inicial + apellido",
    "first": "nombre",
    "firstlast": "nombreapellido",
    "first_last": "nombre_apellido",
    "f.last": "inicial.apellido",
    "first-last": "nombre-apellido",
    "flastl2": "inicial + apellido + inicial 2.º apellido",
    "first.last.last2": "nombre.apellido.apellido2",
    "first.lastl2": "nombre.apellido + inicial 2.º apellido",
    "firstl": "nombre + inicial apellido",
    "first.l": "nombre.inicial apellido",
    "last.first": "apellido.nombre",
    "lastfirst": "apellidonombre",
    "lastf": "apellido + inicial",
    "last": "apellido",
}


def primary_parts(person: PersonName) -> NameParts | None:
    first, last = _clean(person.first_primary), _clean(person.last_primary)
    if not first or len(last) < 2:
        return None
    second = _clean(person.last_second) or None
    return NameParts(first, last, second)


def name_variants(person: PersonName) -> list[NameParts]:
    """Formas en que una empresa puede escribir el nombre, de la más a la menos frecuente."""
    firsts = [_clean(person.first_primary), _clean(person.first_compact)]
    lasts = [_clean(person.last_primary), _clean(person.last_primary_core)]
    second = _clean(person.last_second) or None
    out: list[NameParts] = []
    for first in dict.fromkeys(f for f in firsts if f):
        for last in dict.fromkeys(x for x in lasts if len(x) >= 2):
            out.append(NameParts(first, last, second))
    return out


def render(pattern: str, person: PersonName) -> str | None:
    parts = primary_parts(person)
    return PATTERNS[pattern](parts) if parts else None


def matching_patterns(local: str, person: PersonName) -> list[str]:
    """Patrones que, con alguna variante del nombre, producen exactamente ``local``."""
    local = local.lower()
    found: list[str] = []
    for parts in name_variants(person):
        for pattern, build in PATTERNS.items():
            if pattern not in found and build(parts) == local:
                found.append(pattern)
    return found
