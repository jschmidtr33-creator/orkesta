"""Punto de entrada del worker: ``python -m orkesta.worker``."""

from __future__ import annotations

from orkesta.config import get_settings
from orkesta.db.session import get_session_factory
from orkesta.imports.jobs import handle_import_batch, on_import_failed
from orkesta.imports.service import IMPORT_JOB_KIND
from orkesta.logs import configure_logging
from orkesta.worker.queue import RetryPolicy
from orkesta.worker.runner import FailureHook, JobHandler, Worker

HANDLERS: dict[str, JobHandler] = {IMPORT_JOB_KIND: handle_import_batch}
FAILURE_HOOKS: dict[str, FailureHook] = {IMPORT_JOB_KIND: on_import_failed}


def build_worker() -> Worker:
    settings = get_settings()
    return Worker(
        get_session_factory(),
        HANDLERS,
        retry_policy=RetryPolicy(settings.job_retry_base_seconds, settings.job_retry_max_seconds),
        poll_interval_seconds=settings.worker_poll_interval_seconds,
        lock_timeout_seconds=settings.job_lock_timeout_seconds,
        failure_hooks=FAILURE_HOOKS,
    )


def main() -> None:
    configure_logging(get_settings().log_level)
    worker = build_worker()
    worker.install_signal_handlers()
    worker.run_forever()


if __name__ == "__main__":
    main()
