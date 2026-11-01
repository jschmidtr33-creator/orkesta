"""Enriquecimiento de contactos existentes (importados o manuales) con el motor propio.

Por cada empresa: dominio oficial → formato de correo (evidencia interna, Google y sitio) →
correo estimado de cada contacto sin correo. Por cada persona (opcional): perfil público en
Google para completar enlace, cargo y ciudad. Nunca se sobrescribe un dato que ya existe.
"""

from __future__ import annotations

import logging
import threading
import uuid
from collections.abc import Iterator
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select, update
from sqlalchemy.orm import Session, sessionmaker

from orkesta.core.db import set_context
from orkesta.core.jobs import handler
from orkesta.data.models import Company, Contact, DataPoint
from orkesta.data.service import ContactFilters, _filtered
from orkesta.engine.net.domains import is_non_corporate, registrable_domain
from orkesta.engine.normalize.company import normalize_company
from orkesta.engine.normalize.location import COUNTRIES
from orkesta.engine.normalize.text import fold
from orkesta.enrichment.models import EnrichmentRun
from orkesta.research import serp, website
from orkesta.research.extract import apply_pattern, detect_pattern, is_generic_email
from orkesta.research.fetch import Fetcher
from orkesta.validation.checks import FREE

logger = logging.getLogger("orkesta.enrichment")
ENRICH_JOB = "enrich_contacts"
MAX_CONTACTS = 2000
COMMON_PATTERN = "first.last"


class Canceled(Exception):
    pass


@dataclass
class Member:
    id: uuid.UUID
    first: str
    last: str
    email: str | None
    title: str | None
    city: str | None
    country: str | None
    linkedin: str | None
    status: str


@dataclass
class Group:
    key: str
    name: str
    company_id: uuid.UUID | None
    domain: str = ""
    country: str = ""
    members: list[Member] = field(default_factory=list)


class Progress:
    def __init__(self, factory: sessionmaker[Session], tenant_id: uuid.UUID, run_id: uuid.UUID):
        self.factory, self.tenant_id, self.run_id = factory, tenant_id, run_id
        self.lock = threading.Lock()
        self.done = self.total = 0
        self.log: list[dict[str, str]] = []
        self.totals: dict[str, int] = {}

    @contextmanager
    def tx(self) -> Iterator[Session]:
        with self.factory.begin() as session:
            set_context(session, tenant_id=self.tenant_id)
            yield session

    def add(self, key: str, n: int = 1) -> None:
        with self.lock:
            self.totals[key] = self.totals.get(key, 0) + n

    def note(self, text: str, done: int = 0) -> None:
        with self.lock:
            self.done += done
            if text:
                self.log = [*self.log, {"at": datetime.now(UTC).isoformat(), "text": text}][-30:]
            progress = {"done": self.done, "total": self.total, "log": self.log}
            with self.tx() as session:
                status = session.scalar(
                    select(EnrichmentRun.status).where(EnrichmentRun.id == self.run_id)
                )
                session.execute(
                    update(EnrichmentRun)
                    .where(EnrichmentRun.id == self.run_id)
                    .values(progress=progress, totals=dict(self.totals))
                )
        if status == "canceled":
            raise Canceled


def _groups(session: Session, tenant_id: uuid.UUID, scope: dict[str, Any]) -> list[Group]:
    if scope.get("ids"):
        query = select(Contact).where(
            Contact.tenant_id == tenant_id,
            Contact.id.in_([uuid.UUID(i) for i in scope["ids"]]),
        )
    else:
        f = scope.get("filters") or {}
        query = _filtered(
            tenant_id,
            ContactFilters(
                q=f.get("q", ""), status=f.get("estado", ""), country=f.get("pais", ""),
                industry=f.get("industria", ""), email_status=f.get("correo", ""),
                list_id=uuid.UUID(f["lista"]) if f.get("lista") else None,
            ),
        )  # fmt: skip
    rows = session.execute(
        query.outerjoin(Company, Company.id == Contact.company_id)
        .with_only_columns(Contact, Company)
        .limit(MAX_CONTACTS)
    ).all()
    groups: dict[str, Group] = {}
    for contact, company in rows:
        name = (company.name if company else None) or contact.company_name or ""
        key = str(company.id) if company else (normalize_company(name).match_key or fold(name))
        if not key:
            key = f"sin-empresa-{contact.id}"
        group = groups.setdefault(
            key, Group(key=key, name=name, company_id=company.id if company else None)
        )
        if company and company.domain and not group.domain:
            group.domain = company.domain
        group.country = group.country or contact.country_code or (
            company.country_code if company else ""
        ) or ""  # fmt: skip
        email_domain = contact.email.split("@", 1)[1] if contact.email else ""
        if email_domain and email_domain not in FREE and not group.domain:
            group.domain = registrable_domain(email_domain) or email_domain
        group.members.append(
            Member(
                contact.id, contact.first_name or "", contact.last_name or "", contact.email,
                contact.job_title, contact.city, contact.country_code, contact.linkedin_url,
                contact.status,
            )
        )  # fmt: skip
    return list(groups.values())


def _internal_pattern(session: Session, tenant_id: uuid.UUID, domain: str) -> tuple[str, int]:
    rows = session.execute(
        select(Contact.first_name, Contact.last_name, Contact.email)
        .where(
            Contact.tenant_id == tenant_id,
            Contact.email_domain == domain,
            Contact.email.is_not(None),
            Contact.email_status.notin_(("estimado", "invalido")),
        )
        .limit(200)
    ).all()
    votes: dict[str, int] = {}
    for first, last, email in rows:
        if email and not is_generic_email(email):
            pattern = detect_pattern(first or "", last or "", email)
            if pattern:
                votes[pattern] = votes.get(pattern, 0) + 1
    if not votes:
        return "", 0
    best = max(votes, key=lambda k: votes[k])
    return best, votes[best]


def _site_pattern(fetcher: Fetcher, domain: str) -> tuple[str, int, str]:
    result = website.investigate(fetcher, domain)
    personal = [e for e in result.emails if not is_generic_email(e)]
    votes: dict[str, int] = {}
    for person, _ in result.people:
        for email in personal:
            pattern = detect_pattern(person.first_name, person.last_name, email)
            if pattern:
                votes[pattern] = votes.get(pattern, 0) + 1
    if not votes:
        return "", 0, ""
    best = max(votes, key=lambda k: votes[k])
    return best, votes[best], result.home_url


def _point(tenant_id: uuid.UUID, contact_id: uuid.UUID, field_name: str, value: str,
           label: str, confidence: int) -> DataPoint:  # fmt: skip
    return DataPoint(
        tenant_id=tenant_id, contact_id=contact_id, field=field_name, value=value[:1000],
        source_type="enriquecimiento", source_label=label[:300], confidence=confidence,
    )  # fmt: skip


def _work(
    progress: Progress,
    fetcher: Fetcher,
    stats: serp.SerpStats,
    group: Group,
    profiles: bool,
) -> None:
    country_name = COUNTRIES.get(group.country, ("",))[0] if group.country else ""
    use_serp = serp.enabled() and not stats.fatal
    # 1. Dominio oficial de la empresa.
    if not group.domain and group.name and use_serp:
        group.domain = serp.company_domain(fetcher, stats, group.name, group.country,
                                           country_name or "")  # fmt: skip
        if group.domain:
            progress.add("domains_found")
            if group.company_id:
                with progress.tx() as session:
                    session.execute(
                        update(Company)
                        .where(Company.id == group.company_id, Company.domain.is_(None))
                        .values(domain=group.domain)
                    )
    domain = group.domain if group.domain and not is_non_corporate(group.domain) else ""
    # 2. Formato de correo, de la evidencia más fuerte a la más débil.
    pattern, votes, label, confidence = "", 0, "", 0
    needs_email = [m for m in group.members if not m.email]
    if domain and needs_email:
        with progress.tx() as session:
            pattern, votes = _internal_pattern(session, progress.tenant_id, domain)
        if pattern:
            label = f"Formato {pattern}@{domain} visto en {votes} correo(s) de tu base"
            confidence = 55
        if not pattern and use_serp:
            pattern, votes, _ = serp.email_pattern(fetcher, stats, domain)
            if pattern:
                label = f"Formato {pattern}@{domain} visto en {votes} correo(s) públicos (Google)"
                confidence = 50 if votes > 1 else 40
        if not pattern:
            pattern, votes, _ = _site_pattern(fetcher, domain)
            if pattern:
                label = f"Formato {pattern}@{domain} visto en el sitio oficial"
                confidence = 50
        if not pattern:
            pattern = COMMON_PATTERN
            label = f"Formato más común ({pattern}@{domain}); sin evidencia, por validar"
            confidence = 20
            progress.add("common_pattern")
        else:
            progress.add("evidence_pattern")
    # 3. Persona por persona: correo y perfil público.
    for member in group.members:
        updates: dict[str, Any] = {}
        points: list[DataPoint] = []
        if not member.email and domain and pattern:
            email = apply_pattern(pattern, member.first, member.last, domain)
            if email:
                updates.update(
                    email=email, email_domain=domain, email_status="estimado",
                    email_check_reason=label[:300],
                )  # fmt: skip
                points.append(_point(progress.tenant_id, member.id, "email", email, label,
                                     confidence))  # fmt: skip
                progress.add("emails")
        if profiles and use_serp and not member.linkedin and not stats.fatal:
            profile = serp.find_profile(
                fetcher, stats, first=member.first, last=member.last, company=group.name,
                country=member.country or group.country, country_name=country_name,
            )  # fmt: skip
            if profile is not None:
                updates["linkedin_url"] = profile.url[:300]
                progress.add("profiles")
                src = f"Perfil público · Google · {profile.url}"
                if profile.title and not member.title:
                    updates["job_title"] = profile.title[:300]
                    points.append(_point(progress.tenant_id, member.id, "job_title",
                                         profile.title, src, 65))  # fmt: skip
                    progress.add("titles")
                city = profile.location.split(",")[0].strip() if "," in profile.location else ""
                if city and not member.city:
                    updates["city"] = city[:120]
                    points.append(_point(progress.tenant_id, member.id, "city", city, src, 60))
        if updates:
            if member.status == "nuevo":
                updates["status"] = "enriquecido"
            with progress.tx() as session:
                contact = session.get(Contact, member.id)
                if contact is None:
                    continue
                for key, value in updates.items():
                    # Nunca sobrescribir: solo se completan campos vacíos.
                    if key in ("status", "email_status", "email_check_reason") or not getattr(
                        contact, key
                    ):
                        setattr(contact, key, value)
                if "job_title" in updates:
                    from orkesta.engine.taxonomy.titles import classify_title

                    contact.level_code, contact.area_code = classify_title(contact.job_title)
                contact.search_text = fold(" ".join(
                    x for x in (contact.full_name, contact.email, contact.company_name,
                                contact.job_title, contact.city) if x
                ))[:2000]  # fmt: skip
                session.add_all(points)
            progress.add("enriched")
    progress.note(
        f"{group.name or 'Sin empresa'}: {domain or 'sin dominio'}"
        + (f" · formato {pattern}" if pattern and needs_email else ""),
        done=len(group.members),
    )


def execute(progress: Progress, scope: dict[str, Any], factory: sessionmaker[Session]) -> None:
    with progress.tx() as session:
        groups = _groups(session, progress.tenant_id, scope)
    progress.total = sum(len(g.members) for g in groups)
    profiles = bool(scope.get("profiles", True))
    progress.note(f"Enriqueciendo {progress.total} contactos de {len(groups)} empresas…")
    stats = serp.SerpStats(budget=3000)
    with Fetcher(factory) as fetcher, ThreadPoolExecutor(max_workers=5) as pool:
        futures = [pool.submit(_work, progress, fetcher, stats, g, profiles) for g in groups]
        for future in futures:
            try:
                future.result()
            except Canceled:
                for other in futures:
                    other.cancel()
                raise
            except Exception:
                logger.exception("enrich_group_failed")
    progress.add("serp_calls", stats.calls)
    if stats.fatal == "clave":
        progress.note("El buscador no funcionó: revisa SERPER_API_KEY en Render.")
    elif stats.fatal == "creditos":
        progress.note("Se acabaron los créditos de Serper: recarga en serper.dev.")
    elif not serp.enabled():
        progress.note("Sin buscador (SERPER_API_KEY) solo se usan tu base y los sitios web.")


def _finish(
    factory: sessionmaker[Session], tenant_id: uuid.UUID, run_id: uuid.UUID, **values: Any
) -> None:
    with factory.begin() as session:
        set_context(session, tenant_id=tenant_id)
        session.execute(
            update(EnrichmentRun)
            .where(EnrichmentRun.id == run_id, EnrichmentRun.status != "canceled")
            .values(finished_at=datetime.now(UTC), **values)
        )


def _on_failure(
    factory: sessionmaker[Session], tenant_id: uuid.UUID, payload: dict[str, Any], detail: str
) -> None:
    _finish(factory, tenant_id, uuid.UUID(payload["run_id"]), status="failed",
            error="El enriquecimiento falló. Intenta de nuevo en unos minutos.")  # fmt: skip


@handler(ENRICH_JOB, on_failure=_on_failure)
def run_enrichment(
    factory: sessionmaker[Session], tenant_id: uuid.UUID, payload: dict[str, Any]
) -> None:
    run_id = uuid.UUID(payload["run_id"])
    with factory.begin() as session:
        set_context(session, tenant_id=tenant_id)
        record = session.get(EnrichmentRun, run_id)
        if record is None or record.status == "canceled":
            return
        record.status = "running"
        scope = dict(record.scope)
    progress = Progress(factory, tenant_id, run_id)
    try:
        execute(progress, scope, factory)
        progress.note("Listo.")
    except Canceled:
        return
    _finish(factory, tenant_id, run_id, status="completed", totals=dict(progress.totals))
