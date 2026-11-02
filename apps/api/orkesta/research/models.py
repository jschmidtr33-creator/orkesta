"""Investigación de prospectos: corridas, resultados y caché de páginas públicas."""

from __future__ import annotations

import uuid
from datetime import datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    SmallInteger,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from orkesta.core.db import Base, IdMixin

RUN_STATUSES = ("queued", "running", "completed", "failed", "canceled")
ITEM_KINDS = ("company", "person")


def _in(column: str, values: tuple[str, ...]) -> str:
    return f"{column} IN ({', '.join(repr(v) for v in values)})"


class ResearchRun(IdMixin, Base):
    """Una búsqueda del agente: filtros, avance en vivo y totales."""

    __tablename__ = "research_runs"
    __table_args__ = (
        CheckConstraint(_in("status", RUN_STATUSES), name="status"),
        Index("ix_research_runs_tenant_created", "tenant_id", "created_at"),
    )

    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False
    )
    created_by: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL")
    )
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="queued")
    filters: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    summary: Mapped[str] = mapped_column(String(300), nullable=False, default="")
    # {"step": ..., "done": n, "total": n, "log": [{"at": ..., "text": ...}]}
    progress: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    totals: Mapped[dict[str, int]] = mapped_column(JSONB, nullable=False, default=dict)
    error: Mapped[str | None] = mapped_column(Text)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # Solo las búsquedas guardadas quedan en el historial; las demás se borran solas.
    saved_name: Mapped[str | None] = mapped_column(String(120))
    saved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class ResearchItem(IdMixin, Base):
    """Empresa o decisor encontrado, con cada fuente citada."""

    __tablename__ = "research_items"
    __table_args__ = (
        UniqueConstraint("run_id", "kind", "key"),
        CheckConstraint(_in("kind", ITEM_KINDS), name="kind"),
        Index("ix_research_items_run_kind", "run_id", "kind", "score"),
    )

    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False
    )
    run_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("research_runs.id", ondelete="CASCADE"), nullable=False
    )
    kind: Mapped[str] = mapped_column(String(10), nullable=False)
    key: Mapped[str] = mapped_column(String(400), nullable=False)
    company_key: Mapped[str] = mapped_column(String(300), nullable=False, default="")
    data: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    # [{"type": "wikidata|sitio|noticia|base", "label": ..., "url": ..., "date": ...}]
    sources: Mapped[list[dict[str, Any]]] = mapped_column(JSONB, nullable=False, default=list)
    score: Mapped[int] = mapped_column(SmallInteger, nullable=False, default=0)
    saved_contact_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("contacts.id", ondelete="SET NULL")
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )


class WebPage(Base):
    """Caché global de respuestas públicas (sitios, Wikidata, GDELT). No contiene datos de
    clientes: solo lo que cualquiera puede leer en internet."""

    __tablename__ = "web_cache"

    url_hash: Mapped[str] = mapped_column(String(64), primary_key=True)
    url: Mapped[str] = mapped_column(Text, nullable=False)
    final_url: Mapped[str] = mapped_column(Text, nullable=False, default="")
    status: Mapped[int] = mapped_column(Integer, nullable=False)
    content_type: Mapped[str] = mapped_column(String(120), nullable=False, default="")
    body: Mapped[str] = mapped_column(Text, nullable=False, default="")
    fetched_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class CompanyDomain(Base):
    """Memoria global de ORKESTA: dominio web oficial de cada empresa ya identificada (dato
    público; se reutiliza en todas las búsquedas para no volver a gastar consultas)."""

    __tablename__ = "company_domains"

    key: Mapped[str] = mapped_column(String(320), primary_key=True)  # país|clave de empresa
    domain: Mapped[str] = mapped_column(String(253), nullable=False)
    source: Mapped[str] = mapped_column(String(40), nullable=False, default="")
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class DomainPattern(Base):
    """Memoria global: formato de correo de cada dominio, con la evidencia que lo respalda
    (correos públicos vistos, respuestas recibidas y rebotes de campañas)."""

    __tablename__ = "domain_patterns"

    domain: Mapped[str] = mapped_column(String(253), primary_key=True)
    pattern: Mapped[str] = mapped_column(String(40), nullable=False)
    seen: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    confirmed: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    bounced: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    example: Mapped[str] = mapped_column(Text, nullable=False, default="")
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class PatternStat(Base):
    """Evidencia por dominio y formato: cuántas veces se vio, se confirmó o rebotó."""

    __tablename__ = "pattern_stats"

    domain: Mapped[str] = mapped_column(String(253), primary_key=True)
    pattern: Mapped[str] = mapped_column(String(40), primary_key=True)
    seen: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    confirmed: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    bounced: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class BouncedAddress(Base):
    """Direcciones que rebotaron (solo su huella SHA-256): nunca se vuelven a proponer."""

    __tablename__ = "bounced_addresses"

    address_hash: Mapped[str] = mapped_column(String(64), primary_key=True)
    domain: Mapped[str] = mapped_column(String(253), nullable=False, index=True)
    pattern: Mapped[str] = mapped_column(String(40), nullable=False, default="")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class CompanyDirectory(Base):
    """Directorio global de empresas de ORKESTA: registros públicos oficiales + lo que aprenden
    las búsquedas. Solo datos de empresas (dato público), compartido por todas las cuentas."""

    __tablename__ = "company_directory"
    __table_args__ = (
        Index("ix_company_directory_country_industry", "country", "industry_code"),
        Index("ix_company_directory_country_name", "country", "name_key"),
        Index("ix_company_directory_country_revenue", "country", "revenue"),
    )

    key: Mapped[str] = mapped_column(String(340), primary_key=True)  # país|NIT o país|n:nombre
    country: Mapped[str] = mapped_column(String(2), nullable=False)
    tax_id: Mapped[str | None] = mapped_column(String(30))
    name: Mapped[str] = mapped_column(String(300), nullable=False)
    name_key: Mapped[str] = mapped_column(String(300), nullable=False)
    industry_code: Mapped[str | None] = mapped_column(String(40))
    activity_code: Mapped[str | None] = mapped_column(String(10))
    sector: Mapped[str | None] = mapped_column(String(200))
    city: Mapped[str | None] = mapped_column(String(120))
    region: Mapped[str | None] = mapped_column(String(120))
    place_key: Mapped[str | None] = mapped_column(String(250))
    search_text: Mapped[str] = mapped_column(String(600), nullable=False, default="")
    revenue: Mapped[Decimal | None] = mapped_column(Numeric(20, 2))
    employees: Mapped[int | None] = mapped_column(Integer)
    website: Mapped[str | None] = mapped_column(String(300))
    domain: Mapped[str | None] = mapped_column(String(253))
    linkedin: Mapped[str | None] = mapped_column(String(300))
    year: Mapped[int | None] = mapped_column(SmallInteger)
    source: Mapped[str] = mapped_column(String(60), nullable=False, default="")
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
