"""Motor de SQLAlchemy y fábrica de sesiones, creados una vez por proceso."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from functools import lru_cache

from sqlalchemy import Engine, create_engine
from sqlalchemy.orm import Session, sessionmaker

from orkesta.config import get_settings


def build_engine(database_url: str, pool_size: int = 5, max_overflow: int = 5) -> Engine:
    """Crea un motor con verificación de conexiones antes de usarlas (Railway puede reciclarlas)."""
    return create_engine(
        database_url,
        pool_size=pool_size,
        max_overflow=max_overflow,
        pool_pre_ping=True,
        pool_recycle=1800,
    )


@lru_cache(maxsize=1)
def get_engine() -> Engine:
    settings = get_settings()
    return build_engine(settings.database_url, settings.db_pool_size, settings.db_max_overflow)


@lru_cache(maxsize=1)
def get_session_factory() -> sessionmaker[Session]:
    return sessionmaker(bind=get_engine(), expire_on_commit=False)


@contextmanager
def session_scope() -> Iterator[Session]:
    """Sesión transaccional: confirma al salir sin error y revierte si hay una excepción."""
    session = get_session_factory()()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()
