"""Pruebas contra PostgreSQL real.

La base se crea por sesión de pruebas y las migraciones corren como superusuario. La aplicación
se conecta con un rol SIN privilegios de superusuario, como en Render: así Row-Level Security
se aplica de verdad (un superusuario la saltaría y las pruebas de aislamiento no probarían nada).
"""

from __future__ import annotations

import os
import uuid
from collections.abc import Iterator

import psycopg
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import Engine
from sqlalchemy.orm import Session, sessionmaker

from orkesta.cli import alembic_config
from orkesta.core.config import get_settings
from orkesta.core.db import make_engine
from orkesta.main import create_app

ADMIN_URL = os.environ.get("TEST_DATABASE_URL", "postgresql://postgres@localhost:5432/postgres")
APP_ROLE, APP_PASSWORD = "orkesta_app_test", "clave-del-rol-de-pruebas"


def _plain(url: str) -> str:
    return url.replace("postgresql+psycopg://", "postgresql://")


@pytest.fixture(scope="session")
def database() -> Iterator[tuple[str, str]]:
    name = f"orkesta_test_{uuid.uuid4().hex[:8]}"
    with psycopg.connect(_plain(ADMIN_URL), autocommit=True) as conn:
        conn.execute(f'CREATE DATABASE "{name}"')
        exists = conn.execute("SELECT 1 FROM pg_roles WHERE rolname = %s", (APP_ROLE,)).fetchone()
        if not exists:
            conn.execute(f"CREATE ROLE {APP_ROLE} LOGIN PASSWORD '{APP_PASSWORD}'")
    base = _plain(ADMIN_URL).rsplit("/", 1)[0]
    admin_url = f"{base}/{name}"
    from alembic import command

    command.upgrade(alembic_config(admin_url), "head")
    with psycopg.connect(admin_url, autocommit=True) as conn:
        conn.execute(f"GRANT USAGE ON SCHEMA orkesta TO {APP_ROLE}")
        conn.execute(
            f"GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA orkesta TO {APP_ROLE}"
        )
    host = base.split("@", 1)[1]
    app_url = f"postgresql+psycopg://{APP_ROLE}:{APP_PASSWORD}@{host}/{name}"
    yield admin_url, app_url
    with psycopg.connect(_plain(ADMIN_URL), autocommit=True) as conn:
        conn.execute(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)')


@pytest.fixture(scope="session")
def engine(database: tuple[str, str]) -> Iterator[Engine]:
    get_settings.cache_clear()
    engine = make_engine(database[1])
    yield engine
    engine.dispose()


@pytest.fixture()
def session_factory(engine: Engine, database: tuple[str, str]) -> Iterator[sessionmaker[Session]]:
    yield sessionmaker(bind=engine, expire_on_commit=False)
    with psycopg.connect(database[0], autocommit=True) as conn:
        conn.execute(
            "TRUNCATE orkesta.people_index, orkesta.people_optout, orkesta.company_directory, orkesta.pattern_stats, orkesta.bounced_addresses, orkesta.company_domains, orkesta.domain_patterns, orkesta.buyer_personas, orkesta.admin_audit, orkesta.platform_settings, orkesta.notifications, orkesta.proposal_brands, orkesta.automation_runs, orkesta.automation_rules, orkesta.email_assets, orkesta.tracking_tokens, orkesta.campaign_messages, orkesta.campaign_recipients, orkesta.campaigns, orkesta.mailboxes, orkesta.suppressions, orkesta.proposal_links, orkesta.proposals, orkesta.products, orkesta.enrichment_runs, orkesta.activities, orkesta.deals, orkesta.pipeline_stages, orkesta.jobs, orkesta.data_points, orkesta.list_members, "
            "orkesta.contact_lists, orkesta.imports, orkesta.contacts, orkesta.companies, "
            "orkesta.audit_logs, orkesta.invitations, orkesta.memberships, "
            "orkesta.sessions, orkesta.login_attempts, orkesta.demo_requests, orkesta.users, "
            "orkesta.tenants, orkesta.web_cache CASCADE"
        )


@pytest.fixture()
def client(session_factory: sessionmaker[Session]) -> Iterator[TestClient]:
    from orkesta.platform import router

    # Los límites por IP son por proceso: cada prueba empieza con el contador en cero.
    router.register_limiter.hits.clear()
    router.demo_limiter.hits.clear()
    with TestClient(create_app(session_factory)) as c:
        yield c


def register(client: TestClient, email: str, company: str = "Lácteos Inventados") -> dict:
    response = client.post(
        "/api/auth/register",
        json={
            "company": company,
            "full_name": "Ana López",
            "email": email,
            "password": "ClaveSegura2026",
        },
    )
    assert response.status_code == 200, response.text
    return response.json()


@pytest.fixture(autouse=True)
def _offline_mx() -> Iterator[None]:
    """Sin DNS real en pruebas: todo dominio recibe correo, salvo los «sin-correo»."""
    from orkesta.research import memory

    memory.MX_RESOLVER = lambda d: [] if d.startswith("sin-correo") else ["aspmx.l.google.com"]
    memory._ORDER_CACHE.clear()
    yield
    memory.MX_RESOLVER = None


@pytest.fixture(autouse=True)
def _no_domain_cap(monkeypatch: pytest.MonkeyPatch) -> None:
    """Las pruebas escriben a varios contactos de un mismo dominio; el tope por empresa se
    prueba aparte."""
    from orkesta.campaigns import service

    monkeypatch.setitem(service.DEFAULT_SETTINGS, "per_domain_daily", 0)
