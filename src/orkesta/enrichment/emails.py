"""Patrón de correo y correos por contacto, tras elegir el dominio de una empresa.

Corre dentro del mismo trabajo que resuelve el dominio (un trabajo por grupo de empresas):

1. Si la organización investigó ese dominio hace menos de ``HISTORY_DAYS``, reutiliza la
   decisión; si no, recolecta los correos publicados en el sitio, los observa y decide.
2. Guarda la decisión en el dominio seleccionado y las observaciones como conteos por patrón
   (nunca las direcciones de terceros).
3. Genera hasta tres correos por contacto de esas empresas.
"""

from __future__ import annotations

import uuid
from collections import Counter
from collections.abc import Callable
from datetime import datetime, timedelta

from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from orkesta.db.models import Company, Contact, Domain, EmailCandidate, EmailPatternObservation
from orkesta.engine.email.decide import MODEL_VERSION, PatternDecision, decide, generate_emails
from orkesta.engine.email.harvest import harvest
from orkesta.engine.email.observe import observe
from orkesta.engine.net.fetch import RobotsAwareFetcher
from orkesta.engine.normalize import normalize_person

HISTORY_DAYS = 30
EMAIL_STATUSES = ("confirmed", "probable")  # solo se generan correos sobre dominios fiables


def _recent_decision(session: Session, organization_id: uuid.UUID, domain: str) -> Domain | None:
    since = func.now() - timedelta(days=HISTORY_DAYS)
    return session.scalar(
        select(Domain)
        .where(
            Domain.organization_id == organization_id,
            Domain.domain == domain,
            Domain.pattern_checked_at >= since,
        )
        .order_by(Domain.pattern_checked_at.desc())
        .limit(1)
    )


def _decision_from(row: Domain) -> PatternDecision:
    return PatternDecision(
        email_domain=row.email_domain,
        ranked=[(str(p), float(v)) for p, v in row.pattern_ranked],
        confidence_score=row.pattern_confidence_score or 0,
        confidence_level=row.pattern_confidence_level or "low",
        reasons=[*row.pattern_reason_codes, "HISTORY"]
        if "HISTORY" not in row.pattern_reason_codes
        else list(row.pattern_reason_codes),
    )


def _store_decision(
    session: Session, rows: list[Domain], decision: PatternDecision, now: datetime
) -> None:
    for row in rows:
        row.email_domain = decision.email_domain
        row.email_pattern = decision.pattern
        row.pattern_ranked = [[p, round(v, 4)] for p, v in decision.ranked[:6]]
        row.pattern_confidence_score = decision.confidence_score
        row.pattern_confidence_level = decision.confidence_level
        row.pattern_reason_codes = list(decision.reasons)
        row.pattern_model_version = MODEL_VERSION
        row.pattern_checked_at = now


def _store_observations(
    session: Session, row: Domain, counts: Counter[str], decision: PatternDecision, now: datetime
) -> None:
    session.execute(
        delete(EmailPatternObservation).where(EmailPatternObservation.domain_id == row.id)
    )
    total = sum(counts.values())
    entries: dict[str, int] = dict(counts)
    if decision.pattern and decision.pattern not in entries:
        entries[decision.pattern] = 0  # decidido por el previo aprendido, sin observaciones
    for pattern, count in entries.items():
        chosen = pattern == decision.pattern
        session.add(
            EmailPatternObservation(
                organization_id=row.organization_id,
                domain_id=row.id,
                pattern=pattern,
                origin="observed" if count else "learned",
                observation_count=count,
                exception_count=total - count,
                confidence_score=decision.confidence_score
                if chosen
                else min(decision.confidence_score, round(100 * count / max(total, 1))),
                confidence_level=decision.confidence_level if chosen else "low",
                model_version=MODEL_VERSION,
                observed_at=now,
            )
        )


def generate_for_contacts(
    session: Session,
    companies: list[Company],
    domain_by_company: dict[uuid.UUID, Domain],
    decision: PatternDecision,
    published: set[str],
) -> int:
    """Reemplaza los correos candidatos de los contactos de esas empresas. Devuelve cuántos."""
    company_ids = [c.id for c in companies if c.id in domain_by_company]
    if not company_ids:
        return 0
    contacts = list(session.scalars(select(Contact).where(Contact.company_id.in_(company_ids))))
    if not contacts:
        return 0
    session.execute(
        delete(EmailCandidate).where(EmailCandidate.contact_id.in_([c.id for c in contacts]))
    )
    session.flush()
    created = 0
    for contact in contacts:
        person = normalize_person(contact.first_name_original, contact.last_name_original)
        if not person.is_valid or person.last_is_initial:
            continue
        domain = domain_by_company[contact.company_id]
        for email in generate_emails(person, decision, published):
            session.add(
                EmailCandidate(
                    organization_id=contact.organization_id,
                    contact_id=contact.id,
                    domain_id=domain.id,
                    address=email.address,
                    rank=email.rank,
                    label=email.label,
                    pattern=email.pattern,
                    confidence_score=email.confidence_score,
                    confidence_level=email.confidence_level,
                    reason_codes=list(decision.reasons),
                )
            )
            created += 1
    return created


def apply_email_patterns(
    session: Session,
    companies: list[Company],
    fetcher_factory: Callable[[], RobotsAwareFetcher],
) -> str | None:
    """Patrón y correos para un grupo de empresas con dominio ya elegido.

    Devuelve el nivel de confianza del patrón, o ``None`` si no hay dominio fiable.
    ``fetcher_factory`` solo se invoca si hay que salir a internet.
    """
    rows = list(
        session.scalars(
            select(Domain).where(
                Domain.company_id.in_([c.id for c in companies]),
                Domain.is_selected.is_(True),
                Domain.status.in_(EMAIL_STATUSES),
            )
        )
    )
    if not rows:
        return None
    primary = rows[0]
    now = session.scalar(select(func.now()))
    assert now is not None  # noqa: S101 - func.now() siempre devuelve valor
    published: set[str] = set()

    recent = _recent_decision(session, primary.organization_id, primary.domain)
    if recent is not None:
        decision = _decision_from(recent)
    else:
        found = harvest(primary.domain, fetcher_factory())
        observations = observe(found.emails)
        country = next((c.country_code for c in companies if c.country_code), None)
        decision = decide(
            primary.domain,
            observations,
            country=country,
            mx_provider=primary.mx_provider,
            has_mx=bool(primary.has_mx),
            harvest=found,
        )
        published = {e.address for e in found.emails}
        _store_observations(session, primary, Counter(o.pattern for o in observations), decision,
                            now)  # fmt: skip
    _store_decision(session, rows, decision, now)
    generate_for_contacts(session, companies, {r.company_id: r for r in rows}, decision, published)
    return decision.confidence_level if decision.email_domain else None
