"""Servicio de la base de prospectos: cargar bases, aprender patrones y buscar con filtros."""

from __future__ import annotations

import re
import uuid
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field

from sqlalchemy import func, or_, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from orkesta.audit import audit
from orkesta.catalog.prospects import (
    ProspectRecord,
    learn_patterns,
    merge,
    read_sheets,
    records_from_file,
)
from orkesta.catalog.titles import classify_title
from orkesta.db.models import DomainPattern, Prospect, ProspectUpload
from orkesta.engine.normalize.text import fold
from orkesta.worker.queue import enqueue_job

UPLOAD_JOB_KIND = "import_prospects"
CHUNK = 1000


class ProspectUploadError(ValueError):
    """La carga no se puede procesar."""


# --- Carga -----------------------------------------------------------------------------------


def create_upload(
    session: Session,
    *,
    organization_id: uuid.UUID,
    user_id: uuid.UUID | None,
    filename: str,
    content: bytes,
) -> ProspectUpload:
    """Guarda el archivo y encola su procesamiento en el worker."""
    upload = ProspectUpload(
        organization_id=organization_id,
        user_id=user_id,
        filename=filename[:255],
        content=content,
        status="queued",
    )
    session.add(upload)
    session.flush()
    enqueue_job(
        session,
        organization_id=organization_id,
        batch_id=None,
        kind=UPLOAD_JOB_KIND,
        idempotency_key=f"upload:{upload.id}",
        payload={"upload_id": str(upload.id)},
        max_attempts=2,
    )
    return upload


def process_upload(session: Session, upload_id: uuid.UUID) -> dict[str, int]:
    upload = session.get(ProspectUpload, upload_id, with_for_update=True)
    if upload is None or upload.content is None:
        raise ProspectUploadError("La carga no existe o ya se procesó.")
    upload.status = "running"
    records = records_from_file(upload.content, upload.filename)
    if not records:
        raise ProspectUploadError(
            "No se encontraron personas: el archivo necesita columnas de nombre y empresa."
        )
    summary = upsert_prospects(
        session, upload.organization_id, records, source="base", source_name=upload.filename
    )
    summary["dominios_con_patron"] = refresh_domain_patterns(
        session, upload.organization_id, {r.email_domain for r in records if r.email_domain}
    )
    # Planillas de envíos anteriores (SENT, FECHAENVIO, REPLIED…): historial de contacto.
    from orkesta.campaigns.history import history_from_sheets, upsert_history

    summary["historial"] = upsert_history(
        session,
        upload.organization_id,
        history_from_sheets(read_sheets(upload.content, upload.filename)),
        source=upload.filename,
    )
    upload.summary = summary
    upload.status = "completed"
    upload.content = None  # el archivo no se conserva
    upload.finished_at = session.scalar(select(func.now()))
    audit(
        session,
        organization_id=upload.organization_id,
        action="prospects.uploaded",
        entity_type="prospect_upload",
        entity_id=upload.id,
        personas=summary["personas"],
        nuevas=summary["nuevas"],
        dominios_con_patron=summary["dominios_con_patron"],
    )
    return summary


def mark_upload_failed(session: Session, upload_id: uuid.UUID, error: str) -> None:
    upload = session.get(ProspectUpload, upload_id)
    if upload is not None:
        upload.status = "failed"
        upload.error = error[:2000]
        upload.content = None
        upload.finished_at = session.scalar(select(func.now()))


def _row(
    organization_id: uuid.UUID, record: ProspectRecord, source: str, name: str | None
) -> dict[str, object]:
    level, area = classify_title(record.title)
    return {
        "id": uuid.uuid4(),
        "organization_id": organization_id,
        "dedup_key": record.dedup_key,
        "full_name": record.full_name,
        "first_name": record.first_name,
        "last_name": record.last_name,
        "title": record.title,
        "company": record.company,
        "company_key": record.company_key,
        "email": record.email,
        "email_domain": record.email_domain,
        "email_status": "known" if record.email else None,
        "phone": record.phone,
        "city": record.city,
        "country_code": record.country_code,
        "industry": record.industry,
        "industry_code": record.industry_code,
        "level_code": level,
        "area_code": area,
        "title_search": fold(record.title or ""),
        "company_search": fold(record.company),
        "source": source,
        "source_name": (name or "")[:200] or None,
    }


# Al volver a cargar a una persona, un dato nuevo completa o actualiza, pero un vacío nunca borra.
_UPDATABLE = (
    "full_name", "first_name", "last_name", "title", "company", "company_key", "phone", "city",
    "country_code", "industry", "industry_code", "level_code", "area_code", "title_search",
    "company_search", "source_name",
)  # fmt: skip


def upsert_prospects(
    session: Session,
    organization_id: uuid.UUID,
    records: Sequence[ProspectRecord],
    *,
    source: str,
    source_name: str | None = None,
) -> dict[str, int]:
    """Inserta o completa personas por ``dedup_key``. Devuelve nuevas y actualizadas."""
    unique: dict[str, ProspectRecord] = {}
    for record in records:
        previous = unique.get(record.dedup_key)
        unique[record.dedup_key] = merge(previous, record) if previous else record
    before = _count(session, organization_id)
    items = list(unique.values())
    for start in range(0, len(items), CHUNK):
        rows = [_row(organization_id, r, source, source_name) for r in items[start : start + CHUNK]]
        statement = pg_insert(Prospect).values(rows)
        excluded = statement.excluded
        statement = statement.on_conflict_do_update(
            constraint="uq_prospects_organization_id_dedup_key",
            set_={
                **{
                    name: func.coalesce(func.nullif(excluded[name], ""), Prospect.__table__.c[name])
                    for name in _UPDATABLE
                },
                "updated_at": func.now(),
            },
        )
        session.execute(statement)
    created = _count(session, organization_id) - before
    return {"personas": len(items), "nuevas": created, "actualizadas": len(items) - created}


def _count(session: Session, organization_id: uuid.UUID) -> int:
    return int(
        session.scalar(
            select(func.count())
            .select_from(Prospect)
            .where(Prospect.organization_id == organization_id)
        )
        or 0
    )


def refresh_domain_patterns(
    session: Session, organization_id: uuid.UUID, domains: Iterable[str]
) -> int:
    """Recalcula el patrón de esos dominios con todos los correos conocidos de la organización."""
    wanted = sorted({d for d in domains if d})
    if not wanted:
        return 0
    learned_total = 0
    for start in range(0, len(wanted), CHUNK):
        chunk = wanted[start : start + CHUNK]
        rows = session.execute(
            select(
                Prospect.first_name, Prospect.last_name, Prospect.email, Prospect.email_domain
            ).where(
                Prospect.organization_id == organization_id,
                Prospect.email_domain.in_(chunk),
                Prospect.email.is_not(None),
                # Solo correos reales enseñan patrones; los deducidos nunca.
                Prospect.email_status == "known",
            )
        )
        records = [
            ProspectRecord(
                full_name="",
                first_name=first,
                last_name=last,
                title=None,
                company="",
                company_key="",
                email=email,
                email_domain=domain,
                phone=None,
                city=None,
                country_code=None,
                industry=None,
                industry_code=None,
            )
            for first, last, email, domain in rows
        ]
        learned = learn_patterns(records)
        if not learned:
            continue
        statement = pg_insert(DomainPattern).values(
            [
                {
                    "id": uuid.uuid4(),
                    "organization_id": organization_id,
                    "domain": p.domain,
                    "pattern": p.pattern,
                    "people": p.people,
                    "agreeing": p.agreeing,
                }
                for p in learned.values()
            ]
        )
        session.execute(
            statement.on_conflict_do_update(
                constraint="uq_domain_patterns_organization_id_domain",
                set_={
                    "pattern": statement.excluded.pattern,
                    "people": statement.excluded.people,
                    "agreeing": statement.excluded.agreeing,
                    "updated_at": func.now(),
                },
            )
        )
        learned_total += len(learned)
    return learned_total


def known_pattern(
    session: Session, organization_id: uuid.UUID, site_domain: str
) -> DomainPattern | None:
    """Patrón conocido del dominio del sitio o de la misma marca con otra extensión
    (samsung.com.mx → samsung.com). Gana el dominio exacto; luego el de más personas."""
    brand = site_domain.split(".", 1)[0]
    if not brand:
        return None
    return session.scalar(
        select(DomainPattern)
        .where(
            DomainPattern.organization_id == organization_id,
            or_(
                DomainPattern.domain == site_domain,
                func.split_part(DomainPattern.domain, ".", 1) == brand,
            ),
        )
        .order_by((DomainPattern.domain == site_domain).desc(), DomainPattern.people.desc())
        .limit(1)
    )


# --- Búsqueda --------------------------------------------------------------------------------


@dataclass
class ProspectFilters:
    title: str = ""  # palabras del cargo: todas deben aparecer ("gerente compras")
    company: str = ""
    industries: list[str] = field(default_factory=list)
    countries: list[str] = field(default_factory=list)
    levels: list[str] = field(default_factory=list)
    areas: list[str] = field(default_factory=list)
    only_web: bool = False
    sizes: list[str] = field(default_factory=list)  # solo para elegir empresas a explorar
    city: str = ""
    with_email: bool = False

    @property
    def active(self) -> bool:
        return bool(
            self.title or self.company or self.industries or self.countries or self.city
            or self.with_email or self.levels or self.areas or self.only_web
        )  # fmt: skip


def _words(text: str) -> list[str]:
    return [w for w in re.split(r"[^a-z0-9]+", fold(text)) if w][:8]


def _word_start(word: str) -> str:
    """Expresión de PostgreSQL: ``word`` al inicio de una palabra (\\m)."""
    return r"\m" + re.escape(word)


def search_prospects(
    session: Session,
    organization_id: uuid.UUID,
    filters: ProspectFilters,
    *,
    page: int = 1,
    page_size: int = 50,
) -> tuple[list[Prospect], int]:
    query = select(Prospect).where(Prospect.organization_id == organization_id)
    for word in _words(filters.title):
        # Cada palabra debe empezar una palabra del cargo, en cualquier orden: "ti" encuentra
        # "Director de TI" pero no "estratégico". El índice trigram acelera la expresión.
        query = query.where(Prospect.title_search.op("~")(_word_start(word)))
    for word in _words(filters.company):
        query = query.where(Prospect.company_search.op("~")(_word_start(word)))
    if filters.industries:
        query = query.where(Prospect.industry_code.in_(filters.industries))
    if filters.countries:
        query = query.where(Prospect.country_code.in_(filters.countries))
    if filters.levels:
        query = query.where(Prospect.level_code.in_(filters.levels))
    if filters.areas:
        query = query.where(Prospect.area_code.in_(filters.areas))
    if filters.city:
        query = query.where(
            func.lower(Prospect.city).contains(filters.city.lower(), autoescape=True)
        )
    if filters.only_web:
        query = query.where(Prospect.source == "web")
    if filters.with_email:
        query = query.where(Prospect.email.is_not(None))
    total = int(session.scalar(select(func.count()).select_from(query.subquery())) or 0)
    rows = session.scalars(
        query.order_by(Prospect.company, Prospect.full_name)
        .offset((max(page, 1) - 1) * page_size)
        .limit(page_size)
    ).all()
    return list(rows), total


def iter_search(
    session: Session, organization_id: uuid.UUID, filters: ProspectFilters, limit: int = 50_000
) -> list[Prospect]:
    rows, _ = search_prospects(session, organization_id, filters, page=1, page_size=limit)
    return rows
