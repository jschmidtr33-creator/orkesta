"""base de prospectos

Personas buscables por filtros (solo nombre, cargo, empresa, correo, teléfono, ciudad, país e
industria), patrón real de correo por dominio y cargas de bases. pg_trgm permite buscar cargos y
empresas por similitud ("gerente compras" encuentra "Gerente de Compras").

Revision ID: 98ce7331ea7c
Revises: ef23dd0b2ce6
Create Date: 2026-10-29 21:52:48.421216+00:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "98ce7331ea7c"
down_revision: str | None = "ef23dd0b2ce6"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute("CREATE EXTENSION IF NOT EXISTS pg_trgm")
    # Trabajos sin lote (cargas de bases): batch_id opcional y clave única también sin lote.
    op.alter_column("enrichment_jobs", "batch_id", existing_type=sa.UUID(), nullable=True)
    op.drop_constraint(
        op.f("uq_enrichment_jobs_batch_id_idempotency_key"), "enrichment_jobs", type_="unique"
    )
    op.create_unique_constraint(
        op.f("uq_enrichment_jobs_batch_id_idempotency_key"),
        "enrichment_jobs",
        ["batch_id", "idempotency_key"],
        postgresql_nulls_not_distinct=True,
    )
    op.create_table(
        "domain_patterns",
        sa.Column("domain", sa.String(length=253), nullable=False),
        sa.Column("pattern", sa.String(length=40), nullable=False),
        sa.Column("people", sa.Integer(), nullable=False),
        sa.Column("agreeing", sa.Integer(), nullable=False),
        sa.Column("organization_id", sa.UUID(), nullable=False),
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "agreeing <= people AND agreeing >= 1", name=op.f("ck_domain_patterns_counts")
        ),
        sa.ForeignKeyConstraint(
            ["organization_id"],
            ["organizations.id"],
            name=op.f("fk_domain_patterns_organization_id_organizations"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_domain_patterns")),
        sa.UniqueConstraint(
            "organization_id", "domain", name=op.f("uq_domain_patterns_organization_id_domain")
        ),
    )
    op.create_index(
        op.f("ix_domain_patterns_organization_id"),
        "domain_patterns",
        ["organization_id"],
        unique=False,
    )
    op.create_table(
        "prospects",
        sa.Column("dedup_key", sa.String(length=400), nullable=False),
        sa.Column("full_name", sa.String(length=300), nullable=False),
        sa.Column("first_name", sa.String(length=200), nullable=False),
        sa.Column("last_name", sa.String(length=200), nullable=False),
        sa.Column("title", sa.String(length=300), nullable=True),
        sa.Column("company", sa.String(length=300), nullable=False),
        sa.Column("company_key", sa.String(length=300), nullable=False),
        sa.Column("email", sa.String(length=320), nullable=True),
        sa.Column("email_domain", sa.String(length=253), nullable=True),
        sa.Column("phone", sa.String(length=40), nullable=True),
        sa.Column("city", sa.String(length=120), nullable=True),
        sa.Column("country_code", sa.String(length=2), nullable=True),
        sa.Column("industry", sa.String(length=200), nullable=True),
        sa.Column("industry_code", sa.String(length=40), nullable=True),
        sa.Column("level_code", sa.String(length=30), nullable=True),
        sa.Column("area_code", sa.String(length=40), nullable=True),
        sa.Column("title_search", sa.Text(), nullable=False),
        sa.Column("company_search", sa.Text(), nullable=False),
        sa.Column("source", sa.String(length=20), nullable=False),
        sa.Column("source_name", sa.String(length=200), nullable=True),
        sa.Column("organization_id", sa.UUID(), nullable=False),
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "source IN ('base', 'web', 'wikidata')", name=op.f("ck_prospects_source")
        ),
        sa.CheckConstraint(
            "country_code IS NULL OR length(country_code) = 2",
            name=op.f("ck_prospects_country_code"),
        ),
        sa.CheckConstraint(
            "email IS NULL OR email = lower(email)", name=op.f("ck_prospects_email_lowercase")
        ),
        sa.ForeignKeyConstraint(
            ["organization_id"],
            ["organizations.id"],
            name=op.f("fk_prospects_organization_id_organizations"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_prospects")),
        sa.UniqueConstraint(
            "organization_id", "dedup_key", name=op.f("uq_prospects_organization_id_dedup_key")
        ),
    )
    op.create_index(
        "ix_prospects_company_trgm",
        "prospects",
        ["company_search"],
        unique=False,
        postgresql_using="gin",
        postgresql_ops={"company_search": "gin_trgm_ops"},
    )
    op.create_index(
        "ix_prospects_org_company", "prospects", ["organization_id", "company_key"], unique=False
    )
    op.create_index(
        "ix_prospects_org_country", "prospects", ["organization_id", "country_code"], unique=False
    )
    op.create_index(
        "ix_prospects_org_email_domain",
        "prospects",
        ["organization_id", "email_domain"],
        unique=False,
    )
    op.create_index("ix_prospects_org_level", "prospects", ["organization_id", "level_code"])
    op.create_index("ix_prospects_org_area", "prospects", ["organization_id", "area_code"])
    op.create_index(
        "ix_prospects_org_industry", "prospects", ["organization_id", "industry_code"], unique=False
    )
    op.create_index(
        op.f("ix_prospects_organization_id"), "prospects", ["organization_id"], unique=False
    )
    op.create_index(
        "ix_prospects_title_trgm",
        "prospects",
        ["title_search"],
        unique=False,
        postgresql_using="gin",
        postgresql_ops={"title_search": "gin_trgm_ops"},
    )
    op.create_table(
        "prospect_uploads",
        sa.Column("filename", sa.String(length=255), nullable=False),
        sa.Column("content", sa.LargeBinary(), nullable=True),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column(
            "summary",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("user_id", sa.UUID(), nullable=True),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("organization_id", sa.UUID(), nullable=False),
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "status IN ('queued', 'running', 'completed', 'failed')",
            name=op.f("ck_prospect_uploads_status"),
        ),
        sa.ForeignKeyConstraint(
            ["organization_id"],
            ["organizations.id"],
            name=op.f("fk_prospect_uploads_organization_id_organizations"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["users.id"],
            name=op.f("fk_prospect_uploads_user_id_users"),
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_prospect_uploads")),
    )
    op.create_index(
        op.f("ix_prospect_uploads_organization_id"),
        "prospect_uploads",
        ["organization_id"],
        unique=False,
    )
    op.create_table(
        "web_discoveries",
        sa.Column("domain", sa.String(length=253), nullable=False),
        sa.Column("company", sa.String(length=300), nullable=False),
        sa.Column("checked_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("people_found", sa.Integer(), nullable=False),
        sa.Column("pages_visited", sa.Integer(), nullable=False),
        sa.Column("blocked", sa.Boolean(), nullable=False),
        sa.Column("organization_id", sa.UUID(), nullable=False),
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["organization_id"],
            ["organizations.id"],
            name=op.f("fk_web_discoveries_organization_id_organizations"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_web_discoveries")),
        sa.UniqueConstraint(
            "organization_id", "domain", name=op.f("uq_web_discoveries_organization_id_domain")
        ),
    )
    op.create_index(
        op.f("ix_web_discoveries_organization_id"),
        "web_discoveries",
        ["organization_id"],
        unique=False,
    )
    op.create_table(
        "discovery_runs",
        sa.Column("filters", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column("total", sa.Integer(), nullable=False),
        sa.Column("done", sa.Integer(), nullable=False),
        sa.Column("people_found", sa.Integer(), nullable=False),
        sa.Column("user_id", sa.UUID(), nullable=True),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("organization_id", sa.UUID(), nullable=False),
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "status IN ('queued', 'running', 'completed', 'failed')",
            name=op.f("ck_discovery_runs_status"),
        ),
        sa.ForeignKeyConstraint(
            ["organization_id"],
            ["organizations.id"],
            name=op.f("fk_discovery_runs_organization_id_organizations"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["users.id"],
            name=op.f("fk_discovery_runs_user_id_users"),
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_discovery_runs")),
    )
    op.create_index(
        op.f("ix_discovery_runs_organization_id"),
        "discovery_runs",
        ["organization_id"],
        unique=False,
    )
    op.add_column("prospects", sa.Column("email_status", sa.String(length=10), nullable=True))
    op.add_column("prospects", sa.Column("email_confidence", sa.SmallInteger(), nullable=True))
    op.create_check_constraint(
        op.f("ck_prospects_email_status"),
        "prospects",
        "email_status IS NULL OR email_status IN ('known', 'inferred')",
    )


def downgrade() -> None:
    op.drop_constraint(op.f("ck_prospects_email_status"), "prospects", type_="check")
    op.drop_column("prospects", "email_confidence")
    op.drop_column("prospects", "email_status")
    op.drop_index(op.f("ix_discovery_runs_organization_id"), table_name="discovery_runs")
    op.drop_table("discovery_runs")
    op.drop_index(op.f("ix_web_discoveries_organization_id"), table_name="web_discoveries")
    op.drop_table("web_discoveries")
    op.execute("DELETE FROM enrichment_jobs WHERE batch_id IS NULL")
    op.drop_constraint(
        op.f("uq_enrichment_jobs_batch_id_idempotency_key"), "enrichment_jobs", type_="unique"
    )
    op.create_unique_constraint(
        op.f("uq_enrichment_jobs_batch_id_idempotency_key"),
        "enrichment_jobs",
        ["batch_id", "idempotency_key"],
    )
    op.alter_column("enrichment_jobs", "batch_id", existing_type=sa.UUID(), nullable=False)
    op.drop_index(op.f("ix_prospect_uploads_organization_id"), table_name="prospect_uploads")
    op.drop_table("prospect_uploads")
    op.drop_index(
        "ix_prospects_title_trgm",
        table_name="prospects",
        postgresql_using="gin",
        postgresql_ops={"title_search": "gin_trgm_ops"},
    )
    op.drop_index(op.f("ix_prospects_organization_id"), table_name="prospects")
    op.drop_index("ix_prospects_org_industry", table_name="prospects")
    op.drop_index("ix_prospects_org_email_domain", table_name="prospects")
    op.drop_index("ix_prospects_org_country", table_name="prospects")
    op.drop_index("ix_prospects_org_company", table_name="prospects")
    op.drop_index(
        "ix_prospects_company_trgm",
        table_name="prospects",
        postgresql_using="gin",
        postgresql_ops={"company_search": "gin_trgm_ops"},
    )
    op.drop_index("ix_prospects_org_area", table_name="prospects")
    op.drop_index("ix_prospects_org_level", table_name="prospects")
    op.drop_table("prospects")
    op.drop_index(op.f("ix_domain_patterns_organization_id"), table_name="domain_patterns")
    op.drop_table("domain_patterns")
