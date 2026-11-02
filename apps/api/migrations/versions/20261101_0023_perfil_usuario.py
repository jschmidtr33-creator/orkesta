"""Perfil de usuario: foto, cargo, teléfono, correo de recuperación y zona horaria.

Revision ID: 0023
Revises: 0022
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0023"
down_revision = "0022"
branch_labels = None
depends_on = None

COLUMNS = (
    sa.Column("job_title", sa.String(160)),
    sa.Column("phone", sa.String(40)),
    sa.Column("recovery_email", sa.String(320)),
    sa.Column("timezone", sa.String(60)),
    sa.Column("avatar", sa.LargeBinary()),
    sa.Column("avatar_type", sa.String(30)),
    sa.Column("avatar_version", sa.Integer(), nullable=False, server_default="0"),
)


def upgrade() -> None:
    for column in COLUMNS:
        op.add_column("users", column, schema="orkesta")


def downgrade() -> None:
    for column in reversed(COLUMNS):
        op.drop_column("users", column.name, schema="orkesta")
