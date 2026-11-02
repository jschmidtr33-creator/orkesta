"""Clasificación automática de respuestas a campañas.

Revision ID: 0021
Revises: 0020
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0021"
down_revision = "0020"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("campaign_recipients", sa.Column("reply_category", sa.String(20)),
                  schema="orkesta")  # fmt: skip
    op.add_column("campaign_recipients", sa.Column("reply_snippet", sa.String(500)),
                  schema="orkesta")  # fmt: skip
    op.add_column(
        "campaign_recipients",
        sa.Column("deal_id", sa.UUID(), sa.ForeignKey("orkesta.deals.id", ondelete="SET NULL")),
        schema="orkesta",
    )


def downgrade() -> None:
    for column in ("deal_id", "reply_snippet", "reply_category"):
        op.drop_column("campaign_recipients", column, schema="orkesta")
