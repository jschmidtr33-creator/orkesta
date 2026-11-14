"""Avisos de servicios para la administración de ORKESTA (buscador sin créditos, clave
inválida…). Los usuarios nunca ven estos detalles: en su pantalla solo aparece lo que se
completó. Aquí queda el último aviso de cada servicio para el panel de administración."""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from typing import Any

from sqlalchemy.orm import Session, sessionmaker

logger = logging.getLogger(__name__)
KEY = "alertas_servicios"
TEXTS = {
    "creditos": "Se acabaron los créditos del buscador (Serper). Recarga en serper.dev.",
    "clave": "La clave del buscador (SERPER_API_KEY) no es válida.",
    "verificador": "El verificador de correos (MillionVerifier) no respondió: revisa sus créditos "
    "o la clave en Integraciones.",
}
SERVICE = {"creditos": "buscador", "clave": "buscador", "verificador": "verificador"}


def record(factory: sessionmaker[Session], kind: str) -> None:
    """Guarda el aviso (sin cortar el trabajo si falla)."""
    from orkesta.admin.service import _get, _put

    text = TEXTS.get(kind)
    if not text:
        return
    try:
        with factory.begin() as session:
            current = dict(_get(session, KEY, {}))
            current[SERVICE[kind]] = {"text": text, "at": datetime.now(UTC).isoformat()}
            _put(session, KEY, current)
    except Exception:
        logger.exception("service_alert")


def clear(session: Session, service: str) -> None:
    from orkesta.admin.service import _get, _put

    current = dict(_get(session, KEY, {}))
    if current.pop(service, None) is not None:
        _put(session, KEY, current)


def current(session: Session) -> list[dict[str, Any]]:
    from orkesta.admin.service import _get

    return [{"service": k, **v} for k, v in dict(_get(session, KEY, {})).items()]
