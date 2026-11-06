"""Migración entre proveedores: la copia respeta el aislamiento entre empresas del origen (no lo
desactiva) y no pierde filas."""

from __future__ import annotations

import uuid

import psycopg
from fastapi.testclient import TestClient
from psycopg import sql

from orkesta.cli import alembic_config
from orkesta.transfer import copy_database
from tests.conftest import register


def _counts(url: str) -> dict[str, int]:
    with psycopg.connect(url) as conn:
        tables = [r[0] for r in conn.execute(
            "SELECT tablename FROM pg_tables WHERE schemaname = 'orkesta' "
            "AND tablename <> 'alembic_version'").fetchall()]  # fmt: skip
        count = sql.SQL("SELECT count(*) FROM {}")
        return {t: conn.execute(count.format(sql.Identifier("orkesta", t))).fetchone()[0]  # type: ignore[index]
                for t in tables}  # fmt: skip


def test_copy_reads_each_tenant_through_rls_and_loses_nothing(
    client: TestClient, database: tuple[str, str]
) -> None:
    from alembic import command

    admin_url, app_url = database
    for email in ("ana@lacteos.co", "pedro@banco.co"):
        register(client, email)
        client.post("/api/contacts", json={"first_name": "Laura", "company": "Banco Andino"})
        client.post("/api/lists", json={"name": f"Lista {email}"})
        client.post("/api/auth/logout")
    source = _counts(admin_url)  # el administrador ve todo
    assert source["tenants"] == 2 and source["contacts"] == 2

    # El usuario de la app (sin superusuario) no ve nada sin contexto: así está Render.
    with psycopg.connect(app_url.replace("+psycopg", "")) as conn:
        assert conn.execute("SELECT count(*) FROM orkesta.contacts").fetchone()[0] == 0  # type: ignore[index]

    name = f"orkesta_destino_{uuid.uuid4().hex[:6]}"
    base = admin_url.rsplit("/", 1)[0]
    with psycopg.connect(f"{base}/postgres", autocommit=True) as conn:
        conn.execute(f'CREATE DATABASE "{name}"')
    target = f"{base}/{name}"
    try:
        command.upgrade(alembic_config(target), "head")
        copied = copy_database(app_url, target, log=lambda _m: None)
        after = _counts(target)
        for table, count in source.items():
            assert after[table] == count, table
            assert copied.get(table, 0) == count, table
        # Volver a copiar deja el destino idéntico (no duplica).
        copy_database(app_url, target, log=lambda _m: None)
        assert _counts(target) == source
    finally:
        with psycopg.connect(f"{base}/postgres", autocommit=True) as conn:
            conn.execute(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)')
