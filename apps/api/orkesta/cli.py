"""Comandos de operación.

``python -m orkesta.cli release`` corre antes de cada despliegue: aplica migraciones y crea el
primer administrador de la plataforma si las variables están definidas. Es un solo comando
porque Render no ejecuta ``&&`` en el paso previo al despliegue.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from alembic import command
from alembic.config import Config
from sqlalchemy import select

from orkesta.core.config import get_settings
from orkesta.core.db import session_scope
from orkesta.platform import service as svc
from orkesta.platform.models import User

API_ROOT = Path(__file__).resolve().parent.parent


def alembic_config(url: str | None = None) -> Config:
    config = Config(str(API_ROOT / "alembic.ini"))
    config.set_main_option("script_location", str(API_ROOT / "migrations"))
    if url:
        config.set_main_option("sqlalchemy.url", url.replace("%", "%%"))
    return config


def migrate() -> None:
    command.upgrade(alembic_config(get_settings().database_url), "head")
    print("release: migraciones al día")


def bootstrap_admin() -> None:
    settings = get_settings()
    email, password = (
        settings.orkesta_bootstrap_admin_email,
        settings.orkesta_bootstrap_admin_password,
    )
    if not email or not password:
        print("release: sin administrador inicial configurado")
        return
    with session_scope() as session:
        email = svc.normalize_email(email)
        user = session.scalar(select(User).where(User.email == email))
        if user is not None:
            if not user.is_platform_admin:
                user.is_platform_admin = True
            if user.platform_role != "owner":
                user.platform_role = "owner"  # la cuenta inicial es la creadora del panel
            print("release: el administrador inicial ya existe")
            return
        user = svc.create_user(
            session, email=email, full_name="Administrador", password=password, platform_admin=True
        )
        user.platform_role = "owner"
        svc.create_tenant(session, owner=user, name=settings.orkesta_bootstrap_org_name)
        print(f"release: administrador inicial creado ({email})")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="orkesta")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("release", help="Migraciones y administrador inicial")
    sub.add_parser("migrate", help="Solo migraciones")
    args = parser.parse_args(argv)
    migrate()
    if args.command == "release":
        try:
            bootstrap_admin()
        except ValueError as exc:
            # No detiene el despliegue: la app sigue disponible y se puede registrar en la web.
            print(f"release: no se creó el administrador inicial: {exc}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
