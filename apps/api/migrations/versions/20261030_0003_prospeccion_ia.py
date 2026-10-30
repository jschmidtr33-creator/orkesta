"""prospección con IA

Revision ID: 0003
Revises: 0002
Create Date: 2026-10-30 12:30:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0003"
down_revision: str | None = "0002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None
S = "orkesta"
TENANT_TABLES = ("research_runs", "research_items")
OLD_SOURCES = "('importacion', 'manual', 'enriquecimiento')"
NEW_SOURCES = "('importacion', 'manual', 'enriquecimiento', 'investigacion')"


def _created_at() -> sa.Column:
    return sa.Column(
        "created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False
    )


def upgrade() -> None:
    op.create_table(
        "research_runs",
        sa.Column("tenant_id", sa.UUID(), nullable=False),
        sa.Column("created_by", sa.UUID(), nullable=True),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column("filters", postgresql.JSONB(), nullable=False),
        sa.Column("summary", sa.String(300), nullable=False),
        sa.Column("progress", postgresql.JSONB(), nullable=False),
        sa.Column("totals", postgresql.JSONB(), nullable=False),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("id", sa.UUID(), nullable=False),
        _created_at(),
        sa.CheckConstraint(
            "status IN ('queued', 'running', 'completed', 'failed', 'canceled')",
            name=op.f("ck_research_runs_status"),
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id"],
            [f"{S}.tenants.id"],
            name=op.f("fk_research_runs_tenant_id_tenants"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["created_by"],
            [f"{S}.users.id"],
            name=op.f("fk_research_runs_created_by_users"),
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_research_runs")),
        schema=S,
    )
    op.create_index(
        "ix_research_runs_tenant_created", "research_runs", ["tenant_id", "created_at"], schema=S
    )
    op.create_table(
        "research_items",
        sa.Column("tenant_id", sa.UUID(), nullable=False),
        sa.Column("run_id", sa.UUID(), nullable=False),
        sa.Column("kind", sa.String(10), nullable=False),
        sa.Column("key", sa.String(400), nullable=False),
        sa.Column("company_key", sa.String(300), nullable=False),
        sa.Column("data", postgresql.JSONB(), nullable=False),
        sa.Column("sources", postgresql.JSONB(), nullable=False),
        sa.Column("score", sa.SmallInteger(), nullable=False),
        sa.Column("saved_contact_id", sa.UUID(), nullable=True),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("id", sa.UUID(), nullable=False),
        _created_at(),
        sa.CheckConstraint("kind IN ('company', 'person')", name=op.f("ck_research_items_kind")),
        sa.ForeignKeyConstraint(
            ["tenant_id"],
            [f"{S}.tenants.id"],
            name=op.f("fk_research_items_tenant_id_tenants"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["run_id"],
            [f"{S}.research_runs.id"],
            name=op.f("fk_research_items_run_id_research_runs"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["saved_contact_id"],
            [f"{S}.contacts.id"],
            name=op.f("fk_research_items_saved_contact_id_contacts"),
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_research_items")),
        sa.UniqueConstraint(
            "run_id", "kind", "key", name=op.f("uq_research_items_run_id_kind_key")
        ),
        schema=S,
    )
    op.create_index(
        "ix_research_items_run_kind", "research_items", ["run_id", "kind", "score"], schema=S
    )
    op.create_table(
        "web_cache",
        sa.Column("url_hash", sa.String(64), nullable=False),
        sa.Column("url", sa.Text(), nullable=False),
        sa.Column("final_url", sa.Text(), nullable=False),
        sa.Column("status", sa.Integer(), nullable=False),
        sa.Column("content_type", sa.String(120), nullable=False),
        sa.Column("body", sa.Text(), nullable=False),
        sa.Column(
            "fetched_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("url_hash", name=op.f("pk_web_cache")),
        schema=S,
    )
    for table, column, values in (
        ("contacts", "source", NEW_SOURCES),
        ("data_points", "source_type", NEW_SOURCES),
    ):
        name = f"ck_{table}_{column}"
        op.execute(f"ALTER TABLE {S}.{table} DROP CONSTRAINT {name}")
        op.execute(f"ALTER TABLE {S}.{table} ADD CONSTRAINT {name} CHECK ({column} IN {values})")

    rule = "tenant_id::text = current_setting('app.tenant_id', true)"
    for table in TENANT_TABLES:
        op.execute(f"ALTER TABLE {S}.{table} ENABLE ROW LEVEL SECURITY")
        op.execute(f"ALTER TABLE {S}.{table} FORCE ROW LEVEL SECURITY")
        op.execute(
            f"CREATE POLICY tenant_isolation ON {S}.{table} USING ({rule}) WITH CHECK ({rule})"
        )


def downgrade() -> None:
    for table in TENANT_TABLES:
        op.execute(f"DROP POLICY IF EXISTS tenant_isolation ON {S}.{table}")
    for table, column in (("contacts", "source"), ("data_points", "source_type")):
        name = f"ck_{table}_{column}"
        op.execute(f"DELETE FROM {S}.{table} WHERE {column} = 'investigacion'")
        op.execute(f"ALTER TABLE {S}.{table} DROP CONSTRAINT {name}")
        op.execute(
            f"ALTER TABLE {S}.{table} ADD CONSTRAINT {name} CHECK ({column} IN {OLD_SOURCES})"
        )
    op.drop_table("web_cache", schema=S)
    op.drop_index("ix_research_items_run_kind", table_name="research_items", schema=S)
    op.drop_table("research_items", schema=S)
    op.drop_index("ix_research_runs_tenant_created", table_name="research_runs", schema=S)
    op.drop_table("research_runs", schema=S)
