"""Cola de trabajos sobre PostgreSQL (``FOR UPDATE SKIP LOCKED``) y worker en hilos.

Sin Redis: un servicio menos que pagar. Cada trabajo pertenece a una empresa; el manejador
recibe la fábrica de sesiones para hacer sus propias transacciones (por ejemplo, una por cada
bloque de 1.000 filas de una importación) y fija el contexto de RLS de esa empresa.
"""

from __future__ import annotations

import logging
import os
import socket
import threading
import traceback
import uuid
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import select, update
from sqlalchemy.orm import Session, sessionmaker

from orkesta.data.models import Job

logger = logging.getLogger("orkesta.jobs")
Handler = Callable[[sessionmaker[Session], uuid.UUID, dict[str, Any]], None]
FailureHook = Callable[[sessionmaker[Session], uuid.UUID, dict[str, Any], str], None]
HANDLERS: dict[str, Handler] = {}
ON_FAILURE: dict[str, FailureHook] = {}
STALE_AFTER = timedelta(minutes=20)


def handler(kind: str, on_failure: FailureHook | None = None) -> Callable[[Handler], Handler]:
    def register(fn: Handler) -> Handler:
        HANDLERS[kind] = fn
        if on_failure:
            ON_FAILURE[kind] = on_failure
        return fn

    return register


def enqueue(
    session: Session,
    *,
    tenant_id: uuid.UUID,
    kind: str,
    payload: dict[str, Any],
    max_attempts: int = 3,
) -> Job:
    job = Job(tenant_id=tenant_id, kind=kind, payload=payload, max_attempts=max_attempts)
    session.add(job)
    session.flush()
    return job


def _now() -> datetime:
    return datetime.now(UTC)


def requeue_stale(factory: sessionmaker[Session]) -> int:
    """Trabajos que quedaron «running» por un reinicio del servidor vuelven a la cola."""
    with factory.begin() as session:
        result = session.execute(
            update(Job)
            .where(Job.status == "running", Job.locked_at < _now() - STALE_AFTER)
            .values(status="pending", locked_at=None, locked_by=None)
        )
        return int(getattr(result, "rowcount", 0) or 0)


def run_once(factory: sessionmaker[Session], worker_id: str) -> bool:
    """Toma y ejecuta un trabajo. Devuelve ``False`` si la cola estaba vacía."""
    with factory.begin() as session:
        job = session.scalar(
            select(Job)
            .where(Job.status == "pending", Job.run_after <= _now())
            .order_by(Job.run_after, Job.created_at)
            .with_for_update(skip_locked=True)
            .limit(1)
        )
        if job is None:
            return False
        job.status, job.locked_at, job.locked_by = "running", _now(), worker_id
        job.attempts += 1
        job_id, kind, tenant_id = job.id, job.kind, job.tenant_id
        payload, attempts, max_attempts = dict(job.payload), job.attempts, job.max_attempts

    fn = HANDLERS.get(kind)
    try:
        if fn is None:
            raise RuntimeError(f"Sin manejador para el trabajo «{kind}»")
        fn(factory, tenant_id, payload)
    except Exception as exc:
        detail = f"{type(exc).__name__}: {exc}"
        logger.error("job_failed %s %s\n%s", kind, job_id, traceback.format_exc())
        final = attempts >= max_attempts
        with factory.begin() as session:
            session.execute(
                update(Job)
                .where(Job.id == job_id)
                .values(
                    status="failed" if final else "pending",
                    last_error=detail[:2000],
                    locked_at=None,
                    run_after=_now() + timedelta(seconds=30 * 2**attempts),
                )
            )
        if final and kind in ON_FAILURE:
            ON_FAILURE[kind](factory, tenant_id, payload, detail)
        return True
    with factory.begin() as session:
        session.execute(update(Job).where(Job.id == job_id).values(status="done", locked_at=None))
    return True


class Worker(threading.Thread):
    def __init__(self, factory: sessionmaker[Session], index: int, poll_seconds: float = 2.0):
        super().__init__(name=f"orkesta-worker-{index}", daemon=True)
        self.factory, self.poll = factory, poll_seconds
        self.worker_id = f"{socket.gethostname()}:{os.getpid()}:{index}"
        self.stop_event = threading.Event()

    def run(self) -> None:
        try:
            requeue_stale(self.factory)
        except Exception:
            logger.exception("requeue_failed")
        while not self.stop_event.is_set():
            try:
                busy = run_once(self.factory, self.worker_id)
            except Exception:
                logger.exception("worker_cycle_failed")
                busy = False
            if not busy:
                self.stop_event.wait(self.poll)

    def stop(self) -> None:
        self.stop_event.set()
