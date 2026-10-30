"""Datos públicos: directivos de Wikidata, carga, reparto a las organizaciones y búsqueda web
automática al filtrar."""

from __future__ import annotations

import json
import re
import uuid
from collections.abc import Iterator
from typing import Any

import httpx
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import Engine, select
from sqlalchemy.orm import Session, sessionmaker

from orkesta.api.main import create_app
from orkesta.auth import create_user
from orkesta.catalog import public_data
from orkesta.catalog.companies import load_catalog
from orkesta.catalog.discovery import ensure_discovery
from orkesta.catalog.public_people import parse_bindings
from orkesta.catalog.service import ProspectFilters
from orkesta.db.models import CatalogPerson, DiscoveryRun, EnrichmentJob, Organization, Prospect

pytest_db = pytest.mark.db


def _binding(person: str, name: str, company: str, label: str, site: str = "") -> dict[str, Any]:
    row: dict[str, Any] = {
        "person": {"value": f"http://www.wikidata.org/entity/{person}"},
        "personLabel": {"value": name},
        "company": {"value": f"http://www.wikidata.org/entity/{company}"},
        "companyLabel": {"value": label},
    }
    if site:
        row["website"] = {"value": site}
    return row


def test_parse_bindings_keeps_readable_people_with_site() -> None:
    people = parse_bindings(
        [
            _binding("Q1", "Ana Inventada López", "Q10", "Lácteos Inventados"),
            _binding(
                "Q1",
                "Ana Inventada López",
                "Q10",
                "Lácteos Inventados",
                "https://www.lacteos-inv.com.mx/",
            ),
            _binding("Q2", "Q2", "Q10", "Lácteos Inventados"),  # sin etiqueta
            _binding("Q3", "Madonna", "Q11", "Otra"),  # un solo nombre: no sirve para correos
            _binding("Q4", "Luis Ficticio", "Q12", "Gobierno X", "https://www.algo.gob.mx"),
        ],
        "CEO",
        "MX",
    )
    by_qid = {p.person_qid: p for p in people}
    assert set(by_qid) == {"Q1", "Q4"}
    assert by_qid["Q1"].domain == "lacteos-inv.com.mx" and by_qid["Q1"].title == "CEO"
    assert by_qid["Q4"].domain == ""  # sector público: sin dominio corporativo


def _fake_client(bindings: list[dict[str, Any]]) -> httpx.Client:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=json.dumps({"results": {"bindings": bindings}}))

    return httpx.Client(transport=httpx.MockTransport(handler))


@pytest_db
def test_people_job_loads_and_syncs_into_every_organization(
    session_factory: sessionmaker[Session],
) -> None:
    with session_factory.begin() as session:
        orgs = [Organization(name=f"Org {i}", slug=f"org-{uuid.uuid4().hex[:8]}") for i in range(2)]
        session.add_all(orgs)
        session.flush()
        load_catalog(
            session,
            [{"qid": "Q10", "name": "Lácteos Inventados", "country": "MX", "city": "Monterrey",
              "domain": "lacteos-inv.com.mx", "industry_code": "alimentos_bebidas",
              "industry_labels": "lácteos", "employees": "", "size_band": ""}],
        )  # fmt: skip
        bindings = [
            _binding("Q1", "Ana López", "Q10", "Lácteos Inventados", "https://lacteos-inv.com.mx")
        ]
        added = public_data.run_people_job(
            session, {"country": "MX", "query": 0}, client_factory=lambda: _fake_client(bindings)
        )
        assert added == 2  # una persona en cada organización
        # Repetir no duplica.
        again = public_data.run_people_job(
            session, {"country": "MX", "query": 0}, client_factory=lambda: _fake_client(bindings)
        )
        assert again == 0
        assert session.scalar(select(CatalogPerson.title)) == "CEO"
        ana = session.scalar(select(Prospect).where(Prospect.organization_id == orgs[0].id))
        assert ana is not None
        assert (ana.full_name, ana.level_code, ana.source) == ("Ana López", "c_level", "wikidata")
        assert ana.email and ana.email.endswith("@lacteos-inv.com.mx")
        assert ana.email_status == "inferred" and ana.city == "Monterrey"
        assert ana.industry_code == "alimentos_bebidas"
        assert ana.source_name == "https://www.wikidata.org/wiki/Q1"


@pytest_db
def test_schedule_refresh_is_staggered_and_not_repeated(
    session_factory: sessionmaker[Session],
) -> None:
    with session_factory.begin() as session:
        session.add(Organization(name="Org", slug=f"org-{uuid.uuid4().hex[:8]}"))
        session.flush()
        queued = public_data.schedule_refresh(session)
        assert queued > 100
        assert public_data.schedule_refresh(session) == 0  # ya hay trabajos pendientes
        times = list(
            session.scalars(select(EnrichmentJob.run_after).order_by(EnrichmentJob.run_after))
        )
        assert (times[-1] - times[0]).total_seconds() >= (queued - 1) * 3


@pytest_db
def test_ensure_discovery_reuses_recent_run_for_same_filters(
    session_factory: sessionmaker[Session],
) -> None:
    with session_factory.begin() as session:
        org = Organization(name="Org", slug=f"org-{uuid.uuid4().hex[:8]}")
        session.add(org)
        session.flush()
        load_catalog(
            session,
            [{"qid": f"Q{i}", "name": f"Banco {i}", "country": "CO", "city": "",
              "domain": f"banco{i}.com.co", "industry_code": "banca_finanzas",
              "industry_labels": "", "employees": "", "size_band": ""} for i in range(5)],
        )  # fmt: skip
        filters = ProspectFilters(industries=["banca_finanzas"], countries=["CO"], title="gerente")
        first = ensure_discovery(session, organization_id=org.id, user_id=None, filters=filters)
        assert first is not None and first.total == 5
        same = ensure_discovery(session, organization_id=org.id, user_id=None, filters=filters)
        assert same is not None and same.id == first.id
        other = ProspectFilters(industries=["banca_finanzas"], countries=["CO"])
        # Mismas empresas ya en cola para esta organización → otra búsqueda no tiene qué explorar
        # hasta que terminen; la primera sigue siendo la referencia para esos filtros.
        second = ensure_discovery(session, organization_id=org.id, user_id=None, filters=other)
        assert second is None or second.id != first.id


@pytest.fixture()
def client(engine: Engine, session_factory: sessionmaker[Session]) -> Iterator[TestClient]:
    with session_factory.begin() as session:
        org = Organization(name="Org", slug=f"org-{uuid.uuid4().hex[:8]}")
        session.add(org)
        session.flush()
        create_user(
            session, email="jenn@ejemplo.com", password="clave-de-prueba-1", organization_id=org.id
        )
        load_catalog(
            session,
            [{"qid": "Q1", "name": "Banco Ficticio", "country": "CO", "city": "",
              "domain": "bancoficticio.com.co", "industry_code": "banca_finanzas",
              "industry_labels": "", "employees": "", "size_band": ""}],
        )  # fmt: skip
    with TestClient(create_app(engine_provider=lambda: engine), follow_redirects=False) as c:
        token = re.search(r'name="csrf_token" value="([^"]+)"', c.get("/login").text)
        assert token
        c.post(
            "/login",
            data={
                "email": "jenn@ejemplo.com",
                "password": "clave-de-prueba-1",
                "csrf_token": token.group(1),
            },
        )
        yield c


@pytest_db
def test_filtering_starts_web_search_and_reports_progress(
    client: TestClient, session_factory: sessionmaker[Session]
) -> None:
    page = client.get(
        "/prospectos", params={"buscar": "1", "industria": "banca_finanzas", "pais": "CO"}
    ).text
    assert "Buscando en la web" in page and 'id="live-panel"' in page
    with session_factory() as session:
        run = session.scalar(select(DiscoveryRun))
        assert run is not None and run.total == 1
    status = client.get(f"/prospectos/busqueda/{run.id}").json()
    assert status == {"done": 0, "total": 1, "people_found": 0, "running": True}
    assert client.get(f"/prospectos/busqueda/{uuid.uuid4()}").status_code == 404
    # Volver a la página con los mismos filtros no crea otra búsqueda.
    client.get("/prospectos", params={"buscar": "1", "industria": "banca_finanzas", "pais": "CO"})
    with session_factory() as session:
        assert len(session.scalars(select(DiscoveryRun)).all()) == 1


@pytest_db
def test_first_visit_does_not_start_web_search(
    client: TestClient, session_factory: sessionmaker[Session]
) -> None:
    page = client.get("/prospectos").text
    assert "Empieza con un filtro" in page
    with session_factory() as session:
        assert session.scalar(select(DiscoveryRun)) is None
