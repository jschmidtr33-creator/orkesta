"""Base de datos: motor, sesiones y contexto de empresa para Row-Level Security.

Cada solicitud autenticada fija ``app.tenant_id`` y ``app.user_id`` en su transacción
(``set_config(..., true)``); las políticas RLS de PostgreSQL solo devuelven filas de esa
empresa aunque una consulta olvide filtrar.
"""

from __future__ import annotations

import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import datetime
from functools import lru_cache

from sqlalchemy import DateTime, Engine, MetaData, create_engine, func, text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import DeclarativeBase, Mapped, Session, mapped_column, sessionmaker

from orkesta.core.config import get_settings

NAMING = {
    "ix": "ix_%(column_0_label)s",
    "uq": "uq_%(table_name)s_%(column_0_N_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}


class Base(DeclarativeBase):
    metadata = MetaData(naming_convention=NAMING, schema="orkesta")


class IdMixin:
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


def make_engine(url: str | None = None) -> Engine:
    settings = get_settings()
    return create_engine(
        url or settings.database_url,
        pool_pre_ping=True,
        pool_size=5,
        max_overflow=5,
        connect_args={"options": f"-csearch_path={settings.database_schema},public"},
    )


@lru_cache(maxsize=1)
def get_engine() -> Engine:
    return make_engine()


@lru_cache(maxsize=1)
def get_session_factory() -> sessionmaker[Session]:
    return sessionmaker(bind=get_engine(), expire_on_commit=False)


def set_context(
    session: Session, *, tenant_id: uuid.UUID | None = None, user_id: uuid.UUID | None = None
) -> None:
    """Fija la empresa y el usuario de esta transacción para las políticas RLS."""
    session.execute(
        text("SELECT set_config('app.tenant_id', :t, true), set_config('app.user_id', :u, true)"),
        {"t": str(tenant_id) if tenant_id else "", "u": str(user_id) if user_id else ""},
    )


@contextmanager
def session_scope(factory: sessionmaker[Session] | None = None) -> Iterator[Session]:
    session = (factory or get_session_factory())()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()
