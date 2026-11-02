"""IA propia: modelos versionados y ejemplos etiquetados por las personas.

Revision ID: 0030
Revises: 0029
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0030"
down_revision = "0029"
branch_labels = None
depends_on = None


def upgrade() -> None:
    now = sa.text("now()")
    op.create_table(
        "brain_models",
        sa.Column("id", sa.UUID(), primary_key=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=now),
        sa.Column("task", sa.String(40), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("algorithm", sa.String(30), nullable=False),
        sa.Column("active", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("trigger", sa.String(20), nullable=False, server_default="manual"),
        sa.Column("n_train", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("n_test", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("threshold", sa.Float(), nullable=False, server_default="0.6"),
        sa.Column("metrics", postgresql.JSONB(), nullable=False, server_default="{}"),
        sa.Column("baseline", postgresql.JSONB()),
        sa.Column("data_hash", sa.String(64), nullable=False, server_default=""),
        sa.Column("duration_ms", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("blob", sa.LargeBinary(), nullable=False),
        sa.Column("notes", sa.Text(), nullable=False, server_default=""),
        schema="orkesta",
    )
    op.create_index("ix_brain_models_task_version", "brain_models", ["task", "version"],
                    unique=True, schema="orkesta")  # fmt: skip
    op.create_table(
        "brain_feedback",
        sa.Column("id", sa.UUID(), primary_key=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=now),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=now),
        sa.Column("task", sa.String(40), nullable=False),
        sa.Column("text", sa.Text(), nullable=False, server_default=""),
        sa.Column("data", postgresql.JSONB(), nullable=False, server_default="{}"),
        sa.Column("label", sa.String(60), nullable=False),
        sa.Column("predicted", sa.String(60)),
        sa.Column("tenant_id", sa.UUID(), sa.ForeignKey("orkesta.tenants.id", ondelete="CASCADE")),
        sa.Column("user_id", sa.UUID(), sa.ForeignKey("orkesta.users.id", ondelete="SET NULL")),
        schema="orkesta",
    )
    op.create_index("ix_brain_feedback_task_created", "brain_feedback", ["task", "created_at"],
                    schema="orkesta")  # fmt: skip


def downgrade() -> None:
    op.drop_table("brain_feedback", schema="orkesta")
    op.drop_table("brain_models", schema="orkesta")
