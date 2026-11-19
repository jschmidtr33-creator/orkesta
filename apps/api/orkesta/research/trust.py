"""Qué correos sabe ORKESTA con certeza (sin inventar y sin gastar verificaciones).

Prospección solo muestra un correo cuando hay evidencia real de que funciona:

1. La dirección exacta respondió una campaña, la verificó MillionVerifier (en los últimos
   ``FRESH_DAYS`` días) o se envió desde ORKESTA y llegó sin rebotar. Vale para cualquier
   empresa cliente: lo que una verificó lo aprovechan todas.
2. La persona trabaja en un dominio cuyo formato de correo ya está confirmado (otra persona
   del mismo dominio, con el mismo formato, fue verificada, respondió o recibió sin rebote) y
   el dominio no acepta cualquier dirección (catch-all).

Lo demás no se muestra: al guardar el contacto se busca y se verifica (con créditos).
"""

from __future__ import annotations

import logging
import threading
from datetime import UTC, datetime, timedelta

from sqlalchemy import or_, select
from sqlalchemy.orm import Session, sessionmaker

from orkesta.research import memory
from orkesta.research.extract import detect_pattern
from orkesta.research.models import MailDomain, PatternStat, PersonIndex

logger = logging.getLogger(__name__)

FRESH_DAYS = 180  # una verificación de buzón vale 6 meses (la gente cambia de empresa)
SAFE_STATUSES = ("confirmado", "verificado")


def pattern_evidence(st: PatternStat) -> int:
    return int(st.verified or 0) + int(st.confirmed or 0) + int(st.delivered or 0)


def confirmed_pattern(stats: list[PatternStat]) -> PatternStat | None:
    """Formato confirmado del dominio: con evidencia real y pocos rebotes (menos del 20 %)."""
    good = [
        st for st in stats if pattern_evidence(st) >= 1 and st.bounced * 4 <= pattern_evidence(st)
    ]
    if not good:
        return None
    return max(good, key=lambda st: (pattern_evidence(st), -st.bounced))


class Trust:
    """Cachea por búsqueda lo que se consulta (dominios y direcciones)."""

    def __init__(self, factory: sessionmaker[Session]):
        self.factory = factory
        self.lock = threading.Lock()
        self.domains: dict[str, tuple[PatternStat | None, bool, set[str]]] = {}
        self.addresses: dict[str, str] = {}

    def _domain(self, session: Session, domain: str) -> tuple[PatternStat | None, bool, set[str]]:
        with self.lock:
            if domain in self.domains:
                return self.domains[domain]
        stats = list(session.scalars(select(PatternStat).where(PatternStat.domain == domain)))
        best = confirmed_pattern(stats)
        if best is not None:
            session.expunge(best)
        catch_all = bool(session.scalar(
            select(MailDomain.catch_all).where(MailDomain.domain == domain)))  # fmt: skip
        found = (best, catch_all, memory._bounced(session, domain))
        with self.lock:
            self.domains[domain] = found
        return found

    def _address(self, session: Session, email: str) -> str:
        with self.lock:
            if email in self.addresses:
                return self.addresses[email]
        proof = self._lookup(session, email)
        with self.lock:
            self.addresses[email] = proof
        return proof

    def _lookup(self, session: Session, email: str) -> str:
        fresh = datetime.now(UTC) - timedelta(days=FRESH_DAYS)
        status = session.scalar(
            select(PersonIndex.email_status).where(
                PersonIndex.email == email,
                or_(PersonIndex.email_status == "confirmado",
                    (PersonIndex.email_status == "verificado")
                    & (PersonIndex.email_checked_at >= fresh)),
            ).limit(1)
        )  # fmt: skip
        if status == "confirmado":
            return "Correo confirmado por una respuesta"
        return "Correo verificado por ORKESTA" if status == "verificado" else ""

    def check(self, email: str, first: str, last: str, domain: str) -> tuple[bool, str]:
        """(¿es seguro?, explicación)."""
        email = (email or "").strip().lower()
        if "@" not in email:
            return False, ""
        domain = (domain or email.rsplit("@", 1)[1]).lower()
        try:
            with self.factory() as session:
                best, catch_all, bounced = self._domain(session, domain)
                if memory.address_hash(email) in bounced:
                    return False, ""
                proof = self._address(session, email)
                if proof:
                    return True, proof
                if catch_all or best is None:
                    return False, ""
                if detect_pattern(first, last, email) != best.pattern:
                    return False, ""
                label = memory.PATTERN_LABELS.get(best.pattern, best.pattern)
                n = pattern_evidence(best)
                return (
                    True,
                    f"Formato {label}@{domain} confirmado por {n} correo(s) que llegaron",
                )
        except Exception:
            logger.exception("trust_check")
            return False, ""
