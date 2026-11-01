"""Memoria global de dominios y formatos de correo (IA propia de prospección).

Revision ID: 0017
Revises: 0016
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0017"
down_revision = "0016"
branch_labels = None
depends_on = None


def upgrade() -> None:
    now = sa.text("now()")
    op.create_table(
        "company_domains",
        sa.Column("key", sa.String(320), primary_key=True),
        sa.Column("domain", sa.String(253), nullable=False),
        sa.Column("source", sa.String(40), nullable=False, server_default=""),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=now),
        schema="orkesta",
    )
    op.create_table(
        "domain_patterns",
        sa.Column("domain", sa.String(253), primary_key=True),
        sa.Column("pattern", sa.String(40), nullable=False),
        sa.Column("seen", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("confirmed", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("bounced", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("example", sa.Text(), nullable=False, server_default=""),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=now),
        schema="orkesta",
    )


def downgrade() -> None:
    op.drop_table("domain_patterns", schema="orkesta")
    op.drop_table("company_domains", schema="orkesta")
