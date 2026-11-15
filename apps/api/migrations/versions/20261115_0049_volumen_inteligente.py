"""Ajuste inteligente del volumen del buzón (opcional): sube si responden bien, baja si rebota.

Revision ID: 0049
Revises: 0048
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0049"
down_revision = "0048"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("mailboxes", sa.Column("smart_volume", sa.Boolean(), nullable=False,
                                         server_default=sa.text("false")), schema="orkesta")  # fmt: skip


def downgrade() -> None:
    op.drop_column("mailboxes", "smart_volume", schema="orkesta")
