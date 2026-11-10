"""Imágenes subidas con dirección relativa («/t/i/…»).

Las imágenes se guardaban con la dirección completa del servidor de ese momento (por ejemplo, el
de Render). Al cambiar de servidor esas direcciones dejaron de cargar. Se pasan todas a relativas:
funcionan en cualquier dominio, y los correos les ponen la dirección pública al enviarse.

Revision ID: 0039
Revises: 0038
"""

from __future__ import annotations

from alembic import op

revision = "0039"
down_revision = "0038"
branch_labels = None
depends_on = None

PATTERN = r"https?://[^\s\"'()<>/]+(/t/i/[0-9a-fA-F-]{36}\.(png|jpg|gif|webp))"
# (tabla, columna, es_jsonb)
COLUMNS = (
    ("proposal_brands", "data", True),
    ("proposals", "design", True),
    ("proposals", "sections", True),
    ("products", "description", False),
    ("products", "image_url", False),
    ("campaigns", "steps", True),
    ("mailboxes", "signature", False),
    ("team_templates", "payload", True),
)


def upgrade() -> None:
    pattern = PATTERN.replace("'", "''")
    for table, column, jsonb in COLUMNS:
        value = f"{column}::text" if jsonb else column
        fixed = f"regexp_replace({value}, '{pattern}', '\\1', 'g')"
        # La migración corrige filas de todas las empresas: la seguridad por fila se suspende
        # solo para el dueño de la tabla durante esta actualización y se vuelve a forzar.
        op.execute(f"ALTER TABLE orkesta.{table} NO FORCE ROW LEVEL SECURITY")
        op.execute(
            f"UPDATE orkesta.{table} SET {column} = {fixed}{'::jsonb' if jsonb else ''} "
            f"WHERE {value} ~ '{pattern}'"
        )
        op.execute(f"ALTER TABLE orkesta.{table} FORCE ROW LEVEL SECURITY")


def downgrade() -> None:
    # Las direcciones relativas funcionan en cualquier dominio: no hay nada que deshacer.
    pass
