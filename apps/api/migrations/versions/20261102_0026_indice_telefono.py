"""Índice de personas: teléfono (bases autorizadas) y fuente.

Revision ID: 0026
Revises: 0025
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0026"
down_revision = "0025"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("people_index", sa.Column("phone", sa.String(30)), schema="orkesta")
    op.add_column("people_index", sa.Column("source", sa.String(30), nullable=False,
                                            server_default="busquedas"), schema="orkesta")  # fmt: skip


def downgrade() -> None:
    op.drop_column("people_index", "source", schema="orkesta")
    op.drop_column("people_index", "phone", schema="orkesta")
