"""Directorio propio de empresas (registros públicos + aprendizaje de búsquedas).

Revision ID: 0022
Revises: 0021
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0022"
down_revision = "0021"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "company_directory",
        sa.Column("key", sa.String(340), primary_key=True),
        sa.Column("country", sa.String(2), nullable=False),
        sa.Column("tax_id", sa.String(30)),
        sa.Column("name", sa.String(300), nullable=False),
        sa.Column("name_key", sa.String(300), nullable=False),
        sa.Column("industry_code", sa.String(40)),
        sa.Column("activity_code", sa.String(10)),
        sa.Column("sector", sa.String(200)),
        sa.Column("city", sa.String(120)),
        sa.Column("region", sa.String(120)),
        sa.Column("place_key", sa.String(250)),
        sa.Column("search_text", sa.String(600), nullable=False, server_default=""),
        sa.Column("revenue", sa.Numeric(20, 2)),
        sa.Column("employees", sa.Integer()),
        sa.Column("website", sa.String(300)),
        sa.Column("domain", sa.String(253)),
        sa.Column("linkedin", sa.String(300)),
        sa.Column("year", sa.SmallInteger()),
        sa.Column("source", sa.String(60), nullable=False, server_default=""),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        schema="orkesta",
    )
    for name, cols in (
        ("ix_company_directory_country_industry", ["country", "industry_code"]),
        ("ix_company_directory_country_name", ["country", "name_key"]),
        ("ix_company_directory_country_revenue", ["country", "revenue"]),
    ):
        op.create_index(name, "company_directory", cols, schema="orkesta")


def downgrade() -> None:
    op.drop_table("company_directory", schema="orkesta")
