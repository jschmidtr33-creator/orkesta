"""Índice propio de personas de ORKESTA y puntaje de cada posible cliente.

Índice: perfiles profesionales PÚBLICOS que encuentran las búsquedas (perfiles indexados por
buscadores y sitios oficiales de empresas): nombre, cargo, empresa, ubicación, LinkedIn y correo
CORPORATIVO con su estado. La siguiente búsqueda los encuentra al instante, sin gastar consultas.
No guarda teléfonos personales ni correos personales. Cualquier persona puede pedir salir del
índice (lista de exclusión permanente).

Puntaje: qué tan bien encaja cada posible cliente con lo que se buscó (0-100), con sus razones.
"""

from __future__ import annotations

import hashlib
import logging
from collections.abc import Iterable
from typing import TYPE_CHECKING, Any

from sqlalchemy import and_, case, func, select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session, sessionmaker

from orkesta.engine.normalize.text import fold
from orkesta.engine.taxonomy.industries import size_band
from orkesta.research.extract import is_generic_email
from orkesta.research.models import CompanyDirectory, PersonIndex, PersonOptOut

if TYPE_CHECKING:
    from orkesta.research.agent import Filters, Prospect

logger = logging.getLogger(__name__)

STATUS_RANK = {"rebotado": 0, "estimado": 1, "publicado": 2, "confirmado": 3}


def _linkedin_key(url: str) -> str:
    path = url.split("linkedin.com", 1)[-1].split("?")[0].rstrip("/").lower()
    return f"li:{path}" if path.startswith("/in/") else ""


def person_key(country: str, company_key: str, full_name: str, linkedin: str = "") -> str:
    return (_linkedin_key(linkedin) or f"n:{country}|{company_key}|{fold(full_name)}")[:400]


def optout_hash(value: str) -> str:
    return hashlib.sha256(value.strip().lower().encode()).hexdigest()


def _blocked(session: Session, keys: Iterable[str]) -> set[str]:
    hashes = {optout_hash(k): k for k in keys if k}
    if not hashes:
        return set()
    found = session.scalars(select(PersonOptOut.hash).where(PersonOptOut.hash.in_(list(hashes))))
    return {hashes[h] for h in found}


def learn(factory: sessionmaker[Session], country: str, prospects: Iterable[Prospect]) -> int:
    """Suma al índice los perfiles públicos que encontró una búsqueda."""
    rows: dict[str, dict[str, Any]] = {}
    for p in prospects:
        if not p.full_name or not p.company.name or not country:
            continue
        level, area = p.classification()
        domain = p.company.domain or ""
        email = p.email if (p.email and domain and p.email.endswith("@" + domain)
                            and not is_generic_email(p.email)) else ""  # fmt: skip
        key = person_key(country, p.company.key, p.full_name, p.linkedin)
        rows[key] = {
            "key": key, "country": country, "full_name": p.full_name[:200],
            "first_name": p.first_name[:120], "last_name": p.last_name[:160],
            "title": (p.title or "")[:300], "level": level, "area": area,
            "company": p.company.name[:300], "company_key": p.company.key[:300],
            "domain": domain or None, "location": (p.location or p.company.city or "")[:200],
            "linkedin": (p.linkedin or "")[:300] or None, "email": email or None,
            "email_status": (p.email_status or "estimado") if email else None,
        }  # fmt: skip
    if not rows:
        return 0
    try:
        with factory.begin() as session:
            blocked = _blocked(session, [*rows, *(r["email"] for r in rows.values() if r["email"])])
            values = [r for k, r in rows.items() if k not in blocked and r["email"] not in blocked]
            if not values:
                return 0
            stmt = pg_insert(PersonIndex).values(values)
            locked = PersonIndex.email_status.in_(("confirmado", "publicado"))
            session.execute(stmt.on_conflict_do_update(
                index_elements=["key"],
                set_={
                    "title": func.coalesce(func.nullif(stmt.excluded.title, ""), PersonIndex.title),
                    "level": func.coalesce(stmt.excluded.level, PersonIndex.level),
                    "area": func.coalesce(stmt.excluded.area, PersonIndex.area),
                    "company": stmt.excluded.company, "company_key": stmt.excluded.company_key,
                    "domain": func.coalesce(stmt.excluded.domain, PersonIndex.domain),
                    "location": func.coalesce(func.nullif(stmt.excluded.location, ""),
                                              PersonIndex.location),
                    "linkedin": func.coalesce(stmt.excluded.linkedin, PersonIndex.linkedin),
                    # Un correo confirmado o publicado no se reemplaza por uno estimado.
                    "email": case((locked, PersonIndex.email),
                                  else_=func.coalesce(stmt.excluded.email, PersonIndex.email)),
                    "email_status": case(
                        (locked, PersonIndex.email_status),
                        else_=func.coalesce(stmt.excluded.email_status, PersonIndex.email_status),
                    ),
                    "seen": PersonIndex.seen + 1,
                    "last_seen": func.now(),
                },
            ))  # fmt: skip
            return len(values)
    except Exception:
        logger.exception("people_index_learn")
        return 0


def feedback(session: Session, email: str, outcome: str) -> None:
    """Lo que dicen las campañas: una respuesta confirma el correo; un rebote lo descarta."""
    status = {"replied": "confirmado", "bounced": "rebotado"}.get(outcome)
    if status and email:
        session.execute(
            update(PersonIndex)
            .where(PersonIndex.email == email.lower())
            .values(email_status=status)
        )


def search(session: Session, filters: Filters, limit: int = 400) -> list[dict[str, Any]]:
    """Perfiles del índice que cumplen los filtros (cargo, nivel, función, empresa, sector,
    ciudad, tamaño, palabras clave)."""
    from orkesta.engine.taxonomy import linkedin as li

    query = (
        select(PersonIndex, CompanyDirectory.industry_code, CompanyDirectory.employees)
        .outerjoin(CompanyDirectory, and_(CompanyDirectory.country == PersonIndex.country,
                                          CompanyDirectory.name_key == PersonIndex.company_key))
        .where(PersonIndex.country == filters.country)
    )  # fmt: skip
    industries = list(dict.fromkeys([*filters.industries, *li.sector_parents(filters.sectors)]))
    if industries:
        query = query.where(CompanyDirectory.industry_code.in_(industries))
    for place in [fold(c) for c in filters.cities if fold(c)][:1]:
        query = query.where(func.lower(PersonIndex.location).contains(place))
    rows = session.execute(query.order_by(PersonIndex.seen.desc()).limit(limit * 5)).all()
    companies = {fold(c) for c in filters.companies}
    words = fold(filters.keywords).split()
    out: list[dict[str, Any]] = []
    for person, industry, employees in rows:
        if companies and not any(c in fold(person.company) for c in companies):
            continue
        if words and not all(w in fold(f"{person.title} {person.company}") for w in words):
            continue
        if filters.sizes and employees and size_band(employees) not in filters.sizes:
            continue
        if not (filters.wants_title(person.title or "")
                and filters.wants_employer(person.company)
                and filters.wants_name(person.first_name, person.last_name)):  # fmt: skip
            continue
        out.append({"person": person, "industry": industry or "", "employees": employees})
        if len(out) >= limit:
            break
    return out


def remove(session: Session, value: str) -> int:
    """Saca a una persona del índice para siempre (por LinkedIn o correo)."""
    value = value.strip()
    key = _linkedin_key(value) if "linkedin.com" in value else ""
    target = key or value.lower()
    session.execute(pg_insert(PersonOptOut).values(hash=optout_hash(target))
                    .on_conflict_do_nothing())  # fmt: skip
    condition = PersonIndex.key == key if key else PersonIndex.email == value.lower()
    rows = session.scalars(select(PersonIndex).where(condition)).all()
    for row in rows:
        session.delete(row)
    return len(rows)


def stats(session: Session) -> dict[str, int]:
    total, with_email, confirmed = session.execute(
        select(func.count(), func.count(PersonIndex.email),
               func.count().filter(PersonIndex.email_status.in_(("confirmado", "publicado"))))
    ).one()  # fmt: skip
    return {"people": int(total), "with_email": int(with_email), "verified": int(confirmed)}


# --- Puntaje del posible cliente --------------------------------------------------------------

LEVEL_POINTS = {"c_level": 35, "duenio_socio": 35, "vp": 30, "director": 26, "gerente": 20,
                "jefe": 12}  # fmt: skip


def fit_score(p: Prospect, filters: Filters | None) -> tuple[int, list[str]]:
    """Qué tan bien encaja con lo que se buscó: nivel, función, empresa y calidad del correo."""
    from orkesta.engine.taxonomy import linkedin as li

    reasons: list[str] = []
    level, _area = p.classification()
    points = LEVEL_POINTS.get(level or "", 5)
    if level in ("c_level", "duenio_socio", "vp", "director"):
        reasons.append("Decisor de alto nivel")
    elif level == "gerente":
        reasons.append("Gerente")
    if filters is not None and (filters.functions or filters.titles):
        points += 15
        reasons.append("Su función coincide con la búsqueda")
    elif level:
        points += 8
    industries = (
        set(filters.industries) | set(li.sector_parents(filters.sectors)) if filters else set()
    )
    if industries and p.company.industry_code in industries:
        points += 12
        reasons.append("Empresa del sector buscado")
    elif not industries:
        points += 6
    if p.company.employees and filters and size_band(p.company.employees) in filters.sizes:
        points += 4
        reasons.append("Tamaño de empresa buscado")
    if p.company.domain:
        points += 4
    email_conf = int(p.provenance.get("email", {}).get("confidence", 0) or 0)
    if p.email_status == "publicado" or email_conf >= 75:
        points += 20
        reasons.append("Correo verificado")
    elif email_conf >= 40:
        points += 14
        reasons.append("Correo con formato confirmado de la empresa")
    elif p.email:
        points += 7
        reasons.append("Correo estimado")
    if p.linkedin:
        points += 5
    if len({s.get("type") for s in p.sources}) > 1:
        points += 5
        reasons.append("Confirmado por varias fuentes")
    return min(points, 100), reasons
