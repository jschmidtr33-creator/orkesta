"""Operaciones de base de datos de la importación.

Flujo de un lote::

    create_draft_batch  → status "draft": filas originales guardadas, mapeo propuesto
    build_preview       → vista previa con el mapeo elegido (no escribe nada)
    confirm_import      → status "importing" y trabajo "import_batch" en la cola
    run_import (worker) → status "imported": empresas y contactos creados o reutilizados
"""

from __future__ import annotations

import hashlib
import uuid
from collections.abc import Iterator, Mapping, Sequence
from dataclasses import dataclass
from typing import Any, Literal

from sqlalchemy import Boolean, delete, func, insert, literal_column, select, update
from sqlalchemy.dialects.postgresql import aggregate_order_by
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from orkesta.audit import audit
from orkesta.db.models import (
    Company,
    Contact,
    Domain,
    EmailCandidate,
    EnrichmentBatch,
    EnrichmentJob,
    ImportRow,
)
from orkesta.engine.domain.reasons import DOMAIN_STATUS_LABELS, describe_reasons
from orkesta.engine.email.patterns import PATTERN_LABELS
from orkesta.imports.export import ExportRow
from orkesta.imports.mapping import FIELDS, Field, propose_mapping, validate_mapping
from orkesta.imports.reader import ReadLimits, read_table
from orkesta.imports.rows import ProcessedRow, ProcessSummary, process_rows
from orkesta.worker.queue import enqueue_job

IMPORT_JOB_KIND = "import_batch"
_CHUNK = 1000
CONTACT_EXISTS = "CONTACT_EXISTS"


class ImportStateError(ValueError):
    """La operación no es válida en el estado actual del lote."""


@dataclass(frozen=True)
class DraftResult:
    batch: EnrichmentBatch
    previous_batch: EnrichmentBatch | None


@dataclass(frozen=True)
class Preview:
    rows: list[ProcessedRow]
    summary: ProcessSummary
    errors: list[str]


def _chunks(items: Sequence[Any], size: int = _CHUNK) -> Iterator[Sequence[Any]]:
    for start in range(0, len(items), size):
        yield items[start : start + size]


def create_draft_batch(
    session: Session,
    *,
    organization_id: uuid.UUID,
    user_id: uuid.UUID | None,
    filename: str,
    content: bytes,
    limits: ReadLimits,
) -> DraftResult:
    """Lee el archivo y guarda sus filas tal cual. Lanza ``ImportFileError`` si no es válido."""
    table = read_table(content, filename, limits)
    sha256 = hashlib.sha256(content).hexdigest()
    previous = session.scalar(
        select(EnrichmentBatch)
        .where(
            EnrichmentBatch.organization_id == organization_id,
            EnrichmentBatch.source_sha256 == sha256,
        )
        .order_by(EnrichmentBatch.created_at.desc())
        .limit(1)
    )
    proposed = propose_mapping(table.headers)
    batch = EnrichmentBatch(
        organization_id=organization_id,
        created_by_user_id=user_id,
        source_filename=filename[:255],
        source_sha256=sha256,
        source_format=table.file_format,
        headers=table.headers,
        column_mapping=dict(proposed.mapping),
        settings={"encoding": table.encoding, "delimiter": table.delimiter},
        total_rows=len(table.rows),
    )
    session.add(batch)
    session.flush()

    records = [
        {
            "organization_id": organization_id,
            "batch_id": batch.id,
            "row_number": index,
            "raw": dict(zip(table.headers, values, strict=True)),
        }
        for index, values in enumerate(table.rows, start=2)  # fila 1 = encabezados
    ]
    for chunk in _chunks(records):
        session.execute(insert(ImportRow), list(chunk))

    audit(
        session,
        organization_id=organization_id,
        actor_user_id=user_id,
        action="batch.uploaded",
        entity_type="enrichment_batch",
        entity_id=batch.id,
        rows=len(records),
        file_format=table.file_format,
    )
    return DraftResult(batch=batch, previous_batch=previous)


def iter_raw_rows(session: Session, batch_id: uuid.UUID) -> Iterator[tuple[int, dict[str, str]]]:
    result = session.execute(
        select(ImportRow.row_number, ImportRow.raw)
        .where(ImportRow.batch_id == batch_id)
        .order_by(ImportRow.row_number)
        .execution_options(yield_per=2000)
    )
    yield from ((number, raw) for number, raw in result)


def coerce_mapping(values: Mapping[str, Any], headers: Sequence[str]) -> dict[Field, str | None]:
    """Convierte un formulario o JSON en un mapeo; los valores vacíos significan "sin asignar"."""
    mapping: dict[Field, str | None] = {}
    for field in FIELDS:
        value = values.get(field)
        mapping[field] = value if isinstance(value, str) and value in headers else None
    return mapping


def build_preview(
    session: Session, batch: EnrichmentBatch, mapping: dict[Field, str | None], limit: int = 20
) -> Preview:
    errors = validate_mapping(mapping, list(batch.headers))
    if errors:
        return Preview(rows=[], summary=ProcessSummary(), errors=errors)
    processed, summary = process_rows(iter_raw_rows(session, batch.id), mapping)
    return Preview(rows=processed[:limit], summary=summary, errors=[])


def confirm_import(
    session: Session,
    batch: EnrichmentBatch,
    mapping: dict[Field, str | None],
    *,
    user_id: uuid.UUID | None,
    max_attempts: int = 3,
) -> EnrichmentJob:
    if batch.status != "draft":
        raise ImportStateError("Este lote ya fue confirmado.")
    errors = validate_mapping(mapping, list(batch.headers))
    if errors:
        raise ImportStateError(" ".join(errors))
    batch.column_mapping = {str(key): value for key, value in mapping.items()}
    batch.status = "importing"
    batch.started_at = session.scalar(select(func.now()))
    job = enqueue_job(
        session,
        organization_id=batch.organization_id,
        batch_id=batch.id,
        kind=IMPORT_JOB_KIND,
        idempotency_key=f"import:{batch.id}",
        max_attempts=max_attempts,
    )
    audit(
        session,
        organization_id=batch.organization_id,
        actor_user_id=user_id,
        action="batch.confirmed",
        entity_type="enrichment_batch",
        entity_id=batch.id,
        mapping=dict(mapping),
    )
    return job


def _upsert_companies(
    session: Session, organization_id: uuid.UUID, rows: list[ProcessedRow]
) -> dict[tuple[str, str], uuid.UUID]:
    unique: dict[tuple[str, str], dict[str, Any]] = {}
    for row in rows:
        key = (row.company.normalized, row.location.country_code or "")
        unique.setdefault(
            key,
            {
                "organization_id": organization_id,
                "name_original": row.company.display[:300],
                "name_normalized": row.company.normalized[:300],
                "match_key": row.company.match_key[:300],
                "country_code": row.location.country_code,
                "city": row.location.city,
            },
        )
    ids: dict[tuple[str, str], uuid.UUID] = {}
    items = list(unique.values())
    for chunk in _chunks(items):
        statement = pg_insert(Company).values(list(chunk))
        upsert = statement.on_conflict_do_update(
            constraint="uq_companies_organization_id_name_normalized_country_code",
            set_={
                "city": func.coalesce(Company.city, statement.excluded.city),
                "match_key": func.coalesce(Company.match_key, statement.excluded.match_key),
                "updated_at": func.now(),
            },
        ).returning(Company.id, Company.name_normalized, Company.country_code)
        for company_id, name, country in session.execute(upsert):
            ids[(name, country or "")] = company_id
    return ids


def _upsert_contacts(
    session: Session,
    organization_id: uuid.UUID,
    rows: list[ProcessedRow],
    company_ids: dict[tuple[str, str], uuid.UUID],
) -> dict[tuple[str, str, str, str], tuple[uuid.UUID, bool]]:
    """Crea o reutiliza contactos. Devuelve clave → (id, ya_existía)."""
    result: dict[tuple[str, str, str, str], tuple[uuid.UUID, bool]] = {}
    key_by_triplet: dict[tuple[uuid.UUID, str, str], tuple[str, str, str, str]] = {}
    values = []
    for row in rows:
        company_id = company_ids[(row.company.normalized, row.location.country_code or "")]
        key_by_triplet[(company_id, row.person.first_normalized, row.person.last_normalized)] = (
            row.dedup_key()
        )
        values.append(
            {
                "organization_id": organization_id,
                "company_id": company_id,
                "first_name_original": row.person.first_display[:200],
                "last_name_original": row.person.last_display[:200],
                "first_name_normalized": row.person.first_normalized[:200],
                "last_name_normalized": row.person.last_normalized[:200],
                "title": row.title[:300] if row.title else None,
            }
        )
    for chunk in _chunks(values):
        statement = pg_insert(Contact).values(list(chunk))
        upsert_contacts = statement.on_conflict_do_update(
            constraint="uq_contacts_company_person",
            set_={
                "title": func.coalesce(statement.excluded.title, Contact.title),
                "updated_at": func.now(),
            },
        ).returning(
            Contact.id,
            Contact.company_id,
            Contact.first_name_normalized,
            Contact.last_name_normalized,
            # xmax = 0 solo en filas recién insertadas: distingue inserción de actualización.
            literal_column("(xmax <> 0)", type_=Boolean).label("existed"),
        )
        for contact_id, company_id, first, last, existed in session.execute(upsert_contacts):
            result[key_by_triplet[(company_id, first, last)]] = (contact_id, bool(existed))
    return result


def run_import(session: Session, batch_id: uuid.UUID) -> EnrichmentBatch:
    """Normaliza todas las filas y crea empresas y contactos. Se ejecuta en el worker.

    Todo ocurre en una transacción: si algo falla no queda una importación a medias y el
    trabajo puede reintentarse desde cero.
    """
    batch = session.get(EnrichmentBatch, batch_id, with_for_update=True)
    if batch is None:
        raise ImportStateError("El lote no existe.")
    if batch.status == "imported":
        return batch
    if batch.status != "importing":
        raise ImportStateError(f"El lote está en estado '{batch.status}', no 'importing'.")

    mapping = coerce_mapping(batch.column_mapping, batch.headers)
    processed, summary = process_rows(iter_raw_rows(session, batch.id), mapping)
    valid = [row for row in processed if row.status == "valid"]

    company_ids = _upsert_companies(session, batch.organization_id, valid)
    contacts = _upsert_contacts(session, batch.organization_id, valid, company_ids)

    row_ids = dict(
        session.execute(
            select(ImportRow.row_number, ImportRow.id).where(ImportRow.batch_id == batch.id)
        ).all()
    )
    existing_contacts = 0
    updates = []
    for row in processed:
        issues = list(row.issues)
        contact_id = None
        if row.status in ("valid", "duplicate"):
            contact_id, existed = contacts.get(row.dedup_key(), (None, False))
            if row.status == "valid" and existed:
                issues.append(CONTACT_EXISTS)
                existing_contacts += 1
        updates.append(
            {
                "id": row_ids[row.row_number],
                "status": row.status,
                "issues": issues,
                "normalized": row.normalized_payload(),
                "duplicate_of_row": row.duplicate_of,
                "contact_id": contact_id,
            }
        )
    for chunk in _chunks(updates):
        session.execute(update(ImportRow), list(chunk))

    batch.status = "imported"
    batch.processed_rows = summary.valid + summary.duplicate
    batch.failed_rows = summary.invalid
    batch.finished_at = session.scalar(select(func.now()))
    batch.error_message = None
    batch.import_summary = {
        "total": summary.total,
        "valid": summary.valid,
        "invalid": summary.invalid,
        "duplicate": summary.duplicate,
        "companies": len(company_ids),
        "contacts_new": summary.valid - existing_contacts,
        "contacts_existing": existing_contacts,
        "countries": dict(summary.countries.most_common()),
        "issues": dict(summary.issue_counts.most_common()),
    }
    audit(
        session,
        organization_id=batch.organization_id,
        action="batch.imported",
        entity_type="enrichment_batch",
        entity_id=batch.id,
        valid=summary.valid,
        invalid=summary.invalid,
        duplicate=summary.duplicate,
    )
    session.flush()
    return batch


def mark_import_failed(session: Session, batch_id: uuid.UUID, error: str) -> None:
    """Se llama cuando el trabajo de importación falla de forma definitiva."""
    batch = session.get(EnrichmentBatch, batch_id, with_for_update=True)
    if batch is None or batch.status != "importing":
        return
    batch.status = "failed"
    batch.error_message = error[:2000]
    batch.finished_at = session.scalar(select(func.now()))


ExportFilter = Literal["all", "valid", "issues"]


def export_rows(
    session: Session, batch: EnrichmentBatch, which: ExportFilter = "all"
) -> Iterator[ExportRow]:
    alternatives = (
        select(
            func.string_agg(
                EmailCandidate.address,
                aggregate_order_by(literal_column("' | '"), EmailCandidate.rank),
            )
        )
        .where(EmailCandidate.contact_id == Contact.id, EmailCandidate.rank > 0)
        .correlate(Contact)
        .scalar_subquery()
    )
    query = (
        select(ImportRow, Company.domain_status, Domain, EmailCandidate, alternatives)
        .outerjoin(Contact, Contact.id == ImportRow.contact_id)
        .outerjoin(Company, Company.id == Contact.company_id)
        .outerjoin(Domain, (Domain.company_id == Company.id) & Domain.is_selected.is_(True))
        .outerjoin(
            EmailCandidate,
            (EmailCandidate.contact_id == Contact.id) & (EmailCandidate.rank == 0),
        )
        .where(ImportRow.batch_id == batch.id)
    )
    if which == "valid":
        query = query.where(ImportRow.status == "valid")
    elif which == "issues":
        query = query.where(ImportRow.status.in_(("invalid", "duplicate")))
    query = query.order_by(ImportRow.row_number).execution_options(yield_per=2000)
    for row, company_status, domain, email, alternates in session.execute(query):
        yield ExportRow(
            row_number=row.row_number,
            raw=row.raw,
            status=row.status,
            issues=row.issues,
            duplicate_of=row.duplicate_of_row,
            normalized=row.normalized or {},
            domain={
                **domain_export_values(company_status, domain),
                **email_export_values(domain, email, alternates),
            },
        )


def domain_export_values(company_status: str | None, domain: Domain | None) -> dict[str, str]:
    """Columnas de dominio de la exportación; vacías si la empresa nunca se investigó."""
    if domain is None:
        label = DOMAIN_STATUS_LABELS.get(company_status or "", "")
        return {"domain_status": label} if label else {}
    return {
        "domain": domain.domain,
        "domain_status": DOMAIN_STATUS_LABELS.get(domain.status, domain.status),
        "domain_score": str(domain.confidence_score),
        "has_mx": {True: "sí", False: "no"}.get(domain.has_mx, "")
        if domain.has_mx is not None
        else "",
        "mx_provider": domain.mx_provider or "",
        "domain_reasons": describe_reasons(domain.reason_codes),
    }


CONFIDENCE_LABELS = {"high": "alta", "medium": "media", "low": "baja"}
EMAIL_SOURCE_LABELS = {
    "publicly_found": "publicado por la persona",
    "inferred": "deducido del patrón",
    "verified_by_provider": "verificado",
}


def email_export_values(
    domain: Domain | None, email: EmailCandidate | None, alternates: str | None
) -> dict[str, str]:
    """Columnas de correo; vacías si no hay patrón decidido para el dominio."""
    values: dict[str, str] = {}
    if domain is not None and domain.email_pattern:
        values["email_pattern"] = PATTERN_LABELS.get(domain.email_pattern, domain.email_pattern)
        values["email_reasons"] = describe_reasons(domain.pattern_reason_codes)
    if email is not None:
        values["email"] = email.address
        values["email_confidence"] = (
            f"{CONFIDENCE_LABELS.get(email.confidence_level, email.confidence_level)} "
            f"({email.confidence_score})"
        )
        values["email_source"] = EMAIL_SOURCE_LABELS.get(email.label, email.label)
        values["email_alternatives"] = alternates or ""
    return values


def delete_batch(session: Session, batch: EnrichmentBatch, *, user_id: uuid.UUID | None) -> int:
    """Borra el lote, sus filas y los contactos que solo existían por este lote.

    Las empresas se conservan: son datos de organizaciones, no personales, y su historial
    (dominio, patrón) sirve para lotes futuros. Devuelve el número de contactos borrados.
    """
    contact_ids = set(
        session.scalars(
            select(ImportRow.contact_id).where(
                ImportRow.batch_id == batch.id, ImportRow.contact_id.is_not(None)
            )
        )
    )
    organization_id, batch_id = batch.organization_id, batch.id
    session.execute(delete(EnrichmentBatch).where(EnrichmentBatch.id == batch_id))
    removed = 0
    if contact_ids:
        still_used = set(
            session.scalars(
                select(ImportRow.contact_id).where(ImportRow.contact_id.in_(contact_ids))
            )
        )
        orphan = list(contact_ids - still_used)
        for chunk in _chunks(orphan):
            session.execute(delete(Contact).where(Contact.id.in_(list(chunk))))
        removed = len(orphan)
    audit(
        session,
        organization_id=organization_id,
        actor_user_id=user_id,
        action="batch.deleted",
        entity_type="enrichment_batch",
        entity_id=batch_id,
        contacts_deleted=removed,
    )
    return removed
