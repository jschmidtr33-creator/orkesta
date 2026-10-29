"""Validación de nombres de dominio y extracción del dominio registrable.

Se usa una lista acotada de sufijos de segundo nivel (``com.mx``, ``com.co``...) en lugar de la
Public Suffix List completa: cubre los mercados de ORKESTA sin descargar nada en ejecución.
"""

from __future__ import annotations

import re
from urllib.parse import urlsplit

MULTI_LEVEL_SUFFIXES = frozenset(
    """com.mx org.mx net.mx edu.mx gob.mx com.co net.co org.co edu.co gov.co com.ar org.ar
    net.ar com.pe org.pe net.pe com.br org.br net.br com.ec org.ec com.ve com.bo com.py
    com.uy org.uy com.pa co.cr or.cr com.gt com.hn com.sv com.ni com.do com.cu com.es org.es
    co.uk org.uk com.pr cl.cl""".split()
)

# Sitios que nunca son el dominio oficial de una empresa cliente.
NON_CORPORATE_HOSTS = frozenset(
    """linkedin.com facebook.com instagram.com twitter.com x.com youtube.com tiktok.com
    wikipedia.org wikidata.org google.com bing.com yahoo.com crunchbase.com zoominfo.com
    glassdoor.com glassdoor.com.mx indeed.com computrabajo.com occ.com.mx bumeran.com.mx
    seccionamarilla.com.mx paginasamarillas.com dnb.com bloomberg.com apollo.io rocketreach.co
    lusha.com signalhire.com wix.com wixsite.com blogspot.com wordpress.com godaddysites.com
    sites.google.com medium.com github.com""".split()
)

# Servicios de aparcamiento y venta de dominios.
PARKING_HOSTS = frozenset(
    """sedo.com sedoparking.com dan.com afternic.com hugedomains.com bodis.com parkingcrew.net
    above.com undeveloped.com namebright.com uniregistry.com domainmarket.com buydomains.com
    squadhelp.com atom.com markmonitor.com cscglobal.com cscdbs.com safenames.net comlaude.com
    brandshelter.com""".split()
)

_LABEL = re.compile(r"^(?!-)[a-z0-9-]{1,63}(?<!-)$")


def normalize_host(value: str) -> str | None:
    """Devuelve el host en minúsculas y ASCII (IDNA) o ``None`` si no es un nombre válido."""
    text = value.strip().lower()
    if "://" in text:
        text = urlsplit(text).hostname or ""
    text = text.split("/")[0].split(":")[0].strip(".")
    if text.startswith("www."):
        text = text[4:]
    if not text:
        return None
    try:
        text = text.encode("idna").decode("ascii")
    except UnicodeError:
        return None
    labels = text.split(".")
    if len(text) > 253 or len(labels) < 2 or not all(_LABEL.match(label) for label in labels):
        return None
    if labels[-1].isdigit():
        return None
    return text


def registrable_domain(host: str) -> str | None:
    """``www.ventas.grupomodelo.com.mx`` → ``grupomodelo.com.mx``."""
    normalized = normalize_host(host)
    if normalized is None:
        return None
    labels = normalized.split(".")
    if len(labels) >= 3 and ".".join(labels[-2:]) in MULTI_LEVEL_SUFFIXES:
        return ".".join(labels[-3:])
    return ".".join(labels[-2:])


def is_non_corporate(domain: str) -> bool:
    root = registrable_domain(domain) or domain
    return root in NON_CORPORATE_HOSTS or root in PARKING_HOSTS
