"""Devuelve al cupo del plan las verificaciones que no se hicieron.

Cuando el verificador externo no respondía (por ejemplo, sin créditos), cada intento se
descontaba igual del cupo mensual de la empresa. Las corridas de este mes en las que el
verificador no estuvo disponible y no confirmó ningún buzón vuelven a cero.

Revision ID: 0054
Revises: 0053
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0054"
down_revision = "0053"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    tenants = [r[0] for r in bind.execute(sa.text("SELECT id FROM orkesta.tenants"))]
    for tenant in tenants:
        bind.execute(
            sa.text(
                "SELECT set_config('app.tenant_id', :t, true), set_config('app.scope', '', true), "
                "set_config('app.private', '', true)"
            ),
            {"t": str(tenant)},
        )
        bind.execute(
            sa.text("""
            UPDATE orkesta.validation_runs
            SET totals = jsonb_set(totals, '{paid_checks}', '0')
            WHERE created_at >= date_trunc('month', now())
              AND coalesce((totals->>'paid_checks')::int, 0) > 0
              AND coalesce((totals->>'smtp_blocked')::int, 0) > 0
              AND coalesce((totals->>'valido')::int, 0) = 0
              AND coalesce((totals->>'catch_all')::int, 0) = 0
              AND coalesce((totals->>'riesgoso')::int, 0) = 0
              AND coalesce((totals->>'found')::int, 0) = 0
        """)
        )
    bind.execute(sa.text("SELECT set_config('app.tenant_id', '', true)"))


def downgrade() -> None:
    pass
