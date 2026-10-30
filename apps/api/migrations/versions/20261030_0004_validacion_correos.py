"""validación de correos

Revision ID: 0004
Revises: 0003
Create Date: 2026-10-30 13:00:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0004"
down_revision: str | None = "0003"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None
S = "orkesta"
EMAIL_STATUSES = (
    "('sin_validar', 'estimado', 'valido', 'invalido', 'catch_all', 'riesgoso', 'desconocido')"
)
SOURCES_3 = "('importacion', 'manual', 'enriquecimiento', 'investigacion')"
SOURCES_4 = "('importacion', 'manual', 'enriquecimiento', 'investigacion', 'validacion')"


def _sources(values: str) -> None:
    for table, column in (("contacts", "source"), ("data_points", "source_type")):
        name = f"ck_{table}_{column}"
        op.execute(f"ALTER TABLE {S}.{table} DROP CONSTRAINT {name}")
        op.execute(f"ALTER TABLE {S}.{table} ADD CONSTRAINT {name} CHECK ({column} IN {values})")


def upgrade() -> None:
    op.add_column(
        "contacts",
        sa.Column("email_status", sa.String(20), server_default="sin_validar", nullable=False),
        schema=S,
    )
    op.add_column("contacts", sa.Column("email_checked_at", sa.DateTime(timezone=True)), schema=S)
    op.add_column("contacts", sa.Column("email_check_reason", sa.String(300)), schema=S)
    op.create_check_constraint(
        op.f("ck_contacts_email_status"), "contacts", f"email_status IN {EMAIL_STATUSES}", schema=S
    )
    op.create_index(
        "ix_contacts_tenant_email_status", "contacts", ["tenant_id", "email_status"], schema=S
    )
    _sources(SOURCES_4)
    op.create_table(
        "validation_runs",
        sa.Column("tenant_id", sa.UUID(), nullable=False),
        sa.Column("created_by", sa.UUID(), nullable=True),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column("scope", postgresql.JSONB(), nullable=False),
        sa.Column("summary", sa.String(300), nullable=False),
        sa.Column("progress", postgresql.JSONB(), nullable=False),
        sa.Column("totals", postgresql.JSONB(), nullable=False),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "status IN ('queued', 'running', 'completed', 'failed', 'canceled')",
            name=op.f("ck_validation_runs_status"),
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id"],
            [f"{S}.tenants.id"],
            name=op.f("fk_validation_runs_tenant_id_tenants"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["created_by"],
            [f"{S}.users.id"],
            name=op.f("fk_validation_runs_created_by_users"),
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_validation_runs")),
        schema=S,
    )
    op.create_index(
        "ix_validation_runs_tenant_created",
        "validation_runs",
        ["tenant_id", "created_at"],
        schema=S,
    )
    rule = "tenant_id::text = current_setting('app.tenant_id', true)"
    op.execute(f"ALTER TABLE {S}.validation_runs ENABLE ROW LEVEL SECURITY")
    op.execute(f"ALTER TABLE {S}.validation_runs FORCE ROW LEVEL SECURITY")
    op.execute(
        f"CREATE POLICY tenant_isolation ON {S}.validation_runs USING ({rule}) WITH CHECK ({rule})"
    )


def downgrade() -> None:
    op.execute(f"DROP POLICY IF EXISTS tenant_isolation ON {S}.validation_runs")
    op.drop_index("ix_validation_runs_tenant_created", table_name="validation_runs", schema=S)
    op.drop_table("validation_runs", schema=S)
    op.execute(f"DELETE FROM {S}.data_points WHERE source_type = 'validacion'")
    _sources(SOURCES_3)
    op.drop_index("ix_contacts_tenant_email_status", table_name="contacts", schema=S)
    op.drop_constraint(op.f("ck_contacts_email_status"), "contacts", schema=S)
    op.drop_column("contacts", "email_check_reason", schema=S)
    op.drop_column("contacts", "email_checked_at", schema=S)
    op.drop_column("contacts", "email_status", schema=S)
