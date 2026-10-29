"""Manejadores del worker para la importación de lotes."""

from __future__ import annotations

from sqlalchemy.orm import Session

from orkesta.db.models import EnrichmentJob
from orkesta.imports.service import ImportStateError, mark_import_failed, run_import
from orkesta.worker.runner import PermanentJobError


def handle_import_batch(session: Session, job: EnrichmentJob) -> None:
    try:
        run_import(session, job.batch_id)
    except ImportStateError as exc:
        raise PermanentJobError(str(exc)) from exc


def on_import_failed(session: Session, job: EnrichmentJob) -> None:
    mark_import_failed(session, job.batch_id, job.last_error or "Error desconocido")
