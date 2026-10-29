"""Importación de punta a punta con PostgreSQL real y el worker de verdad."""

from __future__ import annotations

import uuid
from pathlib import Path

import pytest
from sqlalchemy import func, select
from sqlalchemy.orm import Session, sessionmaker

from orkesta.db.models import (
    AuditLog,
    Company,
    Contact,
    EnrichmentBatch,
    EnrichmentJob,
    ImportRow,
    Organization,
)
from orkesta.imports import jobs
from orkesta.imports.reader import ImportFileError, ReadLimits
from orkesta.imports.service import (
    ImportStateError,
    build_preview,
    coerce_mapping,
    confirm_import,
    create_draft_batch,
    delete_batch,
    export_rows,
)
from orkesta.worker.__main__ import FAILURE_HOOKS, HANDLERS
from orkesta.worker.queue import RetryPolicy
from orkesta.worker.runner import Worker

pytestmark = pytest.mark.db

FIXTURE = (Path(__file__).parent / "fixtures" / "contactos_ejemplo.csv").read_bytes()
LIMITS = ReadLimits(max_bytes=5 * 1024 * 1024, max_rows=1000)


@pytest.fixture()
def org_id(session_factory: sessionmaker[Session]) -> uuid.UUID:
    with session_factory.begin() as session:
        org = Organization(name="Org", slug=f"org-{uuid.uuid4().hex[:8]}")
        session.add(org)
        session.flush()
        return org.id


def _worker(factory: sessionmaker[Session]) -> Worker:
    return Worker(
        factory,
        HANDLERS,
        retry_policy=RetryPolicy(1, 1),
        poll_interval_seconds=0.01,
        lock_timeout_seconds=600,
        failure_hooks=FAILURE_HOOKS,
    )


def _upload(
    factory: sessionmaker[Session], org_id: uuid.UUID, content: bytes = FIXTURE
) -> uuid.UUID:
    with factory.begin() as session:
        result = create_draft_batch(
            session,
            organization_id=org_id,
            user_id=None,
            filename="contactos.csv",
            content=content,
            limits=LIMITS,
        )
        return result.batch.id


def _import(factory: sessionmaker[Session], batch_id: uuid.UUID) -> EnrichmentBatch:
    with factory.begin() as session:
        batch = session.get(EnrichmentBatch, batch_id)
        assert batch is not None
        confirm_import(
            session, batch, coerce_mapping(batch.column_mapping, batch.headers), user_id=None
        )
    assert _worker(factory).run_once() is True
    with factory() as session:
        stored = session.get(EnrichmentBatch, batch_id)
        assert stored is not None
        return stored


def _count(factory: sessionmaker[Session], model: type, **filters: object) -> int:
    with factory() as session:
        query = select(func.count()).select_from(model)
        for key, value in filters.items():
            query = query.where(getattr(model, key) == value)
        return int(session.scalar(query) or 0)


def test_draft_keeps_original_rows_and_proposes_mapping(
    session_factory: sessionmaker[Session], org_id: uuid.UUID
) -> None:
    batch_id = _upload(session_factory, org_id)
    with session_factory() as session:
        batch = session.get(EnrichmentBatch, batch_id)
        assert batch is not None
        assert batch.status == "draft" and batch.total_rows == 9
        assert batch.headers == ["Nombre", "Apellido", "Cargo", "Empresa", "Ubicación"]
        assert batch.column_mapping["company"] == "Empresa"
        first = session.scalar(
            select(ImportRow).where(ImportRow.batch_id == batch_id, ImportRow.row_number == 2)
        )
        assert first is not None
        assert first.raw["Empresa"] == "Bancolombia S.A." and first.status == "pending"
        actions = session.scalars(select(AuditLog.action)).all()
        assert "batch.uploaded" in actions


def test_invalid_file_creates_nothing(
    session_factory: sessionmaker[Session], org_id: uuid.UUID
) -> None:
    with pytest.raises(ImportFileError):
        _upload(session_factory, org_id, b"")
    assert _count(session_factory, EnrichmentBatch) == 0


def test_reupload_of_same_file_is_detected(
    session_factory: sessionmaker[Session], org_id: uuid.UUID
) -> None:
    first = _upload(session_factory, org_id)
    with session_factory.begin() as session:
        result = create_draft_batch(
            session,
            organization_id=org_id,
            user_id=None,
            filename="otra-vez.csv",
            content=FIXTURE,
            limits=LIMITS,
        )
        assert result.previous_batch is not None and result.previous_batch.id == first


def test_preview_does_not_write(session_factory: sessionmaker[Session], org_id: uuid.UUID) -> None:
    batch_id = _upload(session_factory, org_id)
    with session_factory() as session:
        batch = session.get(EnrichmentBatch, batch_id)
        assert batch is not None
        preview = build_preview(session, batch, coerce_mapping(batch.column_mapping, batch.headers))
    assert preview.errors == []
    assert (preview.summary.valid, preview.summary.invalid, preview.summary.duplicate) == (6, 2, 1)
    assert _count(session_factory, Contact) == 0


def test_preview_reports_mapping_errors(
    session_factory: sessionmaker[Session], org_id: uuid.UUID
) -> None:
    batch_id = _upload(session_factory, org_id)
    with session_factory() as session:
        batch = session.get(EnrichmentBatch, batch_id)
        assert batch is not None
        preview = build_preview(session, batch, coerce_mapping({"first_name": "Nombre"}, []))
    assert "Falta asignar la columna de Nombre." in preview.errors


def test_full_import_creates_companies_and_contacts(
    session_factory: sessionmaker[Session], org_id: uuid.UUID
) -> None:
    batch = _import(session_factory, _upload(session_factory, org_id))
    assert batch.status == "imported"
    assert batch.import_summary["valid"] == 6
    assert batch.import_summary["invalid"] == 2
    assert batch.import_summary["duplicate"] == 1
    assert batch.import_summary["contacts_new"] == 6
    assert batch.processed_rows == 7 and batch.failed_rows == 2
    assert _count(session_factory, Contact) == 6
    assert _count(session_factory, Company) == 6

    with session_factory() as session:
        rows = {
            r.row_number: r
            for r in session.scalars(select(ImportRow).where(ImportRow.batch_id == batch.id))
        }
        assert rows[2].status == "valid" and rows[2].normalized is not None
        assert rows[2].normalized["country_code"] == "CO"
        # La fila duplicada apunta al mismo contacto que su original.
        assert rows[9].status == "duplicate" and rows[9].duplicate_of_row == 2
        assert rows[9].contact_id == rows[2].contact_id
        assert rows[7].status == "invalid" and rows[7].contact_id is None
        # Las filas originales no se tocan.
        assert rows[3].raw["Nombre"] == "Ing. Juan Carlos"
        bancolombia = session.scalar(
            select(Company).where(
                Company.name_normalized == "bancolombia", Company.country_code == "CO"
            )
        )
        assert bancolombia is not None and bancolombia.city == "Bogotá"


def test_second_import_reuses_existing_contacts(
    session_factory: sessionmaker[Session], org_id: uuid.UUID
) -> None:
    _import(session_factory, _upload(session_factory, org_id))
    second = _import(session_factory, _upload(session_factory, org_id))
    assert second.import_summary["contacts_new"] == 0
    assert second.import_summary["contacts_existing"] == 6
    assert _count(session_factory, Contact) == 6
    with session_factory() as session:
        row = session.scalar(
            select(ImportRow).where(ImportRow.batch_id == second.id, ImportRow.row_number == 2)
        )
        assert row is not None and "CONTACT_EXISTS" in row.issues


def test_confirm_twice_is_rejected(
    session_factory: sessionmaker[Session], org_id: uuid.UUID
) -> None:
    batch_id = _upload(session_factory, org_id)
    _import(session_factory, batch_id)
    with session_factory.begin() as session:
        batch = session.get(EnrichmentBatch, batch_id)
        assert batch is not None
        with pytest.raises(ImportStateError, match="ya fue confirmado"):
            confirm_import(session, batch, coerce_mapping(batch.column_mapping, batch.headers),
                           user_id=None)  # fmt: skip


def test_confirm_with_invalid_mapping_is_rejected(
    session_factory: sessionmaker[Session], org_id: uuid.UUID
) -> None:
    batch_id = _upload(session_factory, org_id)
    with session_factory.begin() as session:
        batch = session.get(EnrichmentBatch, batch_id)
        assert batch is not None
        with pytest.raises(ImportStateError, match="Empresa"):
            confirm_import(session, batch, coerce_mapping({"first_name": "Nombre",
                           "last_name": "Apellido"}, batch.headers), user_id=None)  # fmt: skip


def test_permanent_failure_marks_batch_failed(
    session_factory: sessionmaker[Session], org_id: uuid.UUID, monkeypatch: pytest.MonkeyPatch
) -> None:
    def broken(_: Session, __: uuid.UUID) -> EnrichmentBatch:
        raise ImportStateError("estado inconsistente")

    monkeypatch.setattr(jobs, "run_import", broken)
    batch = _import(session_factory, _upload(session_factory, org_id))
    assert batch.status == "failed"
    assert batch.error_message is not None and "estado inconsistente" in batch.error_message
    assert _count(session_factory, EnrichmentJob, status="failed") == 1


def test_export_filters(session_factory: sessionmaker[Session], org_id: uuid.UUID) -> None:
    batch = _import(session_factory, _upload(session_factory, org_id))
    with session_factory() as session:
        stored = session.get(EnrichmentBatch, batch.id)
        assert stored is not None
        assert len(list(export_rows(session, stored, "all"))) == 9
        assert len(list(export_rows(session, stored, "valid"))) == 6
        issues = list(export_rows(session, stored, "issues"))
        assert {r.status for r in issues} == {"invalid", "duplicate"}


def test_delete_batch_removes_only_its_own_contacts(
    session_factory: sessionmaker[Session], org_id: uuid.UUID
) -> None:
    first = _import(session_factory, _upload(session_factory, org_id))
    one_row = (
        "Nombre,Apellido,Empresa,Ubicación\n"
        "Ana,Muñoz,Alpina Productos Alimenticios S.A. BIC,Medellín\n"
    ).encode()
    second = _import(session_factory, _upload(session_factory, org_id, one_row))
    assert second.import_summary["contacts_existing"] == 1

    with session_factory.begin() as session:
        stored = session.get(EnrichmentBatch, first.id)
        assert stored is not None
        removed = delete_batch(session, stored, user_id=None)

    # 6 contactos del primer lote; Ana Muñoz sigue en el segundo y se conserva.
    assert removed == 5
    assert _count(session_factory, Contact) == 1
    assert _count(session_factory, ImportRow, batch_id=first.id) == 0
    assert _count(session_factory, Company) == 6
