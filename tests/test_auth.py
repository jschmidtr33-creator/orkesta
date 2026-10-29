from __future__ import annotations

import pytest
from sqlalchemy import func, select
from sqlalchemy.orm import Session, sessionmaker

from orkesta import cli
from orkesta.auth import (
    AuthError,
    LoginThrottle,
    authenticate,
    create_user,
    get_or_create_organization,
    hash_password,
    normalize_email,
    verify_password,
)
from orkesta.db.models import Organization, User


def test_password_hash_roundtrip() -> None:
    hashed = hash_password("una contraseña larga")
    assert hashed.startswith("$argon2id$")
    assert verify_password(hashed, "una contraseña larga")
    assert not verify_password(hashed, "otra")
    assert not verify_password("no-es-un-hash", "x")


@pytest.mark.parametrize("email", ["sin-arroba", "a@b", "a b@c.com", ""])
def test_invalid_emails(email: str) -> None:
    with pytest.raises(AuthError):
        normalize_email(email)


def test_email_is_normalized() -> None:
    assert normalize_email("  Jenn@Empresa.COM ") == "jenn@empresa.com"


def test_throttle_locks_after_max_failures_and_expires() -> None:
    throttle = LoginThrottle(max_failures=3, window_seconds=60)
    for t in (0.0, 1.0, 2.0):
        assert not throttle.is_locked("A@x.com", "1.1.1.1", now=t)
        throttle.record_failure("a@x.com", "1.1.1.1", now=t)
    assert throttle.is_locked("a@x.com", "1.1.1.1", now=3.0)
    assert not throttle.is_locked("a@x.com", "2.2.2.2", now=3.0)
    assert not throttle.is_locked("a@x.com", "1.1.1.1", now=100.0)


def test_throttle_reset() -> None:
    throttle = LoginThrottle(max_failures=1, window_seconds=60)
    throttle.record_failure("a@x.com", "ip", now=0.0)
    throttle.reset("a@x.com", "ip")
    assert not throttle.is_locked("a@x.com", "ip", now=0.0)


@pytest.mark.db
def test_create_and_authenticate(session_factory: sessionmaker[Session]) -> None:
    with session_factory.begin() as session:
        org = get_or_create_organization(session, "America Digital")
        create_user(session, email="Jenn@Ejemplo.com", password="x" * 12, organization_id=org.id)
    with session_factory.begin() as session:
        user = authenticate(session, "jenn@ejemplo.com", "x" * 12)
        assert user is not None and user.last_login_at is not None
        assert authenticate(session, "jenn@ejemplo.com", "mala") is None
        assert authenticate(session, "nadie@ejemplo.com", "x" * 12) is None
        assert authenticate(session, "no es correo", "x" * 12) is None


@pytest.mark.db
def test_create_user_rules(session_factory: sessionmaker[Session]) -> None:
    with session_factory.begin() as session:
        org = get_or_create_organization(session, "Org")
        with pytest.raises(AuthError, match="12 caracteres"):
            create_user(session, email="a@b.co", password="corta", organization_id=org.id)
        create_user(session, email="a@b.co", password="y" * 12, organization_id=org.id)
        with pytest.raises(AuthError, match="Ya existe"):
            create_user(session, email="A@B.CO", password="y" * 12, organization_id=org.id)


@pytest.mark.db
def test_inactive_user_cannot_log_in(session_factory: sessionmaker[Session]) -> None:
    with session_factory.begin() as session:
        org = get_or_create_organization(session, "Org")
        user = create_user(session, email="a@b.co", password="y" * 12, organization_id=org.id)
        user.is_active = False
    with session_factory.begin() as session:
        assert authenticate(session, "a@b.co", "y" * 12) is None


@pytest.mark.db
def test_bootstrap_admin_is_idempotent(
    session_factory: sessionmaker[Session], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("ORKESTA_BOOTSTRAP_ADMIN_EMAIL", "admin@ejemplo.com")
    monkeypatch.setenv("ORKESTA_BOOTSTRAP_ADMIN_PASSWORD", "z" * 16)
    monkeypatch.setenv("ORKESTA_BOOTSTRAP_ORG_NAME", "America Digital")
    assert cli.main(["bootstrap-admin"]) == 0
    assert cli.main(["bootstrap-admin"]) == 0
    with session_factory() as session:
        assert session.scalar(select(func.count()).select_from(User)) == 1
        user = session.scalar(select(User))
        assert user is not None and user.role == "admin"
        org = session.scalar(select(Organization))
        assert org is not None and org.name == "America Digital"


def test_bootstrap_admin_without_variables_does_nothing(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.delenv("ORKESTA_BOOTSTRAP_ADMIN_EMAIL", raising=False)
    monkeypatch.delenv("ORKESTA_BOOTSTRAP_ADMIN_PASSWORD", raising=False)
    assert cli.main(["bootstrap-admin"]) == 0
    assert "nada que hacer" in capsys.readouterr().out
