"""catalogo de empresas

Catálogo público de empresas LATAM (Wikidata, CC0) compartido por todas las organizaciones:
nombre, país, ciudad, sitio, industria y tamaño. Sin datos de personas.

Revision ID: 41adcd14c4b7
Revises: 98ce7331ea7c
Create Date: 2026-10-29 22:12:47.281243+00:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "41adcd14c4b7"
down_revision: str | None = "98ce7331ea7c"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "catalog_companies",
        sa.Column("qid", sa.String(length=20), nullable=False),
        sa.Column("name", sa.String(length=300), nullable=False),
        sa.Column("name_search", sa.Text(), nullable=False),
        sa.Column("company_key", sa.String(length=300), nullable=False),
        sa.Column("country_code", sa.String(length=2), nullable=True),
        sa.Column("city", sa.String(length=120), nullable=True),
        sa.Column("domain", sa.String(length=253), nullable=True),
        sa.Column("industry_code", sa.String(length=40), nullable=True),
        sa.Column("industry_labels", sa.String(length=400), nullable=True),
        sa.Column("employees", sa.Integer(), nullable=True),
        sa.Column("size_band", sa.String(length=20), nullable=True),
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
            "country_code IS NULL OR length(country_code) = 2",
            name=op.f("ck_catalog_companies_country_code"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_catalog_companies")),
        sa.UniqueConstraint("qid", name=op.f("uq_catalog_companies_qid")),
    )
    op.create_index(
        "ix_catalog_companies_country_industry",
        "catalog_companies",
        ["country_code", "industry_code"],
        unique=False,
    )
    op.create_index("ix_catalog_companies_domain", "catalog_companies", ["domain"], unique=False)
    op.create_index(
        "ix_catalog_companies_name_trgm",
        "catalog_companies",
        ["name_search"],
        unique=False,
        postgresql_using="gin",
        postgresql_ops={"name_search": "gin_trgm_ops"},
    )


def downgrade() -> None:
    op.drop_index(
        "ix_catalog_companies_name_trgm",
        table_name="catalog_companies",
        postgresql_using="gin",
        postgresql_ops={"name_search": "gin_trgm_ops"},
    )
    op.drop_index("ix_catalog_companies_domain", table_name="catalog_companies")
    op.drop_index("ix_catalog_companies_country_industry", table_name="catalog_companies")
    op.drop_table("catalog_companies")
