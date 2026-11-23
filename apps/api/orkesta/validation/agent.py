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
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import select, update
from sqlalchemy.orm import Session, sessionmaker

from orkesta.core.config import get_settings
from orkesta.core.db import set_context
from orkesta.core.jobs import handler
from orkesta.data.models import Company, Contact, DataPoint
from orkesta.data.service import ContactFilters, _filtered
from orkesta.research.extract import PATTERNS, apply_pattern, detect_pattern
from orkesta.validation.checks import (
    Checker,
    RemoteProber,
    Resolver,
    SmtpHealth,
    SmtpProber,
    Verdict,
    dns_mx,
    helo_identity,
)
from orkesta.validation.models import ValidationRun

logger = logging.getLogger("orkesta.validation")
VALIDATION_JOB = "validate_emails"
MAX_CONTACTS = 5000
WORKERS = 8
RCPT_PER_SESSION = 20
NO_FORMAT = "Ningún formato existe"
FIND_PER_DOMAIN = 40

# Las pruebas reemplazan la red: resolución DNS y conversación SMTP.
RESOLVER: Resolver | None = None
PROBER_FACTORY: Callable[[], SmtpProber | RemoteProber | None] | None = None


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
    reason: str = ""
    checked_at: datetime | None = None


def _checker() -> Checker:
    if PROBER_FACTORY is not None:
        prober = PROBER_FACTORY()
    else:
        settings = get_settings()
        helo, sender = helo_identity(settings.public_base_url or "orkesta.local")
        if settings.verifier_url and settings.verifier_token and settings.verifier_fingerprint:
            prober = RemoteProber(settings.verifier_url, settings.verifier_token,
                                  settings.verifier_fingerprint)  # fmt: skip
        elif key := _api_key():
            # Servicio de verificación (el puerto 25 del servidor está cerrado).
            from orkesta.validation.api_verifier import ApiProber

            return Checker(prober=ApiProber(key), resolver=RESOLVER or dns_mx,
                           health=SmtpHealth())  # fmt: skip
        else:
            prober = SmtpProber(helo, sender) if settings.smtp_probe else None
    return Checker(prober=prober, resolver=RESOLVER or dns_mx)


def _paid(checker: Checker) -> bool:
    from orkesta.validation.api_verifier import ApiProber

    return isinstance(checker.prober, ApiProber)


def _api_key() -> str:
    from orkesta.core.db import get_session_factory
    from orkesta.validation.api_verifier import configured_key

    try:
        with get_session_factory()() as session:
            return configured_key(session)
    except Exception:
        logger.exception("verifier_key")
        return ""


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
            Contact.email_check_reason,
            Contact.email_checked_at,
        )
        .limit(MAX_CONTACTS)
    ).all()
    targets = []
    for cid, email, email_domain, company_domain, first, last, status, reason, at in rows:
        domain = (email.split("@", 1)[1] if email else None) or company_domain or email_domain
        targets.append(
            Target(
                cid,
                email,
                (domain or "").lower(),
                first or "",
                last or "",
                status or "",
                reason=reason or "",
                checked_at=at,
            )
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


def _known(
    progress: Progress, targets: list[Target]
) -> tuple[list[tuple[Target, str]], list[Target]]:
    from orkesta.research.trust import Trust

    trust = Trust(progress.factory)
    known: list[tuple[Target, str]] = []
    rest: list[Target] = []
    for t in targets:
        safe, why = (False, "")
        if t.email:
            safe, why = trust.check(t.email, t.first_name, t.last_name, t.domain)
        if safe:
            known.append((t, why))
        else:
            rest.append(t)
    return known, rest


def info_ok(checker: Checker, domain: str) -> bool:
    """El dominio no acepta cualquier dirección: un «válido» ahí sí prueba el buzón."""
    info = checker.domains.get(domain)
    return info is None or info.catch_all is not True


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
        # Lo que ORKESTA ya comprobó (la dirección exacta o el formato confirmado del dominio)
        # queda válido sin gastar verificaciones.
        known, verify = _known(progress, verify)
        if known:
            with progress.tx() as session:
                for target, why in known:
                    _save_verdict(session, progress.tenant_id, target,
                                  Verdict(target.email or "", "valido", why))  # fmt: skip
                    progress.add("valido")
                    progress.add("conocidos")
    if verify:
        verdicts: list[Verdict] = []
        emails = [t.email or "" for t in verify]
        for start in range(0, len(emails), RCPT_PER_SESSION):
            verdicts += checker.verify(emails[start : start + RCPT_PER_SESSION])
        from orkesta.research.memory import feedback

        with progress.tx() as session:
            for target, verdict in zip(verify, verdicts, strict=True):
                _save_verdict(session, progress.tenant_id, target, verdict)
                progress.add(verdict.status)
                if verdict.status == "valido" and target.email and info_ok(checker, domain):
                    # Buzón válido: la dirección y su formato quedan verificados para todo
                    # ORKESTA (otra empresa que busque a esta persona no gasta otra consulta).
                    pattern = detect_pattern(target.first_name, target.last_name, target.email)
                    feedback(session, target.email, pattern or "", "verified")
                rejected = verdict.status == "invalido" and verdict.mx
                if rejected and target.email:
                    # El buzón no existe: ese formato pierde confianza en el dominio y la
                    # dirección no se vuelve a proponer (para nadie en ORKESTA).
                    pattern = detect_pattern(target.first_name, target.last_name, target.email)
                    feedback(session, target.email, pattern or "", "bounced")
                if retry_estimated and rejected and target.first_name and target.last_name:
                    # Cualquier correo inválido (estimado o de una base) se intenta reemplazar
                    # por el correcto de la misma persona.
                    target.replace = True
                    find = [*find, target]
        all_rejected = len(verdicts) >= 2 and all(
            v.status == "invalido" and v.mx for v in verdicts
        )  # fmt: skip
    else:
        all_rejected = False
    info = checker.domains.get(domain) or checker.domain(domain)
    found_here = 0
    if find and info.mx_hosts and info.catch_all is not True and not info.disposable:
        for target in find[:FIND_PER_DOMAIN]:
            options = [o for o in _candidates(target) if o != target.email]
            if not options:
                continue
            if _paid(checker):
                # Servicio de pago: primero el formato aprendido de la empresa y de a uno,
                # hasta encontrar el buzón (máximo 4 consultas por persona).
                with progress.tx() as session:
                    from orkesta.research.memory import next_email

                    best, _ = next_email(session, target.first_name, target.last_name, domain)
                options = ([best] if best and best in options else []) + [
                    o for o in options if o != best
                ]
                verdicts = []
                for option in options[:4]:
                    verdicts += checker.verify([option])
                    if verdicts[-1].status == "valido" or info.catch_all or not info.smtp_ok:
                        break
            else:
                verdicts = checker.verify(options)
            if bool(info.catch_all) or not info.smtp_ok:
                break  # el dominio resultó catch-all o sin SMTP: no se puede adivinar
            hit = next((v for v in verdicts if v.status == "valido"), None)
            if hit is None:
                if target.replace and any(v.status == "invalido" for v in verdicts):
                    why = f"{NO_FORMAT} en {domain}: quizá ya no trabaja ahí o la empresa usa otro "
                    with progress.tx() as session:
                        session.execute(update(Contact).where(Contact.id == target.id).values(
                            email_checked_at=datetime.now(UTC),
                            email_check_reason=(why + "dominio")[:300]))  # fmt: skip
                    progress.add("no_format")
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
                from orkesta.research.memory import feedback as learn

                learn(session, hit.email, detect_pattern(target.first_name, target.last_name,
                                                         hit.email) or "", "verified")  # fmt: skip
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
    if find and not info.smtp_ok and info.mx_hosts and not info.disposable:
        # Sin conversación SMTP (puerto 25 bloqueado o el servidor no responde) no se puede
        # confirmar el buzón: se deja el correo más probable según el formato aprendido de la
        # empresa, marcado «Estimado» para validarlo antes de escribir.
        from orkesta.research.memory import next_email

        estimated = 0
        with progress.tx() as session:
            for target in find[:FIND_PER_DOMAIN]:
                contact = session.get(Contact, target.id)
                if contact is None or contact.email or target.replace:
                    continue
                email, pattern = next_email(session, target.first_name, target.last_name, domain)
                if not email:
                    continue
                contact.email = email
                contact.email_domain = domain
                contact.email_status = "estimado"
                contact.email_check_reason = (
                    f"Formato {pattern} de {domain}: el dominio recibe correo, buzón por confirmar"
                )[:300]
                contact.search_text = f"{contact.search_text} {email}"[:2000]
                session.add(DataPoint(tenant_id=progress.tenant_id, contact_id=target.id,
                                      field="email", value=email, source_type="validacion",
                                      source_label="Estimado con el formato de la empresa",
                                      confidence=50))  # fmt: skip
                estimated += 1
        progress.add("estimated", estimated)
    if all_rejected and found_here == 0 and info.smtp_ok and info.catch_all is False:
        # Nadie existe en ese dominio con ningún formato: lo más probable es que no sea el
        # dominio de la empresa. Se avisa en cada contacto y ORKESTA lo olvida para que el
        # próximo enriquecimiento busque el correcto.
        from sqlalchemy import delete as sql_delete

        from orkesta.research.models import CompanyDomain

        with progress.tx() as session:
            for target in verify:
                session.execute(update(Contact).where(Contact.id == target.id).values(
                    email_check_reason=f"Ningún formato existe en {domain}: quizá no es "
                    "el dominio de la empresa. Revisa su sitio web y vuelve a enriquecer."
                ))  # fmt: skip
            session.execute(sql_delete(CompanyDomain).where(
                CompanyDomain.domain == domain,
                CompanyDomain.source.notin_(("bases", "bases_clientes", "deducido"))))  # fmt: skip
        progress.add("bad_domain", len(verify))
    if find:
        progress.add("found", found_here)
        progress.add("find_attempted", min(len(find), FIND_PER_DOMAIN))
    if info.catch_all is not None and info.mx_hosts:
        from orkesta.research.memory import note_domain

        with progress.tx() as session:
            note_domain(session, domain, bool(info.catch_all))
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
    recent = datetime.now(UTC) - timedelta(days=30)
    for target in targets:
        fresh = target.checked_at is not None and target.checked_at >= recent
        if target.email and target.email_status == "invalido" and fresh:
            # Ya se verificó hace poco que no existe: no se vuelve a gastar en él.
            if (
                not find_missing
                or target.reason.startswith(NO_FORMAT)
                or not (target.first_name and target.last_name)
            ):
                progress.add("already_tried")
                continue
            target.replace = True
            by_domain.setdefault(target.domain, ([], []))[1].append(target)
            continue
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
    if _paid(checker):
        from orkesta.admin.limits import verifications_left

        with progress.factory() as session:
            checker.prober.limit = verifications_left(session, progress.tenant_id)
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
    if _paid(checker):
        from orkesta.admin import alerts

        prober = checker.prober
        progress.add("paid_checks", int(getattr(prober, "used", 0)))
        if getattr(prober, "limit", None) is not None and prober.used >= prober.limit:
            progress.add("plan_quota_reached", 1)
            progress.note("Usaste las verificaciones de tu plan de este mes: los demás quedaron "
                          "«Sin confirmar». Puedes comprar un paquete extra.")  # fmt: skip
        elif not checker.health.available():
            progress.add("smtp_blocked", 1)
            progress.note("La verificación de buzones no está disponible en este momento: los "
                          "correos que ORKESTA no conocía quedaron «Sin confirmar» y no se "
                          "descontaron de tu plan.")  # fmt: skip
            alerts.record(progress.factory, "verificador")
        else:
            with progress.factory.begin() as session:
                alerts.clear(session, "verificador")
    elif checker.prober is not None and not checker.health.available():
        progress.add("smtp_blocked", 1)
        progress.note("Algunos buzones no se pudieron confirmar: quedaron «Sin confirmar».")


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
