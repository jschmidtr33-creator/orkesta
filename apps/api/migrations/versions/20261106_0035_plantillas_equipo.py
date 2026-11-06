"""Plantillas de equipo: campañas y propuestas que toda la empresa puede reutilizar.

No llevan política de alcance por persona: una plantilla es, a propósito, visible para toda la
empresa (las campañas y propuestas originales siguen siendo privadas).

Revision ID: 0035
Revises: 0034
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0035"
down_revision = "0034"
branch_labels = None
depends_on = None

TENANT = "tenant_id::text = current_setting('app.tenant_id', true)"


def upgrade() -> None:
    op.create_table(
        "team_templates",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("orkesta.tenants.id", ondelete="CASCADE"), nullable=False),
        sa.Column("created_by", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("orkesta.users.id", ondelete="SET NULL")),
        sa.Column("kind", sa.String(10), nullable=False),
        sa.Column("name", sa.String(120), nullable=False),
        sa.Column("description", sa.String(300), nullable=False, server_default=""),
        sa.Column("payload", postgresql.JSONB(), nullable=False, server_default="{}"),
        sa.Column("use_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(),
                  nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(),
                  nullable=False),
        sa.CheckConstraint("kind IN ('campaign', 'proposal')", name=op.f("ck_team_templates_kind")),
        schema="orkesta",
    )  # fmt: skip
    op.create_index("ix_team_templates_tenant_kind", "team_templates", ["tenant_id", "kind"],
                    schema="orkesta")  # fmt: skip
    op.execute("ALTER TABLE orkesta.team_templates ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE orkesta.team_templates FORCE ROW LEVEL SECURITY")
    op.execute(f"CREATE POLICY tenant_isolation ON orkesta.team_templates USING ({TENANT}) "
               f"WITH CHECK ({TENANT})")  # fmt: skip


def downgrade() -> None:
    op.drop_table("team_templates", schema="orkesta")
