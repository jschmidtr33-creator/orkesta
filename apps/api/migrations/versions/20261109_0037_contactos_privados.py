"""Contactos y listas privados: cada persona ve y trabaja solo los suyos, incluida la administración.

Se suma una regla más estricta solo para ``contacts`` (y, por la regla de sus tablas hijas, lo
que cuelga de ellos): ``app.private`` lleva el usuario de la solicitud. Sin valor (procesos
internos, cifras agregadas) no restringe. Los contactos sin dueño, anteriores a esta regla, los
ve solo la administración (``app.private_admin``).

Revision ID: 0037
Revises: 0036
"""

from __future__ import annotations

from alembic import op

revision = "0037"
down_revision = "0036"
branch_labels = None
depends_on = None

TABLES = {"contacts": "owner_user_id", "contact_lists": "created_by"}


def _rule(owner: str) -> str:
    return (
        "coalesce(current_setting('app.private', true), '') = '' "
        f"OR {owner}::text = current_setting('app.private', true) "
        f"OR ({owner} IS NULL AND coalesce(current_setting('app.private_admin', true), '') = '1')"
    )


def upgrade() -> None:
    for table, owner in TABLES.items():
        t, rule = f"orkesta.{table}", _rule(owner)
        op.execute(f"CREATE POLICY private_select ON {t} AS RESTRICTIVE FOR SELECT USING ({rule})")
        op.execute(f"CREATE POLICY private_update ON {t} AS RESTRICTIVE FOR UPDATE "
                   f"USING ({rule}) WITH CHECK (true)")  # fmt: skip
        op.execute(f"CREATE POLICY private_delete ON {t} AS RESTRICTIVE FOR DELETE USING ({rule})")


def downgrade() -> None:
    for table in TABLES:
        for name in ("private_select", "private_update", "private_delete"):
            op.execute(f"DROP POLICY IF EXISTS {name} ON orkesta.{table}")
