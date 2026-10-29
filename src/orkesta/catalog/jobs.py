"""Manejadores del worker para las cargas de bases de prospectos."""

from __future__ import annotations

import uuid

from sqlalchemy.orm import Session

from orkesta.catalog.service import ProspectUploadError, mark_upload_failed, process_upload
from orkesta.db.models import EnrichmentJob
from orkesta.imports.reader import ImportFileError
from orkesta.worker.runner import PermanentJobError


def _upload_id(job: EnrichmentJob) -> uuid.UUID:
    try:
        return uuid.UUID(str(job.payload["upload_id"]))
    except (KeyError, ValueError) as exc:
        raise PermanentJobError("Trabajo de carga sin identificador válido") from exc


def handle_import_prospects(session: Session, job: EnrichmentJob) -> None:
    try:
        process_upload(session, _upload_id(job))
    except (ProspectUploadError, ImportFileError) as exc:
        raise PermanentJobError(str(exc)) from exc


def on_import_prospects_failed(session: Session, job: EnrichmentJob) -> None:
    mark_upload_failed(session, _upload_id(job), job.last_error or "Error desconocido")
