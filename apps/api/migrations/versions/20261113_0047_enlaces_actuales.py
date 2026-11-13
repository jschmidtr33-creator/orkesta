"""Enlaces guardados con la dirección antigua de ORKESTA (Render o la IP temporal) pasan a la
dirección pública actual: propuestas, plantillas, secuencias, reglas y firmas.

Revision ID: 0047
Revises: 0046
"""

from __future__ import annotations

import os

from alembic import op

revision = "0047"
down_revision = "0046"
branch_labels = None
depends_on = None

OLD = r"https?://([a-z0-9-]+\.onrender\.com|[0-9-]+\.sslip\.io)"
COLUMNS = {
    "proposals": ("sections", "design", "items", "signature"),
    "campaigns": ("steps",),
    "automation_rules": ("actions",),
}


ASSET = r"https?://[^\s\"'()<>/]+(/t/i/[0-9a-fA-F-]{36}\.(png|jpg|gif|webp))"


def upgrade() -> None:
    bind = op.get_bind()
    # Imágenes subidas a ORKESTA: relativas, para que se vean con cualquier dominio.
    bind.exec_driver_sql(
        "UPDATE orkesta.products SET image_url = regexp_replace(image_url, %s, '\\1', 'g') "
        "WHERE image_url ~ %s", (ASSET, ASSET),
    )  # fmt: skip
    bind.exec_driver_sql(
        "UPDATE orkesta.proposals SET items = regexp_replace(items::text, %s, '\\1', 'g')::jsonb "
        "WHERE items::text ~ %s", (ASSET, ASSET),
    )  # fmt: skip
    base = os.environ.get("PUBLIC_BASE_URL", "").strip().rstrip("/")
    if not base.startswith("https://"):
        return  # sin dirección pública (pruebas o local): nada más que reemplazar
    for table, columns in COLUMNS.items():
        existing = {
            r[0] for r in bind.exec_driver_sql(
                "SELECT column_name FROM information_schema.columns "
                "WHERE table_schema='orkesta' AND table_name=%s", (table,)
            )
        }  # fmt: skip
        for column in columns:
            if column not in existing:
                continue
            bind.exec_driver_sql(
                f"UPDATE orkesta.{table} SET {column} = "
                f"regexp_replace({column}::text, %s, %s, 'gi')::jsonb "
                f"WHERE {column}::text ~* %s",
                (OLD, base, OLD),
            )
    bind.exec_driver_sql(
        "UPDATE orkesta.mailboxes SET signature = regexp_replace(signature, %s, %s, 'gi') "
        "WHERE signature ~* %s",
        (OLD, base, OLD),
    )


def downgrade() -> None:
    pass
