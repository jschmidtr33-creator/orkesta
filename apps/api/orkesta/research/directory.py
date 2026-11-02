"""Directorio propio de empresas de ORKESTA, armado con registros públicos (sin terceros).

- Importa registros oficiales abiertos (portales Socrata como datos.gov.co, o archivos CSV de
  cualquier registro: RUES, DENUE/INEGI, SUNAT, SII…) detectando solas las columnas.
- Traduce el código de actividad (CIIU Rev. 4 o SCIAN/NAICS) a las industrias de ORKESTA.
- Crece solo: cada empresa que encuentra cualquier búsqueda (con su dominio y LinkedIn) se suma.
- La búsqueda de Cuentas lo consulta primero: miles de empresas reales, filtrables por país,
  industria, ciudad, tamaño e ingresos, sin gastar consultas.

Solo datos de empresas (razón social, NIT, actividad, ciudad, ingresos), nunca de personas.
"""

from __future__ import annotations

import csv
import io
import logging
import re
import threading
from collections.abc import Callable, Iterable, Iterator
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from decimal import Decimal, InvalidOperation
from typing import Any

import httpx
from sqlalchemy import func, or_, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session, sessionmaker

from orkesta.engine.normalize.company import normalize_company
from orkesta.engine.normalize.text import fold
from orkesta.engine.taxonomy.industries import INDUSTRY_LABELS, classify_all, size_band
from orkesta.research.models import CompanyDirectory, CompanyDomain

logger = logging.getLogger(__name__)

# --- Fuentes oficiales precargadas -----------------------------------------------------------

PRESETS: dict[str, dict[str, str]] = {
    "co_supersociedades": {
        "label": "Colombia · Empresas más grandes del país (Supersociedades, datos.gov.co)",
        "country": "CO",
        "kind": "socrata",
        "host": "www.datos.gov.co",
        "query": "10000 empresas mas grandes del pais",
        "dataset": "6cat-2gcs",
        "source": "Supersociedades",
    },
}
REFRESH_EVERY = timedelta(days=30)

# --- Detección de columnas -------------------------------------------------------------------

# Campo → nombres de columna conocidos, en orden de preferencia.
HINTS: dict[str, tuple[str, ...]] = {
    "tax_id": ("nit", "rut", "ruc", "rfc", "cnpj", "cuit", "numero_identificacion",
               "nro_identificacion", "identificacion", "tax_id", "id_tributario"),
    "name": ("razon_social", "raz_n_social", "raz_social", "razonsocial", "nombre_razon_social",
             "denominacion", "nombre_empresa", "nom_estab", "nombre_sociedad", "empresa",
             "company", "company_name", "nombre", "name"),
    "ciiu": ("ciiu", "codigo_ciiu", "ciiu_principal", "cod_ciiu", "actividad_ciiu", "codigo_act",
             "cod_actividad", "codigo_scian", "scian", "naics", "isic", "actividad_economica"),
    "sector": ("descripcion_ciiu", "desc_ciiu", "nombre_act", "actividad", "macrosector",
               "sector", "industria", "industry", "giro"),
    "city": ("ciudad_domicilio", "municipio_domicilio", "ciudad", "municipio", "nom_mun",
             "localidad", "distrito", "comuna", "city"),
    "region": ("departamento_domicilio", "departamento", "regi_n", "region", "entidad",
               "nom_ent", "estado", "provincia", "state"),
    "revenue": ("ingresos_operacionales", "ingresos_operacionales_ano_actual", "ingresos",
                "ventas", "ventas_netas", "revenue", "total_ingresos"),
    "employees": ("numero_empleados", "num_empleados", "empleados", "trabajadores", "per_ocu",
                  "personal_ocupado", "employees"),
    "website": ("sitio_web", "pagina_web", "www", "web", "website", "url"),
    "year": ("a_o_de_corte", "ano_de_corte", "ano_corte", "anio", "ano", "year", "periodo"),
}  # fmt: skip
_CODE_SCHEMES = {"scian": "naics", "naics": "naics", "codigo_scian": "naics"}


def _header(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", fold(str(name)).lower()).strip("_")


def detect_columns(headers: Iterable[str]) -> dict[str, str]:
    """Campo de ORKESTA → columna original. Primero coincidencias exactas, luego parciales."""
    originals = list(headers)
    norm = {_header(h): h for h in originals}
    used: set[str] = set()
    mapping: dict[str, str] = {}
    for fieldname, hints in HINTS.items():
        pick = next((h for h in hints if h in norm and h not in used), "")
        if not pick:
            pick = next(
                (n for h in hints if len(h) >= 4 for n in norm if h in n and n not in used), ""
            )
        if pick:
            mapping[fieldname] = norm[pick]
            used.add(pick)
    return mapping


# --- Normalización de valores -----------------------------------------------------------------


def parse_number(value: Any) -> Decimal | None:
    """Cifras como «$1.234.567», «1,234,567.89», «1234567,5» o «-12.000»."""
    if value is None:
        return None
    if isinstance(value, int | float | Decimal):
        return Decimal(str(value))
    text = re.sub(r"[^\d,.\-]", "", str(value))
    if not re.search(r"\d", text):
        return None
    if "," in text and "." in text:
        decimal_sep = "," if text.rfind(",") > text.rfind(".") else "."
        thousands = "." if decimal_sep == "," else ","
        text = text.replace(thousands, "").replace(decimal_sep, ".")
    elif text.count(".") > 1 or (text.count(".") == 1 and len(text.split(".")[1]) == 3):
        text = text.replace(".", "")
    elif text.count(",") > 1 or (text.count(",") == 1 and len(text.split(",")[1]) == 3):
        text = text.replace(",", "")
    else:
        text = text.replace(",", ".")
    try:
        return Decimal(text)
    except InvalidOperation:
        return None


def parse_employees(value: Any) -> int | None:
    """«120», «51 a 100 personas», «251 y más personas» → número aproximado."""
    numbers = [int(n) for n in re.findall(r"\d+", str(value or "").replace(".", ""))]
    if not numbers:
        return None
    if len(numbers) >= 2:
        return (numbers[0] + numbers[1]) // 2
    return numbers[0]


def clean_tax_id(value: Any) -> str:
    """NIT/RUT/RFC sin puntos ni dígito de verificación («900.123.456-7» → «900123456»)."""
    text = str(value or "").strip().upper()
    if not text:
        return ""
    if re.fullmatch(r"[\d.\s]+-[\dK]", text):
        text = text.rsplit("-", 1)[0]
    return re.sub(r"[^0-9A-Z]", "", text)[:30]


# CIIU Rev. 4 (división, 2 dígitos) → industria de ORKESTA.
_CIIU: list[tuple[range, str]] = [
    (range(1, 4), "agroindustria"), (range(5, 10), "mineria_petroleo"),
    (range(10, 13), "alimentos_bebidas"), (range(13, 16), "textil_moda"),
    (range(16, 19), "manufactura_industrial"), (range(19, 20), "mineria_petroleo"),
    (range(20, 21), "quimica_materiales"), (range(21, 22), "farmaceutica_salud"),
    (range(22, 24), "quimica_materiales"), (range(24, 29), "manufactura_industrial"),
    (range(29, 31), "automotriz"), (range(31, 34), "manufactura_industrial"),
    (range(35, 36), "energia"), (range(36, 40), "servicios_profesionales"),
    (range(41, 44), "construccion_inmobiliario"), (range(45, 46), "automotriz"),
    (range(46, 48), "retail_consumo"), (range(49, 54), "transporte_logistica"),
    (range(55, 57), "turismo_hoteleria"), (range(58, 61), "medios_entretenimiento"),
    (range(61, 62), "telecomunicaciones"), (range(62, 64), "tecnologia_software"),
    (range(64, 65), "banca_finanzas"), (range(65, 66), "seguros"),
    (range(66, 67), "banca_finanzas"), (range(68, 69), "construccion_inmobiliario"),
    (range(69, 76), "servicios_profesionales"), (range(77, 83), "servicios_profesionales"),
    (range(84, 85), "gobierno"), (range(85, 86), "educacion"),
    (range(86, 89), "farmaceutica_salud"), (range(90, 94), "medios_entretenimiento"),
    (range(94, 95), "ong"), (range(95, 97), "servicios_profesionales"),
]  # fmt: skip
# SCIAN / NAICS (sector, 2-3 dígitos) → industria de ORKESTA (México, EE. UU., Canadá).
_NAICS: list[tuple[str, str]] = [
    ("524", "seguros"), ("517", "telecomunicaciones"), ("518", "tecnologia_software"),
    ("5112", "tecnologia_software"), ("5415", "tecnologia_software"), ("336", "automotriz"),
    ("325", "quimica_materiales"), ("3254", "farmaceutica_salud"), ("311", "alimentos_bebidas"),
    ("312", "alimentos_bebidas"), ("313", "textil_moda"), ("314", "textil_moda"),
    ("315", "textil_moda"), ("11", "agroindustria"), ("21", "mineria_petroleo"),
    ("22", "energia"), ("23", "construccion_inmobiliario"), ("31", "manufactura_industrial"),
    ("32", "manufactura_industrial"), ("33", "manufactura_industrial"),
    ("43", "retail_consumo"), ("46", "retail_consumo"), ("42", "retail_consumo"),
    ("44", "retail_consumo"), ("45", "retail_consumo"), ("48", "transporte_logistica"),
    ("49", "transporte_logistica"), ("51", "medios_entretenimiento"), ("52", "banca_finanzas"),
    ("53", "construccion_inmobiliario"), ("54", "servicios_profesionales"),
    ("55", "servicios_profesionales"), ("56", "servicios_profesionales"), ("61", "educacion"),
    ("62", "farmaceutica_salud"), ("71", "medios_entretenimiento"), ("72", "turismo_hoteleria"),
    ("81", "servicios_profesionales"), ("93", "gobierno"),
]  # fmt: skip


def industry_from_code(code: Any, scheme: str = "ciiu") -> tuple[str, str]:
    """(código limpio, industria de ORKESTA) para un código CIIU o SCIAN."""
    digits = re.search(r"\d{2,6}", str(code or ""))
    if not digits:
        return "", ""
    value = digits.group(0)
    if scheme == "naics":
        best = max((p for p, _ in _NAICS if value.startswith(p)), key=len, default="")
        return value, dict(_NAICS).get(best, "")
    division = int(value[:2]) if len(value) in (2, 4, 5) else int(value.zfill(4)[:2])
    return value, next((ind for r, ind in _CIIU if division in r), "")


def name_key(name: str) -> str:
    return (normalize_company(name).match_key or fold(name))[:300]


def _domain(website: str) -> str:
    host = re.sub(r"^[a-z]+://", "", str(website or "").strip().lower()).split("/")[0]
    host = host.removeprefix("www.")
    return host if re.fullmatch(r"[a-z0-9.-]+\.[a-z]{2,}", host) else ""


def normalize_row(
    row: dict[str, Any], mapping: dict[str, str], country: str, source: str
) -> dict[str, Any] | None:
    def get(name: str) -> Any:
        column = mapping.get(name)
        return row.get(column) if column else None

    name = " ".join(str(get("name") or "").split())[:300]
    if len(name) < 2:
        return None
    tax_id = clean_tax_id(get("tax_id"))
    key_name = name_key(name)
    scheme = _CODE_SCHEMES.get(_header(mapping.get("ciiu", "")), "")
    scheme = scheme or ("naics" if country in ("MX", "US", "CA") else "ciiu")
    code, industry = industry_from_code(get("ciiu"), scheme)
    sector = " ".join(str(get("sector") or "").split())[:200]
    if not industry:
        found = classify_all([sector, name])
        industry = found[0] if found else ""
    city = " ".join(str(get("city") or "").split()).title()[:120]
    region = " ".join(str(get("region") or "").split()).title()[:120]
    revenue = parse_number(get("revenue"))
    found_year = re.search(r"(19|20)\d{2}", str(get("year") or ""))
    year = int(found_year.group(0)) if found_year else None
    website = str(get("website") or "").strip()[:300]
    return {
        "key": f"{country}|{tax_id}" if tax_id else f"{country}|n:{key_name}",
        "country": country,
        "tax_id": tax_id or None,
        "name": name,
        "name_key": key_name,
        "industry_code": industry or None,
        "activity_code": code or None,
        "sector": sector or None,
        "city": city or None,
        "region": region or None,
        "place_key": fold(f"{city} {region}").strip()[:250] or None,
        "search_text": fold(f"{name} {sector}")[:600],
        "revenue": revenue if revenue is not None and abs(revenue) < Decimal("1e16") else None,
        "employees": parse_employees(get("employees")),
        "website": website or None,
        "domain": _domain(website) or None,
        "year": year if year and 1990 < year < 2100 else None,
        "source": source[:60],
    }


# --- Guardar ---------------------------------------------------------------------------------

_UPDATE = (
    "tax_id",
    "name",
    "name_key",
    "industry_code",
    "activity_code",
    "sector",
    "city",
    "region",
    "place_key",
    "search_text",
    "revenue",
    "employees",
    "year",
    "source",
)


def upsert(session: Session, rows: list[dict[str, Any]]) -> int:
    unique = {r["key"]: r for r in rows}
    if not unique:
        return 0
    stmt = pg_insert(CompanyDirectory).values(list(unique.values()))
    set_: dict[str, Any] = {c: func.coalesce(stmt.excluded[c], getattr(CompanyDirectory, c))
                            for c in _UPDATE}  # fmt: skip
    set_["domain"] = func.coalesce(CompanyDirectory.domain, stmt.excluded.domain)
    set_["website"] = func.coalesce(CompanyDirectory.website, stmt.excluded.website)
    set_["updated_at"] = func.now()
    session.execute(stmt.on_conflict_do_update(index_elements=["key"], set_=set_))
    return len(unique)


@dataclass
class ImportResult:
    rows: int = 0
    saved: int = 0
    skipped: int = 0
    columns: dict[str, str] = field(default_factory=dict)
    error: str = ""


def import_rows(
    factory: sessionmaker[Session], records: Iterator[dict[str, Any]], country: str, source: str,
    *, progress: Callable[[int], None] | None = None,
) -> ImportResult:  # fmt: skip
    result = ImportResult()
    mapping: dict[str, str] | None = None
    batch: list[dict[str, Any]] = []

    def flush() -> None:
        if batch:
            with factory.begin() as session:
                result.saved += upsert(session, batch)
            batch.clear()
            if progress:
                progress(result.saved)

    for record in records:
        if mapping is None:
            mapping = detect_columns(record.keys())
            result.columns = mapping
            if "name" not in mapping:
                result.error = "No encontré la columna con el nombre o razón social."
                return result
        result.rows += 1
        row = normalize_row(record, mapping, country, source)
        if row is None:
            result.skipped += 1
            continue
        batch.append(row)
        if len(batch) >= 1000:
            flush()
    flush()
    return result


def csv_records(raw: bytes) -> Iterator[dict[str, Any]]:
    """Filas de un CSV de registro público: detecta codificación y separador."""
    try:
        text = raw.decode("utf-8-sig")
    except UnicodeDecodeError:
        text = raw.decode("latin-1")
    sample = text[:20000]
    try:
        dialect: Any = csv.Sniffer().sniff(sample, delimiters=",;|\t")
    except csv.Error:
        dialect = csv.excel
    yield from csv.DictReader(io.StringIO(text), dialect=dialect)


# --- Portales de datos abiertos (Socrata: datos.gov.co y similares) ---------------------------

TRANSPORT: httpx.BaseTransport | None = None  # pruebas
PAGE = 5000


def _client() -> httpx.Client:
    from orkesta.research.fetch import USER_AGENT

    return httpx.Client(
        timeout=60.0,
        transport=TRANSPORT,
        follow_redirects=True,
        headers={"User-Agent": USER_AGENT, "Accept": "application/json"},
    )


def find_dataset(client: httpx.Client, host: str, query: str, fallback: str) -> str:
    """Busca el conjunto de datos en el catálogo del portal (el identificador puede cambiar)."""
    try:
        response = client.get("https://api.us.socrata.com/api/catalog/v1",
                              params={"domains": host, "q": query, "only": "datasets",
                                      "limit": 5})  # fmt: skip
        for item in response.json().get("results", []) if response.status_code == 200 else []:
            columns = [_header(c) for c in item.get("resource", {}).get("columns_field_name", [])]
            mapping = detect_columns(columns)
            if "name" in mapping and ("tax_id" in mapping or "revenue" in mapping):
                return str(item["resource"]["id"])
    except (httpx.HTTPError, ValueError, KeyError):
        logger.info("directory_catalog_unavailable", extra={"host": host})
    return fallback


def socrata_records(client: httpx.Client, host: str, dataset: str) -> Iterator[dict[str, Any]]:
    offset = 0
    while True:
        response = client.get(
            f"https://{host}/resource/{dataset}.json",
            params={"$limit": PAGE, "$offset": offset, "$order": ":id"},
        )
        if response.status_code != 200:
            raise RuntimeError(f"El portal respondió {response.status_code}.")
        page = response.json()
        if not isinstance(page, list):
            raise RuntimeError("Respuesta inesperada del portal.")
        yield from page
        if len(page) < PAGE:
            return
        offset += PAGE


# --- Importaciones en segundo plano ----------------------------------------------------------

STATE: dict[str, dict[str, Any]] = {}
_LOCK = threading.Lock()


def _set(key: str, **values: Any) -> None:
    with _LOCK:
        STATE.setdefault(key, {}).update(values)


def run_preset(factory: sessionmaker[Session], preset_id: str) -> ImportResult:
    preset = PRESETS[preset_id]
    _set(preset_id, status="running", started_at=datetime.now(UTC).isoformat(), saved=0, error="")
    try:
        with _client() as client:
            dataset = find_dataset(client, preset["host"], preset["query"], preset["dataset"])
            result = import_rows(factory, socrata_records(client, preset["host"], dataset),
                                 preset["country"], preset["source"],
                                 progress=lambda n: _set(preset_id, saved=n))  # fmt: skip
    except (httpx.HTTPError, RuntimeError, ValueError) as exc:
        result = ImportResult(error=str(exc)[:300] or "No se pudo descargar el registro.")
    _record(factory, preset_id, result)
    return result


def run_upload(
    factory: sessionmaker[Session], raw: bytes, country: str, source: str
) -> ImportResult:
    key = f"archivo:{source}"
    _set(key, status="running", started_at=datetime.now(UTC).isoformat(), saved=0, error="")
    try:
        result = import_rows(factory, csv_records(raw), country, source,
                             progress=lambda n: _set(key, saved=n))  # fmt: skip
    except (csv.Error, ValueError) as exc:
        result = ImportResult(error=f"No pude leer el archivo: {exc}"[:300])
    _record(factory, key, result)
    return result


def _record(factory: sessionmaker[Session], key: str, result: ImportResult) -> None:
    from orkesta.admin.service import _get, _put

    status = "failed" if result.error and not result.saved else "done"
    finished = datetime.now(UTC).isoformat()
    _set(key, status=status, saved=result.saved, error=result.error, finished_at=finished)
    with factory.begin() as session:
        history = dict(_get(session, "directory_imports", {}))
        history[key] = {"status": status, "saved": result.saved, "rows": result.rows,
                        "error": result.error, "finished_at": finished,
                        "columns": result.columns}  # fmt: skip
        _put(session, "directory_imports", history)


def start(target: Callable[[], Any], key: str) -> bool:
    """Lanza la importación en un hilo; ``False`` si ya hay una en curso para esa fuente."""
    with _LOCK:
        if STATE.get(key, {}).get("status") == "running":
            return False
        STATE.setdefault(key, {})["status"] = "running"
    threading.Thread(target=target, daemon=True, name=f"directorio-{key}").start()
    return True


def maybe_refresh(factory: sessionmaker[Session]) -> None:
    """Carga automática de las fuentes oficiales la primera vez y luego cada mes."""
    from orkesta.admin.service import _get

    with factory() as session:
        history = dict(_get(session, "directory_imports", {}))
    for preset_id in PRESETS:
        last = history.get(preset_id, {})
        wait = REFRESH_EVERY if last.get("status") == "done" else timedelta(hours=6)
        finished = last.get("finished_at")
        if finished and datetime.fromisoformat(finished) > datetime.now(UTC) - wait:
            continue
        start(lambda p=preset_id: run_preset(factory, p), preset_id)  # type: ignore[misc]


def people_stats(session: Session) -> dict[str, int]:
    from orkesta.research import people

    return people.stats(session)


def crawler_overview(session: Session) -> dict[str, Any]:
    from orkesta.research import crawler

    return crawler.overview(session)


def overview(session: Session) -> dict[str, Any]:
    from orkesta.admin.service import _get

    rows = session.execute(
        select(CompanyDirectory.country, func.count(), func.count(CompanyDirectory.domain))
        .group_by(CompanyDirectory.country)
        .order_by(func.count().desc())
    ).all()
    history = dict(_get(session, "directory_imports", {}))
    with _LOCK:
        live = {k: dict(v) for k, v in STATE.items()}
    return {
        "countries": [
            {"country": c, "companies": int(n), "with_domain": int(d)} for c, n, d in rows
        ],
        "total": sum(int(n) for _, n, _ in rows),
        "presets": [
            {
                "id": k,
                **{f: v[f] for f in ("label", "country", "source")},
                "last": history.get(k),
                "live": live.get(k),
            }
            for k, v in PRESETS.items()
        ],
        "crawler": crawler_overview(session),
        "people": people_stats(session),
        "uploads": [
            {"id": k, "last": v, "live": live.get(k)}
            for k, v in history.items()
            if k.startswith("archivo:")
        ],
    }


# --- Aprendizaje: empresas que encuentran las búsquedas ---------------------------------------


def learn(factory: sessionmaker[Session], country: str, companies: Iterable[Any]) -> int:
    """Suma al directorio (o completa) las empresas que encontró una búsqueda."""
    rows: dict[str, dict[str, Any]] = {}
    for c in companies:
        if not c.name or not country:
            continue
        key_name = name_key(c.name)
        rows[key_name] = {
            "name": c.name[:300], "name_key": key_name, "domain": c.domain or None,
            "website": (c.website or None) and c.website[:300], "linkedin": c.linkedin or None,
            "industry_code": c.industry_code or None, "sector": (c.industry or "")[:200] or None,
            "city": (c.city or "")[:120] or None, "employees": c.employees,
        }  # fmt: skip
    if not rows:
        return 0
    try:
        with factory.begin() as session:
            existing = {
                r.name_key: r
                for r in session.scalars(
                    select(CompanyDirectory).where(
                        CompanyDirectory.country == country,
                        CompanyDirectory.name_key.in_(list(rows)),
                    )
                )
            }
            new = []
            for key_name, data in rows.items():
                row = existing.get(key_name)
                if row is None:
                    new.append({
                        **data, "key": f"{country}|n:{key_name}", "country": country,
                        "place_key": fold(data["city"] or "") or None,
                        "search_text": fold(f"{data['name']} {data['sector'] or ''}")[:600],
                        "source": "busquedas",
                    })  # fmt: skip
                    continue
                for attr in ("domain", "website", "linkedin", "industry_code", "employees"):
                    if getattr(row, attr) in (None, "") and data[attr]:
                        setattr(row, attr, data[attr])
            if new:
                session.execute(pg_insert(CompanyDirectory).values(new).on_conflict_do_nothing())
            return len(rows)
    except Exception:
        logger.exception("directory_learn")
        return 0


# --- Búsqueda ---------------------------------------------------------------------------------


@dataclass
class Entry:
    name: str
    name_key: str
    domain: str
    website: str
    linkedin: str
    industry_code: str
    sector: str
    city: str
    region: str
    employees: int | None
    revenue: Decimal | None
    tax_id: str
    source: str
    phone: str = ""
    company_email: str = ""
    about: str = ""
    email_pattern: str = ""

    @property
    def industry(self) -> str:
        return INDUSTRY_LABELS.get(self.industry_code, "") or self.sector

    def description(self) -> str:
        parts = []
        if self.sector:
            parts.append(self.sector)
        if self.revenue:
            parts.append(f"Ingresos reportados: {_money(self.revenue)}")
        if self.about and not parts:
            parts.append(self.about)
        return " · ".join(parts)


def _money(value: Decimal) -> str:
    n = float(value)
    for limit, suffix in ((1e12, " billones"), (1e9, " mil millones"), (1e6, " millones")):
        if abs(n) >= limit:
            return f"{n / limit:,.1f}{suffix}".replace(",", " ")
    return f"{n:,.0f}"


def search(
    session: Session, *, country: str, industries: list[str], exclude_industries: list[str],
    cities: list[str], keywords: str, sizes: list[str], limit: int = 500,
) -> list[Entry]:  # fmt: skip
    query = select(CompanyDirectory).where(CompanyDirectory.country == country)
    if industries:
        query = query.where(CompanyDirectory.industry_code.in_(industries))
    if exclude_industries:
        query = query.where(
            or_(
                CompanyDirectory.industry_code.is_(None),
                CompanyDirectory.industry_code.not_in(exclude_industries),
            )
        )
    places = [fold(c) for c in cities if fold(c)]
    if places:
        query = query.where(or_(*[CompanyDirectory.place_key.contains(p) for p in places]))
    for word in fold(keywords).split()[:6]:
        query = query.where(CompanyDirectory.search_text.contains(word))
    query = query.order_by(
        CompanyDirectory.revenue.desc().nulls_last(),
        CompanyDirectory.employees.desc().nulls_last(),
        CompanyDirectory.name,
    ).limit(limit * 2 if sizes else limit)
    rows = list(session.scalars(query))
    if sizes:
        rows = [r for r in rows if r.employees is None or size_band(r.employees) in sizes]
        rows.sort(key=lambda r: r.employees is None)
    rows = rows[:limit]
    known = _known_domains(session, country, [r.name_key for r in rows if not r.domain])
    return [
        Entry(
            name=r.name, name_key=r.name_key, domain=r.domain or known.get(r.name_key, ""),
            website=r.website or "", linkedin=r.linkedin or "",
            industry_code=r.industry_code or "", sector=r.sector or "", city=r.city or "",
            region=r.region or "", employees=r.employees, revenue=r.revenue,
            tax_id=r.tax_id or "", source=r.source or "", phone=r.phone or "",
            company_email=r.company_email or "", about=r.description or "",
            email_pattern=r.email_pattern or "",
        )
        for r in rows
    ]  # fmt: skip


def _known_domains(session: Session, country: str, keys: list[str]) -> dict[str, str]:
    """Dominios que ORKESTA ya aprendió en búsquedas anteriores (memoria global)."""
    if not keys:
        return {}
    rows = session.execute(
        select(CompanyDomain.key, CompanyDomain.domain).where(
            CompanyDomain.key.in_([f"{country}|{k}"[:320] for k in keys])
        )
    ).all()
    return {k.split("|", 1)[1]: d for k, d in rows}
