from __future__ import annotations

import pytest
from sqlalchemy.orm import Session, sessionmaker

from orkesta.db.models import EnrichmentBatch, EnrichmentJob
from orkesta.worker.queue import RetryPolicy, enqueue_job
from orkesta.worker.runner import JobHandler, PermanentJobError, RetryableJobError, Worker

pytestmark = pytest.mark.db


def _worker(factory: sessionmaker[Session], handlers: dict[str, JobHandler]) -> Worker:
    return Worker(
        factory,
        handlers,
        retry_policy=RetryPolicy(base_seconds=30, max_seconds=300),
        poll_interval_seconds=0.01,
        lock_timeout_seconds=600,
        worker_id="test-worker",
    )


def _enqueue(factory: sessionmaker[Session], batch: EnrichmentBatch, kind: str) -> EnrichmentJob:
    with factory.begin() as session:
        return enqueue_job(
            session,
            organization_id=batch.organization_id,
            batch_id=batch.id,
            kind=kind,
            idempotency_key=f"{kind}-1",
            payload={"n": 1},
        )


def _reload(factory: sessionmaker[Session], job: EnrichmentJob) -> EnrichmentJob:
    with factory() as session:
        found = session.get(EnrichmentJob, job.id)
        assert found is not None
        return found


def test_run_once_returns_false_when_queue_is_empty(
    session_factory: sessionmaker[Session], batch: EnrichmentBatch
) -> None:
    assert _worker(session_factory, {}).run_once() is False


def test_successful_handler_marks_job_succeeded(
    session_factory: sessionmaker[Session], batch: EnrichmentBatch
) -> None:
    seen: list[dict[str, int]] = []

    def handler(_: Session, job: EnrichmentJob) -> None:
        seen.append(job.payload)

    job = _enqueue(session_factory, batch, "ok")
    assert _worker(session_factory, {"ok": handler}).run_once() is True
    assert seen == [{"n": 1}]
    assert _reload(session_factory, job).status == "succeeded"


def test_handler_writes_are_committed(
    session_factory: sessionmaker[Session], batch: EnrichmentBatch
) -> None:
    def handler(_: Session, job: EnrichmentJob) -> None:
        job.payload = {**job.payload, "resultado": "guardado"}

    job = _enqueue(session_factory, batch, "escribe")
    _worker(session_factory, {"escribe": handler}).run_once()
    assert _reload(session_factory, job).payload == {"n": 1, "resultado": "guardado"}


def test_retryable_error_reschedules(
    session_factory: sessionmaker[Session], batch: EnrichmentBatch
) -> None:
    def handler(_: Session, __: EnrichmentJob) -> None:
        raise RetryableJobError("fuente sin respuesta")

    job = _enqueue(session_factory, batch, "lento")
    _worker(session_factory, {"lento": handler}).run_once()
    stored = _reload(session_factory, job)
    assert stored.status == "pending"
    assert stored.last_error == "retryable: fuente sin respuesta"


def test_unexpected_exception_is_treated_as_retryable(
    session_factory: sessionmaker[Session], batch: EnrichmentBatch
) -> None:
    def handler(_: Session, __: EnrichmentJob) -> None:
        raise KeyError("campo")

    job = _enqueue(session_factory, batch, "roto")
    _worker(session_factory, {"roto": handler}).run_once()
    stored = _reload(session_factory, job)
    assert stored.status == "pending"
    assert stored.last_error is not None and stored.last_error.startswith("unexpected: KeyError")


def test_handler_writes_are_rolled_back_on_failure(
    session_factory: sessionmaker[Session], batch: EnrichmentBatch
) -> None:
    def handler(_: Session, job: EnrichmentJob) -> None:
        job.payload = {"no": "debe guardarse"}
        raise PermanentJobError("dato inválido")

    job = _enqueue(session_factory, batch, "invalido")
    _worker(session_factory, {"invalido": handler}).run_once()
    stored = _reload(session_factory, job)
    assert stored.status == "failed"
    assert stored.payload == {"n": 1}


def test_job_without_handler_fails_permanently(
    session_factory: sessionmaker[Session], batch: EnrichmentBatch
) -> None:
    job = _enqueue(session_factory, batch, "desconocido")
    _worker(session_factory, {}).run_once()
    stored = _reload(session_factory, job)
    assert stored.status == "failed" and stored.attempts == 1
    assert stored.last_error is not None and stored.last_error.startswith("no_handler")


def test_run_forever_stops_cleanly_and_drains_queue(
    session_factory: sessionmaker[Session], batch: EnrichmentBatch
) -> None:
    import threading

    done = threading.Event()

    def handler(_: Session, __: EnrichmentJob) -> None:
        done.set()

    job = _enqueue(session_factory, batch, "ok")
    worker = _worker(session_factory, {"ok": handler})
    thread = threading.Thread(target=worker.run_forever, daemon=True)
    thread.start()
    assert done.wait(timeout=5)
    worker.stop()
    thread.join(timeout=5)
    assert not thread.is_alive()
    assert _reload(session_factory, job).status == "succeeded"
