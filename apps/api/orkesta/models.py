"""Importa todos los modelos para que Alembic y SQLAlchemy los conozcan."""


def load_all() -> None:
    import orkesta.platform.models  # noqa: F401
