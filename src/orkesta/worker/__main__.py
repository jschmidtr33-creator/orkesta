"""Punto de entrada del worker: ``python -m orkesta.worker``."""

from __future__ import annotations

from orkesta.config import get_settings
from orkesta.db.session import get_session_factory
from orkesta.logs import configure_logging
from orkesta.worker.queue import RetryPolicy
from orkesta.worker.runner import JobHandler, Worker

# Los manejadores del motor de enriquecimiento se registran aquí a partir de la Etapa 3.
HANDLERS: dict[str, JobHandler] = {}


def main() -> None:
    settings = get_settings()
    configure_logging(settings.log_level)
    worker = Worker(
        get_session_factory(),
        HANDLERS,
        retry_policy=RetryPolicy(settings.job_retry_base_seconds, settings.job_retry_max_seconds),
        poll_interval_seconds=settings.worker_poll_interval_seconds,
        lock_timeout_seconds=settings.job_lock_timeout_seconds,
    )
    worker.install_signal_handlers()
    worker.run_forever()


if __name__ == "__main__":
    main()
