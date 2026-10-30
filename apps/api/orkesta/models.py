"""Importa todos los modelos para que Alembic y SQLAlchemy los conozcan."""


def load_all() -> None:
    import orkesta.data.models
    import orkesta.platform.models
    import orkesta.research.models  # noqa: F401
