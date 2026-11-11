"""Rastreador propio de ORKESTA: lee, poco a poco y con cortesía (robots.txt, pausas por sitio),
el sitio oficial de cada empresa del directorio y guarda lo que la empresa publica de sí misma:
teléfono, correo general, LinkedIn, tamaño, descripción y su formato de correo.

También cosecha a las personas que la empresa publica con su cargo (equipo directivo, junta,
gobierno corporativo) y las suma al índice propio de personas con su correo publicado o
estimado con el formato de la empresa. Así el índice crece cada día sin buscadores pagos.
"""

from __future__ import annotations

import logging
import threading
import time
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session, sessionmaker

from orkesta.research.extract import is_generic_email
from orkesta.research.fetch import Fetcher
from orkesta.research.models import CompanyDirectory

logger = logging.getLogger(__name__)

BATCH = 12  # empresas por tanda (cada 10 minutos ≈ 1.700 al día)
RECRAWL = timedelta(days=60)
RETRY_FAILED = timedelta(days=14)
BUDGET_SECONDS = 240
_RUNNING = threading.Lock()
WEAK_SECTOR = ("mismo dominio",)  # el sitio oficial pesa más que esto
STATS: dict[str, Any] = {}


def due(session: Session, limit: int) -> list[CompanyDirectory]:
    now = datetime.now(UTC)
    stale = (
        (CompanyDirectory.crawled_at.is_(None))
        | (
            (CompanyDirectory.site_status == "leido")
            & (CompanyDirectory.crawled_at < now - RECRAWL)
        )
        | (
            (CompanyDirectory.site_status != "leido")
            & (CompanyDirectory.crawled_at < now - RETRY_FAILED)
        )
    )
    return list(session.scalars(
        select(CompanyDirectory)
        .where(CompanyDirectory.domain.is_not(None), stale)
        .order_by(CompanyDirectory.crawled_at.nulls_first(),
                  CompanyDirectory.industry_code.is_(None).desc(),  # primero las sin sector
                  CompanyDirectory.revenue.desc().nulls_last())
        .limit(limit)
    ))  # fmt: skip


def harvest(row: CompanyDirectory, result: Any, pattern: str) -> list[dict[str, Any]]:
    """Personas con cargo reconocido en el sitio oficial → filas del índice de personas."""
    from orkesta.engine.taxonomy.titles import classify_title
    from orkesta.research.extract import apply_pattern, email_matches_person
    from orkesta.research.people import person_key

    domain = row.domain or ""
    pattern = pattern or row.email_pattern or ""
    personal = [e for e in result.emails if not is_generic_email(e) and e.endswith("@" + domain)]
    rows: dict[str, dict[str, Any]] = {}
    for person, _url in result.people:
        level, area = classify_title(person.title)
        if not person.title or level is None:
            continue  # sin cargo reconocible no sirve para prospectar
        first, last = person.first_name, person.last_name
        email = next((e for e in personal if email_matches_person(e, first, last)), "")
        status = "publicado" if email else None
        if not email and pattern and domain:
            email = apply_pattern(pattern, person.first_name, person.last_name, domain) or ""
            status = "estimado" if email else None
        key = person_key(row.country, row.name_key, person.full_name)
        rows[key] = {
            "key": key, "country": row.country, "full_name": person.full_name[:200],
            "first_name": person.first_name[:120], "last_name": person.last_name[:160],
            "title": person.title[:300], "level": level, "area": area,
            "company": row.name[:300], "company_key": row.name_key[:300],
            "domain": domain or None, "location": (row.city or "")[:200], "linkedin": None,
            "email": email or None, "email_status": status, "source": "sitio_oficial",
        }  # fmt: skip
    return list(rows.values())


def crawl_one(fetcher: Fetcher, row: CompanyDirectory) -> dict[str, Any]:
    from orkesta.research import website

    result = website.investigate(fetcher, row.domain or "")
    pattern = website.learn_patterns(fetcher, result) if result.pages_read else ""
    general = sorted(e for e in result.emails if is_generic_email(e))
    return {
        "people": harvest(row, result, pattern),
        "site_status": "leido" if result.pages_read else ("bloqueado" if result.blocked
                                                           else "sin_acceso"),
        "phone": next(iter(result.phones), None),
        "company_email": general[0] if general else None,
        "linkedin": result.linkedin or None,
        "employees": result.employees,
        "description": result.description[:400] or None,
        "email_pattern": pattern or None,
        "name": result.name,
    }  # fmt: skip


def crawl_batch(factory: sessionmaker[Session], limit: int = BATCH) -> int:
    """Una tanda del rastreador; no se superpone con otra en curso."""
    if not _RUNNING.acquire(blocking=False):
        return 0
    done = 0
    harvested: list[dict[str, Any]] = []
    try:
        with factory() as session:
            rows = [(r.key, r.domain) for r in due(session, limit)]
        started = time.monotonic()
        with Fetcher(factory) as fetcher:
            for key, _domain in rows:
                if time.monotonic() - started > BUDGET_SECONDS:
                    break
                with factory.begin() as session:
                    row = session.get(CompanyDirectory, key)
                    if row is None:
                        continue
                    try:
                        found = crawl_one(fetcher, row)
                    except Exception:
                        logger.exception("crawler_site_failed", extra={"domain": row.domain})
                        found = {"site_status": "error"}
                    row.crawled_at = datetime.now(UTC)
                    row.site_status = found["site_status"]
                    harvested += found.get("people") or []
                    for attr in ("phone", "company_email", "description", "email_pattern"):
                        if found.get(attr):
                            setattr(row, attr, found[attr])
                    for attr in ("linkedin", "employees"):
                        if found.get(attr) and getattr(row, attr) in (None, ""):
                            setattr(row, attr, found[attr])
                    if not row.industry_code or row.industry_source in WEAK_SECTOR:
                        from orkesta.research.sectors import from_description

                        code = from_description(f"{found.get('name') or ''}. "
                                                f"{found.get('description') or ''}")  # fmt: skip
                        if code:
                            row.industry_code, row.industry_source = code, "sitio"
                    done += 1
        if harvested:
            from orkesta.research import people

            added = people.upsert(factory, harvested)
            STATS["people_total"] = int(STATS.get("people_total", 0)) + added
        STATS.update(last_run=datetime.now(UTC).isoformat(), last_batch=done,
                     last_people=len(harvested))  # fmt: skip
        return done
    finally:
        _RUNNING.release()


def start_batch(factory: sessionmaker[Session]) -> None:
    threading.Thread(target=crawl_batch, args=(factory,), daemon=True,
                     name="orkesta-rastreador").start()  # fmt: skip


def overview(session: Session) -> dict[str, Any]:
    rows = session.execute(
        select(CompanyDirectory.site_status, func.count())
        .where(CompanyDirectory.domain.is_not(None))
        .group_by(CompanyDirectory.site_status)
    ).all()
    by = {str(s or "pendiente"): int(n) for s, n in rows}
    patterns = session.scalar(
        select(func.count()).where(CompanyDirectory.email_pattern.is_not(None))
    )
    from orkesta.research.models import PersonIndex

    from_sites = session.scalar(
        select(func.count()).select_from(PersonIndex).where(PersonIndex.source == "sitio_oficial")
    )
    return {"by_status": by, "patterns": int(patterns or 0),
            "people_from_sites": int(from_sites or 0), "running": _RUNNING.locked(),
            **STATS}  # fmt: skip
