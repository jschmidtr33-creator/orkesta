"""Plantillas personales o de equipo: «Guardar como plantilla» queda solo para quien la guarda;
«Compartir con el equipo» la deja visible para toda la empresa. Las que ya existían eran de
equipo y siguen siéndolo.

Revision ID: 0038
Revises: 0037
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0038"
down_revision = "0037"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("team_templates", sa.Column("shared", sa.Boolean(), nullable=False,
                                              server_default=sa.true()), schema="orkesta")  # fmt: skip


def downgrade() -> None:
    op.drop_column("team_templates", "shared", schema="orkesta")
