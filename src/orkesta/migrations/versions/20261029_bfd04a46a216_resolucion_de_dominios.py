"""resolucion de dominios

Clave de coincidencia de empresas, dominio seleccionado por empresa (como mucho uno),
URL final y título del sitio, y resumen de enriquecimiento del lote.

Revision ID: bfd04a46a216
Revises: 71ab18eb89dc
Create Date: 2026-10-29 18:04:25.805293+00:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "bfd04a46a216"
down_revision: str | None = "71ab18eb89dc"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("companies", sa.Column("match_key", sa.String(length=300), nullable=True))
    op.add_column("companies", sa.Column("domain_status", sa.String(length=20), nullable=True))
    op.add_column(
        "companies", sa.Column("domain_checked_at", sa.DateTime(timezone=True), nullable=True)
    )
    op.create_check_constraint(
        op.f("ck_companies_domain_status"),
        "companies",
        "domain_status IS NULL OR domain_status IN ('candidate', 'probable', 'confirmed', 'rejected', 'unknown', 'error')",
    )
    op.create_index(
        "ix_companies_org_match",
        "companies",
        ["organization_id", "match_key", "country_code"],
        unique=False,
    )
    op.add_column(
        "domains",
        sa.Column("is_selected", sa.Boolean(), server_default=sa.text("false"), nullable=False),
    )
    op.add_column("domains", sa.Column("final_url", sa.Text(), nullable=True))
    op.add_column("domains", sa.Column("site_title", sa.String(length=300), nullable=True))
    op.create_index(
        "uq_domains_one_selected",
        "domains",
        ["company_id"],
        unique=True,
        postgresql_where=sa.text("is_selected"),
    )
    op.add_column(
        "enrichment_batches",
        sa.Column(
            "enrichment_summary",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
    )


def downgrade() -> None:
    op.drop_column("enrichment_batches", "enrichment_summary")
    op.drop_index(
        "uq_domains_one_selected", table_name="domains", postgresql_where=sa.text("is_selected")
    )
    op.drop_column("domains", "site_title")
    op.drop_column("domains", "final_url")
    op.drop_column("domains", "is_selected")
    op.drop_index("ix_companies_org_match", table_name="companies")
    op.drop_constraint(op.f("ck_companies_domain_status"), "companies", type_="check")
    op.drop_column("companies", "domain_checked_at")
    op.drop_column("companies", "domain_status")
    op.drop_column("companies", "match_key")
