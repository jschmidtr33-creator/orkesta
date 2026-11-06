"""Directorio: de dónde salió el sector de cada empresa (registro público o motor propio).

Revision ID: 0033
Revises: 0032
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0033"
down_revision = "0032"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("company_directory", sa.Column("industry_source", sa.String(20)),
                  schema="orkesta")  # fmt: skip
    op.execute("UPDATE orkesta.company_directory SET industry_source = 'registro' "
               "WHERE industry_code IS NOT NULL AND source <> 'busquedas'")  # fmt: skip


def downgrade() -> None:
    op.drop_column("company_directory", "industry_source", schema="orkesta")
