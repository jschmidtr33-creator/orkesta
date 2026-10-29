"""Cifrado de credenciales de correo (contraseñas SMTP/IMAP y tokens de Google).

Fernet (AES-128-CBC + HMAC-SHA256) con una clave derivada de ``SECRET_KEY`` mediante HKDF.
Si se cambia ``SECRET_KEY`` las credenciales guardadas dejan de poder leerse y hay que volver a
conectar las cuentas: es el comportamiento seguro.
"""

from __future__ import annotations

import base64
from functools import lru_cache

from cryptography.fernet import Fernet, InvalidToken
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.kdf.hkdf import HKDF

from orkesta.config import get_settings

_DEV_FALLBACK = "orkesta-desarrollo-sin-secret-key-no-usar-en-produccion"


class SecretError(ValueError):
    """La credencial no se puede descifrar (clave cambiada o dato corrupto)."""


@lru_cache(maxsize=4)
def _fernet(secret: str) -> Fernet:
    key = HKDF(
        algorithm=hashes.SHA256(), length=32, salt=b"orkesta", info=b"credenciales-de-correo"
    ).derive(secret.encode())
    return Fernet(base64.urlsafe_b64encode(key))


def _box() -> Fernet:
    return _fernet(get_settings().secret_key or _DEV_FALLBACK)


def encrypt(value: str) -> bytes:
    return _box().encrypt(value.encode())


def decrypt(token: bytes) -> str:
    try:
        return _box().decrypt(token).decode()
    except InvalidToken as exc:
        raise SecretError(
            "No se pudo leer la credencial guardada: vuelve a conectar la cuenta."
        ) from exc
