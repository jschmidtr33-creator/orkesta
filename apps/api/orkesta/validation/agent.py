"""Trabajo en segundo plano: valida correos de contactos y busca los que faltan.

- Agrupa por dominio: una conexión SMTP por dominio verifica varios buzones.
- Buscar faltantes: para contactos sin correo cuya empresa tiene dominio, prueba los formatos
  habituales (nombre.apellido, inicialapellido, …) y se queda con el que el servidor confirma.
  En dominios «catch-all» o sin SMTP disponible no se adivina nada.
"""

from __future__ import annotations

import logging
import threading
import uuid
from collections.abc import Callable, Iterator
from concurrent.futures import ThreadPoolExecutor, as_completed
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select, update
from sqlalchemy.orm import Session, sessionmaker

from orkesta.core.config import get_settings
from orkesta.core.db import set_context
from orkesta.core.jobs import handler
from orkesta.data.models import Company, Contact, DataPoint
from orkesta.data.service import ContactFilters, _filtered
from orkesta.research.extract import PATTERNS, apply_pattern
from orkesta.validation.checks import Checker, Resolver, SmtpProber, Verdict, dns_mx, helo_identity
from orkesta.validation.models import ValidationRun

logger = logging.getLogger("orkesta.validation")
VALIDATION_JOB = "validate_emails"
MAX_CONTACTS = 5000
WORKERS = 8
RCPT_PER_SESSION = 20
FIND_PER_DOMAIN = 40

# Las pruebas reemplazan la red: resolución DNS y conversación SMTP.
RESOLVER: Resolver | None = None
PROBER_FACTORY: Callable[[], SmtpProber | None] | None = None


class Canceled(Exception):
    pass


@dataclass
class Target:
    id: uuid.UUID
    email: str | None
    domain: str
    first_name: str
    last_name: str
    email_status: str = ""
    replace: bool = False  # correo estimado que resultó inválido: buscar el correcto


def _checker() -> Checker:
    if PROBER_FACTORY is not None:
        prober = PROBER_FACTORY()
    else:
        settings = get_settings()
        helo, sender = helo_identity(settings.public_base_url or "orkesta.local")
        prober = SmtpProber(helo, sender) if settings.smtp_probe else None
    return Checker(prober=prober, resolver=RESOLVER or dns_mx)


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
                    select(ValidationRun.status).where(ValidationRun.id == self.run_id)
                )
                session.execute(
                    update(ValidationRun)
                    .where(ValidationRun.id == self.run_id)
                    .values(progress=progress, totals=dict(self.totals))
                )
        if status == "canceled":
            raise Canceled


def _targets(session: Session, tenant_id: uuid.UUID, scope: dict[str, Any]) -> list[Target]:
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
                q=f.get("q", ""),
                status=f.get("estado", ""),
                country=f.get("pais", ""),
                industry=f.get("industria", ""),
                list_id=uuid.UUID(f["lista"]) if f.get("lista") else None,
                email_status=f.get("correo", ""),
            ),
        )
    rows = session.execute(
        query.outerjoin(Company, Company.id == Contact.company_id)
        .with_only_columns(
            Contact.id,
            Contact.email,
            Contact.email_domain,
            Company.domain,
            Contact.first_name,
            Contact.last_name,
            Contact.email_status,
        )
        .limit(MAX_CONTACTS)
    ).all()
    targets = []
    for cid, email, email_domain, company_domain, first, last, status in rows:
        domain = (email.split("@", 1)[1] if email else None) or company_domain or email_domain
        targets.append(
            Target(cid, email, (domain or "").lower(), first or "", last or "", status or "")
        )
    return targets


def _save_verdict(session: Session, tenant_id: uuid.UUID, target: Target, v: Verdict) -> None:
    values: dict[str, Any] = {
        "email_status": v.status,
        "email_checked_at": datetime.now(UTC),
        "email_check_reason": v.reason[:300],
    }
    if v.status == "valido":
        values["status"] = "validado"
    session.execute(update(Contact).where(Contact.id == target.id).values(**values))
    if v.status in ("valido", "invalido"):
        session.add(
            DataPoint(
                tenant_id=tenant_id,
                contact_id=target.id,
                field="email",
                value=v.email,
                source_type="validacion",
                source_label=v.reason[:300],
                confidence=95 if v.status == "valido" else 5,
            )
        )


def _candidates(target: Target) -> list[str]:
    found = []
    for pattern in PATTERNS:
        email = apply_pattern(pattern, target.first_name, target.last_name, target.domain)
        if email and email not in found:
            found.append(email)
    return found


def _domain_work(
    checker: Checker,
    progress: Progress,
    domain: str,
    verify: list[Target],
    find: list[Target],
    retry_estimated: bool = False,
) -> None:
    planned = len(verify) + len(find)
    if verify:
        verdicts: list[Verdict] = []
        emails = [t.email or "" for t in verify]
        for start in range(0, len(emails), RCPT_PER_SESSION):
            verdicts += checker.verify(emails[start : start + RCPT_PER_SESSION])
        with progress.tx() as session:
            for target, verdict in zip(verify, verdicts, strict=True):
                _save_verdict(session, progress.tenant_id, target, verdict)
                progress.add(verdict.status)
                rejected = verdict.status == "invalido" and verdict.mx
                if retry_estimated and rejected and target.email_status == "estimado":
                    target.replace = True
                    find = [*find, target]
    info = checker.domains.get(domain) or checker.domain(domain)
    found_here = 0
    if find and info.mx_hosts and info.catch_all is not True and not info.disposable:
        for target in find[:FIND_PER_DOMAIN]:
            options = [o for o in _candidates(target) if o != target.email]
            if not options:
                continue
            verdicts = checker.verify(options)
            if bool(info.catch_all) or not info.smtp_ok:
                break  # el dominio resultó catch-all o sin SMTP: no se puede adivinar
            hit = next((v for v in verdicts if v.status == "valido"), None)
            if hit is None:
                continue
            found_here += 1
            progress.add("valido")
            if target.replace:
                progress.add("invalido", -1)
                progress.add("replaced")
            with progress.tx() as session:
                contact = session.get(Contact, target.id)
                if contact is None or (contact.email and not target.replace):
                    continue
                contact.email = hit.email
                contact.email_domain = domain
                contact.email_status = "valido"
                contact.email_checked_at = datetime.now(UTC)
                contact.email_check_reason = f"Encontrado: {hit.reason}"[:300]
                contact.status = "validado"
                contact.search_text = f"{contact.search_text} {hit.email}"[:2000]
                session.add(
                    DataPoint(
                        tenant_id=progress.tenant_id,
                        contact_id=target.id,
                        field="email",
                        value=hit.email,
                        source_type="validacion",
                        source_label=f"Encontrado y verificado por SMTP · {info.mx_hosts[0]}",
                        confidence=90,
                    )
                )
    if find:
        progress.add("found", found_here)
        progress.add("find_attempted", min(len(find), FIND_PER_DOMAIN))
    label = f"{domain}: {len(verify)} verificados"
    if find:
        label += f", {found_here} correos encontrados"
    if info.catch_all:
        label += " (acepta todo)"
    progress.note(label, done=planned)


def execute(progress: Progress, scope: dict[str, Any]) -> None:
    with progress.tx() as session:
        targets = _targets(session, progress.tenant_id, scope)
    find_missing = bool(scope.get("find_missing"))
    by_domain: dict[str, tuple[list[Target], list[Target]]] = {}
    skipped = 0
    for target in targets:
        if target.email:
            by_domain.setdefault(target.domain, ([], []))[0].append(target)
        elif find_missing and target.domain and target.first_name and target.last_name:
            by_domain.setdefault(target.domain, ([], []))[1].append(target)
        else:
            skipped += 1
    progress.total = sum(len(v) + len(f) for v, f in by_domain.values())
    progress.add("skipped", skipped)
    progress.note(f"Validando {progress.total} contactos en {len(by_domain)} dominios…")
    checker = _checker()
    with ThreadPoolExecutor(max_workers=WORKERS) as pool:
        futures = {
            pool.submit(_domain_work, checker, progress, domain, verify, find, find_missing): domain
            for domain, (verify, find) in by_domain.items()
        }
        for future in as_completed(futures):
            try:
                future.result()
            except Canceled:
                for other in futures:
                    other.cancel()
                raise
            except Exception:
                logger.exception("validation_domain_failed %s", futures[future])
    if checker.prober is not None and not checker.health.available():
        progress.add("smtp_blocked", 1)
        progress.note("La red del servidor no permite verificar buzones por SMTP (puerto 25).")


def _finish(
    factory: sessionmaker[Session], tenant_id: uuid.UUID, run_id: uuid.UUID, **values: Any
) -> None:
    with factory.begin() as session:
        set_context(session, tenant_id=tenant_id)
        session.execute(
            update(ValidationRun)
            .where(ValidationRun.id == run_id, ValidationRun.status != "canceled")
            .values(finished_at=datetime.now(UTC), **values)
        )


def _on_failure(
    factory: sessionmaker[Session], tenant_id: uuid.UUID, payload: dict[str, Any], detail: str
) -> None:
    _finish(
        factory, tenant_id, uuid.UUID(payload["run_id"]), status="failed",
        error="La validación falló. Intenta de nuevo en unos minutos.",
    )  # fmt: skip


@handler(VALIDATION_JOB, on_failure=_on_failure)
def run_validation(
    factory: sessionmaker[Session], tenant_id: uuid.UUID, payload: dict[str, Any]
) -> None:
    run_id = uuid.UUID(payload["run_id"])
    with factory.begin() as session:
        set_context(session, tenant_id=tenant_id)
        record = session.get(ValidationRun, run_id)
        if record is None or record.status == "canceled":
            return
        record.status = "running"
        scope = dict(record.scope)
    progress = Progress(factory, tenant_id, run_id)
    try:
        execute(progress, scope)
        progress.note("Listo.")
    except Canceled:
        return
    _finish(factory, tenant_id, run_id, status="completed", totals=dict(progress.totals))
