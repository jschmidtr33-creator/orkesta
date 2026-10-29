"""Punto de entrada del worker: ``python -m orkesta.worker``."""

from __future__ import annotations

from sqlalchemy.orm import Session, sessionmaker

from orkesta.campaigns.jobs import handle_campaign_tick
from orkesta.campaigns.service import TICK_JOB_KIND
from orkesta.catalog.discovery import DISCOVERY_JOB_KIND
from orkesta.catalog.jobs import (
    handle_discover_people,
    handle_import_prospects,
    on_discover_people_failed,
    on_import_prospects_failed,
)
from orkesta.catalog.service import UPLOAD_JOB_KIND
from orkesta.config import get_settings
from orkesta.db.session import get_session_factory
from orkesta.enrichment.jobs import handle_resolve_domain, on_resolve_domain_failed
from orkesta.enrichment.service import DOMAIN_JOB_KIND
from orkesta.imports.jobs import handle_import_batch, on_import_failed
from orkesta.imports.service import IMPORT_JOB_KIND
from orkesta.logs import configure_logging
from orkesta.worker.queue import RetryPolicy
from orkesta.worker.runner import FailureHook, JobHandler, Worker

HANDLERS: dict[str, JobHandler] = {
    IMPORT_JOB_KIND: handle_import_batch,
    DOMAIN_JOB_KIND: handle_resolve_domain,
    UPLOAD_JOB_KIND: handle_import_prospects,
    DISCOVERY_JOB_KIND: handle_discover_people,
    TICK_JOB_KIND: handle_campaign_tick,
}
FAILURE_HOOKS: dict[str, FailureHook] = {
    IMPORT_JOB_KIND: on_import_failed,
    DOMAIN_JOB_KIND: on_resolve_domain_failed,
    UPLOAD_JOB_KIND: on_import_prospects_failed,
    DISCOVERY_JOB_KIND: on_discover_people_failed,
}


def build_worker(session_factory: sessionmaker[Session] | None = None) -> Worker:
    settings = get_settings()
    return Worker(
        session_factory or get_session_factory(),
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
