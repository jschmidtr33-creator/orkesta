"""Tiempo de trabajo: minutos con ORKESTA abierto y en uso, por persona.

Una fila por persona y minuto (``at`` redondeado al minuto) mientras la app está abierta y
visible; ``active`` indica si en ese minuto hubo interacción (teclado, ratón, toque). Solo la
jefatura (de su equipo) y la administración lo ven en Mi equipo; nadie ve su propia medición.

Revision ID: 0055
Revises: 0054
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0055"
down_revision = "0054"
branch_labels = None
depends_on = None

TENANT = "tenant_id::text = current_setting('app.tenant_id', true)"


def upgrade() -> None:
    op.create_table(
        "presence_minutes",
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("orkesta.tenants.id", ondelete="CASCADE"), primary_key=True),
        sa.Column("user_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("orkesta.users.id", ondelete="CASCADE"), primary_key=True),
        sa.Column("at", sa.DateTime(timezone=True), primary_key=True),
        sa.Column("active", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("section", sa.String(30), nullable=False, server_default=""),
        schema="orkesta",
    )  # fmt: skip
    op.create_index("ix_presence_user_at", "presence_minutes", ["tenant_id", "user_id", "at"],
                    schema="orkesta")  # fmt: skip
    t = "orkesta.presence_minutes"
    op.execute(f"ALTER TABLE {t} ENABLE ROW LEVEL SECURITY")
    op.execute(f"ALTER TABLE {t} FORCE ROW LEVEL SECURITY")
    op.execute(f"CREATE POLICY tenant_isolation ON {t} USING ({TENANT}) WITH CHECK ({TENANT})")


def downgrade() -> None:
    op.drop_table("presence_minutes", schema="orkesta")
