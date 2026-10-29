"""Descubrimiento de personas en la web: de filtros de empresa a nuevas personas en la base.

1. ``start_discovery`` elige las empresas que cumplen los filtros (industria, país, nombre) con
   dominio corporativo conocido, omite las exploradas en los últimos 30 días y encola un
   trabajo por dominio.
2. ``run_discovery_job`` recorre el sitio (equipo, directivos, prensa), extrae nombre y cargo y
   agrega cada persona nueva con la URL de origen y un correo deducido: con el patrón real de
   la empresa si se conoce (confianza alta) o con el más probable (confianza baja).
"""

from __future__ import annotations

import uuid
from collections.abc import Callable
from datetime import timedelta
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from orkesta.catalog.prospects import FREEMAIL_DOMAINS
from orkesta.catalog.service import ProspectFilters, known_pattern
from orkesta.catalog.titles import classify_title
from orkesta.db.models import DiscoveryRun, Prospect, WebDiscovery
from orkesta.engine.email.decide import decide, decide_from_known, generate_emails
from orkesta.engine.net.fetch import RobotsAwareFetcher
from orkesta.engine.normalize import normalize_company, normalize_person
from orkesta.engine.normalize.text import fold
from orkesta.engine.people.discover import discover_people
from orkesta.worker.queue import enqueue_job

DISCOVERY_JOB_KIND = "discover_people"
RECENT_DAYS = 30
MAX_COMPANIES = 200


class DiscoveryError(ValueError):
    """No se puede iniciar o completar el descubrimiento."""


def candidate_companies(
    session: Session,
    organization_id: uuid.UUID,
    filters: ProspectFilters,
    limit: int = MAX_COMPANIES,
) -> list[dict[str, Any]]:
    """Empresas con dominio corporativo conocido que cumplen los filtros de empresa."""
    query = (
        select(
            Prospect.email_domain,
            func.mode().within_group(Prospect.company).label("company"),
            func.mode().within_group(Prospect.country_code).label("country"),
            func.mode().within_group(Prospect.industry_code).label("industry_code"),
            func.mode().within_group(Prospect.industry).label("industry"),
            func.count().label("people"),
        )
        .where(
            Prospect.organization_id == organization_id,
            Prospect.email_status == "known",
            Prospect.email_domain.is_not(None),
            Prospect.email_domain.not_in(FREEMAIL_DOMAINS),
        )
        .group_by(Prospect.email_domain)
    )
    if filters.industries:
        query = query.where(Prospect.industry_code.in_(filters.industries))
    if filters.countries:
        query = query.where(Prospect.country_code.in_(filters.countries))
    for word in fold(filters.company).split():
        query = query.where(Prospect.company_search.contains(word, autoescape=True))
    since = func.now() - timedelta(days=RECENT_DAYS)
    recent = select(WebDiscovery.domain).where(
        WebDiscovery.organization_id == organization_id, WebDiscovery.checked_at >= since
    )
    query = query.where(Prospect.email_domain.not_in(recent))
    rows = session.execute(query.order_by(func.count().desc()).limit(limit))
    return [dict(r._mapping) for r in rows]


def start_discovery(
    session: Session,
    *,
    organization_id: uuid.UUID,
    user_id: uuid.UUID | None,
    filters: ProspectFilters,
    limit: int = MAX_COMPANIES,
) -> DiscoveryRun:
    companies = candidate_companies(session, organization_id, filters, limit)
    if not companies:
        raise DiscoveryError(
            "No hay empresas por explorar con estos filtros (o se exploraron en los últimos "
            f"{RECENT_DAYS} días)."
        )
    run = DiscoveryRun(
        organization_id=organization_id,
        user_id=user_id,
        filters={
            "industries": filters.industries,
            "countries": filters.countries,
            "company": filters.company,
        },
        status="queued",
        total=len(companies),
    )
    session.add(run)
    session.flush()
    for company in companies:
        enqueue_job(
            session,
            organization_id=organization_id,
            batch_id=None,
            kind=DISCOVERY_JOB_KIND,
            idempotency_key=f"discover:{run.id}:{company['email_domain']}",
            payload={
                "run_id": str(run.id),
                "domain": company["email_domain"],
                "company": company["company"],
                "country": company["country"],
                "industry": company["industry"],
                "industry_code": company["industry_code"],
            },
            max_attempts=2,
        )
    return run


def _existing_names(session: Session, organization_id: uuid.UUID, company_key: str) -> set[str]:
    rows = session.scalars(
        select(Prospect.full_name).where(
            Prospect.organization_id == organization_id, Prospect.company_key == company_key
        )
    )
    return {fold(name) for name in rows}


def run_discovery_job(
    session: Session,
    *,
    organization_id: uuid.UUID,
    payload: dict[str, Any],
    fetcher_factory: Callable[[], RobotsAwareFetcher],
) -> int:
    """Explora un dominio y agrega las personas nuevas. Devuelve cuántas se agregaron."""
    domain: str = payload["domain"]
    company: str = payload["company"] or domain
    country: str | None = payload.get("country")
    harvest = discover_people(domain, fetcher_factory())

    known = known_pattern(session, organization_id, domain)
    if known is not None:
        decision = decide_from_known(
            known.domain, known.pattern, people=known.people, agreeing=known.agreeing,
            country=country, mx_provider=None,
        )  # fmt: skip
    else:
        decision = decide(domain, [], country=country, mx_provider=None, has_mx=True)

    company_key = normalize_company(company).match_key or fold(company)
    existing = _existing_names(session, organization_id, company_key)
    rows: list[dict[str, Any]] = []
    for person in harvest.people:
        if fold(person.full_name) in existing:
            continue
        name = normalize_person(person.first_name, person.last_name)
        emails = generate_emails(name, decision, limit=1) if name.is_valid else []
        email = emails[0] if emails else None
        level, area = classify_title(person.title)
        rows.append(
            {
                "id": uuid.uuid4(),
                "organization_id": organization_id,
                "dedup_key": f"{fold(person.full_name)}|{company_key}"[:400],
                "full_name": person.full_name[:300],
                "first_name": person.first_name[:200],
                "last_name": person.last_name[:200],
                "title": person.title[:300],
                "company": company[:300],
                "company_key": company_key[:300],
                "email": email.address if email else None,
                "email_domain": decision.email_domain if email else None,
                "email_status": "inferred" if email else None,
                "email_confidence": email.confidence_score if email else None,
                "country_code": country,
                "industry": payload.get("industry"),
                "industry_code": payload.get("industry_code"),
                "level_code": level,
                "area_code": area,
                "title_search": fold(person.title),
                "company_search": fold(company),
                "source": "web",
                "source_name": person.url[:200],
            }
        )
    if rows:
        statement = pg_insert(Prospect).values(rows)
        session.execute(
            statement.on_conflict_do_nothing(constraint="uq_prospects_organization_id_dedup_key")
        )
    now = session.scalar(select(func.now()))
    values = {
        "id": uuid.uuid4(),
        "organization_id": organization_id,
        "domain": domain,
        "company": company[:300],
        "checked_at": now,
        "people_found": len(harvest.people),
        "pages_visited": len(harvest.pages_visited),
        "blocked": harvest.blocked,
    }
    statement = pg_insert(WebDiscovery).values(values)
    session.execute(
        statement.on_conflict_do_update(
            constraint="uq_web_discoveries_organization_id_domain",
            set_={k: statement.excluded[k] for k in values if k not in ("id", "organization_id")},
        )
    )
    return len(rows)


def advance_run(session: Session, run_id: uuid.UUID, added: int, *, failed: bool = False) -> None:
    run = session.get(DiscoveryRun, run_id, with_for_update=True)
    if run is None or run.status not in ("queued", "running"):
        return
    run.status = "running"
    run.done += 1
    run.people_found += 0 if failed else added
    if run.done >= run.total:
        run.status = "completed"
        run.finished_at = session.scalar(select(func.now()))
