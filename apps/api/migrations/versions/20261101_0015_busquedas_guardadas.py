"""Búsquedas guardadas de Prospección: solo ellas quedan en el historial.

Revision ID: 0015
Revises: 0014
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0015"
down_revision = "0014"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "research_runs", sa.Column("saved_name", sa.String(120), nullable=True), schema="orkesta"
    )
    op.add_column(
        "research_runs",
        sa.Column("saved_at", sa.DateTime(timezone=True), nullable=True),
        schema="orkesta",
    )
    # Las búsquedas anteriores no se guardaron: se borran solas como las demás.


def downgrade() -> None:
    op.drop_column("research_runs", "saved_at", schema="orkesta")
    op.drop_column("research_runs", "saved_name", schema="orkesta")
