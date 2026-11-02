"""Calentamiento automático de buzones.

Revision ID: 0020
Revises: 0019
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0020"
down_revision = "0019"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "mailboxes",
        sa.Column("warmup", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        schema="orkesta",
    )


def downgrade() -> None:
    op.drop_column("mailboxes", "warmup", schema="orkesta")
