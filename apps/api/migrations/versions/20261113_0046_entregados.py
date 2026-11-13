"""Aprender de los correos estimados que llegaron: evidencia «entregado» por formato y marca de
los envíos ya aprendidos.

Revision ID: 0046
Revises: 0045
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0046"
down_revision = "0045"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("pattern_stats", sa.Column("delivered", sa.Integer(), nullable=False,
                                             server_default="0"), schema="orkesta")  # fmt: skip
    op.add_column("campaign_recipients", sa.Column("learned_at", sa.DateTime(timezone=True)),
                  schema="orkesta")  # fmt: skip


def downgrade() -> None:
    op.drop_column("campaign_recipients", "learned_at", schema="orkesta")
    op.drop_column("pattern_stats", "delivered", schema="orkesta")
