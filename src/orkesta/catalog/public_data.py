"""Datos públicos en producción: catálogo de empresas y directivos, renovados cada semana.

El propio servidor consulta Wikidata (CC0) en trabajos pequeños, uno por país y consulta, para
no exceder el tiempo de bloqueo de la cola ni el límite de consultas simultáneas del servicio:

- ``public_companies`` carga empresas (nombre, sede, sitio oficial, industria, empleados).
- ``public_people`` carga directivos con cargo vigente y los agrega a la base de cada
  organización como prospectos con origen «wikidata» (datos públicos), correo deducido y
  enlace a su ficha de Wikidata.

``schedule_refresh`` encola la renovación si los datos tienen más de ``REFRESH_DAYS`` días;
se llama en cada despliegue (``orkesta.cli release``).
"""

from __future__ import annotations

import uuid
from collections import defaultdict
from collections.abc import Callable
from datetime import timedelta
from typing import Any

import httpx
from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from orkesta.catalog import public_people, wikidata_catalog
from orkesta.catalog.companies import load_catalog
from orkesta.catalog.service import known_pattern
from orkesta.catalog.titles import classify_title
from orkesta.db.models import CatalogCompany, CatalogPerson, EnrichmentJob, Organization, Prospect
from orkesta.engine.email.decide import decide, decide_from_known, generate_emails
from orkesta.engine.net.fetch import USER_AGENT
from orkesta.engine.normalize import normalize_company, normalize_person
from orkesta.engine.normalize.names import split_full_name
from orkesta.engine.normalize.text import fold
from orkesta.evaluation.wikidata_dataset import BUSINESS_TYPES, COUNTRY_ENTITY, _query
from orkesta.worker.queue import enqueue_job

COMPANIES_JOB_KIND = "public_companies"
PEOPLE_JOB_KIND = "public_people"
REFRESH_DAYS = 7
# Separación entre consultas: Wikidata admite pocas consultas simultáneas por IP.
STAGGER_SECONDS = 4
PUBLIC_SOURCE = "wikidata"

HttpClientFactory = Callable[[], httpx.Client]


def http_client() -> httpx.Client:
    return httpx.Client(timeout=60, headers={"User-Agent": USER_AGENT})


def company_queries(country: str) -> list[str]:
    entity = COUNTRY_ENTITY[country]
    queries = [
        wikidata_catalog.QUERY.format(type=t, country=entity, limit=4000)
        for t in BUSINESS_TYPES.split()
    ]
    queries += [q.format(country=entity, limit=8000) for q in wikidata_catalog.EXTRA_QUERIES]
    return queries


def people_queries(country: str) -> list[tuple[str, str]]:
    entity = COUNTRY_ENTITY[country]
    return [
        (public_people.QUERY.format(country=entity, prop=prop, limit=5000), title)
        for prop, title in public_people.ROLES.items()
    ]


# --- Programación --------------------------------------------------------------------------


def _last_refresh(session: Session) -> Any:
    return session.scalar(select(func.max(CatalogPerson.updated_at)))


def schedule_refresh(session: Session, *, force: bool = False) -> int:
    """Encola la renovación de datos públicos si hace falta. Devuelve cuántos trabajos encoló."""
    organization_id = session.scalar(select(Organization.id).order_by(Organization.created_at))
    if organization_id is None:
        return 0
    now = session.scalar(select(func.now()))
    assert now is not None  # noqa: S101 - func.now() siempre devuelve valor
    last = _last_refresh(session)
    if not force and last is not None and now - last < timedelta(days=REFRESH_DAYS):
        return 0
    pending = session.scalar(
        select(func.count())
        .select_from(EnrichmentJob)
        .where(
            EnrichmentJob.kind.in_((COMPANIES_JOB_KIND, PEOPLE_JOB_KIND)),
            EnrichmentJob.status.in_(("pending", "running")),
        )
    )
    if pending:
        return 0
    week = now.strftime("%G-%V")
    tasks: list[tuple[str, dict[str, Any]]] = []
    for country in COUNTRY_ENTITY:
        # Primero las empresas del país y después sus directivos (necesitan industria y sitio).
        tasks += [
            (COMPANIES_JOB_KIND, {"country": country, "query": i})
            for i in range(len(company_queries(country)))
        ]
        tasks += [
            (PEOPLE_JOB_KIND, {"country": country, "query": i})
            for i in range(len(people_queries(country)))
        ]
    for position, (kind, payload) in enumerate(tasks):
        job = enqueue_job(
            session,
            organization_id=organization_id,
            batch_id=None,
            kind=kind,
            idempotency_key=f"{kind}:{week}:{payload['country']}:{payload['query']}",
            payload=payload,
            max_attempts=3,
        )
        job.run_after = now + timedelta(seconds=position * STAGGER_SECONDS)
    return len(tasks)


# --- Empresas ------------------------------------------------------------------------------


def run_companies_job(
    session: Session, payload: dict[str, Any], client_factory: HttpClientFactory = http_client
) -> int:
    country = str(payload["country"])
    query = company_queries(country)[int(payload["query"])]
    companies: dict[str, wikidata_catalog.CatalogCompany] = {}
    with client_factory() as client:
        wikidata_catalog.merge_bindings(companies, _query(client, query, retries=2), country)
    return load_catalog(session, [c.row() for c in companies.values()])


# --- Directivos ----------------------------------------------------------------------------


def run_people_job(
    session: Session, payload: dict[str, Any], client_factory: HttpClientFactory = http_client
) -> int:
    country = str(payload["country"])
    query, title = people_queries(country)[int(payload["query"])]
    with client_factory() as client:
        people = public_people.parse_bindings(_query(client, query, retries=2), title, country)
    if not people:
        return 0
    load_people(session, people)
    added = 0
    for organization_id in session.scalars(select(Organization.id)):
        added += sync_public_people(
            session, organization_id, person_keys={(p.person_qid, p.company_qid) for p in people}
        )
    return added


def load_people(session: Session, people: list[public_people.PublicPerson]) -> None:
    unique = {(p.person_qid, p.company_qid, p.title): p for p in people}
    rows = [
        {
            "id": uuid.uuid4(),
            "person_qid": p.person_qid[:20],
            "company_qid": p.company_qid[:20],
            "name": p.name[:300],
            "title": p.title[:120],
            "company": p.company[:300],
            "country_code": p.country or None,
            "domain": p.domain or None,
        }
        for p in unique.values()
    ]
    for start in range(0, len(rows), 1000):
        statement = pg_insert(CatalogPerson).values(rows[start : start + 1000])
        session.execute(
            statement.on_conflict_do_update(
                constraint="uq_catalog_people_person_qid_company_qid_title",
                set_={
                    "name": statement.excluded.name,
                    "company": statement.excluded.company,
                    "domain": statement.excluded.domain,
                    "updated_at": func.now(),
                },
            )
        )


def sync_public_people(
    session: Session,
    organization_id: uuid.UUID,
    *,
    person_keys: set[tuple[str, str]] | None = None,
) -> int:
    """Agrega los directivos públicos a la base de la organización (sin duplicar a quien ya
    está, por nombre y empresa). Devuelve cuántas personas nuevas quedaron."""
    query = select(CatalogPerson, CatalogCompany).outerjoin(
        CatalogCompany, CatalogCompany.qid == CatalogPerson.company_qid
    )
    if person_keys is not None:
        companies = sorted({company for _, company in person_keys})
        query = query.where(CatalogPerson.company_qid.in_(companies))
    pairs: list[tuple[CatalogPerson, CatalogCompany | None]] = [
        (person, company)
        for person, company in session.execute(query).tuples()
        if person_keys is None or (person.person_qid, person.company_qid) in person_keys
    ]
    by_domain: dict[str | None, list[tuple[CatalogPerson, CatalogCompany | None]]] = defaultdict(
        list
    )
    for person, company in pairs:
        domain = person.domain or (company.domain if company else None)
        by_domain[domain].append((person, company))

    rows: list[dict[str, Any]] = []
    for domain, members in by_domain.items():
        decision = None
        if domain:
            known = known_pattern(session, organization_id, domain)
            country = members[0][0].country_code
            if known is not None:
                decision = decide_from_known(
                    known.domain, known.pattern, people=known.people, agreeing=known.agreeing,
                    country=country, mx_provider=None,
                )  # fmt: skip
            else:
                decision = decide(domain, [], country=country, mx_provider=None, has_mx=True)
        for person, company in members:
            split = split_full_name(person.name)
            if not split.first or not split.last:
                continue
            name = normalize_person(split.first, split.last)
            email = None
            if decision is not None and name.is_valid:
                emails = generate_emails(name, decision, limit=1)
                email = emails[0] if emails else None
            company_name = company.name if company else person.company
            company_key = normalize_company(company_name).match_key or fold(company_name)
            level, area = classify_title(person.title)
            rows.append(
                {
                    "id": uuid.uuid4(),
                    "organization_id": organization_id,
                    "dedup_key": f"{fold(person.name)}|{company_key}"[:400],
                    "full_name": person.name[:300],
                    "first_name": split.first[:200],
                    "last_name": split.last[:200],
                    "title": person.title,
                    "company": company_name[:300],
                    "company_key": company_key[:300],
                    "email": email.address if email else None,
                    "email_domain": decision.email_domain if email and decision else None,
                    "email_status": "inferred" if email else None,
                    "email_confidence": email.confidence_score if email else None,
                    "city": company.city if company else None,
                    "country_code": person.country_code,
                    "industry": (company.industry_labels or "").split("|")[0] or None
                    if company
                    else None,
                    "industry_code": company.industry_code if company else None,
                    "level_code": level,
                    "area_code": area,
                    "title_search": fold(person.title),
                    "company_search": fold(company_name),
                    "source": PUBLIC_SOURCE,
                    "source_name": f"https://www.wikidata.org/wiki/{person.person_qid}",
                }
            )
    added = 0
    for start in range(0, len(rows), 1000):
        statement = pg_insert(Prospect).values(rows[start : start + 1000])
        result = session.execute(
            statement.on_conflict_do_nothing(
                constraint="uq_prospects_organization_id_dedup_key"
            ).returning(Prospect.id)
        )
        added += len(result.fetchall())
    return added
