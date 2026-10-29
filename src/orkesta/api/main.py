"""Aplicación FastAPI: endpoints de operación e interfaz web.

Se arranca en modo fábrica: ``uvicorn --factory orkesta.api.main:create_app``.

``/health`` indica que el proceso responde (lo usa Railway para dar por bueno un despliegue).
``/ready`` indica que además puede atender trabajo: base de datos accesible y migraciones al día.
"""

from __future__ import annotations

import logging
import secrets
import threading
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from functools import lru_cache
from typing import Any
from urllib.parse import quote

from fastapi import FastAPI, Request
from fastapi.exceptions import HTTPException
from fastapi.responses import JSONResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from sqlalchemy import Engine, text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session, sessionmaker
from starlette.middleware.sessions import SessionMiddleware

from orkesta import __version__
from orkesta.auth import LoginThrottle
from orkesta.config import get_settings
from orkesta.db.migrations_state import current_heads, expected_heads
from orkesta.db.session import get_engine
from orkesta.logs import configure_logging
from orkesta.web.campaigns import router as campaigns_router
from orkesta.web.deps import STATIC_DIR, LoginRequired, render
from orkesta.web.lists import router as lists_router
from orkesta.web.prospects import router as prospects_router
from orkesta.web.routes import router as web_router

logger = logging.getLogger("orkesta.api")

SECURITY_HEADERS = {
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
    "Referrer-Policy": "same-origin",
    "Content-Security-Policy": (
        "default-src 'self'; img-src 'self' data:; style-src 'self'; script-src 'self'; "
        "form-action 'self'; frame-ancestors 'none'; base-uri 'self'"
    ),
}


def create_app(engine_provider: Callable[[], Engine] = get_engine) -> FastAPI:
    """Construye la aplicación. ``engine_provider`` permite inyectar otra base en las pruebas."""
    settings = get_settings()

    @asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncIterator[None]:
        configure_logging(settings.log_level)
        if not settings.secret_key:
            logger.warning("secret_key_missing: sesiones con clave temporal hasta reiniciar")
        logger.info("api_started", extra={"environment": settings.environment})
        worker, thread = None, None
        if settings.run_worker_in_web:
            # Un solo servicio: el worker corre en un hilo del proceso web.
            from orkesta.worker.__main__ import build_worker

            worker = build_worker(session_factory())
            thread = threading.Thread(target=worker.run_forever, name="orkesta-worker", daemon=True)
            thread.start()
        yield
        if worker is not None and thread is not None:
            worker.stop()
            thread.join(timeout=30)
        logger.info("api_stopped")

    app = FastAPI(
        title="ORKESTA",
        version=__version__,
        lifespan=lifespan,
        docs_url=None if settings.is_deployed else "/docs",
        redoc_url=None,
        openapi_url=None if settings.is_deployed else "/openapi.json",
    )

    @lru_cache(maxsize=1)
    def session_factory() -> sessionmaker[Session]:
        return sessionmaker(bind=engine_provider(), expire_on_commit=False)

    app.state.session_factory = session_factory
    app.state.login_throttle = LoginThrottle(
        max_failures=settings.login_max_failures,
        window_seconds=settings.login_lockout_minutes * 60,
    )

    app.add_middleware(
        SessionMiddleware,
        secret_key=settings.secret_key or secrets.token_urlsafe(48),
        session_cookie="orkesta_session",
        max_age=settings.session_max_age_hours * 3600,
        same_site="lax",
        https_only=settings.is_deployed,
    )

    @app.middleware("http")
    async def security_headers(
        request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        response = await call_next(request)
        for header, value in SECURITY_HEADERS.items():
            response.headers.setdefault(header, value)
        if settings.is_deployed:
            response.headers.setdefault("Strict-Transport-Security", "max-age=31536000")
        return response

    @app.exception_handler(LoginRequired)
    async def login_required(_: Request, exc: LoginRequired) -> Response:
        return RedirectResponse(f"/login?next={quote(exc.next_path)}", status_code=303)

    @app.exception_handler(HTTPException)
    async def http_error(request: Request, exc: HTTPException) -> Response:
        wants_html = "text/html" in request.headers.get("accept", "")
        if wants_html:
            context: dict[str, Any] = {"status": exc.status_code, "detail": exc.detail}
            return render(request, "error.html", context, status=exc.status_code)
        return JSONResponse({"detail": exc.detail}, status_code=exc.status_code)

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

    app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")
    app.include_router(web_router)
    app.include_router(prospects_router)
    app.include_router(lists_router)
    app.include_router(campaigns_router)
    return app
