"""Aplicación FastAPI: API en ``/api``, app web en ``/app`` y sitio público en ``/``.

Un solo origen para todo: las cookies de sesión son de primera parte (sin CORS ni cookies de
terceros) y un solo servicio que pagar.
"""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from pathlib import Path
from urllib.parse import urlsplit

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse, Response
from sqlalchemy.orm import Session, sessionmaker

from orkesta.core.config import get_settings
from orkesta.core.db import get_session_factory
from orkesta.models import load_all
from orkesta.platform.router import router as platform_router

logger = logging.getLogger("orkesta")
STATIC_ROOT = Path(__file__).resolve().parent / "static"
CSP = (
    "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; "
    "img-src 'self' data:; font-src 'self' data:; connect-src 'self'; "
    "frame-ancestors 'none'; base-uri 'self'; "
    "form-action 'self' https://accounts.google.com"
)
UNSAFE = {"POST", "PUT", "PATCH", "DELETE"}


def _same_origin(request: Request) -> bool:
    """Protección CSRF: una escritura solo se acepta desde el propio sitio."""
    origin = request.headers.get("origin") or request.headers.get("referer")
    if not origin:
        return True  # clientes no navegadores (pruebas, scripts) no envían Origin
    return urlsplit(origin).netloc == request.headers.get("host", "")


def create_app(session_factory: sessionmaker[Session] | None = None) -> FastAPI:
    load_all()
    settings = get_settings()
    app = FastAPI(
        title="ORKESTA",
        docs_url=None if settings.is_deployed else "/api/docs",
        redoc_url=None,
        openapi_url=None if settings.is_deployed else "/api/openapi.json",
    )
    app.state.session_factory = session_factory or get_session_factory()

    @app.middleware("http")
    async def security(
        request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        unsafe = request.method in UNSAFE and request.url.path.startswith("/api")
        if unsafe and not _same_origin(request):
            return JSONResponse({"detail": "Origen no permitido."}, status_code=403)
        response = await call_next(request)
        response.headers["Content-Security-Policy"] = CSP
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
        response.headers["Permissions-Policy"] = "camera=(), microphone=(), geolocation=()"
        if settings.is_deployed:
            response.headers["Strict-Transport-Security"] = "max-age=31536000; includeSubDomains"
        if request.url.path.startswith("/api"):
            response.headers["Cache-Control"] = "no-store"
        return response

    @app.exception_handler(RequestValidationError)
    async def invalid(_: Request, exc: RequestValidationError) -> JSONResponse:
        return JSONResponse(
            {"detail": "Revisa los datos del formulario.", "errors": exc.errors()[:5]},
            status_code=422,
        )

    app.include_router(platform_router)
    _mount_frontends(app)
    return app


def _mount_frontends(app: FastAPI) -> None:
    web, site = STATIC_ROOT / "web", STATIC_ROOT / "site"

    def _file(root: Path, rel: str) -> Path | None:
        candidate = (root / rel).resolve()
        if root.resolve() not in candidate.parents and candidate != root.resolve():
            return None  # intento de salir de la carpeta
        if candidate.is_dir():
            candidate = candidate / "index.html"
        return candidate if candidate.is_file() else None

    def _cache(path: Path) -> dict[str, str]:
        # Archivos con huella en el nombre (assets/…) se cachean un año; el HTML nunca.
        if "assets" in path.parts or "_astro" in path.parts or "fonts" in path.parts:
            return {"Cache-Control": "public, max-age=31536000, immutable"}
        return {"Cache-Control": "no-cache"}

    @app.get("/app", include_in_schema=False)
    @app.get("/app/{rest:path}", include_in_schema=False)
    def web_app(rest: str = "") -> Response:
        found = _file(web, rest) if rest else None
        target = found or web / "index.html"  # rutas del cliente → la app
        if not target.is_file():
            return JSONResponse({"detail": "La app web no está compilada."}, status_code=503)
        return FileResponse(target, headers=_cache(target))

    @app.get("/{rest:path}", include_in_schema=False)
    def public_site(rest: str = "") -> Response:
        if rest.startswith("api/"):
            return JSONResponse({"detail": "No encontrado."}, status_code=404)
        found = _file(site, rest) or _file(site, f"{rest}.html")
        if found is None:
            missing = site / "404.html"
            if missing.is_file():
                return FileResponse(missing, status_code=404, headers={"Cache-Control": "no-cache"})
            return JSONResponse({"detail": "No encontrado."}, status_code=404)
        return FileResponse(found, headers=_cache(found))
