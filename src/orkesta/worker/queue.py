"""Cola de trabajos sobre PostgreSQL.

Varios workers pueden reclamar trabajos a la vez sin pisarse gracias a
``SELECT ... FOR UPDATE SKIP LOCKED``: cada uno salta las filas que otro ya tiene bloqueadas.

Ciclo de vida de un trabajo::

    pending --claim--> running --complete--> succeeded
                          |--fail (reintentable, quedan intentos)--> pending (run_after futuro)
                          |--fail (permanente o sin intentos)------> failed
    running con bloqueo vencido --requeue_stale--> pending   (reanudación tras un corte)
    pending --cancel_batch_jobs--> cancelled
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any

from sqlalchemy import func, select, update
from sqlalchemy.orm import Session

from orkesta.db.models import EnrichmentJob, JobEvent

_MAX_ERROR_LENGTH = 4000


@dataclass(frozen=True)
class RetryPolicy:
    base_seconds: int
    max_seconds: int

    def delay_for(self, attempt: int) -> timedelta:
        """Espera exponencial: base, 2×base, 4×base... con tope en ``max_seconds``."""
        exponent = max(attempt - 1, 0)
        seconds = min(self.base_seconds * (2**exponent), self.max_seconds)
        return timedelta(seconds=seconds)


def _record(session: Session, job: EnrichmentJob, event_type: str, **detail: Any) -> None:
    session.add(
        JobEvent(
            organization_id=job.organization_id,
            job_id=job.id,
            event_type=event_type,
            detail=detail,
        )
    )


def enqueue_job(
    session: Session,
    *,
    organization_id: uuid.UUID,
    batch_id: uuid.UUID | None,
    kind: str,
    idempotency_key: str,
    payload: dict[str, Any] | None = None,
    company_id: uuid.UUID | None = None,
    max_attempts: int = 3,
) -> EnrichmentJob:
    """Encola un trabajo. Si ya existe uno con la misma clave en el lote, devuelve el existente."""
    existing = session.scalar(
        select(EnrichmentJob).where(
            EnrichmentJob.batch_id.is_(None)
            if batch_id is None
            else EnrichmentJob.batch_id == batch_id,
            EnrichmentJob.idempotency_key == idempotency_key,
        )
    )
    if existing is not None:
        return existing
    job = EnrichmentJob(
        organization_id=organization_id,
        batch_id=batch_id,
        company_id=company_id,
        kind=kind,
        idempotency_key=idempotency_key,
        payload=payload or {},
        max_attempts=max_attempts,
    )
    session.add(job)
    session.flush()
    _record(session, job, "enqueued")
    return job


def claim_next_job(session: Session, worker_id: str) -> EnrichmentJob | None:
    """Reclama el trabajo pendiente más antiguo cuyo ``run_after`` ya pasó, o ``None``."""
    job = session.scalar(
        select(EnrichmentJob)
        .where(EnrichmentJob.status == "pending", EnrichmentJob.run_after <= func.now())
        .order_by(EnrichmentJob.run_after, EnrichmentJob.created_at)
        .limit(1)
        .with_for_update(skip_locked=True)
    )
    if job is None:
        return None
    job.status = "running"
    job.attempts += 1
    job.locked_at = session.scalar(select(func.now()))
    job.locked_by = worker_id
    _record(session, job, "claimed", worker_id=worker_id, attempt=job.attempts)
    session.flush()
    return job


def complete_job(session: Session, job: EnrichmentJob) -> None:
    now = session.scalar(select(func.now()))
    job.status = "succeeded"
    job.locked_at = None
    job.locked_by = None
    job.last_error = None
    job.finished_at = now
    _record(session, job, "succeeded", attempt=job.attempts)
    session.flush()


def fail_job(
    session: Session,
    job: EnrichmentJob,
    *,
    error: str,
    retryable: bool,
    policy: RetryPolicy,
) -> None:
    """Registra el fallo y decide entre reprogramar o marcar como fallido definitivo."""
    now: datetime = session.scalar(select(func.now()))  # type: ignore[assignment]
    job.last_error = error[:_MAX_ERROR_LENGTH]
    job.locked_at = None
    job.locked_by = None
    if retryable and job.attempts < job.max_attempts:
        delay = policy.delay_for(job.attempts)
        job.status = "pending"
        job.run_after = now + delay
        _record(
            session,
            job,
            "retry_scheduled",
            attempt=job.attempts,
            delay_seconds=int(delay.total_seconds()),
            error=job.last_error,
        )
    else:
        job.status = "failed"
        job.finished_at = now
        reason = "permanent_error" if not retryable else "attempts_exhausted"
        _record(session, job, "failed", attempt=job.attempts, reason=reason, error=job.last_error)
    session.flush()


def requeue_stale_jobs(session: Session, lock_timeout_seconds: int) -> int:
    """Devuelve a ``pending`` los trabajos cuyo worker dejó de responder (reinicio, corte, OOM).

    El intento interrumpido cuenta: si ya agotó ``max_attempts`` queda como fallido.
    """
    stale = session.scalars(
        select(EnrichmentJob)
        .where(
            EnrichmentJob.status == "running",
            EnrichmentJob.locked_at < func.now() - timedelta(seconds=lock_timeout_seconds),
        )
        .with_for_update(skip_locked=True)
    ).all()
    now = session.scalar(select(func.now()))
    for job in stale:
        previous_worker = job.locked_by
        job.locked_at = None
        job.locked_by = None
        if job.attempts >= job.max_attempts:
            job.status = "failed"
            job.finished_at = now
            job.last_error = "worker_lost: intentos agotados"
            _record(session, job, "failed", reason="worker_lost", worker_id=previous_worker)
        else:
            job.status = "pending"
            job.run_after = now  # type: ignore[assignment]
            _record(session, job, "requeued_stale", worker_id=previous_worker)
    session.flush()
    return len(stale)


def cancel_batch_jobs(session: Session, batch_id: uuid.UUID) -> int:
    """Cancela los trabajos pendientes de un lote. Los que están en curso terminan su intento."""
    result = session.execute(
        update(EnrichmentJob)
        .where(EnrichmentJob.batch_id == batch_id, EnrichmentJob.status == "pending")
        .values(status="cancelled", finished_at=func.now())
        .returning(EnrichmentJob.id, EnrichmentJob.organization_id)
    ).all()
    for job_id, organization_id in result:
        session.add(
            JobEvent(organization_id=organization_id, job_id=job_id, event_type="cancelled")
        )
    session.flush()
    return len(result)
