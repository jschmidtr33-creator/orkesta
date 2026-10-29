"""Fixtures de prueba.

Las pruebas marcadas ``db`` usan un PostgreSQL real. ``TEST_DATABASE_URL`` apunta a un
servidor donde el usuario puede crear bases; cada sesión de pruebas crea una base temporal,
aplica las migraciones y la elimina al terminar. Sin ``TEST_DATABASE_URL`` esas pruebas se omiten.
"""

from __future__ import annotations

import os
import uuid
from collections.abc import Iterator

import pytest
from alembic import command
from sqlalchemy import Engine, create_engine, text
from sqlalchemy.engine import make_url
from sqlalchemy.orm import Session, sessionmaker

from orkesta.config import get_settings, normalize_database_url
from orkesta.db.migrations_state import alembic_config
from orkesta.db.models import EnrichmentBatch, Organization

ADMIN_URL = os.environ.get("TEST_DATABASE_URL")
# La app necesita una DATABASE_URL para construir su configuración; las pruebas que usan base de
# datos la sustituyen por la base temporal (fixture ``database_url``).
os.environ.setdefault("DATABASE_URL", "postgresql://nadie@127.0.0.1:1/ninguna")


def _require_db() -> str:
    if not ADMIN_URL:
        pytest.skip("TEST_DATABASE_URL no está definida")
    return normalize_database_url(ADMIN_URL)


def _create_database(admin_url: str, name: str) -> str:
    admin = create_engine(admin_url, isolation_level="AUTOCOMMIT")
    with admin.connect() as connection:
        connection.execute(text(f'CREATE DATABASE "{name}"'))
    admin.dispose()
    return make_url(admin_url).set(database=name).render_as_string(hide_password=False)


def _drop_database(admin_url: str, name: str) -> None:
    admin = create_engine(admin_url, isolation_level="AUTOCOMMIT")
    with admin.connect() as connection:
        connection.execute(text(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)'))
    admin.dispose()


@pytest.fixture(scope="session")
def database_url() -> Iterator[str]:
    admin_url = _require_db()
    name = f"orkesta_test_{uuid.uuid4().hex[:10]}"
    url = _create_database(admin_url, name)
    command.upgrade(alembic_config(url), "head")
    previous = os.environ.get("DATABASE_URL")
    os.environ["DATABASE_URL"] = url
    get_settings.cache_clear()
    yield url
    if previous is None:
        os.environ.pop("DATABASE_URL", None)
    else:
        os.environ["DATABASE_URL"] = previous
    get_settings.cache_clear()
    _drop_database(admin_url, name)


@pytest.fixture(scope="session")
def engine(database_url: str) -> Iterator[Engine]:
    eng = create_engine(database_url, pool_pre_ping=True)
    yield eng
    eng.dispose()


@pytest.fixture()
def session_factory(engine: Engine) -> Iterator[sessionmaker[Session]]:
    yield sessionmaker(bind=engine, expire_on_commit=False)
    # Limpieza entre pruebas: borrar organizaciones elimina en cascada todo lo demás.
    with engine.begin() as connection:
        connection.execute(text("DELETE FROM organizations"))


@pytest.fixture()
def empty_database_url() -> Iterator[str]:
    """Base vacía, sin migraciones, para probar ``/ready`` con migraciones pendientes."""
    admin_url = _require_db()
    name = f"orkesta_empty_{uuid.uuid4().hex[:10]}"
    url = _create_database(admin_url, name)
    yield url
    _drop_database(admin_url, name)


@pytest.fixture()
def batch(session_factory: sessionmaker[Session]) -> EnrichmentBatch:
    """Organización y lote reales, insertados en la base de pruebas."""
    with session_factory.begin() as session:
        org = Organization(name="Org de prueba", slug=f"test-{uuid.uuid4().hex[:8]}")
        session.add(org)
        session.flush()
        created = EnrichmentBatch(
            organization_id=org.id,
            source_filename="fixture.csv",
            source_sha256="0" * 64,
        )
        session.add(created)
    return created
