"""Señales extraídas de la portada de un sitio para decidir si pertenece a la empresa."""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from html.parser import HTMLParser

from orkesta.engine.normalize.company import CompanyName
from orkesta.engine.normalize.text import fold

logger = logging.getLogger("orkesta.engine.domain")

PARKED_MARKERS = (
    "domain is for sale", "this domain may be for sale", "buy this domain", "domain for sale",
    "este dominio esta en venta", "dominio en venta", "este dominio puede estar a la venta",
    "parked free", "parkingcrew", "sedoparking", "hugedomains", "domain parking",
    "related searches", "this web page is parked", "website coming soon", "under construction",
    "sitio en construccion", "proximamente",
)  # fmt: skip

# Señales de país en la página: nombre del país, prefijo telefónico e identificadores fiscales.
COUNTRY_MARKERS: dict[str, tuple[str, ...]] = {
    "MX": ("mexico", "cdmx", "ciudad de mexico", "monterrey", "guadalajara", "+52", "rfc",
           "s a de c v", "aviso de privacidad"),
    "CO": ("colombia", "bogota", "medellin", "+57", "nit", "s a s"),
    "PE": ("peru", "lima", "+51", "ruc"),
    "CL": ("chile", "santiago", "+56", "rut"),
    "AR": ("argentina", "buenos aires", "+54", "cuit"),
    "EC": ("ecuador", "quito", "guayaquil", "+593", "ruc"),
    "ES": ("espana", "madrid", "barcelona", "+34", "cif"),
    "US": ("united states", "usa"),
}  # fmt: skip

_SKIP_TAGS = {"script", "style", "noscript", "svg", "template"}


class _PageParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.title = ""
        self.site_name = ""
        self.description = ""
        self.text: list[str] = []
        self._in_title = False
        self._skip = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in _SKIP_TAGS:
            self._skip += 1
        elif tag == "title":
            self._in_title = True
        elif tag == "meta":
            values = {k.lower(): (v or "") for k, v in attrs}
            key = values.get("property") or values.get("name") or ""
            if key.lower() in ("og:site_name", "application-name"):
                self.site_name = values.get("content", "")[:200]
            elif key.lower() in ("description", "og:description"):
                self.description = values.get("content", "")[:500]

    def handle_endtag(self, tag: str) -> None:
        if tag in _SKIP_TAGS and self._skip:
            self._skip -= 1
        elif tag == "title":
            self._in_title = False

    def handle_data(self, data: str) -> None:
        if self._skip:
            return
        if self._in_title:
            self.title += data
        elif len(self.text) < 4000:
            self.text.append(data)


@dataclass(frozen=True)
class PageSignals:
    title: str
    site_name: str
    name_in_header: float  # fracción de tokens distintivos presentes en título o site_name
    name_in_body: bool
    country_markers: tuple[str, ...]
    parked: bool


def _contains(haystack: str, token: str) -> bool:
    return re.search(rf"\b{re.escape(token)}\b", haystack) is not None


def name_coverage(company: CompanyName, folded_text: str) -> float:
    """Qué parte del nombre aparece: 1.0 si está entero (tokens o nombre compacto)."""
    tokens = list(company.distinctive_tokens) or list(company.tokens)
    if not tokens:
        return 0.0
    compact_text = folded_text.replace(" ", "")
    if len("".join(tokens)) >= 4 and "".join(tokens) in compact_text:
        return 1.0
    found = sum(1 for t in tokens if len(t) >= 3 and _contains(folded_text, t))
    return found / len(tokens)


def analyze_page(html: str, companies: list[CompanyName], country: str | None) -> PageSignals:
    parser = _PageParser()
    try:
        parser.feed(html[:500_000])
        parser.close()
    except Exception:  # HTML muy dañado: se usa lo que se haya podido leer
        logger.debug("html_parse_incomplete")
    header = fold(f"{parser.title} {parser.site_name}")
    body = fold(" ".join(parser.text) + " " + parser.description)
    everything = f"{header} {body}"

    in_header = max((name_coverage(c, header) for c in companies), default=0.0)
    in_body = any(name_coverage(c, body) >= 0.99 for c in companies)
    # Los prefijos telefónicos se buscan en el texto sin normalizar: fold() quita el "+".
    raw_compact = re.sub(r"[\s().-]", "", " ".join(parser.text))
    markers = tuple(
        marker
        for marker in COUNTRY_MARKERS.get(country or "", ())
        if (marker.startswith("+") and marker in raw_compact)
        or (not marker.startswith("+") and _contains(everything, marker))
    )
    raw_lower = fold(html[:200_000])
    parked = any(marker in raw_lower for marker in PARKED_MARKERS) and len(body) < 3000
    return PageSignals(
        title=" ".join(parser.title.split())[:300],
        site_name=parser.site_name,
        name_in_header=in_header,
        name_in_body=in_body,
        country_markers=markers,
        parked=parked,
    )
