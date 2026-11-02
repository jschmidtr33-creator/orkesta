"""Pruebas A/B: qué versión del correo recibió cada destinatario.

Revision ID: 0027
Revises: 0026
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0027"
down_revision = "0026"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("campaign_messages", sa.Column("variant", sa.String(1), nullable=False,
                                                 server_default="A"), schema="orkesta")  # fmt: skip


def downgrade() -> None:
    op.drop_column("campaign_messages", "variant", schema="orkesta")
