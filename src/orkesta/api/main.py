"""Aplicación FastAPI.

``/health`` indica que el proceso responde (lo usa Railway para dar por bueno un despliegue).
``/ready`` indica que además puede atender trabajo: base de datos accesible y migraciones al día.
"""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from typing import Any

from fastapi import FastAPI
from fastapi.responses import JSONResponse
from sqlalchemy import Engine, text
from sqlalchemy.exc import SQLAlchemyError

from orkesta import __version__
from orkesta.config import get_settings
from orkesta.db.migrations_state import current_heads, expected_heads
from orkesta.db.session import get_engine
from orkesta.logs import configure_logging

logger = logging.getLogger("orkesta.api")


def create_app(engine_provider: Callable[[], Engine] = get_engine) -> FastAPI:
    """Construye la aplicación. ``engine_provider`` permite inyectar otra base en las pruebas."""

    @asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncIterator[None]:
        settings = get_settings()
        configure_logging(settings.log_level)
        logger.info("api_started", extra={"environment": settings.environment})
        yield
        logger.info("api_stopped")

    app = FastAPI(title="ORKESTA", version=__version__, lifespan=lifespan)

    @app.get("/health", tags=["operación"])
    def health() -> dict[str, str]:
        return {"status": "ok", "version": __version__}

    @app.get("/ready", tags=["operación"])
    def ready() -> JSONResponse:
        checks: dict[str, Any] = {}
        try:
            engine = engine_provider()
            with engine.connect() as connection:
                connection.execute(text("SELECT 1"))
            checks["database"] = "ok"
            current = current_heads(engine)
            expected = expected_heads()
            checks["migrations"] = "ok" if current == expected else "pending"
            if current != expected:
                checks["migrations_current"] = sorted(current)
                checks["migrations_expected"] = sorted(expected)
        except SQLAlchemyError as exc:
            logger.warning("ready_check_failed", extra={"error": type(exc).__name__})
            checks["database"] = "unreachable"

        is_ready = checks.get("database") == "ok" and checks.get("migrations") == "ok"
        status_code = 200 if is_ready else 503
        body = {"status": "ready" if is_ready else "not_ready", "checks": checks}
        return JSONResponse(status_code=status_code, content=body)

    return app


app = create_app()
