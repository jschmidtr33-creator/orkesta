"""Memoria que aprende de cada búsqueda y de cada campaña (IA propia de ORKESTA).

- Dominio oficial de cada empresa ya identificada: la siguiente búsqueda no gasta consultas.
- Formato de correo por dominio, reforzado con evidencia: correos públicos vistos (+1),
  respuestas a correos estimados (confirmado) y rebotes (resta confianza). Así los correos
  estimados mejoran con el uso de todos los clientes.

Solo guarda datos de empresas (dominio y formato), nunca datos de personas.
"""

from __future__ import annotations

import hashlib
import logging
from collections.abc import Callable
from typing import TYPE_CHECKING

from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session, sessionmaker

from orkesta.research.models import BouncedAddress, CompanyDomain, PatternStat

if TYPE_CHECKING:
    from orkesta.research.fetch import Fetcher

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


# Formatos más comunes en empresas de Latinoamérica, en orden: si uno rebota se prueba el siguiente.
FALLBACK_ORDER = ("first.last", "flast", "first", "firstlast", "f.last", "first_last", "firstl",
                  "last.first", "lastf")  # fmt: skip


def address_hash(email: str) -> str:
    return hashlib.sha256(email.strip().lower().encode()).hexdigest()


def _bad(stat: PatternStat) -> bool:
    """La evidencia en contra supera a la evidencia a favor: el formato no sirve aquí."""
    return stat.bounced > stat.confirmed + stat.seen


def _stats(session: Session, domain: str) -> list[PatternStat]:
    return list(session.scalars(select(PatternStat).where(PatternStat.domain == domain)))


def _best(stats: list[PatternStat], domain: str) -> tuple[str, int, str]:
    good = [st for st in stats if not _bad(st) and (st.confirmed or st.seen)]
    if not good:
        return "", 0, ""
    st = max(good, key=lambda x: (x.confirmed * 5 + x.seen * 2 - x.bounced * 3, x.confirmed))
    label = PATTERN_LABELS.get(st.pattern, st.pattern)
    if st.confirmed:
        return (
            st.pattern,
            75,
            f"Formato {label}@{domain} confirmado por {st.confirmed} respuesta(s)",
        )
    confidence = 55 if st.seen > 1 else 45
    return st.pattern, confidence, f"Formato {label}@{domain} visto en {st.seen} correo(s) públicos"


def known_pattern(factory: sessionmaker[Session], domain: str) -> tuple[str, int, str]:
    """(patrón, confianza 0-100, explicación) aprendido para un dominio; vacío si no hay."""
    try:
        with factory() as session:
            return _best(_stats(session, domain), domain)
    except Exception:
        logger.exception("memory_pattern_read")
        return "", 0, ""


def _chain(stats: list[PatternStat], primary: str) -> list[str]:
    bad = {st.pattern for st in stats if _bad(st)}
    order = [primary, *FALLBACK_ORDER] if primary else list(FALLBACK_ORDER)
    return [p for p in dict.fromkeys(order) if p not in bad]


def _bounced(session: Session, domain: str) -> set[str]:
    return set(
        session.scalars(select(BouncedAddress.address_hash).where(BouncedAddress.domain == domain))
    )


def candidates(
    factory: sessionmaker[Session], domain: str, primary: str
) -> tuple[list[str], set[str]]:
    """Formatos a probar en orden (sin los que la evidencia descartó) y huellas de direcciones
    que ya rebotaron en el dominio."""
    try:
        with factory() as session:
            return _chain(_stats(session, domain), primary), _bounced(session, domain)
    except Exception:
        logger.exception("memory_candidates")
        return [primary] if primary else list(FALLBACK_ORDER), set()


def remember_pattern(
    factory: sessionmaker[Session], domain: str, pattern: str, seen: int, example: str = ""
) -> None:
    if not domain or not pattern or seen <= 0:
        return
    try:
        with factory.begin() as session:
            stmt = pg_insert(PatternStat).values(domain=domain, pattern=pattern, seen=seen)
            session.execute(stmt.on_conflict_do_update(
                index_elements=["domain", "pattern"],
                set_={"seen": func.greatest(PatternStat.seen, seen), "updated_at": func.now()},
            ))  # fmt: skip
    except Exception:
        logger.exception("memory_pattern_write")


def feedback(session: Session, email: str, pattern: str, outcome: str) -> None:
    """Aprende de una campaña: una respuesta confirma el formato; un rebote le resta confianza y
    la dirección no se vuelve a proponer."""
    if "@" not in email or outcome not in ("replied", "bounced"):
        return
    domain = email.rsplit("@", 1)[1].lower()
    if pattern:
        column = "confirmed" if outcome == "replied" else "bounced"
        stmt = pg_insert(PatternStat).values(domain=domain, pattern=pattern, **{column: 1})
        session.execute(stmt.on_conflict_do_update(
            index_elements=["domain", "pattern"],
            set_={column: getattr(PatternStat, column) + 1, "updated_at": func.now()},
        ))  # fmt: skip
    digest = address_hash(email)
    if outcome == "bounced":
        session.execute(
            pg_insert(BouncedAddress)
            .values(address_hash=digest, domain=domain, pattern=pattern)
            .on_conflict_do_nothing()
        )
    else:
        row = session.get(BouncedAddress, digest)
        if row is not None:
            session.delete(row)


def next_email(session: Session, first: str, last: str, domain: str) -> tuple[str, str]:
    """Siguiente correo a probar para una persona cuyo correo estimado rebotó."""
    from orkesta.research.extract import apply_pattern

    stats = _stats(session, domain)
    primary = _best(stats, domain)[0]
    bounced = _bounced(session, domain)
    for pattern in _chain(stats, primary):
        email = apply_pattern(pattern, first, last, domain)
        if email and address_hash(email) not in bounced:
            return email, pattern
    return "", ""


# --- Servidores de correo del dominio (DNS, sin terceros ni puerto 25) ----------------------

PROVIDERS = (
    ("google", "Google Workspace", ("google.com", "googlemail.com")),
    ("microsoft", "Microsoft 365", ("outlook.com", "protection.outlook.com")),
    ("zoho", "Zoho Mail", ("zoho.com", "zoho.eu")),
    ("proofpoint", "Proofpoint", ("pphosted.com", "ppe-hosted.com")),
    ("mimecast", "Mimecast", ("mimecast.com",)),
)


def mail_provider(hosts: list[str]) -> tuple[str, str]:
    for code, label, suffixes in PROVIDERS:
        if any(h.lower().endswith(s) for h in hosts for s in suffixes):
            return code, label
    return ("propio", "servidor propio") if hosts else ("", "")


def mail_servers(fetcher: Fetcher, domain: str) -> list[str] | None:
    """Servidores MX del dominio (caché 30 días). ``[]`` = el dominio no recibe correo;
    ``None`` = no se pudo consultar."""
    import json
    from datetime import timedelta

    from orkesta.research.fetch import Page

    url = f"mx://{domain}"
    cached = fetcher._cached(url, timedelta(days=30))
    if cached is not None and cached.status == 200:
        try:
            return list(json.loads(cached.text))
        except ValueError:
            pass
    hosts = (MX_RESOLVER or _resolve_mx)(domain)
    if hosts is None:
        return None
    fetcher._store(Page(url, url, 200, "application/json", json.dumps(hosts)))
    return hosts


def _resolve_mx(domain: str) -> list[str] | None:
    """MX por DNS. Distingue «no recibe correo» ([]) de «no se pudo consultar» (None)."""
    import dns.exception
    import dns.resolver

    resolver = dns.resolver.Resolver()
    resolver.lifetime = 5.0
    try:
        answers = resolver.resolve(domain, "MX")
        hosts = [str(r.exchange).rstrip(".") for r in sorted(answers, key=lambda r: r.preference)]
        return [h for h in hosts if h and h != "."]
    except dns.resolver.NXDOMAIN:
        return []
    except dns.resolver.NoAnswer:
        try:
            resolver.resolve(domain, "A")
            return [domain]  # sin MX pero con sitio: el propio dominio puede recibir (RFC 5321)
        except dns.resolver.NXDOMAIN:
            return []
        except dns.exception.DNSException:
            return None
    except dns.exception.DNSException:
        return None


MX_RESOLVER: Callable[[str], list[str] | None] | None = None  # pruebas
