"""Automatización: cada persona activa o desactiva una regla para sus propias cosas.

`muted_users` guarda quiénes la apagaron para sí: la regla no corre sobre sus contactos ni sus
oportunidades, y sigue corriendo para el resto del equipo.

Revision ID: 0041
Revises: 0040
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0041"
down_revision = "0040"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "automation_rules",
        sa.Column(
            "muted_users",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
        schema="orkesta",
    )


def downgrade() -> None:
    op.drop_column("automation_rules", "muted_users", schema="orkesta")
