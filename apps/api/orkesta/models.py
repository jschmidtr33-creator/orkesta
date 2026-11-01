"""Importa todos los modelos para que Alembic y SQLAlchemy los conozcan."""


def load_all() -> None:
    import orkesta.automation.models
    import orkesta.campaigns.models
    import orkesta.crm.models
    import orkesta.data.models
    import orkesta.enrichment.models
    import orkesta.platform.models
    import orkesta.proposals.models
    import orkesta.research.models
    import orkesta.validation.models  # noqa: F401
