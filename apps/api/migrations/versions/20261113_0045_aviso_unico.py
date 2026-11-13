"""Cada aviso de automatización corre una sola vez por regla y evento (no se repite por
clics simultáneos de los filtros de seguridad de correo ni por reintentos).

Revision ID: 0045
Revises: 0044
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0045"
down_revision = "0044"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("automation_runs", sa.Column("dedupe_key", sa.String(120)), schema="orkesta")
    op.create_index("uq_automation_runs_once", "automation_runs", ["rule_id", "dedupe_key"],
                    unique=True, schema="orkesta",
                    postgresql_where=sa.text("dedupe_key IS NOT NULL"))  # fmt: skip


def downgrade() -> None:
    op.drop_index("uq_automation_runs_once", table_name="automation_runs", schema="orkesta")
    op.drop_column("automation_runs", "dedupe_key", schema="orkesta")
