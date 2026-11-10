"""Cada contacto tiene dueño: los antiguos sin dueño pasan a quien los creó o importó.

Hasta ahora la administración veía los contactos sin dueño (anteriores a la regla de contactos
privados). Se asigna el dueño por el primer dato registrado (quién lo ingresó o importó) o por
la importación de origen, siempre que esa persona siga en la empresa.

Revision ID: 0044
Revises: 0043
"""

from __future__ import annotations

from alembic import op

revision = "0044"
down_revision = "0043"
branch_labels = None
depends_on = None

TABLES = ("contacts", "data_points", "imports", "memberships")


def upgrade() -> None:
    for table in TABLES:
        op.execute(f"ALTER TABLE orkesta.{table} NO FORCE ROW LEVEL SECURITY")
    op.execute(
        """
        UPDATE orkesta.contacts c SET owner_user_id = pick.user_id
        FROM (
            SELECT DISTINCT ON (d.contact_id) d.contact_id,
                   coalesce(d.actor_user_id, j.created_by) AS user_id
            FROM orkesta.data_points d
            LEFT JOIN orkesta.imports j ON j.id = d.source_ref
            WHERE coalesce(d.actor_user_id, j.created_by) IS NOT NULL
            ORDER BY d.contact_id, d.created_at
        ) pick
        WHERE c.id = pick.contact_id AND c.owner_user_id IS NULL
          AND EXISTS (SELECT 1 FROM orkesta.memberships m WHERE m.tenant_id = c.tenant_id
                      AND m.user_id = pick.user_id AND m.status = 'active')
        """
    )
    for table in TABLES:
        op.execute(f"ALTER TABLE orkesta.{table} FORCE ROW LEVEL SECURITY")


def downgrade() -> None:
    pass  # no se puede saber cuáles no tenían dueño
