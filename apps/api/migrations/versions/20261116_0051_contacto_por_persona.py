"""Cada persona puede trabajar cualquier contacto, aunque otra del equipo ya lo tenga.

Los contactos son privados (cada persona ve los suyos), así que la regla «sin repetidos» pasa a
ser por persona: la misma persona no tiene dos veces el mismo contacto, pero dos personas del
equipo sí pueden tener cada una su propia ficha del mismo contacto.

Revision ID: 0051
Revises: 0050
"""

from __future__ import annotations

from alembic import op

revision = "0051"
down_revision = "0050"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TABLE orkesta.contacts DROP CONSTRAINT uq_contacts_tenant_id_dedup_key")
    op.execute(
        "ALTER TABLE orkesta.contacts ADD CONSTRAINT uq_contacts_owner_dedup "
        "UNIQUE NULLS NOT DISTINCT (tenant_id, owner_user_id, dedup_key)"
    )


def downgrade() -> None:
    pass  # con fichas por persona ya no se puede volver a una sola por empresa
