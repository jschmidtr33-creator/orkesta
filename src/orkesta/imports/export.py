"""Exportación de resultados a CSV y XLSX.

Se conservan las columnas originales en su orden y se añaden las columnas de ORKESTA.
Protección contra inyección de fórmulas: en CSV, las celdas que empiezan por ``=``, ``+``,
``-``, ``@`` o caracteres de control se prefijan con un apóstrofo; en XLSX todas las celdas se
escriben con tipo texto, de modo que Excel o Google Sheets nunca las ejecutan.
"""

from __future__ import annotations

import csv
import io
from collections.abc import Iterable, Sequence
from dataclasses import dataclass

_FORMULA_PREFIXES = ("=", "+", "-", "@", "\t", "\r", "\n")

ORKESTA_COLUMNS: tuple[tuple[str, str], ...] = (
    ("status", "ORKESTA estado"),
    ("issues", "ORKESTA incidencias"),
    ("duplicate_of", "ORKESTA duplicado de fila"),
    ("first_name", "ORKESTA nombre"),
    ("last_name", "ORKESTA apellido"),
    ("title", "ORKESTA cargo"),
    ("company", "ORKESTA empresa"),
    ("company_normalized", "ORKESTA empresa normalizada"),
    ("country_code", "ORKESTA país (ISO)"),
    ("country", "ORKESTA país"),
    ("city", "ORKESTA ciudad"),
    ("location_confidence", "ORKESTA confianza ubicación"),
)

STATUS_LABELS = {
    "valid": "válida",
    "invalid": "con errores",
    "duplicate": "duplicada",
    "pending": "pendiente",
}
CONFIDENCE_LABELS = {"high": "alta", "medium": "media", "low": "baja"}


@dataclass(frozen=True)
class ExportRow:
    row_number: int
    raw: dict[str, str]
    status: str
    issues: Sequence[str]
    duplicate_of: int | None
    normalized: dict[str, str | None]


def sanitize_cell(value: object) -> str:
    text = "" if value is None else str(value)
    if text.startswith(_FORMULA_PREFIXES):
        return "'" + text
    return text


def _orkesta_values(row: ExportRow, issue_messages: dict[str, str]) -> list[str]:
    values: dict[str, str] = {
        "status": STATUS_LABELS.get(row.status, row.status),
        "issues": "; ".join(issue_messages.get(code, code) for code in row.issues),
        "duplicate_of": str(row.duplicate_of) if row.duplicate_of else "",
    }
    for key, value in row.normalized.items():
        if key == "location_confidence":
            values[key] = CONFIDENCE_LABELS.get(value or "", "")
        else:
            values[key] = value or ""
    return [values.get(key, "") for key, _ in ORKESTA_COLUMNS]


def _table(
    headers: list[str], rows: Iterable[ExportRow], issue_messages: dict[str, str]
) -> Iterable[list[str]]:
    yield list(headers) + [label for _, label in ORKESTA_COLUMNS]
    for row in rows:
        yield [row.raw.get(h, "") for h in headers] + _orkesta_values(row, issue_messages)


def to_csv(headers: list[str], rows: Iterable[ExportRow], issue_messages: dict[str, str]) -> bytes:
    buffer = io.StringIO(newline="")
    writer = csv.writer(buffer)
    for values in _table(headers, rows, issue_messages):
        writer.writerow([sanitize_cell(v) for v in values])
    # BOM UTF-8: Excel abre bien las tildes y la ñ.
    return buffer.getvalue().encode("utf-8-sig")


def to_xlsx(headers: list[str], rows: Iterable[ExportRow], issue_messages: dict[str, str]) -> bytes:
    from openpyxl import Workbook
    from openpyxl.cell import WriteOnlyCell

    workbook = Workbook(write_only=True)
    sheet = workbook.create_sheet("ORKESTA")
    for values in _table(headers, rows, issue_messages):
        cells = []
        for value in values:
            # Tipo texto explícito: Excel muestra "=SUMA(...)" como texto y nunca lo ejecuta.
            cell = WriteOnlyCell(sheet, value=value)
            cell.data_type = "s"
            cells.append(cell)
        sheet.append(cells)
    buffer = io.BytesIO()
    workbook.save(buffer)
    return buffer.getvalue()
