"""Mapeo de columnas del archivo a los campos de ORKESTA.

Se propone automáticamente comparando cada encabezado con sinónimos en español e inglés, y el
usuario puede corregirlo antes de confirmar la importación.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Literal

from orkesta.engine.normalize.text import fold

Field = Literal["first_name", "last_name", "full_name", "title", "company", "location"]

FIELDS: tuple[Field, ...] = ("first_name", "last_name", "full_name", "title", "company", "location")
# Además de la empresa hace falta el nombre: Nombre + Apellido, o bien Nombre completo.
REQUIRED_FIELDS: tuple[Field, ...] = ("company",)

FIELD_LABELS: dict[Field, str] = {
    "first_name": "Nombre",
    "last_name": "Apellido",
    "full_name": "Nombre completo",
    "title": "Cargo",
    "company": "Empresa",
    "location": "Ubicación",
}

# Sinónimos en forma plegada. El orden importa poco: puntúa más la coincidencia exacta.
SYNONYMS: dict[Field, tuple[str, ...]] = {
    "first_name": (
        "nombre",
        "nombres",
        "primer nombre",
        "nombre de pila",
        "first name",
        "firstname",
        "first",
        "given name",
        "nombre contacto",
        "nombre del contacto",
        "name",
    ),
    "last_name": (
        "apellido",
        "apellidos",
        "primer apellido",
        "last name",
        "lastname",
        "last",
        "surname",
        "family name",
        "apellido del contacto",
    ),
    "full_name": (
        "nombre completo",
        "full name",
        "fullname",
        "nombre y apellido",
        "nombre y apellidos",
        "nombres y apellidos",
        "contacto",
        "contact name",
        "person name",
    ),
    "title": (
        "cargo",
        "puesto",
        "titulo",
        "titulo del cargo",
        "title",
        "job title",
        "jobtitle",
        "position",
        "posicion",
        "rol",
        "role",
        "ocupacion",
        "cargo actual",
    ),
    "company": (
        "empresa",
        "compania",
        "company",
        "company name",
        "companyname",
        "organizacion",
        "organization",
        "account",
        "account name",
        "cuenta",
        "razon social",
        "employer",
        "nombre de la empresa",
        "empresa actual",
        "current company",
    ),
    "location": (
        "ubicacion",
        "location",
        "localizacion",
        "ciudad",
        "city",
        "pais",
        "country",
        "region",
        "geografia",
        "ubicacion del contacto",
        "person location",
        "lugar",
        "sede",
    ),
}

_EXACT = 100
_WORD = 50


@dataclass(frozen=True)
class ProposedMapping:
    mapping: dict[Field, str | None]
    confidence: dict[Field, Literal["exact", "partial", "none"]]


def _score(header: str, field: Field) -> int:
    folded = fold(header)
    if not folded:
        return 0
    best = 0
    for synonym in SYNONYMS[field]:
        if folded == synonym or folded.replace(" ", "") == synonym.replace(" ", ""):
            return _EXACT
        if re.search(rf"\b{re.escape(synonym)}\b", folded):
            # Sinónimos más largos son más específicos: "nombre de la empresa" > "nombre".
            best = max(best, _WORD + len(synonym))
    return best


def propose_mapping(headers: list[str]) -> ProposedMapping:
    scored = [
        (score, field, header)
        for field in FIELDS
        for header in headers
        if (score := _score(header, field)) > 0
    ]
    scored.sort(key=lambda item: (-item[0], FIELDS.index(item[1]), headers.index(item[2])))

    mapping: dict[Field, str | None] = dict.fromkeys(FIELDS)
    confidence: dict[Field, Literal["exact", "partial", "none"]] = dict.fromkeys(FIELDS, "none")
    used: set[str] = set()
    for score, field, header in scored:
        if mapping[field] is not None or header in used:
            continue
        mapping[field] = header
        confidence[field] = "exact" if score >= _EXACT else "partial"
        used.add(header)

    # Una sola columna de nombre sin columna de apellido suele traer el nombre completo
    # ("NOMBRE" en exportaciones de LinkedIn): se asigna a Nombre completo.
    if mapping["first_name"] and not mapping["last_name"] and not mapping["full_name"]:
        mapping["full_name"], mapping["first_name"] = mapping["first_name"], None
        confidence["full_name"], confidence["first_name"] = "partial", "none"
    return ProposedMapping(mapping=mapping, confidence=confidence)


def uses_full_name(mapping: Mapping[Field, str | None]) -> bool:
    """El nombre se separa desde Nombre completo cuando no hay Nombre y Apellido por separado."""
    return bool(mapping.get("full_name")) and not (
        mapping.get("first_name") and mapping.get("last_name")
    )


def validate_mapping(mapping: dict[Field, str | None], headers: list[str]) -> list[str]:
    """Devuelve errores legibles; lista vacía si el mapeo es usable."""
    errors = []
    if not uses_full_name(mapping):
        if not mapping.get("first_name"):
            errors.append("Falta asignar la columna de Nombre (o la de Nombre completo).")
        if not mapping.get("last_name"):
            errors.append("Falta asignar la columna de Apellido.")
    for field in REQUIRED_FIELDS:
        if not mapping.get(field):
            errors.append(f"Falta asignar la columna de {FIELD_LABELS[field]}.")
    chosen = [h for h in mapping.values() if h]
    for header in chosen:
        if header not in headers:
            errors.append(f"La columna '{header}' no existe en el archivo.")
    duplicates = {h for h in chosen if chosen.count(h) > 1}
    for header in sorted(duplicates):
        errors.append(f"La columna '{header}' está asignada a más de un campo.")
    return errors
