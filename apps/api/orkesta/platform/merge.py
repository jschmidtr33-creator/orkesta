"""Unir la cuenta de una empresa a otra, sin perder nada.

Caso típico: cada persona de un equipo creó su propia cuenta y ahora trabajan juntas. Todo lo de
la empresa de origen (personas, contactos, listas, búsquedas, campañas, historial, propuestas,
pipeline, buzones) pasa a la de destino y cada registro conserva su dueño, así que la
supervisión funciona desde el primer momento. La empresa de origen desaparece al final.

Todo ocurre en una sola transacción: si algo falla, no cambia nada.

Cómo, respetando el aislamiento entre empresas (RLS) sin privilegios especiales:
1. Se resuelven los choques (nombres o claves que ya existen en el destino) dentro del origen.
2. Se leen las filas de origen con ``tenant_id`` ya reemplazado por el del destino.
3. Se borra la empresa de origen (sus filas se van en cascada).
4. Se vuelven a escribir las mismas filas, con sus mismos identificadores, en el destino.
"""

# Los nombres de tablas y columnas salen del catálogo de PostgreSQL, nunca de quien usa la app.
# ruff: noqa: S608

from __future__ import annotations

import uuid
from collections import defaultdict
from typing import Any, cast

import psycopg
from sqlalchemy import text
from sqlalchemy.orm import Session

from orkesta.core.db import set_context, set_scope
from orkesta.platform.models import ROLES, Tenant

SCHEMA = "orkesta"


class MergeError(ValueError):
    pass


def _rows(session: Session, sql: str, **params: Any) -> list[Any]:
    return list(session.execute(text(sql), params).all())


def _tenant_tables(session: Session) -> list[str]:
    """Tablas con datos de una empresa, ordenadas para insertar padres antes que hijos."""
    tables = {r[0] for r in _rows(session, """
        SELECT c.relname FROM pg_class c
        JOIN pg_namespace n ON n.oid = c.relnamespace
        JOIN pg_attribute a ON a.attrelid = c.oid AND a.attname = 'tenant_id' AND NOT a.attisdropped
        WHERE n.nspname = :s AND c.relkind = 'r'""", s=SCHEMA)}  # fmt: skip
    parents: dict[str, set[str]] = defaultdict(set)
    for child, parent in _rows(session, """
        SELECT cl.relname, pl.relname FROM pg_constraint k
        JOIN pg_class cl ON cl.oid = k.conrelid JOIN pg_class pl ON pl.oid = k.confrelid
        WHERE k.contype = 'f' AND k.connamespace = CAST(:s AS regnamespace)
          AND k.conrelid <> k.confrelid""", s=SCHEMA):  # fmt: skip
        if child in tables and parent in tables:
            parents[child].add(parent)
    ordered: list[str] = []
    pending = set(tables)
    while pending:
        ready = sorted(t for t in pending if not (parents[t] & pending))
        if not ready:
            raise MergeError(f"Dependencias circulares entre tablas: {sorted(pending)}")
        ordered += ready
        pending -= set(ready)
    return ordered


def _columns(session: Session, table: str) -> list[str]:
    return [r[0] for r in _rows(session, """
        SELECT a.attname FROM pg_attribute a JOIN pg_class c ON c.oid = a.attrelid
        JOIN pg_namespace n ON n.oid = c.relnamespace
        WHERE n.nspname = :s AND c.relname = :t AND a.attnum > 0 AND NOT a.attisdropped
          AND a.attgenerated = '' ORDER BY a.attnum""", s=SCHEMA, t=table)]  # fmt: skip


def _remap(session: Session, refs: list[tuple[str, str]], old: uuid.UUID, new: uuid.UUID) -> None:
    for table, column in refs:
        session.execute(text(f"UPDATE {SCHEMA}.{table} SET {column} = :new WHERE {column} = :old"),
                        {"new": new, "old": old})  # fmt: skip


def _resolve_conflicts(
    session: Session, source: Tenant, target: Tenant, role: str | None
) -> dict[str, int]:
    """Deja el origen listo para entrar al destino sin chocar con nada de lo que ya existe."""
    s, t = source.id, target.id
    report: dict[str, int] = defaultdict(int)

    set_context(session, tenant_id=t)
    t_companies = dict(_rows(session, "SELECT match_key, id FROM orkesta.companies"))
    t_stages = {
        n.lower(): i for n, i in _rows(session, "SELECT name, id FROM orkesta.pipeline_stages")
    }
    t_mailboxes = dict(_rows(session, "SELECT email, id FROM orkesta.mailboxes"))
    t_suppressed = {r[0] for r in _rows(session, "SELECT email FROM orkesta.suppressions")}
    t_products = {r[0].lower() for r in _rows(session, "SELECT name FROM orkesta.products")}
    t_personas = {r[0].lower() for r in _rows(session, "SELECT name FROM orkesta.buyer_personas")}
    t_dedup = {r[0] for r in _rows(session, "SELECT dedup_key FROM orkesta.contacts")}
    t_lists = {
        (c, n.lower())
        for c, n in _rows(session, "SELECT created_by, name FROM orkesta.contact_lists")
    }
    t_members = {
        r[0]
        for r in _rows(session, "SELECT user_id FROM orkesta.memberships WHERE tenant_id = :t", t=t)
    }
    t_brand = bool(
        _rows(session, "SELECT 1 FROM orkesta.proposal_brands WHERE tenant_id = :t", t=t)
    )
    t_max = int(_rows(session, "SELECT coalesce(max(number), 0) FROM orkesta.proposals")[0][0])

    set_context(session, tenant_id=s)
    for key, sid in _rows(session, "SELECT match_key, id FROM orkesta.companies"):
        if key in t_companies:  # la misma empresa cliente: se unen sus contactos y oportunidades
            _remap(
                session,
                [("contacts", "company_id"), ("deals", "company_id")],
                sid,
                t_companies[key],
            )
            session.execute(text("DELETE FROM orkesta.companies WHERE id = :i"), {"i": sid})
            report["empresas_cliente_unidas"] += 1
    for name, sid in _rows(session, "SELECT name, id FROM orkesta.pipeline_stages"):
        if name.lower() in t_stages:  # misma etapa: las oportunidades pasan a la del destino
            new = t_stages[name.lower()]
            _remap(session, [("deals", "stage_id")], sid, new)
            session.execute(
                text(
                    "UPDATE orkesta.automation_rules SET "
                    "conditions = CAST(replace(CAST(conditions AS text), :o, :n) AS jsonb), "
                    "actions = CAST(replace(CAST(actions AS text), :o, :n) AS jsonb)"
                ),
                {"o": str(sid), "n": str(new)},
            )
            session.execute(text("DELETE FROM orkesta.pipeline_stages WHERE id = :i"), {"i": sid})
            report["etapas_unidas"] += 1
    for email, sid in _rows(session, "SELECT email, id FROM orkesta.mailboxes"):
        if email in t_mailboxes:
            _remap(
                session,
                [("campaigns", "mailbox_id"), ("campaign_messages", "mailbox_id")],
                sid,
                t_mailboxes[email],
            )
            session.execute(text("DELETE FROM orkesta.mailboxes WHERE id = :i"), {"i": sid})
            report["buzones_repetidos"] += 1
    for (email,) in _rows(session, "SELECT email FROM orkesta.suppressions"):
        if email in t_suppressed:
            session.execute(text("DELETE FROM orkesta.suppressions WHERE email = :e"), {"e": email})
    suffix = f" ({source.name})"[:40]
    for table, taken in (("products", t_products), ("buyer_personas", t_personas)):
        for sid, name in _rows(session, f"SELECT id, name FROM orkesta.{table}"):
            if name.lower() in taken:
                session.execute(text(f"UPDATE orkesta.{table} SET name = :n WHERE id = :i"),
                                {"n": (name[: 160 - len(suffix)] + suffix), "i": sid})  # fmt: skip
    for sid, owner, name in _rows(
        session, "SELECT id, created_by, name FROM orkesta.contact_lists"
    ):
        if (owner, name.lower()) in t_lists:
            session.execute(text("UPDATE orkesta.contact_lists SET name = :n WHERE id = :i"),
                            {"n": (name[: 120 - len(suffix)] + suffix), "i": sid})  # fmt: skip
    # Un contacto que ya existe en el destino: cada persona conserva el suyo.
    for sid, key, owner in _rows(
        session, "SELECT id, dedup_key, owner_user_id FROM orkesta.contacts"
    ):
        if key in t_dedup:
            tag = str(owner or sid)[:8]
            session.execute(text("UPDATE orkesta.contacts SET dedup_key = :k WHERE id = :i"),
                            {"k": f"{key}#{tag}"[:300], "i": sid})  # fmt: skip
            report["contactos_que_ya_existian"] += 1
    # Propuestas: siguen la numeración del destino. Las firmadas guardan el número y el emisor
    # con que se firmaron, para que su constancia siga siendo válida.
    proposals = _rows(
        session, "SELECT id, number, signature FROM orkesta.proposals ORDER BY number"
    )
    session.execute(text("UPDATE orkesta.proposals SET number = -number"))
    for i, (pid, number, signature) in enumerate(proposals, start=1):
        sig = dict(signature or {})
        if sig.get("doc_hash") and "frozen" not in sig:
            sig["frozen"] = {"number": number, "issuer": source.name}
        session.execute(
            text(
                "UPDATE orkesta.proposals SET number = :n, signature = CAST(:sig AS jsonb) "
                "WHERE id = :i"
            ),
            {"n": t_max + i, "sig": _json(sig), "i": pid},
        )
    report["propuestas_renumeradas"] = len(proposals)
    if t_brand:
        session.execute(text("DELETE FROM orkesta.proposal_brands WHERE tenant_id = :s"), {"s": s})
    # Personas: quien ya está en el destino conserva su rol allí.
    for (uid,) in _rows(
        session, "SELECT user_id FROM orkesta.memberships WHERE tenant_id = :s", s=s
    ):
        if uid in t_members:
            session.execute(
                text("DELETE FROM orkesta.memberships WHERE tenant_id = :s AND user_id = :u"),
                {"s": s, "u": uid},
            )
        elif role:
            session.execute(
                text(
                    "UPDATE orkesta.memberships SET role = :r WHERE tenant_id = :s AND user_id = :u"
                ),
                {"r": role, "s": s, "u": uid},
            )
            report["personas_movidas"] += 1
        else:
            report["personas_movidas"] += 1
    # Trabajos pendientes que nombran a la empresa de origen.
    session.execute(text(
        "UPDATE orkesta.jobs SET payload = CAST(replace(CAST(payload AS text), :o, :n) AS jsonb)"),
        {"o": str(s), "n": str(t)})  # fmt: skip
    return report


def _json(value: Any) -> str:
    import json

    return json.dumps(value, ensure_ascii=False, default=str)


def merge_tenants(
    session: Session, *, source_id: uuid.UUID, target_id: uuid.UUID, role: str | None = None
) -> dict[str, Any]:
    if source_id == target_id:
        raise MergeError("Elige dos empresas distintas.")
    if role is not None and role not in ROLES:
        raise MergeError("Rol no válido.")
    source, target = session.get(Tenant, source_id), session.get(Tenant, target_id)
    if source is None or target is None:
        raise MergeError("Empresa no encontrada.")
    source_name = source.name
    set_scope(session, None)
    report = _resolve_conflicts(session, source, target, role)
    session.flush()

    raw = cast(psycopg.Connection[Any], session.connection().connection.dbapi_connection)
    tables = _tenant_tables(session)
    dump: list[tuple[str, list[str], bytes]] = []
    moved: dict[str, int] = {}
    set_context(session, tenant_id=source_id)
    with raw.cursor() as cur:
        for table in tables:
            cols = _columns(session, table)
            select_list = ", ".join(
                f"CAST('{target_id}' AS uuid)" if c == "tenant_id" else f'"{c}"' for c in cols
            )
            where = f"FROM {SCHEMA}.{table} WHERE tenant_id = '{source_id}'"
            count = int(_rows(session, f"SELECT count(*) {where}")[0][0])
            if not count:
                continue
            chunks = bytearray()
            query = f"COPY (SELECT {select_list} {where}) TO STDOUT"
            with cur.copy(query) as copy:
                for chunk in copy:
                    chunks += bytes(chunk)
            dump.append((table, cols, bytes(chunks)))
            moved[table] = count

    # La empresa de origen se va con todas sus filas; enseguida vuelven, en el destino.
    session.execute(text("DELETE FROM orkesta.tenants WHERE id = :s"), {"s": source_id})
    set_context(session, tenant_id=target_id)
    with raw.cursor() as cur:
        for i, (table, cols, data) in enumerate(dump):
            # COPY no admite tablas con RLS: se carga en una tabla temporal y de ahí se inserta
            # (las políticas del destino se aplican a cada fila).
            col_list = ", ".join(f'"{c}"' for c in cols)
            tmp = f"merge_tmp_{i}"
            cur.execute(
                f"CREATE TEMP TABLE {tmp} ON COMMIT DROP AS "
                f"SELECT {col_list} FROM {SCHEMA}.{table} WITH NO DATA"
            )
            with cur.copy(f"COPY {tmp} ({col_list}) FROM STDIN") as copy:
                copy.write(data)
            cur.execute(f"INSERT INTO {SCHEMA}.{table} ({col_list}) SELECT {col_list} FROM {tmp}")
    session.expire_all()
    return {"source": source_name, "target": target.name, "moved": moved, **report}
