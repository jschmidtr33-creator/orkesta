"""Correos de punta a punta: dominio → patrón publicado → correo por contacto → exportación.

PostgreSQL real, worker real y red simulada. Comprueba también que no se guardan las direcciones
de terceros publicadas en el sitio: solo patrones y conteos.
"""

from __future__ import annotations

import re
import uuid
from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import Engine, select
from sqlalchemy.orm import Session, sessionmaker

from fakes import FakeFetcher, FakeResolver, page
from orkesta.api.main import create_app
from orkesta.auth import create_user
from orkesta.db.models import (
    Contact,
    Domain,
    EmailCandidate,
    EmailPatternObservation,
    EnrichmentBatch,
    Organization,
)
from orkesta.enrichment import jobs
from orkesta.enrichment.service import EngineDeps, start_domain_enrichment
from orkesta.imports.export import ORKESTA_COLUMNS
from orkesta.imports.reader import ReadLimits
from orkesta.imports.service import coerce_mapping, confirm_import, create_draft_batch, export_rows
from orkesta.worker.__main__ import FAILURE_HOOKS, HANDLERS
from orkesta.worker.queue import RetryPolicy
from orkesta.worker.runner import Worker

pytestmark = pytest.mark.db

DOMAIN = "lacteosinventados.com.mx"
CSV = (
    "Nombre,Apellido,Cargo,Empresa,Ubicación\n"
    'Ana,López,Directora de Compras,Lácteos Inventados,"Ciudad de México, México"\n'
    'Mariana,Torres,Gerente Comercial,Lácteos Inventados,"Ciudad de México, México"\n'
    'Luis,P.,Analista,Lácteos Inventados,"Ciudad de México, México"\n'
).encode()
HOME = page(
    f"https://{DOMAIN}/",
    "<title>Lácteos Inventados</title><body>Lácteos Inventados · Ciudad de México · "
    'Tel. +52 55 0000 0000 <a href="/contacto">Contacto</a></body>',
)
CONTACT = page(
    f"https://{DOMAIN}/contacto",
    "<body><h1>Contacto</h1>"
    f"<p>Ventas: ventas@{DOMAIN}</p>"
    f"<p>Mariana Torres, Gerente Comercial: mariana.torres@{DOMAIN}</p>"
    f"<p>Jorge Díaz, Logística: jorge.diaz@{DOMAIN}</p>"
    f"<p>Sofía Gómez, Recursos Humanos: sofia.gomez@{DOMAIN}</p></body>",
)


@pytest.fixture()
def network() -> Iterator[FakeFetcher]:
    resolver = FakeResolver(
        ips={DOMAIN: ["93.184.216.34"]}, mx_hosts={DOMAIN: ["aspmx.l.google.com"]}
    )
    fetcher = FakeFetcher(pages={f"https://{DOMAIN}/": HOME, f"https://{DOMAIN}/contacto": CONTACT})
    previous = jobs.engine_factory
    jobs.engine_factory = lambda: EngineDeps(resolver, fetcher, None)
    yield fetcher
    jobs.engine_factory = previous


def _drain(factory: sessionmaker[Session]) -> None:
    worker = Worker(
        factory,
        HANDLERS,
        retry_policy=RetryPolicy(1, 1),
        poll_interval_seconds=0.01,
        lock_timeout_seconds=600,
        failure_hooks=FAILURE_HOOKS,
    )
    while worker.run_once():
        pass


def _enriched_batch(factory: sessionmaker[Session]) -> tuple[uuid.UUID, uuid.UUID]:
    with factory.begin() as session:
        org = Organization(name="Org", slug=f"org-{uuid.uuid4().hex[:8]}")
        session.add(org)
        session.flush()
        batch = create_draft_batch(
            session,
            organization_id=org.id,
            user_id=None,
            filename="leads.csv",
            content=CSV,
            limits=ReadLimits(1 << 20, 1000),
        ).batch
        confirm_import(
            session, batch, coerce_mapping(batch.column_mapping, batch.headers), user_id=None
        )
        org_id, batch_id = org.id, batch.id
    _drain(factory)
    with factory.begin() as session:
        batch = session.get(EnrichmentBatch, batch_id)
        assert batch is not None
        start_domain_enrichment(session, batch, user_id=None)
    _drain(factory)
    return org_id, batch_id


def _emails(session: Session, first_name: str) -> list[EmailCandidate]:
    return list(
        session.scalars(
            select(EmailCandidate)
            .join(Contact, Contact.id == EmailCandidate.contact_id)
            .where(Contact.first_name_original == first_name)
            .order_by(EmailCandidate.rank)
        )
    )


def test_pattern_from_published_emails_generates_contact_emails(
    session_factory: sessionmaker[Session], network: FakeFetcher
) -> None:
    _, batch_id = _enriched_batch(session_factory)
    with session_factory() as session:
        domain = session.scalar(select(Domain).where(Domain.is_selected.is_(True)))
        assert domain is not None and domain.domain == DOMAIN
        assert domain.email_domain == DOMAIN
        assert domain.email_pattern == "first.last"
        assert domain.pattern_confidence_level == "high"

        ana = _emails(session, "Ana")
        assert ana[0].address == f"ana.lopez@{DOMAIN}"
        assert ana[0].label == "inferred" and ana[0].confidence_level == "high"
        assert len(ana) == 3 and ana[1].confidence_level == "low"

        # Su propio correo publicado va primero y marcado como tal.
        mariana = _emails(session, "Mariana")
        assert mariana[0].address == f"mariana.torres@{DOMAIN}"
        assert mariana[0].label == "publicly_found"

        # Apellido con inicial: no se inventa un correo.
        assert _emails(session, "Luis") == []

        # Solo patrones y conteos de lo publicado; nunca direcciones de terceros.
        observations = list(session.scalars(select(EmailPatternObservation)))
        assert {o.pattern for o in observations} == {"first.last"}
        assert observations[0].observation_count == 3
        stored = {e.address for e in session.scalars(select(EmailCandidate))}
        assert f"jorge.diaz@{DOMAIN}" not in stored
        assert f"ventas@{DOMAIN}" not in stored

        batch = session.get(EnrichmentBatch, batch_id)
        assert batch is not None
        assert batch.enrichment_summary["patterns"] == {"high": 1}

        keys = {key for key, _ in ORKESTA_COLUMNS}
        rows = {r.row_number: r for r in export_rows(session, batch)}
        values = rows[min(rows)].domain
        assert set(values) <= keys
        assert values["email"] == f"ana.lopez@{DOMAIN}"
        assert values["email_confidence"].startswith("alta")
        assert values["email_pattern"]
        assert values["email_alternatives"].count("@") == 2


def test_pattern_is_reused_without_visiting_the_site_again(
    session_factory: sessionmaker[Session], network: FakeFetcher
) -> None:
    org_id, _ = _enriched_batch(session_factory)
    network.requested.clear()
    with session_factory.begin() as session:
        batch = create_draft_batch(
            session,
            organization_id=org_id,
            user_id=None,
            filename="otra.csv",
            content=CSV,
            limits=ReadLimits(1 << 20, 1000),
        ).batch
        confirm_import(
            session, batch, coerce_mapping(batch.column_mapping, batch.headers), user_id=None
        )
        second = batch.id
    _drain(session_factory)
    with session_factory.begin() as session:
        batch = session.get(EnrichmentBatch, second)
        assert batch is not None
        start_domain_enrichment(session, batch, user_id=None)
    _drain(session_factory)
    assert network.requested == []
    with session_factory() as session:
        assert _emails(session, "Ana")[0].address == f"ana.lopez@{DOMAIN}"


def test_batch_page_shows_emails_and_pattern_summary(
    engine: Engine, session_factory: sessionmaker[Session], network: FakeFetcher
) -> None:
    org_id, batch_id = _enriched_batch(session_factory)
    with session_factory.begin() as session:
        create_user(
            session, email="jenn@ejemplo.com", password="clave-de-prueba-1", organization_id=org_id
        )
    with TestClient(create_app(engine_provider=lambda: engine), follow_redirects=False) as client:
        token = re.search(r'name="csrf_token" value="([^"]+)"', client.get("/login").text)
        assert token
        client.post(
            "/login",
            data={
                "email": "jenn@ejemplo.com",
                "password": "clave-de-prueba-1",
                "csrf_token": token.group(1),
            },
        )
        html = client.get(f"/lotes/{batch_id}").text
    assert "Patrón de correo por empresa" in html
    assert f"ana.lopez@{DOMAIN}" in html
    assert "confianza alta" in html
    assert "publicado" in html
