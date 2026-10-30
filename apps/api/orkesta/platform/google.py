"""Inicio de sesión con Google (OpenID Connect). Solo pide nombre, correo y foto de perfil:
permisos básicos que Google no exige verificar. El permiso para enviar correos llega aparte,
en la etapa de campañas."""

from __future__ import annotations

import secrets
from dataclasses import dataclass
from functools import lru_cache
from urllib.parse import urlencode

import httpx
import jwt

from orkesta.core.config import get_settings
from orkesta.core.security import read_state, sign_state

AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
TOKEN_URL = "https://oauth2.googleapis.com/token"
JWKS_URL = "https://www.googleapis.com/oauth2/v3/certs"
ISSUERS = ("https://accounts.google.com", "accounts.google.com")
CALLBACK_PATH = "/api/auth/google/callback"


class GoogleAuthError(ValueError):
    pass


@dataclass(frozen=True)
class GoogleIdentity:
    sub: str
    email: str
    name: str
    hosted_domain: str | None


def redirect_uri() -> str:
    return get_settings().public_base_url.rstrip("/") + CALLBACK_PATH


def authorization_url(next_path: str = "/app") -> tuple[str, str]:
    """URL de Google y estado firmado (también va en una cookie para atarlo al navegador)."""
    settings = get_settings()
    nonce = secrets.token_urlsafe(16)
    state = sign_state({"nonce": nonce, "next": next_path if next_path.startswith("/") else "/app"})
    query = urlencode(
        {
            "client_id": settings.google_client_id,
            "redirect_uri": redirect_uri(),
            "response_type": "code",
            "scope": "openid email profile",
            "state": state,
            "nonce": nonce,
            "prompt": "select_account",
        }
    )
    return f"{AUTH_URL}?{query}", state


@lru_cache(maxsize=1)
def _jwks() -> jwt.PyJWKClient:
    return jwt.PyJWKClient(JWKS_URL, cache_keys=True)


def exchange_code(code: str, state: str, cookie_state: str | None) -> tuple[GoogleIdentity, str]:
    data = read_state(state)
    if data is None or not cookie_state or cookie_state != state:
        raise GoogleAuthError("La sesión con Google expiró. Inténtalo de nuevo.")
    settings = get_settings()
    try:
        response = httpx.post(
            TOKEN_URL,
            data={
                "code": code,
                "client_id": settings.google_client_id,
                "client_secret": settings.google_client_secret,
                "redirect_uri": redirect_uri(),
                "grant_type": "authorization_code",
            },
            timeout=15,
        )
        response.raise_for_status()
        id_token = response.json()["id_token"]
        key = _jwks().get_signing_key_from_jwt(id_token)
        claims = jwt.decode(
            id_token,
            key.key,
            algorithms=["RS256"],
            audience=settings.google_client_id,
            issuer=ISSUERS,
        )
    except (httpx.HTTPError, KeyError, jwt.PyJWTError) as exc:
        raise GoogleAuthError(
            "Google no confirmó el inicio de sesión. Inténtalo de nuevo."
        ) from exc
    if claims.get("nonce") != data.get("nonce"):
        raise GoogleAuthError("La respuesta de Google no corresponde a esta solicitud.")
    if not claims.get("email_verified") or not claims.get("email"):
        raise GoogleAuthError("Tu cuenta de Google no tiene un correo verificado.")
    identity = GoogleIdentity(
        sub=str(claims["sub"]),
        email=str(claims["email"]).lower(),
        name=str(claims.get("name") or ""),
        hosted_domain=claims.get("hd"),
    )
    return identity, data.get("next", "/app")
