"""Catálogo público de empresas: carga desde el archivo generado y consulta por filtros."""

from __future__ import annotations

import uuid
from collections.abc import Iterable
from datetime import timedelta
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from orkesta.db.models import CatalogCompany, WebDiscovery
from orkesta.engine.normalize import normalize_company
from orkesta.engine.normalize.text import fold

CHUNK = 1000


def load_catalog(session: Session, rows: Iterable[dict[str, str]]) -> int:
    """Inserta o actualiza empresas por QID de Wikidata. Devuelve cuántas filas se cargaron."""
    # Una empresa puede venir repetida (varios tipos en Wikidata): la última versión gana, y así
    # un mismo INSERT nunca toca dos veces la misma fila.
    unique: dict[str, dict[str, str]] = {}
    for r in rows:
        if r.get("qid") and r.get("name"):
            unique[r["qid"][:20]] = r
    items = list(unique.values())
    for start in range(0, len(items), CHUNK):
        values = []
        for r in items[start : start + CHUNK]:
            employees = r.get("employees") or ""
            values.append(
                {
                    "id": uuid.uuid4(),
                    "qid": r["qid"][:20],
                    "name": r["name"][:300],
                    "name_search": fold(r["name"]),
                    "company_key": (normalize_company(r["name"]).match_key or fold(r["name"]))[
                        :300
                    ],
                    "country_code": (r.get("country") or None),
                    "city": (r.get("city") or None),
                    "domain": (r.get("domain") or None),
                    "industry_code": (r.get("industry_code") or None),
                    "industry_labels": (r.get("industry_labels") or None),
                    "employees": int(employees) if employees.isdigit() else None,
                    "size_band": (r.get("size_band") or None),
                }
            )
        statement = pg_insert(CatalogCompany).values(values)
        session.execute(
            statement.on_conflict_do_update(
                constraint="uq_catalog_companies_qid",
                set_={k: statement.excluded[k] for k in values[0] if k not in ("id", "qid")}
                | {"updated_at": func.now()},
            )
        )
    return len(items)


def catalog_candidates(
    session: Session,
    organization_id: uuid.UUID,
    *,
    industries: list[str],
    countries: list[str],
    company: str,
    sizes: list[str],
    exclude_domains: set[str],
    recent_days: int,
    limit: int,
) -> list[dict[str, Any]]:
    """Empresas del catálogo con sitio oficial que cumplen los filtros, no exploradas hace poco."""
    query = select(CatalogCompany).where(CatalogCompany.domain.is_not(None))
    if industries:
        query = query.where(CatalogCompany.industry_code.in_(industries))
    if countries:
        query = query.where(CatalogCompany.country_code.in_(countries))
    if sizes:
        query = query.where(CatalogCompany.size_band.in_(sizes))
    for word in fold(company).split():
        query = query.where(CatalogCompany.name_search.contains(word, autoescape=True))
    since = func.now() - timedelta(days=recent_days)
    recent = select(WebDiscovery.domain).where(
        WebDiscovery.organization_id == organization_id, WebDiscovery.checked_at >= since
    )
    query = query.where(CatalogCompany.domain.not_in(recent))
    if exclude_domains:
        query = query.where(CatalogCompany.domain.not_in(sorted(exclude_domains)))
    # Las más grandes primero: más personas publicadas y más interés comercial.
    query = query.order_by(CatalogCompany.employees.desc().nulls_last(), CatalogCompany.name)
    return [
        {
            "email_domain": c.domain,
            "company": c.name,
            "country": c.country_code,
            "industry_code": c.industry_code,
            "industry": (c.industry_labels or "").split("|")[0] or None,
            "people": 0,
        }
        for c in session.scalars(query.limit(limit))
    ]
