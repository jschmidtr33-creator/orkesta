"""Manejadores del worker para las cargas de bases de prospectos."""

from __future__ import annotations

import uuid

import httpx
from sqlalchemy.orm import Session

from orkesta.catalog.discovery import advance_run, run_discovery_job
from orkesta.catalog.service import ProspectUploadError, mark_upload_failed, process_upload
from orkesta.db.models import EnrichmentJob
from orkesta.enrichment.service import EngineFactory, default_engine
from orkesta.imports.reader import ImportFileError
from orkesta.worker.runner import PermanentJobError, RetryableJobError

# Se puede sustituir en pruebas por un motor con red simulada.
engine_factory: EngineFactory = default_engine


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


def _run_id(job: EnrichmentJob) -> uuid.UUID:
    try:
        return uuid.UUID(str(job.payload["run_id"]))
    except (KeyError, ValueError) as exc:
        raise PermanentJobError("Trabajo de descubrimiento sin identificador válido") from exc


def handle_discover_people(session: Session, job: EnrichmentJob) -> None:
    run_id = _run_id(job)
    added = run_discovery_job(
        session,
        organization_id=job.organization_id,
        payload=job.payload,
        fetcher_factory=lambda: engine_factory().fetcher,
    )
    advance_run(session, run_id, added)


def on_discover_people_failed(session: Session, job: EnrichmentJob) -> None:
    advance_run(session, _run_id(job), 0, failed=True)


def handle_public_companies(session: Session, job: EnrichmentJob) -> None:
    from orkesta.catalog.public_data import run_companies_job

    try:
        run_companies_job(session, job.payload)
    except (httpx.HTTPError, ValueError) as exc:
        raise RetryableJobError(f"Wikidata no respondió: {exc}") from exc


def handle_public_people(session: Session, job: EnrichmentJob) -> None:
    from orkesta.catalog.public_data import run_people_job

    try:
        run_people_job(session, job.payload)
    except (httpx.HTTPError, ValueError) as exc:
        raise RetryableJobError(f"Wikidata no respondió: {exc}") from exc
