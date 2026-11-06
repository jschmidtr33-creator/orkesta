"""Copia completa de una base de ORKESTA a otra (migración entre proveedores, p. ej. Render → AWS).

La base de origen aplica el aislamiento entre empresas (RLS) incluso a su dueño, así que una copia
normal (pg_dump) no ve las filas. Esta copia respeta esa regla: lee cada tabla protegida empresa
por empresa, fijando el contexto de cada una, sin desactivar nada en el origen. El destino se
escribe como administrador (sin disparadores mientras se copia) y queda idéntico al origen.
"""

from __future__ import annotations

from typing import Any

import psycopg
from psycopg import sql

SCHEMA = "orkesta"
SKIP = {"alembic_version"}


def _plain(url: str) -> str:
    return url.replace("postgresql+psycopg://", "postgresql://")


def _tables(conn: psycopg.Connection[Any]) -> dict[str, bool]:
    rows = conn.execute(
        "SELECT c.relname, c.relrowsecurity FROM pg_class c JOIN pg_namespace n "
        "ON n.oid = c.relnamespace WHERE n.nspname = %s AND c.relkind = 'r'", (SCHEMA,)
    ).fetchall()  # fmt: skip
    return {name: bool(rls) for name, rls in rows if name not in SKIP}


def _columns(conn: psycopg.Connection[Any], table: str) -> list[str]:
    rows = conn.execute(
        "SELECT a.attname FROM pg_attribute a JOIN pg_class c ON c.oid = a.attrelid "
        "JOIN pg_namespace n ON n.oid = c.relnamespace WHERE n.nspname = %s AND c.relname = %s "
        "AND a.attnum > 0 AND NOT a.attisdropped AND a.attgenerated = '' ORDER BY a.attnum",
        (SCHEMA, table),
    ).fetchall()  # fmt: skip
    return [r[0] for r in rows]


def copy_database(source: str, target: str, log: Any = print) -> dict[str, int]:
    """Copia todas las tablas de `source` a `target` (que ya tiene las migraciones aplicadas).

    `target` debe conectarse como administrador: copia sin disparadores (claves foráneas) para
    no depender del orden y sin RLS en el destino."""
    copied: dict[str, int] = {}
    with psycopg.connect(_plain(source)) as src, psycopg.connect(_plain(target)) as dst:
        source_tables = _tables(src)
        target_tables = _tables(dst)
        common = [t for t in source_tables if t in target_tables]
        missing = sorted(set(source_tables) - set(target_tables))
        if missing:
            log(f"Tablas del origen que no existen en el destino (se omiten): {missing}")
        tenants = [str(r[0]) for r in src.execute(sql.SQL("SELECT id FROM {}.tenants").format(
            sql.Identifier(SCHEMA))).fetchall()]  # fmt: skip
        src.commit()
        dst.execute("SET session_replication_role = replica")  # sin disparadores ni FK al copiar
        dst.execute(sql.SQL("TRUNCATE {} CASCADE").format(sql.SQL(", ").join(
            sql.Identifier(SCHEMA, t) for t in common)))  # fmt: skip
        for table in common:
            target_cols = set(_columns(dst, table))
            cols = [c for c in _columns(src, table) if c in target_cols]
            col_list = sql.SQL(", ").join(sql.Identifier(c) for c in cols)
            name = sql.Identifier(SCHEMA, table)
            contexts: list[str | None] = list(tenants) if source_tables[table] else [None]
            for tenant in contexts:
                with src.transaction():
                    src.execute("SELECT set_config('app.tenant_id', %s, true), "
                                "set_config('app.user_id', '', true)", (tenant or "",))  # fmt: skip
                    read = sql.SQL("COPY (SELECT {} FROM {}) TO STDOUT").format(col_list, name)
                    write = sql.SQL("COPY {} ({}) FROM STDIN").format(name, col_list)
                    with src.cursor().copy(read) as cin, dst.cursor().copy(write) as cout:
                        for chunk in cin:
                            cout.write(chunk)
            total = int(dst.execute(sql.SQL("SELECT count(*) FROM {}").format(name)).fetchone()[0])  # type: ignore[index]
            copied[table] = total
        # Secuencias (números correlativos) al valor más alto copiado.
        for seq, table, column in dst.execute(
            "SELECT s.relname, t.relname, a.attname FROM pg_class s "
            "JOIN pg_depend d ON d.objid = s.oid JOIN pg_class t ON t.oid = d.refobjid "
            "JOIN pg_attribute a ON a.attrelid = t.oid AND a.attnum = d.refobjsubid "
            "JOIN pg_namespace n ON n.oid = s.relnamespace "
            "WHERE s.relkind = 'S' AND n.nspname = %s", (SCHEMA,)
        ).fetchall():  # fmt: skip
            reset = sql.SQL("SELECT setval({}, COALESCE((SELECT max({}) FROM {}), 0) + 1, false)")
            dst.execute(reset.format(sql.Literal(f"{SCHEMA}.{seq}"), sql.Identifier(column),
                                     sql.Identifier(SCHEMA, table)))  # fmt: skip
        dst.execute("SET session_replication_role = DEFAULT")
        dst.commit()
    return copied
