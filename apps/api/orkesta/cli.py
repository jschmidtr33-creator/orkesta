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
from orkesta.core.security import hash_password
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
    try:
        from orkesta.core.db import get_session_factory
        from orkesta.research import sectors

        found = sectors.backfill(get_session_factory())
        print(f"release: sector deducido para {sum(found.values())} empresas {found}")
    except Exception as exc:  # nunca debe frenar un despliegue
        print(f"release: no se pudo deducir sectores: {exc}")


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
            if not user.password_hash:
                # El panel siempre pide contraseña: la cuenta creadora debe tener una.
                user.password_hash = hash_password(password)
            print("release: el administrador inicial ya existe")
            return
        user = svc.create_user(
            session, email=email, full_name="Administrador", password=password, platform_admin=True
        )
        user.platform_role = "owner"
        svc.create_tenant(session, owner=user, name=settings.orkesta_bootstrap_org_name)
        print(f"release: administrador inicial creado ({email})")


def copy_from_env() -> int:
    """Migración entre proveedores: el destino ya tiene las migraciones (release)."""
    import os

    from orkesta.transfer import copy_database

    source, target = os.environ.get("ORKESTA_SOURCE_URL"), os.environ.get("ORKESTA_TARGET_URL")
    if not source or not target:
        print("Faltan ORKESTA_SOURCE_URL y ORKESTA_TARGET_URL", file=sys.stderr)
        return 2
    copied = copy_database(source, target, log=lambda m: print(m, flush=True))
    print(f"copiar-base: {sum(copied.values())} filas en {len(copied)} tablas")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="orkesta")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("release", help="Migraciones y administrador inicial")
    sub.add_parser("migrate", help="Solo migraciones")
    sub.add_parser("copiar-base", help="Copia todo desde ORKESTA_SOURCE_URL a ORKESTA_TARGET_URL")
    args = parser.parse_args(argv)
    if args.command == "copiar-base":
        return copy_from_env()
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
