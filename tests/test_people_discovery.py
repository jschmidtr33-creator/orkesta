"""Descubrimiento de personas en sitios públicos (datos inventados, red simulada)."""

from __future__ import annotations

import json
import re
import uuid
from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import Engine, select
from sqlalchemy.orm import Session, sessionmaker

from fakes import FakeFetcher, FakeResolver, page
from orkesta.api.main import create_app
from orkesta.auth import create_user
from orkesta.catalog import jobs as catalog_jobs
from orkesta.catalog.discovery import DiscoveryError, candidate_companies, start_discovery
from orkesta.catalog.prospects import records_from_file
from orkesta.catalog.service import ProspectFilters, refresh_domain_patterns, upsert_prospects
from orkesta.db.models import DiscoveryRun, Organization, Prospect, WebDiscovery
from orkesta.engine.people.discover import discover_people
from orkesta.engine.people.extract import extract_people, person_name
from orkesta.enrichment.service import EngineDeps
from orkesta.worker.__main__ import FAILURE_HOOKS, HANDLERS
from orkesta.worker.queue import RetryPolicy
from orkesta.worker.runner import Worker

DOMAIN = "lacteos.com.mx"
TEAM_HTML = """
<html><head>
<script type="application/ld+json">{"@context":"https://schema.org","@type":"Organization",
 "employee":[{"@type":"Person","name":"Laura Gómez","jobTitle":"Chief Financial Officer"}]}</script>
</head><body>
<nav>Inicio Nosotros Equipo Contacto</nav>
<h2>Nuestro equipo directivo</h2>
<div class="card"><h3>Mariana Torres</h3><p>Directora de Compras</p></div>
<div class="card"><strong>Director General</strong><span>Carlos Méndez Ruiz</span></div>
<p>Ana López, Gerente de Compras</p>
<p>Servicios Integrales</p><p>Gerente de Ventas</p>
<p>Ciudad de México</p><p>Atención al cliente</p>
</body></html>
"""
PRESS_HTML = (
    "<p>Lácteos Inventados designó a Roberto Silva como nuevo director de tecnología para "
    "México.</p>"
)
HOME_HTML = (
    "<title>Lácteos Inventados</title><body>Lácteos Inventados "
    '<a href="/nosotros/equipo">Nuestro equipo</a> <a href="/prensa/nombramiento">Prensa</a> '
    '<a href="/productos">Productos</a></body>'
)


# --- Extracción ------------------------------------------------------------------------------


def test_extracts_people_from_structured_data_cards_and_text() -> None:
    people = {p.full_name: p for p in extract_people(TEAM_HTML, "https://x/equipo")}
    assert people["Laura Gómez"].title == "Chief Financial Officer"
    assert people["Laura Gómez"].method == "jsonld"
    assert people["Mariana Torres"].title == "Directora de Compras"
    assert people["Carlos Méndez Ruiz"].title == "Director General"  # cargo antes del nombre
    assert people["Ana López"].title == "Gerente de Compras"
    assert set(people) == {"Laura Gómez", "Mariana Torres", "Carlos Méndez Ruiz", "Ana López"}


def test_press_appointment_trims_the_tail() -> None:
    people = extract_people(PRESS_HTML, "https://x/prensa")
    assert [(p.full_name, p.title, p.method) for p in people] == [
        ("Roberto Silva", "director de tecnología", "press")
    ]


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("Mariana Torres", ("Mariana", "Torres")),
        ("Ing. Juan Pérez", ("Juan", "Pérez")),
        ("José de la Cruz", ("José", "de la Cruz")),
        ("María José Pérez López", ("María José", "Pérez López")),
        ("Servicios Integrales", None),
        ("Director General", None),
        ("Ciudad de México", None),
        ("Mariana", None),
        ("MARIANA torres", None),
        ("Juan Pérez 2024", None),
    ],
)
def test_person_name(text: str, expected: tuple[str, str] | None) -> None:
    assert person_name(text) == expected


def test_discover_follows_people_pages_and_respects_robots() -> None:
    fetcher = FakeFetcher(
        pages={
            f"https://{DOMAIN}/": page(f"https://{DOMAIN}/", HOME_HTML),
            f"https://{DOMAIN}/nosotros/equipo": page(
                f"https://{DOMAIN}/nosotros/equipo", TEAM_HTML
            ),
            f"https://{DOMAIN}/prensa/nombramiento": page(
                f"https://{DOMAIN}/prensa/nombramiento", PRESS_HTML
            ),
        },
        disallowed={f"https://{DOMAIN}/productos"},
    )
    found = discover_people(DOMAIN, fetcher)
    assert {p.full_name for p in found.people} >= {"Mariana Torres", "Roberto Silva"}
    assert f"https://{DOMAIN}/productos" not in fetcher.requested
    source = next(p for p in found.people if p.full_name == "Mariana Torres")
    assert source.url == f"https://{DOMAIN}/nosotros/equipo"


# --- Flujo completo --------------------------------------------------------------------------

pytest_db = pytest.mark.db
BASE = (
    "email,first_name,last_name,title,company,country,company_industry\n"
    "ana.lopez@lacteos.com.mx,Ana,López,Gerente de Compras,Lácteos Inventados,Mexico,Dairy\n"
    "jorge.diaz@lacteos.com.mx,Jorge,Díaz,Director de TI,Lácteos Inventados,Mexico,Dairy\n"
    "luis@gmail.com,Luis,Ruiz,CEO,Otra Empresa,Mexico,Retail\n"
).encode()


@pytest.fixture()
def network() -> Iterator[FakeFetcher]:
    fetcher = FakeFetcher(
        pages={
            f"https://{DOMAIN}/": page(f"https://{DOMAIN}/", HOME_HTML),
            f"https://{DOMAIN}/nosotros/equipo": page(
                f"https://{DOMAIN}/nosotros/equipo", TEAM_HTML
            ),
            f"https://{DOMAIN}/prensa/nombramiento": page(
                f"https://{DOMAIN}/prensa/nombramiento", PRESS_HTML
            ),
        }
    )
    previous = catalog_jobs.engine_factory
    catalog_jobs.engine_factory = lambda: EngineDeps(FakeResolver(), fetcher, None)
    yield fetcher
    catalog_jobs.engine_factory = previous


@pytest.fixture()
def org_id(session_factory: sessionmaker[Session]) -> uuid.UUID:
    with session_factory.begin() as session:
        org = Organization(name="Org", slug=f"org-{uuid.uuid4().hex[:8]}")
        session.add(org)
        session.flush()
        upsert_prospects(session, org.id, records_from_file(BASE, "base.csv"), source="base")
        refresh_domain_patterns(session, org.id, [DOMAIN])
        return org.id


def _drain(factory: sessionmaker[Session]) -> None:
    worker = Worker(
        factory, HANDLERS, retry_policy=RetryPolicy(1, 1), poll_interval_seconds=0.01,
        lock_timeout_seconds=600, failure_hooks=FAILURE_HOOKS,
    )  # fmt: skip
    while worker.run_once():
        pass


@pytest_db
def test_candidate_companies_use_corporate_domains_only(
    session_factory: sessionmaker[Session], org_id: uuid.UUID
) -> None:
    with session_factory() as session:
        companies = candidate_companies(session, org_id, ProspectFilters(countries=["MX"]))
    assert [c["email_domain"] for c in companies] == [DOMAIN]  # gmail.com nunca
    assert companies[0]["people"] == 2


@pytest_db
def test_discovery_adds_new_people_with_inferred_emails(
    session_factory: sessionmaker[Session], org_id: uuid.UUID, network: FakeFetcher
) -> None:
    with session_factory.begin() as session:
        run_id = start_discovery(
            session, organization_id=org_id, user_id=None, filters=ProspectFilters()
        ).id
    _drain(session_factory)
    with session_factory() as session:
        run = session.get(DiscoveryRun, run_id)
        assert run is not None and run.status == "completed" and run.done == 1
        web = {
            p.full_name: p
            for p in session.scalars(
                select(Prospect).where(Prospect.organization_id == org_id, Prospect.source == "web")
            )
        }
        # Ana López ya estaba en la base: no se duplica.
        assert "Ana López" not in web
        assert {"Mariana Torres", "Laura Gómez", "Carlos Méndez Ruiz", "Roberto Silva"} <= set(web)
        mariana = web["Mariana Torres"]
        assert mariana.email == f"mariana.torres@{DOMAIN}"  # patrón real aprendido de la base
        assert mariana.email_status == "inferred" and (mariana.email_confidence or 0) >= 70
        assert (mariana.level_code, mariana.area_code) == ("director", "compras")
        assert mariana.source_name == f"https://{DOMAIN}/nosotros/equipo"
        assert run.people_found == len(web)
        discovery = session.scalar(
            select(WebDiscovery).where(WebDiscovery.organization_id == org_id)
        )
        assert discovery is not None and discovery.people_found >= 5
    # Lo deducido nunca enseña patrones: el patrón sigue saliendo solo de correos reales.
    with session_factory.begin() as session:
        assert refresh_domain_patterns(session, org_id, [DOMAIN]) == 1
    # Explorada hace menos de 30 días: no se repite.
    with session_factory.begin() as session, pytest.raises(DiscoveryError):
        start_discovery(session, organization_id=org_id, user_id=None, filters=ProspectFilters())


@pytest_db
def test_discovery_from_the_page(
    engine: Engine, session_factory: sessionmaker[Session], org_id: uuid.UUID, network: FakeFetcher
) -> None:
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
        token = re.search(r'name="csrf_token" value="([^"]+)"', client.get("/prospectos").text)
        assert token
        response = client.post(
            "/prospectos/descubrir", data={"csrf_token": token.group(1), "pais": "MX"}
        )
        assert response.status_code == 303
        _drain(session_factory)
        html = client.get("/prospectos", params={"web": "1"}).text
    assert "Mariana Torres" in html and "deducido" in html
    assert "Ana López" not in html  # solo encontrados en la web


def test_people_evaluation_reports_only_counts(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from orkesta.evaluation import people as evaluation

    fetcher = FakeFetcher(
        pages={
            f"https://{DOMAIN}/": page(f"https://{DOMAIN}/", HOME_HTML),
            f"https://{DOMAIN}/nosotros/equipo": page(
                f"https://{DOMAIN}/nosotros/equipo", TEAM_HTML
            ),
        }
    )
    monkeypatch.setattr(
        evaluation, "default_engine", lambda: EngineDeps(FakeResolver(), fetcher, None)
    )
    dataset = tmp_path / "d.csv"
    dataset.write_text(
        f"empresa,ubicacion,dominio_esperado\nLácteos,México,{DOMAIN}\nOtra,México,otra.com\n"
    )
    assert evaluation.main([str(dataset), "--out", str(tmp_path / "out"), "--workers", "1"]) == 0
    report = (tmp_path / "out" / "reporte.md").read_text() + (
        tmp_path / "out" / "metricas.json"
    ).read_text()
    assert "Mariana" not in report and "Torres" not in report  # nunca nombres
    metrics = json.loads((tmp_path / "out" / "metricas.json").read_text())
    assert metrics["empresas"] == 2 and metrics["con_personas"] == 1
