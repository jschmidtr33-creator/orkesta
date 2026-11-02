"""Rastreador propio de ORKESTA: lee, poco a poco y con cortesía (robots.txt, pausas por sitio),
el sitio oficial de cada empresa del directorio y guarda lo que la empresa publica de sí misma:
teléfono, correo general, LinkedIn, tamaño, descripción y su formato de correo.

Solo datos de empresas; las personas que aparezcan en los sitios no se guardan aquí.
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
                  CompanyDirectory.revenue.desc().nulls_last())
        .limit(limit)
    ))  # fmt: skip


def crawl_one(fetcher: Fetcher, row: CompanyDirectory) -> dict[str, Any]:
    from orkesta.research import website

    result = website.investigate(fetcher, row.domain or "")
    pattern = website.learn_patterns(fetcher, result) if result.pages_read else ""
    general = sorted(e for e in result.emails if is_generic_email(e))
    return {
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
                    for attr in ("phone", "company_email", "description", "email_pattern"):
                        if found.get(attr):
                            setattr(row, attr, found[attr])
                    for attr in ("linkedin", "employees"):
                        if found.get(attr) and getattr(row, attr) in (None, ""):
                            setattr(row, attr, found[attr])
                    done += 1
        STATS.update(last_run=datetime.now(UTC).isoformat(), last_batch=done)
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
    return {"by_status": by, "patterns": int(patterns or 0),
            "running": _RUNNING.locked(), **STATS}  # fmt: skip
