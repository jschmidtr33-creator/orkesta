"""Bucle del worker: reclama un trabajo, ejecuta su manejador y registra el resultado.

Cada tipo de trabajo (``kind``) tiene un manejador registrado. Las etapas siguientes registran
los manejadores del motor de enriquecimiento; un trabajo sin manejador falla de forma
permanente con un error explícito en lugar de quedarse colgado.
"""

from __future__ import annotations

import logging
import os
import signal
import socket
import threading
from collections.abc import Callable
from types import FrameType

from sqlalchemy.orm import Session, sessionmaker

from orkesta.db.models import EnrichmentJob
from orkesta.worker.queue import (
    RetryPolicy,
    claim_next_job,
    complete_job,
    fail_job,
    requeue_stale_jobs,
)

logger = logging.getLogger("orkesta.worker")

JobHandler = Callable[[Session, EnrichmentJob], None]
# Se ejecuta en la misma transacción en la que un trabajo queda fallido de forma definitiva,
# para que el objeto afectado (p. ej. el lote) no se quede en un estado intermedio.
FailureHook = Callable[[Session, EnrichmentJob], None]


class RetryableJobError(Exception):
    """Fallo transitorio (timeout, fuente caída): el trabajo se reintenta si quedan intentos."""


class PermanentJobError(Exception):
    """Fallo que no se arregla reintentando (datos inválidos): el trabajo falla de inmediato."""


def default_worker_id() -> str:
    return f"{socket.gethostname()}:{os.getpid()}"


class Worker:
    def __init__(
        self,
        session_factory: sessionmaker[Session],
        handlers: dict[str, JobHandler],
        *,
        retry_policy: RetryPolicy,
        poll_interval_seconds: float,
        lock_timeout_seconds: int,
        worker_id: str | None = None,
        failure_hooks: dict[str, FailureHook] | None = None,
    ) -> None:
        self._session_factory = session_factory
        self._handlers = dict(handlers)
        self._failure_hooks = dict(failure_hooks or {})
        self._retry_policy = retry_policy
        self._poll_interval = poll_interval_seconds
        self._lock_timeout = lock_timeout_seconds
        self.worker_id = worker_id or default_worker_id()
        self._stop = threading.Event()

    def stop(self) -> None:
        self._stop.set()

    def install_signal_handlers(self) -> None:
        """SIGTERM (Railway al redesplegar) y SIGINT terminan el trabajo actual y salen."""

        def _handle(signum: int, _frame: FrameType | None) -> None:
            logger.info("worker_stopping", extra={"signal": signum})
            self.stop()

        signal.signal(signal.SIGTERM, _handle)
        signal.signal(signal.SIGINT, _handle)

    def recover_stale(self) -> int:
        with self._session_factory.begin() as session:
            count = requeue_stale_jobs(session, self._lock_timeout)
        if count:
            logger.info("stale_jobs_requeued", extra={"count": count})
        return count

    def run_once(self) -> bool:
        """Procesa como máximo un trabajo. Devuelve ``True`` si había uno."""
        with self._session_factory.begin() as session:
            job = claim_next_job(session, self.worker_id)
            if job is None:
                return False
            job_id, kind = job.id, job.kind

        log_extra = {"job_id": str(job_id), "kind": kind}
        handler = self._handlers.get(kind)
        error: str | None = None
        retryable = False

        if handler is None:
            error, retryable = f"no_handler: no hay manejador para el tipo '{kind}'", False
        else:
            try:
                with self._session_factory.begin() as session:
                    current = session.get(EnrichmentJob, job_id)
                    if current is None:
                        raise PermanentJobError("el trabajo desapareció durante la ejecución")
                    handler(session, current)
            except PermanentJobError as exc:
                error, retryable = f"permanent: {exc}", False
            except RetryableJobError as exc:
                error, retryable = f"retryable: {exc}", True
            except Exception as exc:
                logger.exception("job_handler_crashed", extra=log_extra)
                error, retryable = f"unexpected: {type(exc).__name__}: {exc}", True

        with self._session_factory.begin() as session:
            current = session.get(EnrichmentJob, job_id, with_for_update=True)
            if current is None:
                return True
            if error is None:
                complete_job(session, current)
                logger.info("job_succeeded", extra=log_extra)
            else:
                fail_job(
                    session, current, error=error, retryable=retryable, policy=self._retry_policy
                )
                hook = self._failure_hooks.get(kind)
                if current.status == "failed" and hook is not None:
                    hook(session, current)
                logger.warning(
                    "job_failed",
                    extra={**log_extra, "retryable": retryable, "status": current.status},
                )
        return True

    def run_forever(self) -> None:
        logger.info("worker_started", extra={"worker_id": self.worker_id})
        self.recover_stale()
        idle_cycles = 0
        while not self._stop.is_set():
            try:
                processed = self.run_once()
            except Exception:
                # Error de infraestructura (p. ej. base de datos caída): esperar y reintentar.
                logger.exception("worker_cycle_failed")
                processed = False
            if processed:
                idle_cycles = 0
                continue
            idle_cycles += 1
            # Revisar bloqueos vencidos de vez en cuando mientras no hay trabajo.
            if idle_cycles % 30 == 0:
                try:
                    self.recover_stale()
                except Exception:
                    logger.exception("stale_recovery_failed")
            self._stop.wait(self._poll_interval)
        logger.info("worker_stopped", extra={"worker_id": self.worker_id})
