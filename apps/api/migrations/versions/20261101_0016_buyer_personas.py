"""Buyer personas del asistente de redacción.

Revision ID: 0016
Revises: 0015
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0016"
down_revision = "0015"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "buyer_personas",
        sa.Column("tenant_id", sa.UUID(), nullable=False),
        sa.Column("name", sa.String(length=120), nullable=False),
        sa.Column("data", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id"], ["orkesta.tenants.id"],
            name=op.f("fk_buyer_personas_tenant_id_tenants"), ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_buyer_personas")),
        sa.UniqueConstraint("tenant_id", "name", name=op.f("uq_buyer_personas_tenant_id_name")),
        schema="orkesta",
    )  # fmt: skip
    rule = "tenant_id::text = current_setting('app.tenant_id', true)"
    op.execute("ALTER TABLE orkesta.buyer_personas ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE orkesta.buyer_personas FORCE ROW LEVEL SECURITY")
    op.execute(
        f"CREATE POLICY tenant_isolation ON orkesta.buyer_personas USING ({rule}) WITH CHECK ({rule})"
    )


def downgrade() -> None:
    op.execute("DROP POLICY IF EXISTS tenant_isolation ON orkesta.buyer_personas")
    op.drop_table("buyer_personas", schema="orkesta")
