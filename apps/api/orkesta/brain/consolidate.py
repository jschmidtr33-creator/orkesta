"""Aprovechar toda la data cargada (IA propia).

Cada noche, y cuando la administración lo pide desde el panel, ORKESTA repasa lo que hay en la
plataforma y lo convierte en conocimiento de EMPRESAS (nunca copia personas de un cliente a
otro):

1. Formato de correo de cada dominio: de cada correo corporativo de las bases de contactos de
   todos los clientes (importadas, guardadas o manuales) que no sea estimado ni inválido.
2. Dominio oficial de cada empresa: el que usan los correos de su gente o el que ya tiene la
   empresa. Pasa a la memoria de dominios y al directorio de empresas.
3. Empresas de cada cliente que no tenían sitio web: se completan con lo aprendido.
4. Correos enviados: si pasaron 3 días sin rebote, el formato queda «entregado» (evidencia
   fuerte para los próximos estimados); los rebotes quedan para no volver a proponerlos.
"""

from __future__ import annotations

import logging
import threading
import uuid
from collections import Counter, defaultdict
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import func, select, text, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session, sessionmaker

logger = logging.getLogger(__name__)
DELIVERED_AFTER = timedelta(days=3)
_STATE_KEY = "aprendizaje"
_LOCK = threading.Lock()
SKIP_STATUSES = ("estimado", "invalido")


def _corporate(domain: str) -> bool:
    from orkesta.engine.net.domains import is_non_corporate
    from orkesta.validation.checks import FREE

    return bool(domain) and domain not in FREE and not is_non_corporate(domain)


def _learn_bases(session: Session, tenant_id: uuid.UUID, acc: dict[str, Any]) -> None:
    """Formatos y dominios de las bases de un cliente (se acumulan para todos)."""
    from orkesta.data.models import Company, Contact
    from orkesta.research.agent import company_key
    from orkesta.research.extract import detect_pattern, is_generic_email

    rows = session.execute(
        select(Contact.first_name, Contact.last_name, Contact.email, Contact.email_domain,
               Contact.email_status, Contact.company_name, Contact.country_code,
               Company.name, Company.domain, Company.country_code)
        .outerjoin(Company, Company.id == Contact.company_id)
        .where(Contact.tenant_id == tenant_id)
        .execution_options(yield_per=2000)
    )  # fmt: skip
    for first, last, email, edomain, status, cname, ccountry, coname, codomain, cocountry in rows:
        acc["contacts"] += 1
        name = coname or cname or ""
        country = ccountry or cocountry or ""
        key = (country, company_key(name)) if name and country else None
        if key and codomain and _corporate(codomain):
            acc["domains"][key][codomain.lower()] += 2
        if not email or "@" not in email or status in SKIP_STATUSES:
            continue
        domain = (edomain or email.rsplit("@", 1)[1]).lower()
        if not _corporate(domain) or is_generic_email(email):
            continue
        acc["corporate"] += 1
        if key:
            acc["domains"][key][domain] += 1
        pattern = detect_pattern(first or "", last or "", email)
        if pattern:
            column = "confirmed" if status == "valido" else "seen"
            acc["patterns"][(domain, pattern)][column] += 1


def _save_patterns(session: Session, patterns: dict[tuple[str, str], Counter[str]]) -> int:
    from orkesta.research.models import PatternStat

    rows = [{"domain": d[:253], "pattern": p, "seen": c["seen"], "confirmed": c["confirmed"]}
            for (d, p), c in patterns.items()]  # fmt: skip
    for start in range(0, len(rows), 1000):
        stmt = pg_insert(PatternStat).values(rows[start : start + 1000])
        # Idempotente: repasar la misma base no infla la evidencia.
        session.execute(stmt.on_conflict_do_update(
            index_elements=["domain", "pattern"],
            set_={"seen": func.greatest(PatternStat.seen, stmt.excluded.seen),
                  "confirmed": func.greatest(PatternStat.confirmed, stmt.excluded.confirmed),
                  "updated_at": func.now()},
        ))  # fmt: skip
    return len({d for d, _ in patterns})


def _save_domains(session: Session, domains: dict[tuple[str, str], Counter[str]]) -> int:
    """Memoria de dominios: solo cuando el dominio es claro (60 % de la evidencia o más). No
    reemplaza lo que ya se sabía por otras fuentes."""
    from orkesta.research.models import CompanyDomain

    rows = []
    for (country, ckey), counts in domains.items():
        if not ckey or not counts:
            continue
        domain, votes = counts.most_common(1)[0]
        if votes / sum(counts.values()) >= 0.6:
            rows.append({"key": f"{country}|{ckey}"[:320], "domain": domain[:253],
                         "source": "bases_clientes"})  # fmt: skip
    added = 0
    for start in range(0, len(rows), 1000):
        stmt = pg_insert(CompanyDomain).values(rows[start : start + 1000])
        result = session.execute(stmt.on_conflict_do_nothing(index_elements=["key"]))
        added += int(getattr(result, "rowcount", 0) or 0)
    return added


def _fill_directory(session: Session) -> int:
    """El directorio de empresas toma el sitio web de lo aprendido (el rastreador lo leerá)."""
    result = session.execute(text("""
        UPDATE orkesta.company_directory d SET domain = m.domain, updated_at = now()
        FROM orkesta.company_domains m
        WHERE d.domain IS NULL AND m.key = d.country || '|' || d.name_key
    """))  # fmt: skip
    return int(getattr(result, "rowcount", 0) or 0)


def _fill_companies(session: Session, tenant_id: uuid.UUID) -> int:
    """Empresas de un cliente sin sitio web: se completan con la memoria de dominios."""
    from orkesta.data.models import Company
    from orkesta.research.agent import company_key
    from orkesta.research.models import CompanyDomain

    missing = session.execute(
        select(Company.id, Company.name, Company.country_code).where(
            Company.tenant_id == tenant_id, Company.domain.is_(None),
            Company.country_code.is_not(None),
        )
    ).all()  # fmt: skip
    if not missing:
        return 0
    keys = {f"{c}|{company_key(n)}"[:320]: i for i, n, c in missing if n}
    found = dict(session.execute(
        select(CompanyDomain.key, CompanyDomain.domain).where(CompanyDomain.key.in_(list(keys)))
    ).tuples().all())  # fmt: skip
    for key, domain in found.items():
        session.execute(
            update(Company).where(Company.id == keys[key], Company.domain.is_(None))
            .values(domain=domain)
        )  # fmt: skip
    return len(found)


def _learn_sends(session: Session, tenant_id: uuid.UUID) -> dict[str, int]:
    """Envíos que ya cuentan algo: llegaron (3 días sin rebote) o rebotaron sin aprenderse."""
    from orkesta.campaigns.models import Recipient
    from orkesta.data.models import Contact
    from orkesta.research.extract import detect_pattern
    from orkesta.research.memory import feedback

    out = {"delivered": 0, "bounced": 0}
    cutoff = datetime.now(UTC) - DELIVERED_AFTER
    catch_all = set(session.scalars(
        select(func.distinct(Contact.email_domain)).where(
            Contact.tenant_id == tenant_id, Contact.email_status == "catch_all")
    ))  # fmt: skip
    from orkesta.campaigns.models import Message

    first_sent = (
        select(func.min(Message.sent_at))
        .where(Message.recipient_id == Recipient.id)
        .scalar_subquery()
    )
    rows = session.execute(
        select(Recipient, Contact)
        .join(Contact, Contact.id == Recipient.contact_id)
        .where(Recipient.tenant_id == tenant_id, Recipient.sent_count > 0,
               Recipient.learned_at.is_(None),
               Recipient.status.in_(("active", "completed", "unsubscribed", "bounced")),
               first_sent < cutoff)
        .limit(5000)
    ).all()  # fmt: skip
    for recipient, contact in rows:
        recipient.learned_at = datetime.now(UTC)
        email = recipient.email.lower()
        if "@" not in email:
            continue
        domain = email.rsplit("@", 1)[1]
        if recipient.status == "bounced":
            feedback(session, email, "", "bounced")  # la dirección no se vuelve a proponer
            out["bounced"] += 1
            continue
        if domain in catch_all or (contact.email or "").lower() != email:
            continue  # el servidor acepta todo: que llegue no prueba nada
        pattern = detect_pattern(contact.first_name or "", contact.last_name or "", email)
        if pattern:
            feedback(session, email, pattern, "delivered")
            out["delivered"] += 1
        if contact.email_status == "estimado":
            contact.email_check_reason = "Se envió y no rebotó: el formato funciona"
    return out


def run(factory: sessionmaker[Session], *, trigger: str = "nocturno") -> dict[str, Any]:
    """Repasa toda la data y guarda el resultado para el panel."""
    from orkesta.admin.service import _put
    from orkesta.core.db import set_context
    from orkesta.platform.models import Tenant

    if not _LOCK.acquire(blocking=False):
        return {"state": "running"}
    started = datetime.now(UTC)
    try:
        acc: dict[str, Any] = {
            "contacts": 0, "corporate": 0,
            "patterns": defaultdict(Counter), "domains": defaultdict(Counter),
        }  # fmt: skip
        result: dict[str, Any] = {"companies_filled": 0, "delivered": 0, "bounced": 0}
        with factory() as session:
            tenants = list(session.scalars(select(Tenant.id)))
        for tenant_id in tenants:
            with factory.begin() as session:
                set_context(session, tenant_id=tenant_id)
                _learn_bases(session, tenant_id, acc)
                sends = _learn_sends(session, tenant_id)
                result["delivered"] += sends["delivered"]
                result["bounced"] += sends["bounced"]
        with factory.begin() as session:
            result["pattern_domains"] = _save_patterns(session, acc["patterns"])
            result["domains_added"] = _save_domains(session, acc["domains"])
            result["directory_filled"] = _fill_directory(session)
        for tenant_id in tenants:
            with factory.begin() as session:
                set_context(session, tenant_id=tenant_id)
                result["companies_filled"] += _fill_companies(session, tenant_id)
        result.update(contacts=acc["contacts"], corporate=acc["corporate"], state="done",
                      trigger=trigger, finished_at=datetime.now(UTC).isoformat(),
                      seconds=round((datetime.now(UTC) - started).total_seconds()))  # fmt: skip
        with factory.begin() as session:
            _put(session, _STATE_KEY, result)
        from orkesta.research import memory

        memory._ORDER_CACHE.clear()
        memory._MODEL_CACHE.clear()
        return result
    except Exception:
        logger.exception("consolidate_failed")
        return {"state": "error"}
    finally:
        _LOCK.release()


def start(factory: sessionmaker[Session]) -> bool:
    """Lanza el repaso en segundo plano (desde el panel). ``False`` si ya está corriendo."""
    if _LOCK.locked():
        return False
    threading.Thread(target=run, args=(factory,), kwargs={"trigger": "manual"},
                     daemon=True).start()  # fmt: skip
    return True


def last(session: Session) -> dict[str, Any] | None:
    from orkesta.admin.service import _get

    data = _get(session, _STATE_KEY, None)
    if isinstance(data, dict) and _LOCK.locked():
        return {**data, "state": "running"}
    if data is None and _LOCK.locked():
        return {"state": "running"}
    return data if isinstance(data, dict) else None
