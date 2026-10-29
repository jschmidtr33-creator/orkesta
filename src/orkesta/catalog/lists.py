"""Listas de prospección: personas elegidas de la base, con correo principal y dos alternativos.

El correo de cada miembro se calcula al agregarlo (y se puede recalcular):

- **conocido**: el correo real que venía en una base de la organización (confianza 100);
- **patrón real**: el patrón aprendido de correos reales de esa empresa;
- **previo del país**: el patrón más frecuente del país cuando aún no se conoce el de la empresa.

Sin correo propio, el dominio sale de los colegas de la misma empresa con correo real.
"""

from __future__ import annotations

import uuid
from collections import Counter, defaultdict
from collections.abc import Iterable, Sequence
from dataclasses import dataclass

from sqlalchemy import delete, func, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from orkesta.catalog.prospects import FREEMAIL_DOMAINS
from orkesta.catalog.service import known_pattern
from orkesta.db.models import Prospect, ProspectList, ProspectListMember
from orkesta.engine.email.decide import (
    PatternDecision,
    decide,
    decide_from_known,
    generate_emails,
)
from orkesta.engine.normalize import normalize_person

MAX_LIST_ADD = 5000
CHUNK = 1000


class ListError(ValueError):
    """Operación de lista inválida (nombre repetido, lista inexistente…)."""


@dataclass(frozen=True)
class EmailSet:
    email: str | None
    confidence: int | None
    source: str | None
    alt_1: str | None = None
    alt_1_confidence: int | None = None
    alt_2: str | None = None
    alt_2_confidence: int | None = None
    pattern: str | None = None


def create_list(
    session: Session,
    *,
    organization_id: uuid.UUID,
    user_id: uuid.UUID | None,
    name: str,
    description: str | None = None,
) -> ProspectList:
    clean_name = " ".join(name.split())[:120]
    if not clean_name:
        raise ListError("La lista necesita un nombre.")
    exists = session.scalar(
        select(ProspectList.id).where(
            ProspectList.organization_id == organization_id,
            func.lower(ProspectList.name) == clean_name.lower(),
        )
    )
    if exists:
        raise ListError(f"Ya existe una lista llamada «{clean_name}».")
    item = ProspectList(
        organization_id=organization_id,
        user_id=user_id,
        name=clean_name,
        description=(description or "").strip()[:500] or None,
    )
    session.add(item)
    session.flush()
    return item


def get_list(session: Session, organization_id: uuid.UUID, list_id: uuid.UUID) -> ProspectList:
    item = session.get(ProspectList, list_id)
    if item is None or item.organization_id != organization_id:
        raise ListError("La lista no existe.")
    return item


# --- Correos ---------------------------------------------------------------------------------


def _company_domains(
    session: Session, organization_id: uuid.UUID, company_keys: set[str]
) -> dict[str, str]:
    """Dominio corporativo más común entre los correos reales de cada empresa."""
    if not company_keys:
        return {}
    rows = session.execute(
        select(Prospect.company_key, Prospect.email_domain, func.count())
        .where(
            Prospect.organization_id == organization_id,
            Prospect.company_key.in_(sorted(company_keys)),
            Prospect.email_status == "known",
            Prospect.email_domain.is_not(None),
            Prospect.email_domain.not_in(FREEMAIL_DOMAINS),
        )
        .group_by(Prospect.company_key, Prospect.email_domain)
    )
    best: dict[str, Counter[str]] = defaultdict(Counter)
    for key, domain, count in rows:
        if domain:
            best[key][domain] += count
    return {key: counter.most_common(1)[0][0] for key, counter in best.items()}


def compute_emails(
    session: Session, organization_id: uuid.UUID, prospects: Sequence[Prospect]
) -> dict[uuid.UUID, EmailSet]:
    domains = _company_domains(
        session,
        organization_id,
        {p.company_key for p in prospects if p.email_status != "known" or not p.email},
    )
    decisions: dict[tuple[str, str | None], tuple[PatternDecision, str]] = {}

    def decision_for(domain: str, country: str | None) -> tuple[PatternDecision, str]:
        key = (domain, country)
        if key not in decisions:
            known = known_pattern(session, organization_id, domain)
            if known is not None:
                decisions[key] = (
                    decide_from_known(
                        known.domain,
                        known.pattern,
                        people=known.people,
                        agreeing=known.agreeing,
                        country=country,
                        mx_provider=None,
                    ),
                    "real_pattern",
                )
            else:
                decisions[key] = (
                    decide(domain, [], country=country, mx_provider=None, has_mx=True),
                    "country_prior",
                )
        return decisions[key]

    out: dict[uuid.UUID, EmailSet] = {}
    for prospect in prospects:
        person = normalize_person(prospect.first_name, prospect.last_name)
        known_email = prospect.email if prospect.email_status == "known" else None
        corporate = bool(prospect.email_domain) and prospect.email_domain not in FREEMAIL_DOMAINS
        domain = (prospect.email_domain if corporate else None) or domains.get(prospect.company_key)
        generated = []
        decision_source = None
        pattern = None
        if domain and person.is_valid and not person.last_is_initial:
            decision, decision_source = decision_for(domain, prospect.country_code)
            generated = generate_emails(person, decision, limit=3)
            pattern = decision.pattern
        if known_email:
            alternatives = [g for g in generated if g.address != known_email][:2]
            out[prospect.id] = EmailSet(
                known_email, 100, "known",
                *(_alt(alternatives, 0)), *(_alt(alternatives, 1)), pattern,
            )  # fmt: skip
        elif generated:
            first = generated[0]
            out[prospect.id] = EmailSet(
                first.address, first.confidence_score, decision_source,
                *(_alt(generated, 1)), *(_alt(generated, 2)), pattern,
            )  # fmt: skip
        else:
            out[prospect.id] = EmailSet(None, None, None)
    return out


def _alt(items: Sequence[object], index: int) -> tuple[str | None, int | None]:
    if index < len(items):
        item = items[index]
        return item.address, item.confidence_score  # type: ignore[attr-defined]
    return None, None


# --- Miembros --------------------------------------------------------------------------------


def _member_row(
    organization_id: uuid.UUID, list_id: uuid.UUID, pid: uuid.UUID, e: EmailSet
) -> dict[str, object]:
    return {
        "id": uuid.uuid4(),
        "organization_id": organization_id,
        "list_id": list_id,
        "prospect_id": pid,
        "email": e.email,
        "email_confidence": e.confidence,
        "email_source": e.source,
        "alt_email_1": e.alt_1,
        "alt_email_1_confidence": e.alt_1_confidence,
        "alt_email_2": e.alt_2,
        "alt_email_2_confidence": e.alt_2_confidence,
        "email_pattern": e.pattern,
    }


def add_to_list(
    session: Session,
    *,
    organization_id: uuid.UUID,
    list_id: uuid.UUID,
    prospect_ids: Iterable[uuid.UUID],
    exclude_in_other_lists: bool = False,
) -> dict[str, int]:
    """Agrega personas (con sus correos). Devuelve agregadas, ya estaban, excluidas y sin correo."""
    get_list(session, organization_id, list_id)
    wanted = list(dict.fromkeys(prospect_ids))[:MAX_LIST_ADD]
    already = set(
        session.scalars(
            select(ProspectListMember.prospect_id).where(ProspectListMember.list_id == list_id)
        )
    )
    in_other: set[uuid.UUID] = set()
    if exclude_in_other_lists and wanted:
        in_other = set(
            session.scalars(
                select(ProspectListMember.prospect_id).where(
                    ProspectListMember.organization_id == organization_id,
                    ProspectListMember.list_id != list_id,
                    ProspectListMember.prospect_id.in_(wanted),
                )
            )
        )
    new_ids = [pid for pid in wanted if pid not in already and pid not in in_other]
    added = without_email = 0
    for start in range(0, len(new_ids), CHUNK):
        chunk = new_ids[start : start + CHUNK]
        prospects = list(
            session.scalars(
                select(Prospect).where(
                    Prospect.organization_id == organization_id, Prospect.id.in_(chunk)
                )
            )
        )
        emails = compute_emails(session, organization_id, prospects)
        rows = [_member_row(organization_id, list_id, p.id, emails[p.id]) for p in prospects]
        if rows:
            session.execute(
                pg_insert(ProspectListMember)
                .values(rows)
                .on_conflict_do_nothing(constraint="uq_prospect_list_members_list_id_prospect_id")
            )
        added += len(rows)
        without_email += sum(1 for p in prospects if emails[p.id].email is None)
    return {
        "agregadas": added,
        "ya_estaban": len([pid for pid in wanted if pid in already]),
        "en_otras_listas": len(in_other),
        "sin_correo": without_email,
    }


def remove_members(
    session: Session,
    organization_id: uuid.UUID,
    list_id: uuid.UUID,
    member_ids: Iterable[uuid.UUID],
) -> int:
    ids = list(member_ids)
    if not ids:
        return 0
    result = session.execute(
        delete(ProspectListMember).where(
            ProspectListMember.organization_id == organization_id,
            ProspectListMember.list_id == list_id,
            ProspectListMember.id.in_(ids),
        )
    )
    return int(result.rowcount or 0)  # type: ignore[attr-defined]


def refresh_emails(session: Session, organization_id: uuid.UUID, list_id: uuid.UUID) -> int:
    """Recalcula los correos de todos los miembros (p. ej. tras cargar una base nueva)."""
    members = list(
        session.execute(
            select(ProspectListMember, Prospect)
            .join(Prospect, Prospect.id == ProspectListMember.prospect_id)
            .where(ProspectListMember.list_id == list_id)
        )
    )
    emails = compute_emails(session, organization_id, [p for _, p in members])
    for member, prospect in members:
        e = emails[prospect.id]
        member.email, member.email_confidence, member.email_source = e.email, e.confidence, e.source
        member.alt_email_1, member.alt_email_1_confidence = e.alt_1, e.alt_1_confidence
        member.alt_email_2, member.alt_email_2_confidence = e.alt_2, e.alt_2_confidence
        member.email_pattern = e.pattern
    return len(members)


def list_counts(session: Session, organization_id: uuid.UUID) -> dict[uuid.UUID, tuple[int, int]]:
    """Por lista: (miembros, miembros con correo)."""
    rows = session.execute(
        select(
            ProspectListMember.list_id,
            func.count(),
            func.count(ProspectListMember.email),
        )
        .where(ProspectListMember.organization_id == organization_id)
        .group_by(ProspectListMember.list_id)
    )
    return {list_id: (int(total), int(with_email)) for list_id, total, with_email in rows}


def members_page(
    session: Session, list_id: uuid.UUID, *, page: int = 1, page_size: int = 50
) -> tuple[list[tuple[ProspectListMember, Prospect]], int]:
    base = (
        select(ProspectListMember, Prospect)
        .join(Prospect, Prospect.id == ProspectListMember.prospect_id)
        .where(ProspectListMember.list_id == list_id)
    )
    total = int(
        session.scalar(
            select(func.count())
            .select_from(ProspectListMember)
            .where(ProspectListMember.list_id == list_id)
        )
        or 0
    )
    rows = session.execute(
        base.order_by(Prospect.company, Prospect.full_name)
        .offset((max(page, 1) - 1) * page_size)
        .limit(page_size)
    ).all()
    return [(m, p) for m, p in rows], total
