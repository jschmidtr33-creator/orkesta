"""Doble verificación, resumen diario y suscripciones de notificaciones push.

Revision ID: 0029
Revises: 0028
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0029"
down_revision = "0028"
branch_labels = None
depends_on = None

COLUMNS = (
    sa.Column("mfa_secret", sa.Text()),
    sa.Column("mfa_enabled", sa.Boolean(), nullable=False, server_default=sa.text("false")),
    sa.Column("mfa_recovery", postgresql.JSONB(), nullable=False, server_default="[]"),
    sa.Column("mfa_last_step", sa.BigInteger()),
    sa.Column("digest_enabled", sa.Boolean(), nullable=False, server_default=sa.text("true")),
    sa.Column("digest_last_sent", sa.Date()),
)


def upgrade() -> None:
    for column in COLUMNS:
        op.add_column("users", column, schema="orkesta")
    op.create_table(
        "push_subscriptions",
        sa.Column("id", sa.UUID(), primary_key=True),
        sa.Column(
            "user_id",
            sa.UUID(),
            sa.ForeignKey("orkesta.users.id", ondelete="CASCADE"),
            nullable=False,
            index=True,
        ),
        sa.Column("endpoint", sa.String(1000), nullable=False, unique=True),
        sa.Column("p256dh", sa.String(200), nullable=False),
        sa.Column("auth", sa.String(100), nullable=False),
        sa.Column("user_agent", sa.String(300), nullable=False, server_default=""),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        schema="orkesta",
    )


def downgrade() -> None:
    op.drop_table("push_subscriptions", schema="orkesta")
    for column in reversed(COLUMNS):
        op.drop_column("users", column.name, schema="orkesta")
