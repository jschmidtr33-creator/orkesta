"""Limpia tareas pendientes y avisos repetidos (la misma cosa varias veces).

Deja la tarea abierta más antigua de cada grupo idéntico (empresa, contacto y texto) y marca
como leídos los avisos sin leer repetidos (deja el más reciente).

Revision ID: 0050
Revises: 0049
"""

from __future__ import annotations

from alembic import op

revision = "0050"
down_revision = "0049"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
        DELETE FROM orkesta.activities a
        USING (
            SELECT id, row_number() OVER (
                PARTITION BY tenant_id, contact_id, body ORDER BY created_at, id) AS n
            FROM orkesta.activities
            WHERE type = 'task' AND done_at IS NULL
        ) d
        WHERE a.id = d.id AND d.n > 1
    """)
    op.execute("""
        UPDATE orkesta.notifications n SET read_at = now()
        FROM (
            SELECT id, row_number() OVER (
                PARTITION BY tenant_id, user_id, kind, title ORDER BY created_at DESC, id) AS k
            FROM orkesta.notifications
            WHERE read_at IS NULL
        ) d
        WHERE n.id = d.id AND d.k > 1
    """)


def downgrade() -> None:
    pass  # limpieza de datos: no se deshace
