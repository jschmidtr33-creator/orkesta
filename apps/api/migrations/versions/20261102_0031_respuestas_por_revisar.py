"""Respuestas que la IA propia no pudo clasificar con seguridad (se preguntan a la persona).

Revision ID: 0031
Revises: 0030
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0031"
down_revision = "0030"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "campaign_recipients",
        sa.Column("reply_review", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        schema="orkesta",
    )


def downgrade() -> None:
    op.drop_column("campaign_recipients", "reply_review", schema="orkesta")
