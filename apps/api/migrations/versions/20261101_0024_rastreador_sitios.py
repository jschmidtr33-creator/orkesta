"""Rastreador propio: lo que ORKESTA lee del sitio oficial de cada empresa del directorio.

Revision ID: 0024
Revises: 0023
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0024"
down_revision = "0023"
branch_labels = None
depends_on = None

COLUMNS = (
    sa.Column("crawled_at", sa.DateTime(timezone=True)),
    sa.Column("site_status", sa.String(20)),
    sa.Column("phone", sa.String(30)),
    sa.Column("company_email", sa.String(320)),
    sa.Column("description", sa.String(400)),
    sa.Column("email_pattern", sa.String(20)),
)


def upgrade() -> None:
    for column in COLUMNS:
        op.add_column("company_directory", column, schema="orkesta")
    op.create_index("ix_company_directory_crawl", "company_directory", ["crawled_at"],
                    schema="orkesta")  # fmt: skip


def downgrade() -> None:
    op.drop_index("ix_company_directory_crawl", "company_directory", schema="orkesta")
    for column in reversed(COLUMNS):
        op.drop_column("company_directory", column.name, schema="orkesta")
