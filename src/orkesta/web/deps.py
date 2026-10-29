"""Dependencias de la interfaz web: sesión de base de datos, usuario actual, CSRF y avisos."""

from __future__ import annotations

import secrets
import uuid
from collections.abc import Iterator
from pathlib import Path
from typing import Annotated, Any

from fastapi import Depends, HTTPException, Request
from fastapi.responses import Response
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session, sessionmaker

from orkesta.db.models import User
from orkesta.engine.domain.reasons import DOMAIN_STATUS_LABELS
from orkesta.imports.export import CONFIDENCE_LABELS, STATUS_LABELS
from orkesta.imports.mapping import FIELD_LABELS, FIELDS, REQUIRED_FIELDS
from orkesta.imports.rows import ISSUES

TEMPLATES_DIR = Path(__file__).parent / "templates"
STATIC_DIR = Path(__file__).parent / "static"

BATCH_STATUS_LABELS = {
    "draft": "Pendiente de confirmar",
    "importing": "Importando",
    "imported": "Importado",
    "queued": "En cola",
    "running": "Buscando dominios",
    "completed": "Enriquecido",
    "failed": "Falló",
    "cancelled": "Cancelado",
}

templates = Jinja2Templates(directory=str(TEMPLATES_DIR))
templates.env.globals.update(
    FIELDS=FIELDS,
    FIELD_LABELS=FIELD_LABELS,
    REQUIRED_FIELDS=REQUIRED_FIELDS,
    ISSUES=ISSUES,
    ROW_STATUS_LABELS=STATUS_LABELS,
    CONFIDENCE_LABELS=CONFIDENCE_LABELS,
    BATCH_STATUS_LABELS=BATCH_STATUS_LABELS,
    DOMAIN_STATUS_LABELS=DOMAIN_STATUS_LABELS,
)


def _thousands(value: int | None) -> str:
    return f"{value or 0:,}".replace(",", ".")


def _datetime(value: Any) -> str:
    if value is None:
        return "—"
    return str(value.astimezone().strftime("%d/%m/%Y %H:%M"))


templates.env.filters["thousands"] = _thousands
templates.env.filters["fecha"] = _datetime


class LoginRequired(Exception):
    """Se transforma en una redirección a /login (ver el manejador en la app)."""

    def __init__(self, next_path: str) -> None:
        self.next_path = next_path


def get_db(request: Request) -> Iterator[Session]:
    factory: sessionmaker[Session] = request.app.state.session_factory()
    session = factory()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


# scope="function": la transacción se confirma ANTES de enviar la respuesta. Con el valor por
# defecto ("request") se confirmaría después, y un navegador que sigue una redirección al
# instante (subir archivo → mapeo) podría pedir un lote que todavía no está guardado.
DbSession = Annotated[Session, Depends(get_db, scope="function")]


def current_user(request: Request, db: DbSession) -> User:
    raw_id = request.session.get("user_id")
    try:
        user_id = uuid.UUID(str(raw_id)) if raw_id else None
    except ValueError:
        user_id = None
    user = db.get(User, user_id) if user_id else None
    if user is None or not user.is_active:
        request.session.pop("user_id", None)
        raise LoginRequired(request.url.path)
    return user


def csrf_token(request: Request) -> str:
    token = request.session.get("csrf")
    if not isinstance(token, str):
        token = secrets.token_urlsafe(32)
        request.session["csrf"] = token
    return token


def check_csrf(request: Request, submitted: str | None) -> None:
    expected = request.session.get("csrf")
    if not expected or not submitted or not secrets.compare_digest(expected, submitted):
        raise HTTPException(status_code=403, detail="Formulario caducado. Recarga la página.")


def flash(request: Request, message: str, kind: str = "info") -> None:
    messages = list(request.session.get("flash", []))
    messages.append({"kind": kind, "message": message})
    request.session["flash"] = messages


def pop_flashes(request: Request) -> list[dict[str, str]]:
    messages: list[dict[str, str]] = request.session.pop("flash", [])
    return messages


def render(
    request: Request, template: str, context: dict[str, Any] | None = None, status: int = 200
) -> Response:
    data = {
        "request": request,
        "csrf_token": csrf_token(request),
        "flashes": pop_flashes(request),
        **(context or {}),
    }
    return templates.TemplateResponse(request, template, data, status_code=status)
