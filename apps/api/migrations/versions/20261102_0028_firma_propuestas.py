"""Firma electrónica de propuestas: firmante, imagen de la firma, huella del documento y
evidencia (IP, navegador, fecha).

Revision ID: 0028
Revises: 0027
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0028"
down_revision = "0027"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("proposals", sa.Column("signature", postgresql.JSONB(), nullable=False,
                                         server_default="{}"), schema="orkesta")  # fmt: skip


def downgrade() -> None:
    op.drop_column("proposals", "signature", schema="orkesta")
