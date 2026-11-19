"""Precios por usuario: planes Personal, Inicial, Pro y Enterprise con todas las funcionalidades.

Los valores guardados en el panel mandan sobre los de código, así que se reescriben aquí los de
los cuatro planes de pago (precio, tope y mínimo de usuarios, verificaciones, contactos y
correos). Demo y Cortesía quedan como estaban.

Revision ID: 0052
Revises: 0051
"""

from __future__ import annotations

import json

import sqlalchemy as sa
from alembic import op

revision = "0052"
down_revision = "0051"
branch_labels = None
depends_on = None

PAID = {
    "personal": {"users": 1, "min_users": 1, "verifications_per_user": 1000,
                 "price_month": 25, "price_year": 21},
    "inicial": {"users": 0, "min_users": 5, "verifications_per_user": 2500,
                "price_month": 39, "price_year": 33},
    "pro": {"users": 0, "min_users": 10, "verifications_per_user": 5000,
            "price_month": 59, "price_year": 50},
    "empresa": {"users": 0, "min_users": 10, "verifications_per_user": 10000,
                "price_month": 99, "price_year": 84},
}  # fmt: skip


def upgrade() -> None:
    bind = op.get_bind()
    row = bind.execute(
        sa.text("SELECT value FROM orkesta.platform_settings WHERE key = 'app'")
    ).first()
    if row is None:
        return  # sin valores guardados: rigen los del código
    value = dict(row[0] or {})
    limits = dict(value.get("limits") or {})
    for plan, fields in PAID.items():
        limits[plan] = {
            **(limits.get(plan) or {}),
            "contacts": 0,
            "emails_per_day": 2000,
            **fields,
        }
    value["limits"] = limits
    bind.execute(
        sa.text("UPDATE orkesta.platform_settings SET value = CAST(:v AS jsonb) WHERE key = 'app'"),
        {"v": json.dumps(value)},
    )


def downgrade() -> None:
    pass
