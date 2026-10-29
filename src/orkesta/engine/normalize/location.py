"""Interpretación de ubicaciones libres ("Bogotá, Distrito Capital, Colombia",
"Greater Mexico City Area", "Lima, Perú") a país ISO 3166-1 alfa-2 y ciudad.

Confianza:
- ``high``: el país aparece de forma explícita.
- ``medium``: el país se deduce de una ciudad que solo existe en un país del diccionario.
- ``low``: la ciudad es ambigua (Santiago, Córdoba, San José) y se elige el país más probable.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from functools import lru_cache
from typing import Literal

from orkesta.engine.normalize.text import clean, fold

Confidence = Literal["high", "medium", "low"]

# País → nombre visible y variantes (en forma plegada). Incluye códigos ISO2/ISO3 y gentilicios
# frecuentes en exportaciones de CRM y LinkedIn.
COUNTRIES: dict[str, tuple[str, tuple[str, ...]]] = {
    "CO": ("Colombia", ("colombia", "co", "col", "colombian")),
    "MX": ("México", ("mexico", "mx", "mex", "mejico", "estados unidos mexicanos", "mexican")),
    "PE": ("Perú", ("peru", "pe", "per")),
    "CL": ("Chile", ("chile", "cl", "chl")),
    "AR": ("Argentina", ("argentina", "ar", "arg")),
    "EC": ("Ecuador", ("ecuador", "ec", "ecu")),
    "VE": ("Venezuela", ("venezuela", "ve", "ven")),
    "BO": ("Bolivia", ("bolivia", "bo", "bol", "estado plurinacional de bolivia")),
    "PY": ("Paraguay", ("paraguay", "py", "pry")),
    "UY": ("Uruguay", ("uruguay", "uy", "ury")),
    "BR": ("Brasil", ("brasil", "brazil", "br", "bra")),
    "PA": ("Panamá", ("panama", "pa", "pan", "republica de panama")),
    "CR": ("Costa Rica", ("costa rica", "cr", "cri")),
    "GT": ("Guatemala", ("guatemala", "gt", "gtm")),
    "HN": ("Honduras", ("honduras", "hn", "hnd")),
    "SV": ("El Salvador", ("el salvador", "salvador", "sv", "slv")),
    "NI": ("Nicaragua", ("nicaragua", "ni", "nic")),
    "DO": (
        "República Dominicana",
        ("republica dominicana", "dominican republic", "do", "dom", "rep dominicana", "rd"),
    ),
    "PR": ("Puerto Rico", ("puerto rico", "pr", "pri")),
    "CU": ("Cuba", ("cuba", "cu", "cub")),
    "ES": ("España", ("espana", "spain", "es", "esp")),
    "US": (
        "Estados Unidos",
        (
            "estados unidos",
            "united states",
            "united states of america",
            "usa",
            "us",
            "eeuu",
            "ee uu",
            "u s a",
            "u s",
        ),
    ),
    "CA": ("Canadá", ("canada", "ca", "can")),
    "PT": ("Portugal", ("portugal", "pt", "prt")),
    "GB": ("Reino Unido", ("reino unido", "united kingdom", "uk", "gb", "gbr", "england")),
}

# Ciudad (forma plegada) → (país, nombre visible). Ciudades principales de LATAM y mercados afines.
CITIES: dict[str, tuple[str, str]] = {
    # Colombia
    "bogota": ("CO", "Bogotá"),
    "bogota dc": ("CO", "Bogotá"),
    "bogota d c": ("CO", "Bogotá"),
    "medellin": ("CO", "Medellín"),
    "cali": ("CO", "Cali"),
    "barranquilla": ("CO", "Barranquilla"),
    "cartagena": ("CO", "Cartagena"),
    "bucaramanga": ("CO", "Bucaramanga"),
    "pereira": ("CO", "Pereira"),
    "manizales": ("CO", "Manizales"),
    "cucuta": ("CO", "Cúcuta"),
    "ibague": ("CO", "Ibagué"),
    "santa marta": ("CO", "Santa Marta"),
    "villavicencio": ("CO", "Villavicencio"),
    "pasto": ("CO", "Pasto"),
    "neiva": ("CO", "Neiva"),
    "armenia": ("CO", "Armenia"),
    "monteria": ("CO", "Montería"),
    "envigado": ("CO", "Envigado"),
    "chia": ("CO", "Chía"),
    "itagui": ("CO", "Itagüí"),
    "rionegro": ("CO", "Rionegro"),
    "tunja": ("CO", "Tunja"),
    "popayan": ("CO", "Popayán"),
    "sincelejo": ("CO", "Sincelejo"),
    # México
    "ciudad de mexico": ("MX", "Ciudad de México"),
    "cdmx": ("MX", "Ciudad de México"),
    "mexico city": ("MX", "Ciudad de México"),
    "mexico df": ("MX", "Ciudad de México"),
    "df": ("MX", "Ciudad de México"),
    "distrito federal": ("MX", "Ciudad de México"),
    "guadalajara": ("MX", "Guadalajara"),
    "monterrey": ("MX", "Monterrey"),
    "puebla": ("MX", "Puebla"),
    "queretaro": ("MX", "Querétaro"),
    "santiago de queretaro": ("MX", "Querétaro"),
    "tijuana": ("MX", "Tijuana"),
    "merida": ("MX", "Mérida"),
    "leon": ("MX", "León"),
    "zapopan": ("MX", "Zapopan"),
    "san pedro garza garcia": ("MX", "San Pedro Garza García"),
    "cancun": ("MX", "Cancún"),
    "toluca": ("MX", "Toluca"),
    "aguascalientes": ("MX", "Aguascalientes"),
    "san luis potosi": ("MX", "San Luis Potosí"),
    "chihuahua": ("MX", "Chihuahua"),
    "ciudad juarez": ("MX", "Ciudad Juárez"),
    "hermosillo": ("MX", "Hermosillo"),
    "culiacan": ("MX", "Culiacán"),
    "saltillo": ("MX", "Saltillo"),
    "morelia": ("MX", "Morelia"),
    "veracruz": ("MX", "Veracruz"),
    "naucalpan": ("MX", "Naucalpan"),
    "tlalnepantla": ("MX", "Tlalnepantla"),
    "oaxaca": ("MX", "Oaxaca"),
    # Perú
    "lima": ("PE", "Lima"),
    "arequipa": ("PE", "Arequipa"),
    "trujillo": ("PE", "Trujillo"),
    "cusco": ("PE", "Cusco"),
    "chiclayo": ("PE", "Chiclayo"),
    "piura": ("PE", "Piura"),
    "miraflores": ("PE", "Miraflores"),
    "san isidro": ("PE", "San Isidro"),
    # Chile
    "santiago": ("CL", "Santiago"),
    "santiago de chile": ("CL", "Santiago"),
    "valparaiso": ("CL", "Valparaíso"),
    "vina del mar": ("CL", "Viña del Mar"),
    "concepcion": ("CL", "Concepción"),
    "antofagasta": ("CL", "Antofagasta"),
    "las condes": ("CL", "Las Condes"),
    "providencia": ("CL", "Providencia"),
    # Argentina
    "buenos aires": ("AR", "Buenos Aires"),
    "caba": ("AR", "Buenos Aires"),
    "cordoba": ("AR", "Córdoba"),
    "rosario": ("AR", "Rosario"),
    "mendoza": ("AR", "Mendoza"),
    "la plata": ("AR", "La Plata"),
    "mar del plata": ("AR", "Mar del Plata"),
    "tucuman": ("AR", "Tucumán"),
    # Ecuador
    "quito": ("EC", "Quito"),
    "guayaquil": ("EC", "Guayaquil"),
    "cuenca": ("EC", "Cuenca"),
    # Venezuela
    "caracas": ("VE", "Caracas"),
    "maracaibo": ("VE", "Maracaibo"),
    "valencia": ("VE", "Valencia"),
    "barquisimeto": ("VE", "Barquisimeto"),
    # Bolivia, Paraguay, Uruguay
    "la paz": ("BO", "La Paz"),
    "santa cruz de la sierra": ("BO", "Santa Cruz de la Sierra"),
    "santa cruz": ("BO", "Santa Cruz de la Sierra"),
    "cochabamba": ("BO", "Cochabamba"),
    "asuncion": ("PY", "Asunción"),
    "ciudad del este": ("PY", "Ciudad del Este"),
    "montevideo": ("UY", "Montevideo"),
    "punta del este": ("UY", "Punta del Este"),
    # Brasil
    "sao paulo": ("BR", "São Paulo"),
    "rio de janeiro": ("BR", "Rio de Janeiro"),
    "brasilia": ("BR", "Brasília"),
    "belo horizonte": ("BR", "Belo Horizonte"),
    "curitiba": ("BR", "Curitiba"),
    "porto alegre": ("BR", "Porto Alegre"),
    "recife": ("BR", "Recife"),
    "salvador de bahia": ("BR", "Salvador"),
    # Centroamérica y Caribe
    "ciudad de panama": ("PA", "Ciudad de Panamá"),
    "panama city": ("PA", "Ciudad de Panamá"),
    "san jose": ("CR", "San José"),
    "heredia": ("CR", "Heredia"),
    "escazu": ("CR", "Escazú"),
    "ciudad de guatemala": ("GT", "Ciudad de Guatemala"),
    "guatemala city": ("GT", "Ciudad de Guatemala"),
    "tegucigalpa": ("HN", "Tegucigalpa"),
    "san pedro sula": ("HN", "San Pedro Sula"),
    "san salvador": ("SV", "San Salvador"),
    "managua": ("NI", "Managua"),
    "santo domingo": ("DO", "Santo Domingo"),
    "santiago de los caballeros": ("DO", "Santiago de los Caballeros"),
    "punta cana": ("DO", "Punta Cana"),
    "san juan": ("PR", "San Juan"),
    "la habana": ("CU", "La Habana"),
    "havana": ("CU", "La Habana"),
    # España y Estados Unidos
    "madrid": ("ES", "Madrid"),
    "barcelona": ("ES", "Barcelona"),
    "sevilla": ("ES", "Sevilla"),
    "bilbao": ("ES", "Bilbao"),
    "malaga": ("ES", "Málaga"),
    "miami": ("US", "Miami"),
    "new york": ("US", "Nueva York"),
    "nueva york": ("US", "Nueva York"),
    "houston": ("US", "Houston"),
    "los angeles": ("US", "Los Ángeles"),
    "san francisco": ("US", "San Francisco"),
    "austin": ("US", "Austin"),
    "dallas": ("US", "Dallas"),
    "chicago": ("US", "Chicago"),
    "doral": ("US", "Doral"),
    "toronto": ("CA", "Toronto"),
    "montreal": ("CA", "Montreal"),
    "lisboa": ("PT", "Lisboa"),
    "london": ("GB", "Londres"),
    "londres": ("GB", "Londres"),
}

# Ciudades que existen con peso en varios países: se asigna el más probable con confianza baja.
AMBIGUOUS_CITIES = frozenset(
    {
        "santiago",
        "cordoba",
        "san jose",
        "valencia",
        "leon",
        "merida",
        "trujillo",
        "santa cruz",
        "san juan",
        "concepcion",
        "armenia",
        "salvador",
        "guadalajara",
        "cartagena",
        "la paz",
        "san isidro",
        "providencia",
        "victoria",
        "rosario",
        "heredia",
    }
)

# Regiones administrativas: si aparecen sin ciudad, permiten deducir el país.
REGIONS: dict[str, str] = {
    "distrito capital": "CO",
    "cundinamarca": "CO",
    "antioquia": "CO",
    "valle del cauca": "CO",
    "atlantico": "CO",
    "santander": "CO",
    "bolivar": "CO",
    "risaralda": "CO",
    "caldas": "CO",
    "jalisco": "MX",
    "nuevo leon": "MX",
    "estado de mexico": "MX",
    "edomex": "MX",
    "baja california": "MX",
    "yucatan": "MX",
    "guanajuato": "MX",
    "sonora": "MX",
    "coahuila": "MX",
    "veracruz de ignacio de la llave": "MX",
    "quintana roo": "MX",
    "region metropolitana": "CL",
    "provincia de buenos aires": "AR",
    "pichincha": "EC",
    "guayas": "EC",
    "florida": "US",
    "texas": "US",
    "california": "US",
    "comunidad de madrid": "ES",
    "cataluna": "ES",
}

_NOISE = re.compile(
    r"\b(greater|area metropolitana de|area metropolitana|metropolitan area|metro area|"
    r"metropolitana|metropolitan|area|region de|region|zona|provincia de|departamento de|"
    r"estado de|state of|city of|municipio de|y alrededores|and surrounding)\b"
)

_COUNTRY_INDEX: dict[str, str] = {
    variant: code for code, (_, variants) in COUNTRIES.items() for variant in variants
}
# Códigos de dos letras: solo cuentan como país cuando ocupan un segmento entero.
_SHORT_CODES = {v for v in _COUNTRY_INDEX if len(v) <= 3}


def _alternation(words: list[str]) -> re.Pattern[str]:
    """Un solo patrón para todas las palabras, con las más largas primero."""
    ordered = sorted(words, key=len, reverse=True)
    return re.compile(r"\b(" + "|".join(re.escape(w) for w in ordered) + r")\b")


_COUNTRY_PATTERN = _alternation([v for v in _COUNTRY_INDEX if v not in _SHORT_CODES])
_CITY_PATTERN = _alternation([k for k in CITIES if len(k) > 3])


@dataclass(frozen=True)
class Location:
    original: str
    country_code: str | None
    country_name: str | None
    city: str | None
    confidence: Confidence | None

    @property
    def recognized(self) -> bool:
        return self.country_code is not None


def _segments(text: str) -> list[str]:
    parts = re.split(r"[,;/|\-–—]| y alrededores", text)
    out = []
    for part in parts:
        folded = _NOISE.sub(" ", fold(part))
        folded = re.sub(r"\s+", " ", folded).strip()
        if folded:
            out.append(folded)
    return out


def _find_country(segments: list[str]) -> str | None:
    # 1) Un segmento completo que es un país (de derecha a izquierda: "Ciudad, Región, País").
    for segment in reversed(segments):
        if segment in _COUNTRY_INDEX:
            return _COUNTRY_INDEX[segment]
    # 2) Un país largo contenido en un segmento ("greater mexico city" no; "lima peru" sí).
    for segment in reversed(segments):
        if segment in CITIES:
            continue
        match = _COUNTRY_PATTERN.search(segment)
        if match:
            return _COUNTRY_INDEX[match.group(1)]
    return None


def _find_city(segments: list[str], country: str | None) -> tuple[str, str] | None:
    candidates: list[tuple[str, tuple[str, str]]] = []
    for segment in segments:
        if segment in CITIES:
            candidates.append((segment, CITIES[segment]))
            continue
        # Ciudad contenida en el segmento, prefiriendo la coincidencia más larga.
        found = [m.group(1) for m in _CITY_PATTERN.finditer(segment)]
        if found:
            key = max(found, key=len)
            candidates.append((key, CITIES[key]))
    if not candidates:
        return None
    if country is not None:
        for key, (city_country, display) in candidates:
            if city_country == country:
                return key, display
        # Ciudad ambigua cuyo homónimo está en el país indicado ("Santiago, República Dominicana").
        key, (_, display) = candidates[0]
        return (key, display) if key in AMBIGUOUS_CITIES else None
    return candidates[0][0], candidates[0][1][1]


@lru_cache(maxsize=50_000)
def parse_location(value: str | None) -> Location:
    original = clean(value)
    if not original:
        return Location(original, None, None, None, None)

    segments = _segments(original)
    country = _find_country(segments)
    city = _find_city(segments, country)

    confidence: Confidence | None
    if country is not None:
        confidence = "high"
    elif city is not None:
        key, _ = city
        country = CITIES[key][0]
        confidence = "low" if key in AMBIGUOUS_CITIES else "medium"
    else:
        region_country = next((REGIONS[s] for s in segments if s in REGIONS), None)
        if region_country is not None:
            country, confidence = region_country, "medium"
        else:
            confidence = None

    country_name = COUNTRIES[country][0] if country else None
    return Location(
        original=original,
        country_code=country,
        country_name=country_name,
        city=city[1] if city else None,
        confidence=confidence,
    )
