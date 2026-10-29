"""Búsqueda de dominios para un lote importado.

Flujo::

    start_domain_enrichment → lote "queued", un trabajo "resolve_domain" por empresa y país
    handle (worker)         → historial reciente o motor; guarda dominios y evidencias
    _advance                → cuenta resultados; el último trabajo deja el lote "completed"
"""

from __future__ import annotations

import uuid
from collections import defaultdict
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any

from sqlalchemy import func, select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from orkesta.audit import audit
from orkesta.db.models import (
    Company,
    Contact,
    Domain,
    EnrichmentBatch,
    EnrichmentJob,
    EvidenceRecord,
    ImportRow,
)
from orkesta.engine.domain.model import DomainModel, active_model
from orkesta.engine.domain.resolver import DomainResolution, resolve_domain
from orkesta.engine.domain.scoring import ScoredCandidate
from orkesta.engine.domain.wikidata import CompanySource
from orkesta.engine.net.dns import Resolver
from orkesta.engine.net.fetch import RobotsAwareFetcher
from orkesta.engine.normalize import normalize_company
from orkesta.worker.queue import enqueue_job

DOMAIN_JOB_KIND = "resolve_domain"
HISTORY_DAYS = 30
STORED_CANDIDATES = 5


class EnrichmentStateError(ValueError):
    """La operación no es válida en el estado actual del lote."""


@dataclass(frozen=True)
class EngineDeps:
    resolver: Resolver
    fetcher: RobotsAwareFetcher
    wikidata: CompanySource | None
    model: DomainModel | None = None


EngineFactory = Callable[[], EngineDeps]


def default_engine() -> EngineDeps:
    from orkesta.engine.domain.wikidata import WikidataSource
    from orkesta.engine.net.dns import DnsPythonResolver
    from orkesta.engine.net.fetch import SafeFetcher

    resolver = DnsPythonResolver()
    return EngineDeps(resolver, SafeFetcher(resolver=resolver), WikidataSource(), active_model())


def _batch_companies(session: Session, batch_id: uuid.UUID) -> list[Company]:
    return list(
        session.scalars(
            select(Company)
            .join(Contact, Contact.company_id == Company.id)
            .join(ImportRow, ImportRow.contact_id == Contact.id)
            .where(ImportRow.batch_id == batch_id)
            .distinct()
        )
    )


def start_domain_enrichment(
    session: Session, batch: EnrichmentBatch, *, user_id: uuid.UUID | None, max_attempts: int = 3
) -> int:
    """Encola la búsqueda de dominios del lote. Devuelve el número de empresas a investigar."""
    if batch.status not in ("imported", "completed"):
        raise EnrichmentStateError("Solo se pueden enriquecer lotes importados.")
    companies = _batch_companies(session, batch.id)
    if not companies:
        raise EnrichmentStateError("El lote no tiene empresas válidas.")

    groups: dict[tuple[str, str], list[uuid.UUID]] = defaultdict(list)
    for company in companies:
        if not company.match_key:
            company.match_key = normalize_company(company.name_original).match_key[:300]
        groups[(company.match_key, company.country_code or "")].append(company.id)

    run = int(batch.enrichment_summary.get("run", 0)) + 1
    for (match_key, country), ids in groups.items():
        enqueue_job(
            session,
            organization_id=batch.organization_id,
            batch_id=batch.id,
            kind=DOMAIN_JOB_KIND,
            idempotency_key=f"domain:{run}:{match_key}:{country}"[:200],
            payload={"company_ids": [str(i) for i in ids], "country": country or None},
            max_attempts=max_attempts,
        )
    batch.status = "queued"
    batch.finished_at = None
    batch.enrichment_summary = {
        "run": run,
        "total": len(groups),
        "done": 0,
        "failed": 0,
        "statuses": {},
    }
    audit(
        session,
        organization_id=batch.organization_id,
        actor_user_id=user_id,
        action="batch.enrichment_started",
        entity_type="enrichment_batch",
        entity_id=batch.id,
        companies=len(groups),
    )
    return len(groups)


def _recent_selection(
    session: Session, organization_id: uuid.UUID, match_key: str, country: str | None
) -> Domain | None:
    """Dominio elegido hace poco para la misma empresa (cualquier lote): evita repetir red."""
    since = func.now() - timedelta(days=HISTORY_DAYS)
    return session.scalar(
        select(Domain)
        .join(Company, Company.id == Domain.company_id)
        .where(
            Company.organization_id == organization_id,
            Company.match_key == match_key,
            Company.country_code.is_(None) if country is None else Company.country_code == country,
            Domain.is_selected.is_(True),
            Domain.last_checked_at >= since,
        )
        .order_by(Domain.last_checked_at.desc())
        .limit(1)
    )


def _upsert_domain(
    session: Session,
    company: Company,
    scored: ScoredCandidate,
    *,
    selected: bool,
    now: datetime,
    method: str | None = None,
) -> uuid.UUID:
    evidence = scored.evidence
    values: dict[str, Any] = {
        "organization_id": company.organization_id,
        "company_id": company.id,
        "domain": scored.domain,
        "status": scored.status,
        "confidence_score": scored.score,
        "confidence_level": scored.confidence_level,
        "reason_codes": list(scored.reasons),
        "has_mx": evidence.has_mx if evidence.resolves else None,
        "mx_provider": evidence.mx_provider,
        "discovery_method": (method or evidence.method)[:40],
        "model_version": scored.model_version,
        "is_selected": selected,
        "final_url": evidence.final_url,
        "site_title": evidence.page.title[:300] if evidence.page else None,
        "last_checked_at": now,
    }
    statement = pg_insert(Domain).values(values)
    upsert = statement.on_conflict_do_update(
        constraint="uq_domains_company_id_domain",
        set_={k: statement.excluded[k] for k in values if k not in ("company_id", "domain")}
        | {"updated_at": func.now()},
    ).returning(Domain.id)
    domain_id: uuid.UUID = session.execute(upsert).scalar_one()
    return domain_id


def _store_evidence(
    session: Session, company: Company, domain_id: uuid.UUID, resolution: DomainResolution
) -> None:
    selected = resolution.selected
    if selected is None:
        return
    now = session.scalar(select(func.now()))
    records: list[tuple[str, str, str | None, str]] = []
    for hit in resolution.wikidata_hits:
        if hit.domain == selected.domain:
            records.append(("wikidata", hit.url, f"Sitio oficial (P856): {hit.domain}", "api"))
    evidence = selected.evidence
    if evidence.final_url:
        title = evidence.page.title if evidence.page else None
        records.append(("website", evidence.final_url, title, "http"))
    if evidence.mx_provider:
        records.append(("dns", f"dns:{selected.domain}", f"MX: {evidence.mx_provider}", "dns"))
    for source_type, url, snippet, method in records:
        session.add(
            EvidenceRecord(
                organization_id=company.organization_id,
                domain_id=domain_id,
                source_type=source_type,
                url=url[:2000],
                snippet=(snippet or "")[:500] or None,
                method=method,
                observed_at=now,
            )
        )


def save_resolution(
    session: Session, companies: list[Company], resolution: DomainResolution
) -> None:
    now: datetime = session.scalar(select(func.now()))  # type: ignore[assignment]
    company_ids = [c.id for c in companies]
    session.execute(
        update(Domain).where(Domain.company_id.in_(company_ids)).values(is_selected=False)
    )
    keep = [c for c in resolution.candidates if c.status != "rejected"][:STORED_CANDIDATES]
    for company in companies:
        for scored in keep:
            if resolution.selected is not None and scored.domain == resolution.selected.domain:
                continue
            _upsert_domain(session, company, scored, selected=False, now=now)
        if resolution.selected is not None:
            domain_id = _upsert_domain(
                session, company, resolution.selected, selected=True, now=now
            )
            _store_evidence(session, company, domain_id, resolution)
        company.domain_status = resolution.status
        company.domain_checked_at = now


def _copy_selection(session: Session, companies: list[Company], source: Domain) -> None:
    now: datetime = session.scalar(select(func.now()))  # type: ignore[assignment]
    for company in companies:
        if company.id == source.company_id:
            company.domain_status = source.status
            company.domain_checked_at = now
            continue
        session.execute(
            update(Domain).where(Domain.company_id == company.id).values(is_selected=False)
        )
        values: dict[str, Any] = {
            "organization_id": company.organization_id,
            "company_id": company.id,
            "domain": source.domain,
            "status": source.status,
            "confidence_score": source.confidence_score,
            "confidence_level": source.confidence_level,
            "reason_codes": [*source.reason_codes, "HISTORY"],
            "has_mx": source.has_mx,
            "mx_provider": source.mx_provider,
            "discovery_method": "history",
            "model_version": source.model_version,
            "is_selected": True,
            "final_url": source.final_url,
            "site_title": source.site_title,
            "last_checked_at": source.last_checked_at,
        }
        statement = pg_insert(Domain).values(values)
        session.execute(
            statement.on_conflict_do_update(
                constraint="uq_domains_company_id_domain",
                set_={
                    k: statement.excluded[k] for k in values if k not in ("company_id", "domain")
                },
            )
        )
        company.domain_status = source.status
        company.domain_checked_at = now


def resolve_job(session: Session, job: EnrichmentJob, engine: EngineFactory) -> str:
    """Resuelve el dominio de un grupo de empresas. Devuelve el estado resultante."""
    ids = [uuid.UUID(i) for i in job.payload.get("company_ids", [])]
    companies = list(session.scalars(select(Company).where(Company.id.in_(ids))))
    if not companies:
        return "unknown"
    country: str | None = job.payload.get("country")
    match_key = companies[0].match_key or normalize_company(companies[0].name_original).match_key

    recent = _recent_selection(session, job.organization_id, match_key, country)
    if recent is not None:
        _copy_selection(session, companies, recent)
        return recent.status

    names = [normalize_company(c.name_original) for c in companies]
    deps = engine()
    resolution = resolve_domain(
        names,
        country,
        resolver=deps.resolver,
        fetcher=deps.fetcher,
        wikidata=deps.wikidata,
        model=deps.model,
    )
    save_resolution(session, companies, resolution)
    return resolution.status


def advance_batch(session: Session, batch_id: uuid.UUID, outcome: str) -> None:
    """Registra el resultado de un trabajo; el último deja el lote como completado."""
    batch = session.get(EnrichmentBatch, batch_id, with_for_update=True)
    if batch is None or batch.status not in ("queued", "running"):
        return
    summary = dict(batch.enrichment_summary)
    statuses = dict(summary.get("statuses", {}))
    if outcome == "failed":
        summary["failed"] = int(summary.get("failed", 0)) + 1
    else:
        summary["done"] = int(summary.get("done", 0)) + 1
        statuses[outcome] = int(statuses.get(outcome, 0)) + 1
    summary["statuses"] = statuses
    batch.enrichment_summary = summary
    if batch.status == "queued":
        batch.status = "running"
        batch.started_at = session.scalar(select(func.now()))
    if int(summary["done"]) + int(summary["failed"]) >= int(summary.get("total", 0)):
        batch.status = "completed"
        batch.finished_at = session.scalar(select(func.now()))
        audit(
            session,
            organization_id=batch.organization_id,
            action="batch.enrichment_completed",
            entity_type="enrichment_batch",
            entity_id=batch.id,
            done=summary["done"],
            failed=summary["failed"],
        )


def mark_company_error(session: Session, job: EnrichmentJob) -> None:
    ids = [uuid.UUID(i) for i in job.payload.get("company_ids", [])]
    if ids:
        session.execute(
            update(Company)
            .where(Company.id.in_(ids))
            .values(domain_status="error", domain_checked_at=func.now())
        )
