"""Memoria que aprende de cada búsqueda y de cada campaña (IA propia de ORKESTA).

- Dominio oficial de cada empresa ya identificada: la siguiente búsqueda no gasta consultas.
- Formato de correo por dominio, reforzado con evidencia: correos públicos vistos (+1),
  respuestas a correos estimados (confirmado) y rebotes (resta confianza). Así los correos
  estimados mejoran con el uso de todos los clientes.

Solo guarda datos de empresas (dominio y formato), nunca datos de personas.
"""

from __future__ import annotations

import logging

from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session, sessionmaker

from orkesta.research.models import CompanyDomain, DomainPattern

logger = logging.getLogger(__name__)

PATTERN_LABELS = {
    "first.last": "nombre.apellido",
    "flast": "inicial + apellido",
    "first": "nombre",
    "firstl": "nombre + inicial",
    "first_last": "nombre_apellido",
    "firstlast": "nombreapellido",
    "f.last": "inicial.apellido",
    "last": "apellido",
    "last.first": "apellido.nombre",
}


def _key(country: str, company_key: str) -> str:
    return f"{country}|{company_key}"[:320]


def known_domain(factory: sessionmaker[Session], country: str, company_key: str) -> str:
    try:
        with factory() as session:
            return (
                session.scalar(
                    select(CompanyDomain.domain).where(
                        CompanyDomain.key == _key(country, company_key)
                    )
                )
                or ""
            )
    except Exception:
        logger.exception("memory_domain_read")
        return ""


def remember_domain(
    factory: sessionmaker[Session], country: str, company_key: str, domain: str, source: str
) -> None:
    if not domain or not company_key:
        return
    try:
        with factory.begin() as session:
            stmt = pg_insert(CompanyDomain).values(
                key=_key(country, company_key), domain=domain, source=source[:40]
            )
            session.execute(stmt.on_conflict_do_update(
                index_elements=["key"], set_={"domain": domain, "source": source[:40],
                                              "updated_at": func.now()},
            ))  # fmt: skip
    except Exception:
        logger.exception("memory_domain_write")


def known_pattern(factory: sessionmaker[Session], domain: str) -> tuple[str, int, str]:
    """(patrón, confianza 0-100, explicación) aprendido para un dominio; vacío si no hay."""
    try:
        with factory() as session:
            row = session.get(DomainPattern, domain)
    except Exception:
        logger.exception("memory_pattern_read")
        return "", 0, ""
    if row is None:
        return "", 0, ""
    if row.bounced > row.confirmed + row.seen:
        return "", 0, ""  # la evidencia dice que este formato no funciona
    label = PATTERN_LABELS.get(row.pattern, row.pattern)
    if row.confirmed:
        return (
            row.pattern,
            75,
            f"Formato {label}@{domain} confirmado por {row.confirmed} respuesta(s)",
        )
    if row.seen:
        confidence = 55 if row.seen > 1 else 45
        return (
            row.pattern,
            confidence,
            f"Formato {label}@{domain} visto en {row.seen} correo(s) públicos",
        )
    return "", 0, ""


def remember_pattern(
    factory: sessionmaker[Session], domain: str, pattern: str, seen: int, example: str = ""
) -> None:
    if not domain or not pattern or seen <= 0:
        return
    try:
        with factory.begin() as session:
            row = session.get(DomainPattern, domain, with_for_update=True)
            if row is None:
                session.add(DomainPattern(domain=domain, pattern=pattern, seen=seen,
                                          example=example[:500]))  # fmt: skip
            elif row.pattern == pattern:
                row.seen = max(row.seen, seen)
                row.example = row.example or example[:500]
            elif seen > row.seen + row.confirmed:
                row.pattern, row.seen, row.confirmed, row.bounced = pattern, seen, 0, 0
                row.example = example[:500]
    except Exception:
        logger.exception("memory_pattern_write")


def feedback(session: Session, email: str, pattern: str, outcome: str) -> None:
    """Aprende de una campaña: una respuesta confirma el formato; un rebote le resta confianza."""
    if "@" not in email or not pattern:
        return
    domain = email.rsplit("@", 1)[1].lower()
    row = session.get(DomainPattern, domain, with_for_update=True)
    if row is None:
        if outcome == "replied":
            session.add(DomainPattern(domain=domain, pattern=pattern, confirmed=1))
        return
    if row.pattern != pattern:
        if outcome == "replied" and row.confirmed == 0:
            row.pattern, row.confirmed, row.bounced, row.seen = pattern, 1, 0, 0
        return
    if outcome == "replied":
        row.confirmed += 1
    elif outcome == "bounced":
        row.bounced += 1
