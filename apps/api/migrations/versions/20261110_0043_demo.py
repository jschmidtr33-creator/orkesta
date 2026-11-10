"""Demo de 2 semanas para empresas: fecha en que vence la prueba. ORKESTA pasa a ser solo por
invitación: se cierra el registro abierto.

Revision ID: 0043
Revises: 0042
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0043"
down_revision = "0042"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("tenants", sa.Column("trial_ends_at", sa.DateTime(timezone=True)),
                  schema="orkesta")  # fmt: skip
    op.execute(
        "UPDATE orkesta.platform_settings SET value = value || '{\"signup_open\": false}'::jsonb "
        "WHERE key = 'app'"
    )


def downgrade() -> None:
    op.drop_column("tenants", "trial_ends_at", schema="orkesta")
