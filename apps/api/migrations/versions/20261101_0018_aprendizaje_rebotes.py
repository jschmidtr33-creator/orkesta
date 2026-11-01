"""Aprendizaje de rebotes: evidencia por formato y direcciones que rebotaron (huella).

Revision ID: 0018
Revises: 0017
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0018"
down_revision = "0017"
branch_labels = None
depends_on = None


def upgrade() -> None:
    now = sa.text("now()")
    op.create_table(
        "pattern_stats",
        sa.Column("domain", sa.String(253), primary_key=True),
        sa.Column("pattern", sa.String(40), primary_key=True),
        sa.Column("seen", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("confirmed", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("bounced", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=now),
        schema="orkesta",
    )
    op.create_table(
        "bounced_addresses",
        sa.Column("address_hash", sa.String(64), primary_key=True),
        sa.Column("domain", sa.String(253), nullable=False),
        sa.Column("pattern", sa.String(40), nullable=False, server_default=""),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=now),
        schema="orkesta",
    )
    op.create_index(
        "ix_bounced_addresses_domain", "bounced_addresses", ["domain"], schema="orkesta"
    )
    # La evidencia ya acumulada pasa a la tabla por formato.
    op.execute(
        "INSERT INTO orkesta.pattern_stats (domain, pattern, seen, confirmed, bounced) "
        "SELECT domain, pattern, seen, confirmed, bounced FROM orkesta.domain_patterns"
    )


def downgrade() -> None:
    op.drop_index("ix_bounced_addresses_domain", table_name="bounced_addresses", schema="orkesta")
    op.drop_table("bounced_addresses", schema="orkesta")
    op.drop_table("pattern_stats", schema="orkesta")
