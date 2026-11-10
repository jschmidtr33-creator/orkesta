"""Catálogo: cada producto guarda quién lo creó.

Cada persona edita y elimina solo lo que ella agregó al catálogo. Los productos que ya existían
quedan sin dueño y los sigue administrando la administración o la jefatura.

Revision ID: 0040
Revises: 0039
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0040"
down_revision = "0039"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "products",
        sa.Column("created_by", postgresql.UUID(as_uuid=True), nullable=True),
        schema="orkesta",
    )
    op.create_foreign_key(
        op.f("fk_products_created_by_users"),
        "products",
        "users",
        ["created_by"],
        ["id"],
        source_schema="orkesta",
        referent_schema="orkesta",
        ondelete="SET NULL",
    )


def downgrade() -> None:
    op.drop_constraint(op.f("fk_products_created_by_users"), "products", schema="orkesta")
    op.drop_column("products", "created_by", schema="orkesta")
