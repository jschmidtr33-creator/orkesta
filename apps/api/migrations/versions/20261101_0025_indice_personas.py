"""Índice propio de perfiles profesionales públicos y lista de exclusión.

Revision ID: 0025
Revises: 0024
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0025"
down_revision = "0024"
branch_labels = None
depends_on = None


def upgrade() -> None:
    now = sa.text("now()")
    op.create_table(
        "people_index",
        sa.Column("key", sa.String(400), primary_key=True),
        sa.Column("country", sa.String(2), nullable=False),
        sa.Column("full_name", sa.String(200), nullable=False),
        sa.Column("first_name", sa.String(120), nullable=False, server_default=""),
        sa.Column("last_name", sa.String(160), nullable=False, server_default=""),
        sa.Column("title", sa.String(300), nullable=False, server_default=""),
        sa.Column("level", sa.String(20)),
        sa.Column("area", sa.String(30)),
        sa.Column("company", sa.String(300), nullable=False),
        sa.Column("company_key", sa.String(300), nullable=False),
        sa.Column("domain", sa.String(253)),
        sa.Column("location", sa.String(200), nullable=False, server_default=""),
        sa.Column("linkedin", sa.String(300)),
        sa.Column("email", sa.String(320)),
        sa.Column("email_status", sa.String(12)),
        sa.Column("seen", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("first_seen", sa.DateTime(timezone=True), nullable=False, server_default=now),
        sa.Column("last_seen", sa.DateTime(timezone=True), nullable=False, server_default=now),
        schema="orkesta",
    )
    for name, cols in (
        ("ix_people_index_country_company", ["country", "company_key"]),
        ("ix_people_index_country_level", ["country", "level"]),
        ("ix_people_index_email", ["email"]),
    ):
        op.create_index(name, "people_index", cols, schema="orkesta")
    op.create_table(
        "people_optout",
        sa.Column("hash", sa.String(64), primary_key=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=now),
        schema="orkesta",
    )


def downgrade() -> None:
    op.drop_table("people_optout", schema="orkesta")
    op.drop_table("people_index", schema="orkesta")
