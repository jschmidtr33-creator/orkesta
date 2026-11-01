"""Reglas del módulo de datos comerciales.

Importación en dos pasos: (1) se sube el archivo y ORKESTA propone qué columna corresponde a
cada uno de los 8 campos; (2) el usuario confirma o corrige y un trabajo en segundo plano carga
los contactos por bloques. Un contacto se identifica por su correo o, sin correo, por nombre y
empresa: si ya existe, solo se completan los campos vacíos (nunca se borra un dato) y cada
valor queda registrado con su fuente.
"""

from __future__ import annotations

import csv
import io
import uuid
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import Select, Table, and_, delete, func, literal_column, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session, sessionmaker

from orkesta.automation.events import emit, emit_many
from orkesta.core import jobs
from orkesta.core.db import set_context
from orkesta.data.models import (
    CONTACT_STATUSES,
    EMAIL_STATUSES,
    STATUS_LABELS,
    Company,
    Contact,
    ContactList,
    DataPoint,
    ImportJob,
    ListMember,
)
from orkesta.engine.files.contacts import (
    MAX_BYTES,
    ProspectRecord,
    Sheet,
    map_columns,
    merge,
    read_sheets,
    to_records,
)
from orkesta.engine.files.reader import ImportFileError
from orkesta.engine.net.domains import registrable_domain
from orkesta.engine.normalize import normalize_company, parse_location
from orkesta.engine.normalize.location import COUNTRIES
from orkesta.engine.normalize.names import split_full_name
from orkesta.engine.normalize.text import fold
from orkesta.engine.taxonomy.industries import INDUSTRY_LABELS, classify
from orkesta.engine.taxonomy.titles import classify_title

IMPORT_JOB = "import_contacts"


def _table(model: Any) -> Table:
    """Tabla de un modelo, para inserciones masivas de bajo nivel (executemany)."""
    table: Table = model.__table__
    return table


CHUNK = 1000
SAMPLE_ROWS = 5
# Campos que el usuario puede asignar a una columna (los 8 que se conservan, con variantes).
MAPPABLE = {
    "first": "Nombre",
    "last": "Apellido",
    "full": "Nombre completo",
    "title": "Cargo",
    "company": "Empresa",
    "email": "Correo",
    "phone": "Teléfono",
    "city": "Ciudad",
    "country": "País",
    "location": "Ubicación (ciudad y país juntos)",
    "industry": "Industria",
}
FIELD_LABELS = {
    "first_name": "Nombre",
    "last_name": "Apellido",
    "job_title": "Cargo",
    "company": "Empresa",
    "email": "Correo",
    "phone": "Teléfono",
    "city": "Ciudad",
    "country_code": "País",
    "industry": "Industria",
}


class DataError(ValueError):
    def __init__(self, message: str, status: int = 400) -> None:
        super().__init__(message)
        self.status = status


def now() -> datetime:
    return datetime.now(UTC)


def country_name(code: str | None) -> str | None:
    return COUNTRIES[code][0] if code and code in COUNTRIES else None


def search_text(*parts: str | None) -> str:
    return fold(" ".join(p for p in parts if p))[:2000]


def dedup_key(email: str | None, full_name: str, company_key: str) -> str:
    return email if email else f"{fold(full_name)}|{company_key}"[:400]


# --- Importación: paso 1 (vista previa) ----------------------------------------------------


def create_import(
    session: Session,
    *,
    tenant_id: uuid.UUID,
    user_id: uuid.UUID,
    filename: str,
    content: bytes,
) -> ImportJob:
    name = (filename or "base").strip()[:300]
    if not name.lower().endswith((".csv", ".txt", ".xlsx")):
        raise DataError("Sube un archivo .xlsx o .csv.")
    if len(content) > MAX_BYTES:
        raise DataError(f"El archivo supera el máximo de {MAX_BYTES // (1024 * 1024)} MB.")
    try:
        sheets = read_sheets(content, name)
    except ImportFileError as exc:
        raise DataError(str(exc)) from exc
    summary = [_sheet_summary(sheet) for sheet in sheets]
    if not any(s["usable"] for s in summary):
        raise DataError(
            "No encontramos columnas de nombre y empresa (o correo). Revisa que el archivo "
            "tenga encabezados como Nombre, Empresa o Correo."
        )
    record = ImportJob(
        tenant_id=tenant_id,
        created_by=user_id,
        filename=name,
        size_bytes=len(content),
        status="preview",
        sheets=summary,
        mapping={s["name"]: s["suggested"] for s in summary if s["usable"]},
        content=content,
    )
    session.add(record)
    session.flush()
    return record


def _usable(columns: dict[str, int]) -> bool:
    has_name = bool({"first", "full"} & set(columns))
    return has_name and bool({"company", "email"} & set(columns))


def _sheet_summary(sheet: Sheet) -> dict[str, Any]:
    columns = map_columns(sheet)
    return {
        "name": sheet.name,
        "headers": sheet.headers,
        "rows": len(sheet.rows),
        "suggested": columns,
        "usable": _usable(columns),
        "sample": [row[: len(sheet.headers)] for row in sheet.rows[:SAMPLE_ROWS]],
    }


def confirm_import(
    session: Session,
    *,
    tenant_id: uuid.UUID,
    user_id: uuid.UUID,
    import_id: uuid.UUID,
    mapping: dict[str, dict[str, int]],
    list_id: uuid.UUID | None = None,
    new_list_name: str = "",
) -> ImportJob:
    record = get_import(session, tenant_id, import_id)
    if record.status != "preview":
        raise DataError("Esta importación ya se confirmó.")
    sheets = {s["name"]: s for s in record.sheets}
    clean: dict[str, dict[str, int]] = {}
    for sheet_name, columns in mapping.items():
        if sheet_name not in sheets:
            continue
        width = len(sheets[sheet_name]["headers"])
        valid = {
            f: int(i) for f, i in columns.items() if f in MAPPABLE and 0 <= int(i) < max(width, 1)
        }
        if len(set(valid.values())) != len(valid):
            raise DataError(f"En «{sheet_name}» hay una columna asignada a dos campos.")
        if valid and _usable(valid):
            clean[sheet_name] = valid
    if not clean:
        raise DataError("Asigna al menos el nombre y la empresa (o el correo) en una hoja.")
    if new_list_name.strip():
        list_id = create_list(session, tenant_id=tenant_id, user_id=user_id, name=new_list_name).id
    elif list_id is not None:
        get_list(session, tenant_id, list_id)
    record.mapping, record.list_id, record.status = clean, list_id, "queued"
    jobs.enqueue(
        session, tenant_id=tenant_id, kind=IMPORT_JOB, payload={"import_id": str(record.id)}
    )
    return record


def get_import(session: Session, tenant_id: uuid.UUID, import_id: uuid.UUID) -> ImportJob:
    record = session.get(ImportJob, import_id)
    if record is None or record.tenant_id != tenant_id:
        raise DataError("Importación no encontrada.", 404)
    return record


# --- Importación: paso 2 (trabajo en segundo plano) ----------------------------------------


@dataclass
class Totals:
    filas: int = 0
    omitidas: int = 0
    repetidas_en_archivo: int = 0
    nuevos: int = 0
    actualizados: int = 0
    sobre_limite: int = 0
    empresas: set[str] = field(default_factory=set)

    def as_dict(self) -> dict[str, int]:
        return {
            "filas": self.filas,
            "omitidas": self.omitidas,
            "repetidas_en_archivo": self.repetidas_en_archivo,
            "nuevos": self.nuevos,
            "actualizados": self.actualizados,
            "sobre_limite": self.sobre_limite,
            "empresas": len(self.empresas),
        }


def _records(record: ImportJob, content: bytes, totals: Totals) -> list[ProspectRecord]:
    merged: dict[str, ProspectRecord] = {}
    for sheet in read_sheets(content, record.filename):
        columns = record.mapping.get(sheet.name)
        if not columns:
            continue
        totals.filas += len(sheet.rows)
        rows = to_records(sheet, {k: int(v) for k, v in columns.items()})
        if "company" not in columns:
            # Sin columna de empresa: se acepta la fila con correo (la empresa queda pendiente).
            rows += _records_without_company(sheet, columns)
        totals.omitidas += len(sheet.rows) - len(rows)
        for item in rows:
            key = dedup_key(item.email, item.full_name, item.company_key)
            if key in merged:
                totals.repetidas_en_archivo += 1
                merged[key] = merge(merged[key], item)
            else:
                merged[key] = item
    return list(merged.values())


def _records_without_company(sheet: Sheet, columns: dict[str, int]) -> list[ProspectRecord]:
    def value(row: list[str], key: str) -> str:
        index = columns.get(key)
        return row[int(index)].strip() if index is not None and int(index) < len(row) else ""

    out: list[ProspectRecord] = []
    for row in sheet.rows:
        email = value(row, "email").lower()
        if "@" not in email:
            continue
        first, last = value(row, "first"), value(row, "last")
        if not (first and last) and value(row, "full"):
            split = split_full_name(value(row, "full"))
            first, last = split.first or first, split.last or last
        if not first:
            continue
        location = parse_location(
            ", ".join(v for v in (value(row, "city"), value(row, "country")) if v)
            or value(row, "location")
        )
        industry = value(row, "industry") or None
        host = email.split("@", 1)[1]
        out.append(
            ProspectRecord(
                full_name=" ".join(x for x in (first, last) if x)[:300],
                first_name=first[:200],
                last_name=last[:200],
                title=value(row, "title")[:300] or None,
                company="",
                company_key="",
                email=email,
                email_domain=registrable_domain(host) or host,
                phone=value(row, "phone")[:40] or None,
                city=(value(row, "city") or location.city or "")[:120] or None,
                country_code=location.country_code,
                industry=industry,
                industry_code=classify(industry) if industry else None,
            )
        )
    return out


@jobs.handler(IMPORT_JOB, on_failure=lambda f, t, p, d: _mark_failed(f, t, p, d))
def run_import(
    factory: sessionmaker[Session], tenant_id: uuid.UUID, payload: dict[str, Any]
) -> None:
    import_id = uuid.UUID(str(payload["import_id"]))
    with factory.begin() as session:
        set_context(session, tenant_id=tenant_id)
        record = get_import(session, tenant_id, import_id)
        if record.status not in ("queued", "running"):
            return
        record.status = "running"
        content = record.content or b""
        totals = Totals()
        items = _records(record, content, totals)
        user_id, list_id, label = record.created_by, record.list_id, record.filename
        record.totals = {**totals.as_dict(), "por_procesar": len(items)}
    for start in range(0, len(items), CHUNK):
        with factory.begin() as session:
            set_context(session, tenant_id=tenant_id)
            _upsert_chunk(
                session, tenant_id=tenant_id, user_id=user_id, import_id=import_id,
                label=label, items=items[start : start + CHUNK], list_id=list_id, totals=totals,
            )  # fmt: skip
            progress = session.get(ImportJob, import_id)
            assert progress is not None  # noqa: S101
            progress.totals = {
                **totals.as_dict(),
                "por_procesar": max(0, len(items) - start - CHUNK),
            }
    with factory.begin() as session:
        set_context(session, tenant_id=tenant_id)
        done = session.get(ImportJob, import_id)
        assert done is not None  # noqa: S101
        done.status, done.finished_at, done.content = "completed", now(), None
        done.totals = {**totals.as_dict(), "por_procesar": 0}
        from orkesta.platform.service import audit

        audit(
            session, tenant_id=tenant_id, actor_id=user_id, action="contacts.imported",
            entity_type="import", entity_id=import_id, file=label, totals=done.totals,
        )  # fmt: skip


def _mark_failed(
    factory: sessionmaker[Session], tenant_id: uuid.UUID, payload: dict[str, Any], detail: str
) -> None:
    with factory.begin() as session:
        set_context(session, tenant_id=tenant_id)
        record = session.get(ImportJob, uuid.UUID(str(payload["import_id"])))
        if record is not None:
            record.status, record.finished_at, record.content = "failed", now(), None
            record.error = (
                detail
                if detail.startswith(("ImportFileError", "DataError"))
                else "No pudimos procesar el archivo. Revisa su formato e inténtalo de nuevo."
            )


def _company_rows(tenant_id: uuid.UUID, items: Sequence[ProspectRecord]) -> list[dict[str, Any]]:
    companies: dict[str, dict[str, Any]] = {}
    for item in items:
        if not item.company_key:
            continue
        row = companies.setdefault(
            item.company_key,
            {
                "id": uuid.uuid4(),
                "tenant_id": tenant_id,
                "name": item.company,
                "match_key": item.company_key,
                "domain": None,
                "industry": item.industry,
                "industry_code": item.industry_code,
                "city": item.city,
                "country_code": item.country_code,
            },
        )
        if item.corporate_email and not row["domain"]:
            row["domain"] = item.email_domain
        for key, value in (
            ("industry", item.industry), ("industry_code", item.industry_code),
            ("city", item.city), ("country_code", item.country_code),
        ):  # fmt: skip
            row[key] = row[key] or value
    return list(companies.values())


def _upsert_companies(session: Session, rows: list[dict[str, Any]]) -> dict[str, uuid.UUID]:
    if not rows:
        return {}
    # executemany con «insertmanyvalues»: mucho más rápido que un VALUES gigante compilado.
    insert_stmt = pg_insert(_table(Company))
    excluded = insert_stmt.excluded
    stmt = insert_stmt.on_conflict_do_update(
        constraint="uq_companies_tenant_id_match_key",
        set_={
            "domain": func.coalesce(Company.domain, excluded.domain),
            "industry": func.coalesce(Company.industry, excluded.industry),
            "industry_code": func.coalesce(Company.industry_code, excluded.industry_code),
            "city": func.coalesce(Company.city, excluded.city),
            "country_code": func.coalesce(Company.country_code, excluded.country_code),
        },
    ).returning(Company.__table__.c.id, Company.__table__.c.match_key)
    result = session.connection().execute(stmt, rows)
    return {key: cid for cid, key in result.tuples()}


def _check_contact_limit(session: Session, tenant_id: uuid.UUID, adding: int = 1) -> None:
    from orkesta.admin.limits import LimitError, check_contacts

    try:
        check_contacts(session, tenant_id, adding)
    except LimitError as exc:
        raise DataError(str(exc), exc.status) from exc


def _fit_to_plan(
    session: Session, tenant_id: uuid.UUID, items: Sequence[ProspectRecord], totals: Totals
) -> list[ProspectRecord]:
    """Deja pasar las actualizaciones y solo los contactos nuevos que caben en el plan."""
    from orkesta.admin.limits import contact_capacity

    room = contact_capacity(session, tenant_id)
    if room is None or room >= len(items):
        return list(items)  # caben todos: no hace falta revisar cuáles ya existen
    keys = [dedup_key(i.email, i.full_name, i.company_key) for i in items]
    existing = set(
        session.scalars(
            select(Contact.dedup_key).where(
                Contact.tenant_id == tenant_id, Contact.dedup_key.in_(keys)
            )
        )
    )
    kept: list[ProspectRecord] = []
    for item, key in zip(items, keys, strict=True):
        if key in existing:
            kept.append(item)
        elif room > 0:
            kept.append(item)
            room -= 1
            existing.add(key)
        else:
            totals.sobre_limite += 1
    return kept


def _upsert_chunk(
    session: Session,
    *,
    tenant_id: uuid.UUID,
    user_id: uuid.UUID | None,
    import_id: uuid.UUID,
    label: str,
    items: Sequence[ProspectRecord],
    list_id: uuid.UUID | None,
    totals: Totals,
) -> None:
    items = _fit_to_plan(session, tenant_id, items, totals)
    if not items:
        return
    company_ids = _upsert_companies(session, _company_rows(tenant_id, items))
    totals.empresas.update(company_ids)
    rows = []
    for item in items:
        level, area = classify_title(item.title)
        rows.append(
            {
                "id": uuid.uuid4(),
                "tenant_id": tenant_id,
                "company_id": company_ids.get(item.company_key),
                "owner_user_id": user_id,
                "dedup_key": dedup_key(item.email, item.full_name, item.company_key),
                "first_name": item.first_name,
                "last_name": item.last_name,
                "full_name": item.full_name,
                "job_title": item.title,
                "level_code": level,
                "area_code": area,
                "company_name": item.company or None,
                "email": item.email,
                "email_domain": item.email_domain,
                "phone": item.phone,
                "city": item.city,
                "country_code": item.country_code,
                "industry": item.industry,
                "industry_code": item.industry_code,
                "status": "nuevo",
                "source": "importacion",
                "search_text": search_text(
                    item.full_name, item.email, item.company, item.title, item.city
                ),
            }
        )
    table = _table(Contact)
    insert_contacts = pg_insert(table)
    ex = insert_contacts.excluded

    def fill(column: Any, new: Any) -> Any:
        # Solo completa lo vacío: nunca se pisa ni se borra un dato existente.
        return func.coalesce(func.nullif(column, ""), new)

    upsert: Any = insert_contacts.on_conflict_do_update(
        constraint="uq_contacts_tenant_id_dedup_key",
        set_={
            "company_id": func.coalesce(table.c.company_id, ex.company_id),
            "first_name": fill(table.c.first_name, ex.first_name),
            "last_name": fill(table.c.last_name, ex.last_name),
            "job_title": fill(table.c.job_title, ex.job_title),
            "level_code": func.coalesce(table.c.level_code, ex.level_code),
            "area_code": func.coalesce(table.c.area_code, ex.area_code),
            "company_name": fill(table.c.company_name, ex.company_name),
            "email": func.coalesce(table.c.email, ex.email),
            "email_domain": func.coalesce(table.c.email_domain, ex.email_domain),
            "phone": fill(table.c.phone, ex.phone),
            "city": fill(table.c.city, ex.city),
            "country_code": func.coalesce(table.c.country_code, ex.country_code),
            "industry": fill(table.c.industry, ex.industry),
            "industry_code": func.coalesce(table.c.industry_code, ex.industry_code),
            "updated_at": func.now(),
        },
    ).returning(table.c.id, table.c.dedup_key, literal_column("(xmax = 0)").label("inserted"))
    result = session.connection().execute(upsert, rows).all()
    ids = {row.dedup_key: row.id for row in result}
    inserted = sum(1 for row in result if row.inserted)
    emit_many(session, tenant_id, "contact.created", [r.id for r in result if r.inserted],
              source="importacion", user_id=user_id)  # fmt: skip
    totals.nuevos += inserted
    totals.actualizados += len(result) - inserted
    # Procedencia: cada valor presente en el archivo queda registrado con su fuente.
    points = []
    for row, item in zip(rows, items, strict=True):
        contact_id = ids.get(row["dedup_key"])
        if contact_id is None:
            continue
        values = {
            "first_name": item.first_name,
            "last_name": item.last_name,
            "job_title": item.title,
            "company": item.company,
            "email": item.email,
            "phone": item.phone,
            "city": item.city,
            "country_code": item.country_code,
            "industry": item.industry,
        }
        points += [
            {
                "id": uuid.uuid4(),
                "tenant_id": tenant_id,
                "contact_id": contact_id,
                "field": name,
                "value": str(value)[:1000],
                "source_type": "importacion",
                "source_label": label,
                "source_ref": import_id,
                "confidence": 100,
                "actor_user_id": user_id,
            }
            for name, value in values.items()
            if value
        ]
    if points:
        session.connection().execute(pg_insert(_table(DataPoint)), points)
    if list_id is not None and ids:
        members = [
            {"id": uuid.uuid4(), "tenant_id": tenant_id, "list_id": list_id, "contact_id": cid}
            for cid in set(ids.values())
        ]
        session.connection().execute(
            pg_insert(_table(ListMember)).on_conflict_do_nothing(), members
        )


# --- Contactos -----------------------------------------------------------------------------


@dataclass
class ContactFilters:
    q: str = ""
    status: str = ""
    country: str = ""
    industry: str = ""
    company_id: uuid.UUID | None = None
    list_id: uuid.UUID | None = None
    email_status: str = ""


def _filtered(tenant_id: uuid.UUID, f: ContactFilters) -> Select[Any]:
    query = select(Contact).where(Contact.tenant_id == tenant_id)
    for word in fold(f.q).split()[:6]:
        query = query.where(Contact.search_text.contains(word, autoescape=True))
    if f.status in CONTACT_STATUSES:
        query = query.where(Contact.status == f.status)
    if f.country:
        query = query.where(Contact.country_code == f.country.upper()[:2])
    if f.industry:
        query = query.where(Contact.industry_code == f.industry)
    if f.company_id:
        query = query.where(Contact.company_id == f.company_id)
    if f.email_status == "sin_correo":
        query = query.where(Contact.email.is_(None))
    elif f.email_status in EMAIL_STATUSES:
        query = query.where(Contact.email_status == f.email_status, Contact.email.is_not(None))
    if f.list_id:
        query = query.where(
            Contact.id.in_(select(ListMember.contact_id).where(ListMember.list_id == f.list_id))
        )
    return query


def search_contacts(
    session: Session, tenant_id: uuid.UUID, f: ContactFilters, *, page: int, size: int
) -> tuple[list[Contact], int]:
    base = _filtered(tenant_id, f)
    total = session.scalar(select(func.count()).select_from(base.subquery())) or 0
    rows = session.scalars(
        base.order_by(Contact.updated_at.desc(), Contact.id).offset((page - 1) * size).limit(size)
    ).all()
    return list(rows), int(total)


def status_counts(session: Session, tenant_id: uuid.UUID, f: ContactFilters) -> dict[str, int]:
    base = _filtered(tenant_id, ContactFilters(**{**f.__dict__, "status": ""})).subquery()
    counts = {s: 0 for s in CONTACT_STATUSES}
    for status, n in session.execute(
        select(base.c.status, func.count()).group_by(base.c.status)
    ).tuples():
        counts[status] = int(n)
    counts["todos"] = sum(counts.values())
    return counts


def contact_dict(contact: Contact) -> dict[str, Any]:
    return {
        "id": str(contact.id),
        "full_name": contact.full_name,
        "first_name": contact.first_name,
        "last_name": contact.last_name,
        "job_title": contact.job_title,
        "company": contact.company_name,
        "company_id": str(contact.company_id) if contact.company_id else None,
        "email": contact.email,
        "phone": contact.phone,
        "city": contact.city,
        "country_code": contact.country_code,
        "country": country_name(contact.country_code),
        "industry": contact.industry,
        "industry_label": INDUSTRY_LABELS.get(contact.industry_code or "", contact.industry),
        "status": contact.status,
        "status_label": STATUS_LABELS[contact.status],
        "email_status": contact.email_status if contact.email else None,
        "email_check_reason": contact.email_check_reason,
        "linkedin_url": contact.linkedin_url,
        "email_checked_at": contact.email_checked_at.isoformat()
        if contact.email_checked_at
        else None,
        "source": contact.source,
        "updated_at": contact.updated_at.isoformat() if contact.updated_at else None,
    }


def get_contact(session: Session, tenant_id: uuid.UUID, contact_id: uuid.UUID) -> Contact:
    contact = session.get(Contact, contact_id)
    if contact is None or contact.tenant_id != tenant_id:
        raise DataError("Contacto no encontrado.", 404)
    return contact


def contact_detail(session: Session, contact: Contact) -> dict[str, Any]:
    points = session.scalars(
        select(DataPoint)
        .where(DataPoint.contact_id == contact.id)
        .order_by(DataPoint.created_at.desc())
    ).all()
    by_field: dict[str, list[dict[str, Any]]] = {}
    for p in points:
        by_field.setdefault(p.field, []).append(
            {
                "value": p.value,
                "source_type": p.source_type,
                "source_label": p.source_label,
                "obtained_at": p.created_at.isoformat(),
                "confidence": p.confidence,
            }
        )
    current = {
        "first_name": contact.first_name or None,
        "last_name": contact.last_name or None,
        "job_title": contact.job_title,
        "company": contact.company_name,
        "email": contact.email,
        "phone": contact.phone,
        "city": contact.city,
        "country_code": country_name(contact.country_code) or contact.country_code,
        "industry": contact.industry,
    }
    lists = session.execute(
        select(ContactList.id, ContactList.name)
        .join(ListMember, ListMember.list_id == ContactList.id)
        .where(ListMember.contact_id == contact.id)
        .order_by(ContactList.name)
    ).tuples()
    return {
        **contact_dict(contact),
        "fields": [
            {
                "field": name,
                "label": label,
                "value": current[name],
                # Un campo sin valor queda marcado para el enriquecimiento (nunca se inventa).
                "pending": not current[name],
                "sources": by_field.get(name, []),
            }
            for name, label in FIELD_LABELS.items()
        ],
        "lists": [{"id": str(i), "name": n} for i, n in lists],
    }


@dataclass
class ContactInput:
    first_name: str = ""
    last_name: str = ""
    job_title: str = ""
    company: str = ""
    email: str = ""
    phone: str = ""
    city: str = ""
    country: str = ""
    industry: str = ""


def _clean_input(data: ContactInput) -> dict[str, Any]:
    email = data.email.strip().lower() or None
    if email and ("@" not in email or "." not in email.split("@")[-1]):
        raise DataError("El correo no tiene un formato válido.")
    first, last = data.first_name.strip(), data.last_name.strip()
    if not first:
        raise DataError("Escribe al menos el nombre.")
    location = parse_location(", ".join(x for x in (data.city, data.country) if x.strip()))
    country = location.country_code
    if data.country.strip() and not country:
        raise DataError("No reconocemos ese país. Escríbelo completo, por ejemplo «Colombia».")
    company = data.company.strip()
    industry = data.industry.strip() or None
    return {
        "first_name": first[:200],
        "last_name": last[:200],
        "full_name": f"{first} {last}".strip()[:300],
        "job_title": data.job_title.strip()[:300] or None,
        "company_name": company[:300] or None,
        "company_key": (normalize_company(company).match_key or fold(company))[:300]
        if company
        else "",
        "email": email,
        "email_domain": (registrable_domain(email.split("@", 1)[1]) or email.split("@", 1)[1])
        if email
        else None,
        "phone": data.phone.strip()[:40] or None,
        "city": (data.city.strip() or location.city or "")[:120] or None,
        "country_code": country,
        "industry": industry[:200] if industry else None,
        "industry_code": classify(industry) if industry else None,
    }


def _ensure_company(
    session: Session, tenant_id: uuid.UUID, values: dict[str, Any]
) -> uuid.UUID | None:
    if not values["company_key"]:
        return None
    ids = _upsert_companies(
        session,
        [
            {
                "id": uuid.uuid4(),
                "tenant_id": tenant_id,
                "name": values["company_name"],
                "match_key": values["company_key"],
                "domain": values["email_domain"],
                "industry": values["industry"],
                "industry_code": values["industry_code"],
                "city": values["city"],
                "country_code": values["country_code"],
            }
        ],
    )
    return ids.get(values["company_key"])


def _record_points(
    session: Session,
    contact: Contact,
    values: dict[str, Any],
    user_id: uuid.UUID,
    label: str,
) -> None:
    for name in FIELD_LABELS:
        value = values.get("company_name" if name == "company" else name)
        if value:
            session.add(
                DataPoint(
                    tenant_id=contact.tenant_id,
                    contact_id=contact.id,
                    field=name,
                    value=str(value)[:1000],
                    source_type="manual",
                    source_label=label,
                    confidence=100,
                    actor_user_id=user_id,
                )
            )


def create_contact(
    session: Session, *, tenant_id: uuid.UUID, user: Any, data: ContactInput
) -> Contact:
    values = _clean_input(data)
    key = dedup_key(values["email"], values["full_name"], values["company_key"])
    existing = session.scalar(
        select(Contact.id).where(Contact.tenant_id == tenant_id, Contact.dedup_key == key)
    )
    if existing is not None:
        raise DataError("Ese contacto ya existe en tu base.", 409)
    _check_contact_limit(session, tenant_id)
    contact = Contact(
        tenant_id=tenant_id,
        company_id=_ensure_company(session, tenant_id, values),
        owner_user_id=user.id,
        dedup_key=key,
        source="manual",
        status="nuevo",
        search_text=search_text(
            values["full_name"],
            values["email"],
            values["company_name"],
            values["job_title"],
            values["city"],
        ),
        **{k: v for k, v in values.items() if k != "company_key"},
    )
    contact.level_code, contact.area_code = classify_title(values["job_title"])
    session.add(contact)
    session.flush()
    _record_points(
        session, contact, values, user.id, f"Ingreso manual de {user.full_name or user.email}"
    )
    emit(
        session,
        tenant_id,
        "contact.created",
        contact_id=contact.id,
        source="manual",
        user_id=user.id,
    )
    return contact


def update_contact(session: Session, *, contact: Contact, user: Any, data: ContactInput) -> Contact:
    values = _clean_input(data)
    changed = {}
    for key, value in values.items():
        if key == "company_key":
            continue
        if getattr(contact, key) != value:
            changed[key] = value
            setattr(contact, key, value)
    if "company_name" in changed:
        contact.company_id = _ensure_company(session, contact.tenant_id, values)
    if "email" in changed:
        contact.email_status, contact.email_checked_at, contact.email_check_reason = (
            "sin_validar",
            None,
            None,
        )
    contact.level_code, contact.area_code = classify_title(contact.job_title)
    contact.search_text = search_text(
        contact.full_name, contact.email, contact.company_name, contact.job_title, contact.city
    )
    _record_points(session, contact, changed, user.id, f"Edición de {user.full_name or user.email}")
    session.flush()
    return contact


def delete_contacts(session: Session, tenant_id: uuid.UUID, ids: Iterable[uuid.UUID]) -> int:
    result = session.execute(
        delete(Contact).where(Contact.tenant_id == tenant_id, Contact.id.in_(list(ids)))
    )
    return int(getattr(result, "rowcount", 0) or 0)


# --- Listas --------------------------------------------------------------------------------


def create_list(
    session: Session,
    *,
    tenant_id: uuid.UUID,
    user_id: uuid.UUID | None,
    name: str,
    description: str = "",
) -> ContactList:
    name = " ".join(name.split())[:120]
    if len(name) < 2:
        raise DataError("Escribe un nombre para la lista.")
    duplicate = session.scalar(
        select(ContactList.id).where(
            ContactList.tenant_id == tenant_id, func.lower(ContactList.name) == name.lower()
        )
    )
    if duplicate is not None:
        raise DataError("Ya existe una lista con ese nombre.", 409)
    item = ContactList(
        tenant_id=tenant_id, name=name, description=description[:500], created_by=user_id
    )
    session.add(item)
    session.flush()
    return item


def get_list(session: Session, tenant_id: uuid.UUID, list_id: uuid.UUID) -> ContactList:
    item = session.get(ContactList, list_id)
    if item is None or item.tenant_id != tenant_id:
        raise DataError("Lista no encontrada.", 404)
    return item


def lists_with_counts(session: Session, tenant_id: uuid.UUID) -> list[dict[str, Any]]:
    rows = session.execute(
        select(ContactList, func.count(ListMember.id))
        .outerjoin(ListMember, ListMember.list_id == ContactList.id)
        .where(ContactList.tenant_id == tenant_id)
        .group_by(ContactList.id)
        .order_by(ContactList.name)
    ).tuples()
    return [
        {
            "id": str(item.id),
            "name": item.name,
            "description": item.description,
            "contacts": int(count),
            "created_at": item.created_at.isoformat(),
        }
        for item, count in rows
    ]


def add_to_list(
    session: Session,
    *,
    tenant_id: uuid.UUID,
    list_id: uuid.UUID,
    contact_ids: Sequence[uuid.UUID] | None = None,
    filters: ContactFilters | None = None,
    limit: int = 50_000,
) -> int:
    get_list(session, tenant_id, list_id)
    if contact_ids is not None:
        ids = session.scalars(
            select(Contact.id).where(Contact.tenant_id == tenant_id, Contact.id.in_(contact_ids))
        ).all()
    else:
        ids = session.scalars(
            _filtered(tenant_id, filters or ContactFilters())
            .with_only_columns(Contact.id)
            .limit(limit)
        ).all()
    if not ids:
        return 0
    added = 0
    for start in range(0, len(ids), CHUNK):
        rows = [
            {"id": uuid.uuid4(), "tenant_id": tenant_id, "list_id": list_id, "contact_id": cid}
            for cid in ids[start : start + CHUNK]
        ]
        result = session.execute(
            pg_insert(ListMember).values(rows).on_conflict_do_nothing().returning(ListMember.id)
        )
        added += len(result.fetchall())
    return added


def remove_from_list(
    session: Session, tenant_id: uuid.UUID, list_id: uuid.UUID, contact_ids: Sequence[uuid.UUID]
) -> int:
    get_list(session, tenant_id, list_id)
    result = session.execute(
        delete(ListMember).where(
            and_(ListMember.list_id == list_id, ListMember.contact_id.in_(contact_ids))
        )
    )
    return int(getattr(result, "rowcount", 0) or 0)


# --- Exportación ---------------------------------------------------------------------------

ORIGIN_LABELS = {
    "importacion": "Importación",
    "manual": "Manual",
    "enriquecimiento": "Enriquecimiento",
    "investigacion": "Investigación IA",
}
EXPORT_COLUMNS = (
    "Nombre", "Apellido", "Cargo", "Empresa", "Correo", "Teléfono", "Ciudad", "País",
    "Industria", "Estado", "Origen",
)  # fmt: skip


def _safe(value: str | None) -> str:
    """Evita que Excel interprete un valor como fórmula (inyección CSV)."""
    text_value = value or ""
    return "'" + text_value if text_value[:1] in ("=", "+", "-", "@", "\t", "\r") else text_value


def export_csv(session: Session, tenant_id: uuid.UUID, f: ContactFilters) -> bytes:
    buffer = io.StringIO(newline="")
    writer = csv.writer(buffer)
    writer.writerow(EXPORT_COLUMNS)
    for c in session.scalars(
        _filtered(tenant_id, f).order_by(Contact.full_name).execution_options(yield_per=2000)
    ):
        writer.writerow(
            [
                _safe(v)
                for v in (
                    c.first_name, c.last_name, c.job_title, c.company_name, c.email, c.phone,
                    c.city, country_name(c.country_code) or c.country_code, c.industry,
                    STATUS_LABELS[c.status],
                    ORIGIN_LABELS.get(c.source, "Manual"),
                )
            ]
        )  # fmt: skip
    return buffer.getvalue().encode("utf-8-sig")


def emails_validated(session: Session, tenant_id: uuid.UUID) -> int:
    return int(
        session.scalar(
            select(func.count()).where(
                Contact.tenant_id == tenant_id, Contact.email_status == "valido"
            )
        )
        or 0
    )


def contacts_total(session: Session, tenant_id: uuid.UUID) -> int:
    return int(
        session.scalar(
            select(func.count()).select_from(Contact).where(Contact.tenant_id == tenant_id)
        )
        or 0
    )


def recent_imports(session: Session, tenant_id: uuid.UUID, limit: int = 10) -> list[ImportJob]:
    return list(
        session.scalars(
            select(ImportJob)
            .where(ImportJob.tenant_id == tenant_id, ImportJob.status != "preview")
            .order_by(ImportJob.created_at.desc())
            .limit(limit)
        )
    )
