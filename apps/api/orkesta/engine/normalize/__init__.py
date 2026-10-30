"""Normalización determinista de nombres, empresas y ubicaciones.

Nunca modifica el valor original: cada función devuelve el original junto a sus formas
normalizadas, para que la exportación conserve los datos tal como llegaron.
"""

from orkesta.engine.normalize.company import CompanyName, normalize_company
from orkesta.engine.normalize.location import Location, parse_location
from orkesta.engine.normalize.names import PersonName, normalize_person

__all__ = [
    "CompanyName",
    "Location",
    "PersonName",
    "normalize_company",
    "normalize_person",
    "parse_location",
]
