"""Verificación de buzones con un servicio especializado (MillionVerifier) cuando el servidor no
puede hablar SMTP por el puerto 25 (AWS lo bloquea). La clave se carga desde el panel de
administración y se guarda cifrada. Responde como el verificador propio: un código por
dirección (250 existe, 550 no existe, 0 sin respuesta), así el resto de la validación y el
aprendizaje de formatos no cambian."""

from __future__ import annotations

import logging
import re
from typing import Any

import httpx
from sqlalchemy.orm import Session

from orkesta.validation.checks import SmtpUnavailable

logger = logging.getLogger(__name__)
KEY = "verificador"
BASE = "https://api.millionverifier.com/api/v3"
CANARY = re.compile(r"^zz-[0-9a-f]{12}@")
# Resultado de MillionVerifier → (código SMTP equivalente, motivo)
CODES = {
    "ok": (250, "existe"),
    "catch_all": (250, "acepta todo"),
    "invalid": (550, "no existe"),
    "disposable": (550, "desechable"),
    "unknown": (0, "sin respuesta"),
    "error": (0, "error"),
}


def configured_key(session: Session) -> str:
    from orkesta.admin.service import _get
    from orkesta.campaigns.gmail import decrypt

    data = dict(_get(session, KEY, {}))
    token = str(data.get("key") or "")
    if not token:
        return ""
    try:
        return decrypt(token)
    except Exception:
        return ""


def save_key(session: Session, key: str) -> None:
    from orkesta.admin.service import _put
    from orkesta.campaigns.gmail import encrypt

    _put(session, KEY, {"provider": "millionverifier", "key": encrypt(key) if key else ""})


def balance(key: str) -> int | None:
    """Créditos disponibles (``None`` si la clave no sirve o no hay conexión)."""
    try:
        r = httpx.get(f"{BASE}/credits", params={"api": key}, timeout=15)
        data: dict[str, Any] = r.json()
    except (httpx.HTTPError, ValueError):
        return None
    if r.status_code != 200 or data.get("error"):
        return None
    try:
        return int(data.get("credits", 0))
    except (TypeError, ValueError):
        return None


class ApiProber:
    """Mismo contrato que el verificador por SMTP (``probe(mx, direcciones)``)."""

    def __init__(self, key: str, timeout: int = 20):
        self.key, self.timeout = key, timeout

    def _one(self, client: httpx.Client, email: str) -> tuple[int, str, str]:
        try:
            r = client.get(f"{BASE}/", params={"api": self.key, "email": email,
                                               "timeout": self.timeout})  # fmt: skip
            data = r.json()
        except (httpx.HTTPError, ValueError) as exc:
            raise SmtpUnavailable(f"verificador externo: {exc}") from exc
        error = str(data.get("error") or "")
        if error:
            # Sin créditos o clave inválida: no tiene sentido seguir con este verificador.
            raise SmtpUnavailable(f"verificador externo: {error}"[:200])
        result = str(data.get("result") or "unknown").lower()
        code, why = CODES.get(result, (0, result))
        return code, why, result

    def probe(self, mx: str, addresses: list[str]) -> dict[str, tuple[int, str]]:
        real = [a for a in addresses if not CANARY.match(a)]
        out: dict[str, tuple[int, str]] = {}
        kinds: list[str] = []
        with httpx.Client(timeout=self.timeout + 10) as client:
            for email in real:
                code, why, kind = self._one(client, email)
                out[email] = (code, f"MillionVerifier: {why}")
                kinds.append(kind)
        # La dirección de prueba (para saber si el dominio acepta todo) no se gasta: se deduce.
        for canary in (a for a in addresses if CANARY.match(a)):
            if "catch_all" in kinds:
                out[canary] = (250, "acepta todo")
            elif any(k in ("ok", "invalid") for k in kinds):
                out[canary] = (550, "no existe")
            else:
                out[canary] = (0, "")
        return out
