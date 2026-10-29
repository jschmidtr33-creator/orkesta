"""Comandos de administración: ``python -m orkesta.cli <comando>``.

- ``create-admin --email correo@empresa.com``: crea un administrador. La contraseña se pide por
  teclado o se toma de ``ORKESTA_ADMIN_PASSWORD``.
- ``bootstrap-admin``: crea el primer administrador desde variables de entorno
  (``ORKESTA_BOOTSTRAP_ADMIN_EMAIL``, ``ORKESTA_BOOTSTRAP_ADMIN_PASSWORD`` y opcionalmente
  ``ORKESTA_BOOTSTRAP_ORG_NAME``). No hace nada si ya hay usuarios o faltan las variables, así
  que puede ejecutarse en cada despliegue.
"""

from __future__ import annotations

import argparse
import getpass
import os
import sys
from pathlib import Path

from sqlalchemy import func, select

from orkesta.audit import audit
from orkesta.auth import AuthError, create_user, get_or_create_organization
from orkesta.db.models import User
from orkesta.db.session import session_scope

DEFAULT_CATALOG = Path(__file__).parent / "catalog" / "data" / "empresas_latam.csv.gz"


def _create_admin(email: str, password: str, org_name: str) -> str:
    with session_scope() as session:
        org = get_or_create_organization(session, org_name)
        user = create_user(
            session, email=email, password=password, organization_id=org.id, role="admin"
        )
        audit(
            session,
            organization_id=org.id,
            action="user.created",
            entity_type="user",
            entity_id=user.id,
            role="admin",
        )
        return user.email


def cmd_create_admin(args: argparse.Namespace) -> int:
    password = os.environ.get("ORKESTA_ADMIN_PASSWORD")
    if not password:
        password = getpass.getpass("Contraseña (mínimo 12 caracteres): ")
        if getpass.getpass("Repite la contraseña: ") != password:
            print("Las contraseñas no coinciden.", file=sys.stderr)
            return 1
    try:
        email = _create_admin(args.email, password, args.org_name)
    except AuthError as exc:
        print(str(exc), file=sys.stderr)
        return 1
    print(f"Administrador creado: {email}")
    return 0


def cmd_bootstrap_admin(_: argparse.Namespace) -> int:
    email = os.environ.get("ORKESTA_BOOTSTRAP_ADMIN_EMAIL", "").strip()
    password = os.environ.get("ORKESTA_BOOTSTRAP_ADMIN_PASSWORD", "")
    org_name = os.environ.get("ORKESTA_BOOTSTRAP_ORG_NAME", "ORKESTA")
    if not email or not password:
        print("bootstrap-admin: variables no definidas, nada que hacer.")
        return 0
    with session_scope() as session:
        if (session.scalar(select(func.count()).select_from(User)) or 0) > 0:
            print("bootstrap-admin: ya existen usuarios, nada que hacer.")
            return 0
    try:
        created = _create_admin(email, password, org_name)
    except AuthError as exc:
        print(f"bootstrap-admin: {exc}", file=sys.stderr)
        return 1
    print(f"bootstrap-admin: administrador creado: {created}")
    return 0


def cmd_catalog_load(args: argparse.Namespace) -> int:
    from orkesta.catalog.companies import load_catalog
    from orkesta.catalog.wikidata_catalog import read_catalog

    path = Path(args.path) if args.path else DEFAULT_CATALOG
    if not path.exists():
        print(f"catalog-load: no existe {path}, nada que hacer.")
        return 0
    rows = read_catalog(path)
    with session_scope() as session:
        loaded = load_catalog(session, rows)
    print(f"catalog-load: {loaded} empresas cargadas desde {path.name}")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="orkesta")
    sub = parser.add_subparsers(dest="command", required=True)

    create = sub.add_parser("create-admin", help="Crea un usuario administrador")
    create.add_argument("--email", required=True)
    create.add_argument("--org-name", default="ORKESTA")
    create.set_defaults(func=cmd_create_admin)

    bootstrap = sub.add_parser("bootstrap-admin", help="Crea el primer admin desde variables")
    bootstrap.set_defaults(func=cmd_bootstrap_admin)

    catalog = sub.add_parser("catalog-load", help="Carga el catálogo público de empresas")
    catalog.add_argument("path", nargs="?", help="Archivo .csv.gz (por defecto, el incluido)")
    catalog.set_defaults(func=cmd_catalog_load)

    args = parser.parse_args(argv)
    result: int = args.func(args)
    return result


if __name__ == "__main__":
    sys.exit(main())
