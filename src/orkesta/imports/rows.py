"""Validación, normalización y deduplicación de filas, sin base de datos.

La misma función alimenta la vista previa (antes de confirmar) y la importación definitiva en el
worker, de modo que lo que el usuario ve es exactamente lo que se guarda.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from typing import Literal

from orkesta.engine.normalize import (
    CompanyName,
    Location,
    PersonName,
    normalize_company,
    normalize_person,
    parse_location,
)
from orkesta.engine.normalize.names import looks_like_name
from orkesta.engine.normalize.text import clean
from orkesta.imports.mapping import Field

RowStatus = Literal["valid", "invalid", "duplicate"]

# Código → (gravedad, mensaje). Los errores impiden crear el contacto; los avisos no.
ISSUES: dict[str, tuple[Literal["error", "warning"], str]] = {
    "FIRST_NAME_MISSING": ("error", "Falta el nombre"),
    "LAST_NAME_MISSING": ("error", "Falta el apellido"),
    "NAME_INVALID": ("error", "El nombre o el apellido no son válidos"),
    "COMPANY_MISSING": ("error", "Falta la empresa"),
    "COMPANY_INVALID": ("error", "La empresa no identifica a una organización"),
    "LOCATION_MISSING": ("warning", "Falta la ubicación"),
    "LOCATION_UNRECOGNIZED": ("warning", "No se reconoce el país de la ubicación"),
    "LOCATION_AMBIGUOUS": ("warning", "Ciudad en varios países; se eligió el más probable"),
    "NAME_CLEANED": ("warning", "Se retiraron tratamientos, credenciales o símbolos del nombre"),
    "TITLE_MISSING": ("warning", "Falta el cargo"),
    # Solo se conoce al guardar: la persona ya estaba en ORKESTA por un lote anterior.
    "CONTACT_EXISTS": ("warning", "El contacto ya existía en ORKESTA y se reutilizó"),
}


@dataclass(frozen=True)
class ProcessedRow:
    row_number: int
    raw: dict[str, str]
    person: PersonName
    company: CompanyName
    location: Location
    title: str | None
    issues: tuple[str, ...]
    status: RowStatus
    duplicate_of: int | None = None

    @property
    def errors(self) -> list[str]:
        return [code for code in self.issues if ISSUES[code][0] == "error"]

    @property
    def warnings(self) -> list[str]:
        return [code for code in self.issues if ISSUES[code][0] == "warning"]

    def dedup_key(self) -> tuple[str, str, str, str]:
        return (
            self.person.first_normalized,
            self.person.last_normalized,
            self.company.normalized,
            self.location.country_code or "",
        )

    def normalized_payload(self) -> dict[str, str | None]:
        """Valores normalizados que se guardan con la fila y se exportan."""
        return {
            "first_name": self.person.first_display,
            "last_name": self.person.last_display,
            "title": self.title,
            "company": self.company.display,
            "company_normalized": self.company.normalized,
            "country_code": self.location.country_code,
            "country": self.location.country_name,
            "city": self.location.city,
            "location_confidence": self.location.confidence,
        }


@dataclass
class ProcessSummary:
    total: int = 0
    valid: int = 0
    invalid: int = 0
    duplicate: int = 0
    companies: int = 0
    issue_counts: Counter[str] = field(default_factory=Counter)
    countries: Counter[str] = field(default_factory=Counter)


def _value(raw: Mapping[str, str], mapping: Mapping[Field, str | None], key: Field) -> str:
    column = mapping.get(key)
    return clean(raw.get(column, "")) if column else ""


def process_row(
    row_number: int, raw: dict[str, str], mapping: Mapping[Field, str | None]
) -> ProcessedRow:
    first = _value(raw, mapping, "first_name")
    last = _value(raw, mapping, "last_name")
    company_raw = _value(raw, mapping, "company")
    location_raw = _value(raw, mapping, "location")
    title = _value(raw, mapping, "title") or None

    person = normalize_person(first, last)
    company = normalize_company(company_raw)
    location = parse_location(location_raw)

    issues: list[str] = []
    if not first:
        issues.append("FIRST_NAME_MISSING")
    if not last:
        issues.append("LAST_NAME_MISSING")
    if (
        first
        and last
        and (not person.is_valid or not looks_like_name(first) or not looks_like_name(last))
    ):
        issues.append("NAME_INVALID")
    if not company_raw:
        issues.append("COMPANY_MISSING")
    elif not company.is_valid:
        issues.append("COMPANY_INVALID")

    if mapping.get("location"):
        if not location_raw:
            issues.append("LOCATION_MISSING")
        elif not location.recognized:
            issues.append("LOCATION_UNRECOGNIZED")
        elif location.confidence == "low":
            issues.append("LOCATION_AMBIGUOUS")
    if person.removed and person.is_valid:
        issues.append("NAME_CLEANED")
    if mapping.get("title") and not title:
        issues.append("TITLE_MISSING")

    has_error = any(ISSUES[code][0] == "error" for code in issues)
    return ProcessedRow(
        row_number=row_number,
        raw=raw,
        person=person,
        company=company,
        location=location,
        title=title,
        issues=tuple(issues),
        status="invalid" if has_error else "valid",
    )


def process_rows(
    rows: Iterable[tuple[int, dict[str, str]]], mapping: Mapping[Field, str | None]
) -> tuple[list[ProcessedRow], ProcessSummary]:
    """Procesa todas las filas y marca como duplicadas las repeticiones dentro del archivo."""
    seen: dict[tuple[str, str, str, str], int] = {}
    companies: set[tuple[str, str]] = set()
    processed: list[ProcessedRow] = []
    summary = ProcessSummary()

    for row_number, raw in rows:
        row = process_row(row_number, raw, mapping)
        if row.status == "valid":
            key = row.dedup_key()
            if key in seen:
                row = ProcessedRow(
                    row_number=row.row_number,
                    raw=row.raw,
                    person=row.person,
                    company=row.company,
                    location=row.location,
                    title=row.title,
                    issues=row.issues,
                    status="duplicate",
                    duplicate_of=seen[key],
                )
            else:
                seen[key] = row.row_number
                companies.add((row.company.normalized, row.location.country_code or ""))
                if row.location.country_code:
                    summary.countries[row.location.country_code] += 1
        processed.append(row)
        summary.total += 1
        setattr(summary, row.status, getattr(summary, row.status) + 1)
        summary.issue_counts.update(row.issues)

    summary.companies = len(companies)
    return processed, summary
