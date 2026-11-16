"""Segundo paso para entrar al panel de administración: un código de 6 números por correo.

Lo envía el correo del sistema. Vence en 10 minutos, sirve una sola vez y admite 5 intentos.
Solo se guarda su huella. Quien tiene activada la app de autenticación usa esa en su lugar.
Si el correo del sistema no está configurado, el panel sigue pidiendo solo la contraseña (y el
panel avisa que conviene configurarlo), para que nadie quede fuera.
"""

from __future__ import annotations

import hashlib
import hmac
import secrets
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy.orm import Session

from orkesta.platform.models import User

KEY = "codigos_panel"
VALID = timedelta(minutes=10)
TRIES = 5


def _hash(code: str) -> str:
    from orkesta.core.config import get_settings

    key = get_settings().signing_key.encode()
    return hmac.new(key, f"panel:{code}".encode(), hashlib.sha256).hexdigest()


def available(session: Session) -> bool:
    from orkesta.core import system_mail

    return system_mail.config(session) is not None


def masked(email: str) -> str:
    name, _, domain = email.partition("@")
    return f"{name[:2]}{'•' * max(1, len(name) - 2)}@{domain}"


def _store(session: Session) -> dict[str, Any]:
    from orkesta.admin.service import _get

    return dict(_get(session, KEY, {}))


def send(session: Session, user: User) -> bool:
    from orkesta.admin.service import _put
    from orkesta.core import system_mail

    code = f"{secrets.randbelow(1_000_000):06d}"
    data = _store(session)
    data[str(user.id)] = {"hash": _hash(code), "tries": 0,
                          "expires": (datetime.now(UTC) + VALID).isoformat()}  # fmt: skip
    _put(session, KEY, data)
    text = (f"Tu código para entrar al panel de administración de ORKESTA es: {code}\n\n"
            "Vence en 10 minutos. Si no fuiste tú, cambia tu contraseña.")  # fmt: skip
    return system_mail.send(session, to=user.email, subject=f"{code} es tu código de ORKESTA",
                            text=text)  # fmt: skip


def check(session: Session, user: User, code: str) -> bool:
    from orkesta.admin.service import _put

    data = _store(session)
    entry = data.get(str(user.id))
    if not entry:
        return False
    expired = datetime.fromisoformat(entry["expires"]) < datetime.now(UTC)
    ok = (
        not expired
        and entry["tries"] < TRIES
        and hmac.compare_digest(entry["hash"], _hash("".join(code.split())))
    )
    if ok or expired or entry["tries"] + 1 >= TRIES:
        data.pop(str(user.id), None)  # usado, vencido o sin más intentos
    else:
        data[str(user.id)] = {**entry, "tries": entry["tries"] + 1}
    _put(session, KEY, data)
    return ok
