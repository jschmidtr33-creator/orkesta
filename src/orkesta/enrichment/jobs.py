"""Manejadores del worker para la búsqueda de dominios."""

from __future__ import annotations

from sqlalchemy.orm import Session

from orkesta.db.models import EnrichmentJob
from orkesta.engine.domain.resolver import TransientResolutionError
from orkesta.enrichment import service
from orkesta.worker.runner import RetryableJobError, batch_of

# Se puede sustituir en pruebas por un motor con red simulada.
engine_factory: service.EngineFactory = service.default_engine


def handle_resolve_domain(session: Session, job: EnrichmentJob) -> None:
    try:
        outcome = service.resolve_job(session, job, engine_factory)
    except TransientResolutionError as exc:
        raise RetryableJobError(str(exc)) from exc
    service.advance_batch(session, batch_of(job), outcome.domain_status, outcome.pattern_level)


def on_resolve_domain_failed(session: Session, job: EnrichmentJob) -> None:
    service.mark_company_error(session, job)
    service.advance_batch(session, batch_of(job), "failed")
