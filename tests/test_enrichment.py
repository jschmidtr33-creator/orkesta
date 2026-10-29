"""Búsqueda de dominios de punta a punta: PostgreSQL real, worker real y red simulada."""

from __future__ import annotations

import uuid
from collections.abc import Iterator

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from fakes import FakeFetcher, FakeResolver, page
from orkesta.db.models import (
    Company,
    Domain,
    EnrichmentBatch,
    EnrichmentJob,
    EvidenceRecord,
    Organization,
)
from orkesta.enrichment import jobs
from orkesta.enrichment.service import (
    EngineDeps,
    EnrichmentStateError,
    start_domain_enrichment,
)
from orkesta.imports.reader import ReadLimits
from orkesta.imports.service import coerce_mapping, confirm_import, create_draft_batch, export_rows
from orkesta.worker.__main__ import FAILURE_HOOKS, HANDLERS
from orkesta.worker.queue import RetryPolicy
from orkesta.worker.runner import Worker

pytestmark = pytest.mark.db

IP = ["93.184.216.34"]
CSV = (
    "Nombre,Apellido,Empresa,Ubicación\n"
    'Ana,López,Lácteos Inventados,"Ciudad de México, México"\n'
    'Luis,Pérez,Lácteos Inventados México,"Monterrey, México"\n'
    'Eva,Ruiz,Empresa Fantasma,"Lima, Perú"\n'
).encode()
SITE = page(
    "https://lacteosinventados.com.mx/",
    "<title>Lácteos Inventados</title><body>Lácteos Inventados · Ciudad de México · "
    "Tel. +52 55 0000 0000</body>",
)


@pytest.fixture()
def network() -> Iterator[tuple[FakeResolver, FakeFetcher]]:
    resolver = FakeResolver(
        ips={"lacteosinventados.com.mx": IP},
        mx_hosts={"lacteosinventados.com.mx": ["aspmx.l.google.com"]},
    )
    fetcher = FakeFetcher(pages={"https://lacteosinventados.com.mx/": SITE})
    previous = jobs.engine_factory
    jobs.engine_factory = lambda: EngineDeps(resolver, fetcher, None)
    yield resolver, fetcher
    jobs.engine_factory = previous


def _worker(factory: sessionmaker[Session]) -> Worker:
    return Worker(
        factory,
        HANDLERS,
        retry_policy=RetryPolicy(1, 1),
        poll_interval_seconds=0.01,
        lock_timeout_seconds=600,
        failure_hooks=FAILURE_HOOKS,
    )


def _drain(factory: sessionmaker[Session]) -> int:
    worker, count = _worker(factory), 0
    while worker.run_once():
        count += 1
    return count


def _imported_batch(factory: sessionmaker[Session], org_id: uuid.UUID) -> uuid.UUID:
    with factory.begin() as session:
        batch = create_draft_batch(
            session,
            organization_id=org_id,
            user_id=None,
            filename="leads.csv",
            content=CSV,
            limits=ReadLimits(1 << 20, 1000),
        ).batch
        confirm_import(
            session, batch, coerce_mapping(batch.column_mapping, batch.headers), user_id=None
        )
        batch_id = batch.id
    _drain(factory)
    return batch_id


@pytest.fixture()
def org_id(session_factory: sessionmaker[Session]) -> uuid.UUID:
    with session_factory.begin() as session:
        org = Organization(name="Org", slug=f"org-{uuid.uuid4().hex[:8]}")
        session.add(org)
        session.flush()
        return org.id


def _start(factory: sessionmaker[Session], batch_id: uuid.UUID) -> int:
    with factory.begin() as session:
        batch = session.get(EnrichmentBatch, batch_id)
        assert batch is not None
        return start_domain_enrichment(session, batch, user_id=None)


def _batch(factory: sessionmaker[Session], batch_id: uuid.UUID) -> EnrichmentBatch:
    with factory() as session:
        batch = session.get(EnrichmentBatch, batch_id)
        assert batch is not None
        return batch


def test_name_variants_are_investigated_once(
    session_factory: sessionmaker[Session],
    org_id: uuid.UUID,
    network: tuple[FakeResolver, FakeFetcher],
) -> None:
    batch_id = _imported_batch(session_factory, org_id)
    # Lácteos Inventados (CDMX y Monterrey, mismo país) + Empresa Fantasma (Perú) = 2 grupos.
    assert _start(session_factory, batch_id) == 2
    assert _batch(session_factory, batch_id).status == "queued"
    assert _drain(session_factory) == 2

    batch = _batch(session_factory, batch_id)
    assert batch.status == "completed"
    assert batch.enrichment_summary["statuses"] == {"confirmed": 1, "unknown": 1}

    with session_factory() as session:
        selected = session.scalars(select(Domain).where(Domain.is_selected.is_(True))).all()
        # Las dos variantes de nombre reciben el mismo dominio.
        assert {d.domain for d in selected} == {"lacteosinventados.com.mx"} and len(selected) == 2
        assert selected[0].mx_provider == "google_workspace" and selected[0].status == "confirmed"
        assert "NAME_IN_TITLE" in selected[0].reason_codes
        ghost = session.scalar(select(Company).where(Company.name_normalized == "empresa fantasma"))
        assert ghost is not None and ghost.domain_status == "unknown"
        sources = {e.source_type for e in session.scalars(select(EvidenceRecord))}
        assert sources == {"website", "dns"}


def test_export_includes_domain_columns(
    session_factory: sessionmaker[Session],
    org_id: uuid.UUID,
    network: tuple[FakeResolver, FakeFetcher],
) -> None:
    batch_id = _imported_batch(session_factory, org_id)
    _start(session_factory, batch_id)
    _drain(session_factory)
    with session_factory() as session:
        batch = session.get(EnrichmentBatch, batch_id)
        assert batch is not None
        rows = {r.row_number: r for r in export_rows(session, batch)}
    assert rows[2].domain["domain"] == "lacteosinventados.com.mx"
    assert rows[2].domain["domain_status"] == "confirmado"
    assert rows[2].domain["has_mx"] == "sí"
    assert "título del sitio" in rows[2].domain["domain_reasons"]
    assert rows[4].domain == {"domain_status": "no encontrado"}


def test_recent_result_is_reused_without_network(
    session_factory: sessionmaker[Session],
    org_id: uuid.UUID,
    network: tuple[FakeResolver, FakeFetcher],
) -> None:
    resolver, fetcher = network
    first = _imported_batch(session_factory, org_id)
    _start(session_factory, first)
    _drain(session_factory)
    fetcher.requested.clear()
    resolver.queries.clear()

    second = _imported_batch(session_factory, org_id)
    _start(session_factory, second)
    _drain(session_factory)
    assert _batch(session_factory, second).status == "completed"
    # Lácteos Inventados sale del historial; solo Empresa Fantasma (sin dominio) vuelve a la red.
    assert not any("lacteos" in q for q in resolver.queries)
    assert not any("lacteos" in url for url in fetcher.requested)
    assert _batch(session_factory, second).enrichment_summary["statuses"]["confirmed"] == 1


def test_cannot_start_twice_while_running(
    session_factory: sessionmaker[Session],
    org_id: uuid.UUID,
    network: tuple[FakeResolver, FakeFetcher],
) -> None:
    batch_id = _imported_batch(session_factory, org_id)
    _start(session_factory, batch_id)
    with pytest.raises(EnrichmentStateError):
        _start(session_factory, batch_id)


def test_rerun_after_completion_is_allowed(
    session_factory: sessionmaker[Session],
    org_id: uuid.UUID,
    network: tuple[FakeResolver, FakeFetcher],
) -> None:
    batch_id = _imported_batch(session_factory, org_id)
    _start(session_factory, batch_id)
    _drain(session_factory)
    assert _start(session_factory, batch_id) == 2
    assert _batch(session_factory, batch_id).enrichment_summary["run"] == 2
    _drain(session_factory)
    assert _batch(session_factory, batch_id).status == "completed"


def test_dns_outage_retries_then_marks_error_and_completes(
    session_factory: sessionmaker[Session],
    org_id: uuid.UUID,
    network: tuple[FakeResolver, FakeFetcher],
) -> None:
    batch_id = _imported_batch(session_factory, org_id)

    class Down(FakeResolver):
        def resolve_ips(self, host: str) -> list[str]:
            from orkesta.engine.net.dns import DnsError

            raise DnsError("caído")

    down = Down()
    jobs.engine_factory = lambda: EngineDeps(down, FakeFetcher(), None)
    _start(session_factory, batch_id)
    with session_factory.begin() as session:
        for job in session.scalars(
            select(EnrichmentJob).where(EnrichmentJob.kind == "resolve_domain")
        ):
            job.max_attempts = 1
    _drain(session_factory)

    batch = _batch(session_factory, batch_id)
    assert batch.status == "completed"
    assert batch.enrichment_summary["failed"] == 2
    with session_factory() as session:
        statuses = set(session.scalars(select(Company.domain_status)))
        assert statuses == {"error"}
