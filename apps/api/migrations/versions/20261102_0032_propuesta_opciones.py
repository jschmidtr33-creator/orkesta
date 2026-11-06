"""Propuestas con opciones: el cliente elige un producto en vez de sumarlos todos.

Revision ID: 0032
Revises: 0031
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0032"
down_revision = "0031"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("proposals", sa.Column("pricing_mode", sa.String(10), nullable=False,
                                         server_default="total"), schema="orkesta")  # fmt: skip
    op.add_column("proposals", sa.Column("chosen_item", sa.Integer()), schema="orkesta")


def downgrade() -> None:
    op.drop_column("proposals", "chosen_item", schema="orkesta")
    op.drop_column("proposals", "pricing_mode", schema="orkesta")
