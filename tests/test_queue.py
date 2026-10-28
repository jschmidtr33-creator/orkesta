from __future__ import annotations

from datetime import timedelta

import pytest
from sqlalchemy import func, select, update
from sqlalchemy.orm import Session, sessionmaker

from orkesta.db.models import EnrichmentBatch, EnrichmentJob, JobEvent
from orkesta.worker.queue import (
    RetryPolicy,
    cancel_batch_jobs,
    claim_next_job,
    complete_job,
    enqueue_job,
    fail_job,
    requeue_stale_jobs,
)

pytestmark = pytest.mark.db

POLICY = RetryPolicy(base_seconds=30, max_seconds=300)


def _enqueue(
    factory: sessionmaker[Session], batch: EnrichmentBatch, key: str, max_attempts: int = 3
) -> EnrichmentJob:
    with factory.begin() as session:
        return enqueue_job(
            session,
            organization_id=batch.organization_id,
            batch_id=batch.id,
            kind="test",
            idempotency_key=key,
            max_attempts=max_attempts,
        )


def _events(factory: sessionmaker[Session], job: EnrichmentJob) -> list[str]:
    with factory() as session:
        return list(
            session.scalars(
                select(JobEvent.event_type)
                .where(JobEvent.job_id == job.id)
                .order_by(JobEvent.created_at, JobEvent.event_type)
            )
        )


def _status(factory: sessionmaker[Session], job: EnrichmentJob) -> EnrichmentJob:
    with factory() as session:
        found = session.get(EnrichmentJob, job.id)
        assert found is not None
        return found


def test_retry_policy_is_exponential_with_cap() -> None:
    assert POLICY.delay_for(1) == timedelta(seconds=30)
    assert POLICY.delay_for(2) == timedelta(seconds=60)
    assert POLICY.delay_for(3) == timedelta(seconds=120)
    assert POLICY.delay_for(10) == timedelta(seconds=300)


def test_enqueue_is_idempotent(
    session_factory: sessionmaker[Session], batch: EnrichmentBatch
) -> None:
    first = _enqueue(session_factory, batch, "empresa-1")
    second = _enqueue(session_factory, batch, "empresa-1")
    assert first.id == second.id
    with session_factory() as session:
        assert session.scalar(select(func.count()).select_from(EnrichmentJob)) == 1


def test_claim_and_complete(session_factory: sessionmaker[Session], batch: EnrichmentBatch) -> None:
    job = _enqueue(session_factory, batch, "empresa-1")
    with session_factory.begin() as session:
        claimed = claim_next_job(session, "w1")
        assert claimed is not None and claimed.id == job.id
        assert claimed.status == "running" and claimed.attempts == 1
        complete_job(session, claimed)
    stored = _status(session_factory, job)
    assert stored.status == "succeeded"
    assert stored.locked_by is None and stored.finished_at is not None
    assert _events(session_factory, job) == ["enqueued", "claimed", "succeeded"]


def test_two_workers_never_claim_the_same_job(
    session_factory: sessionmaker[Session], batch: EnrichmentBatch
) -> None:
    job_a = _enqueue(session_factory, batch, "a")
    job_b = _enqueue(session_factory, batch, "b")
    first = session_factory()
    second = session_factory()
    try:
        first.begin()
        second.begin()
        claimed_1 = claim_next_job(first, "w1")  # bloquea su fila sin confirmar
        claimed_2 = claim_next_job(second, "w2")  # debe saltarla y tomar la otra
        claimed_3 = claim_next_job(second, "w2")  # no queda ninguna libre
        assert claimed_1 is not None and claimed_2 is not None
        assert {claimed_1.id, claimed_2.id} == {job_a.id, job_b.id}
        assert claimed_3 is None
        first.commit()
        second.commit()
    finally:
        first.close()
        second.close()


def test_retryable_failure_is_rescheduled_with_backoff(
    session_factory: sessionmaker[Session], batch: EnrichmentBatch
) -> None:
    job = _enqueue(session_factory, batch, "empresa-1")
    with session_factory.begin() as session:
        claimed = claim_next_job(session, "w1")
        assert claimed is not None
        fail_job(session, claimed, error="timeout", retryable=True, policy=POLICY)
    stored = _status(session_factory, job)
    assert stored.status == "pending" and stored.last_error == "timeout"
    with session_factory.begin() as session:
        # El run_after está 30 s en el futuro: todavía no se puede reclamar.
        assert claim_next_job(session, "w1") is None
        delay = session.scalar(
            select(EnrichmentJob.run_after - func.now()).where(EnrichmentJob.id == job.id)
        )
        assert delay is not None and timedelta(seconds=25) < delay <= timedelta(seconds=30)


def test_attempts_are_exhausted_then_job_fails(
    session_factory: sessionmaker[Session], batch: EnrichmentBatch
) -> None:
    job = _enqueue(session_factory, batch, "empresa-1", max_attempts=2)
    for _ in range(2):
        with session_factory.begin() as session:
            session.execute(
                update(EnrichmentJob)
                .where(EnrichmentJob.id == job.id)
                .values(run_after=func.now() - timedelta(seconds=1))
            )
            claimed = claim_next_job(session, "w1")
            assert claimed is not None
            fail_job(session, claimed, error="timeout", retryable=True, policy=POLICY)
    stored = _status(session_factory, job)
    assert stored.status == "failed" and stored.attempts == 2
    assert _events(session_factory, job)[-1] == "failed"


def test_permanent_failure_does_not_retry(
    session_factory: sessionmaker[Session], batch: EnrichmentBatch
) -> None:
    job = _enqueue(session_factory, batch, "empresa-1", max_attempts=5)
    with session_factory.begin() as session:
        claimed = claim_next_job(session, "w1")
        assert claimed is not None
        fail_job(session, claimed, error="fila inválida", retryable=False, policy=POLICY)
    stored = _status(session_factory, job)
    assert stored.status == "failed" and stored.attempts == 1


def test_stale_running_job_is_requeued(
    session_factory: sessionmaker[Session], batch: EnrichmentBatch
) -> None:
    job = _enqueue(session_factory, batch, "empresa-1")
    with session_factory.begin() as session:
        assert claim_next_job(session, "w-caido") is not None
    with session_factory.begin() as session:
        session.execute(
            update(EnrichmentJob)
            .where(EnrichmentJob.id == job.id)
            .values(locked_at=func.now() - timedelta(minutes=30))
        )
    with session_factory.begin() as session:
        assert requeue_stale_jobs(session, lock_timeout_seconds=600) == 1
    stored = _status(session_factory, job)
    assert stored.status == "pending" and stored.locked_by is None and stored.attempts == 1
    assert "requeued_stale" in _events(session_factory, job)


def test_fresh_running_job_is_not_requeued(
    session_factory: sessionmaker[Session], batch: EnrichmentBatch
) -> None:
    _enqueue(session_factory, batch, "empresa-1")
    with session_factory.begin() as session:
        assert claim_next_job(session, "w1") is not None
    with session_factory.begin() as session:
        assert requeue_stale_jobs(session, lock_timeout_seconds=600) == 0


def test_stale_job_without_attempts_left_fails(
    session_factory: sessionmaker[Session], batch: EnrichmentBatch
) -> None:
    job = _enqueue(session_factory, batch, "empresa-1", max_attempts=1)
    with session_factory.begin() as session:
        assert claim_next_job(session, "w-caido") is not None
        session.execute(
            update(EnrichmentJob)
            .where(EnrichmentJob.id == job.id)
            .values(locked_at=func.now() - timedelta(minutes=30))
        )
    with session_factory.begin() as session:
        requeue_stale_jobs(session, lock_timeout_seconds=600)
    assert _status(session_factory, job).status == "failed"


def test_cancel_only_affects_pending_jobs(
    session_factory: sessionmaker[Session], batch: EnrichmentBatch
) -> None:
    running = _enqueue(session_factory, batch, "a")
    pending = _enqueue(session_factory, batch, "b")
    with session_factory.begin() as session:
        claimed = claim_next_job(session, "w1")
        assert claimed is not None and claimed.id == running.id
    with session_factory.begin() as session:
        assert cancel_batch_jobs(session, batch.id) == 1
    assert _status(session_factory, pending).status == "cancelled"
    assert _status(session_factory, running).status == "running"
