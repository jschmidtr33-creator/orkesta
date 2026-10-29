from __future__ import annotations

import pytest

from orkesta.engine.normalize import normalize_company, normalize_person, parse_location
from orkesta.engine.normalize.text import clean, fold

# --- texto ---------------------------------------------------------------------------------


def test_clean_removes_invisible_characters_and_collapses_spaces() -> None:
    assert clean("\ufeff  Juan\u200b   Pérez \n") == "Juan Pérez"
    assert clean(None) == ""


def test_fold_removes_accents_and_punctuation() -> None:
    assert fold("Muñoz-Ávila, S.A.") == "munoz avila s a"


# --- personas ------------------------------------------------------------------------------


def test_compound_first_name_and_particle_surname() -> None:
    p = normalize_person("MARÍA JOSÉ", "DE LA CRUZ PÉREZ")
    assert p.first_display == "María José"
    assert p.last_display == "De la Cruz Pérez"
    assert p.first_primary == "maria"
    assert p.first_compact == "mariajose"
    assert p.last_primary == "delacruz"
    assert p.last_primary_core == "cruz"
    assert p.last_second == "perez"
    assert p.first_normalized == "maria jose"
    assert p.last_normalized == "de la cruz perez"


def test_two_surnames_first_is_primary() -> None:
    p = normalize_person("Luis", "Pérez Ávila")
    assert (p.last_primary, p.last_second) == ("perez", "avila")


def test_single_surname_has_no_second() -> None:
    p = normalize_person("josé", "muñoz")
    assert p.first_display == "José" and p.last_display == "Muñoz"
    assert p.last_primary == "munoz" and p.last_second is None


def test_hyphenated_surname_stays_together() -> None:
    p = normalize_person("Ana", "Rodríguez-Gómez")
    assert p.last_display == "Rodríguez-Gómez"
    assert p.last_primary == "rodriguezgomez"


def test_honorifics_credentials_nicknames_and_emojis_are_removed() -> None:
    p = normalize_person("Ing. Juan Carlos (JC) 🚀", "Rodríguez, MBA")
    assert p.first_display == "Juan Carlos"
    assert p.last_display == "Rodríguez"
    assert "Ing" in p.removed and "MBA" in p.removed and "(JC)" in p.removed


def test_original_values_are_preserved() -> None:
    p = normalize_person("  Dra. ANA  ", "LÓPEZ")
    assert p.first_original == "Dra. ANA"
    assert p.last_original == "LÓPEZ"


@pytest.mark.parametrize(("first", "last"), [("", "Pérez"), ("Juan", ""), ("123", "Pérez")])
def test_invalid_names(first: str, last: str) -> None:
    assert not normalize_person(first, last).is_valid


# --- empresas ------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("raw", "display", "normalized", "legal_form"),
    [
        ("Bancolombia S.A.", "Bancolombia", "bancolombia", "s a"),
        ("Grupo Bimbo, S.A.B. de C.V.", "Grupo Bimbo", "grupo bimbo", "s a b de c v"),
        (
            "Alpina Productos Alimenticios S.A. BIC",
            "Alpina Productos Alimenticios",
            "alpina productos alimenticios",
            "s a bic",
        ),
        (
            "Empresas Públicas de Medellín E.S.P.",
            "Empresas Públicas de Medellín",
            "empresas publicas de medellin",
            "e s p",
        ),
        ("Constructora XYZ S.A.S. BIC", "Constructora XYZ", "constructora xyz", "s a s bic"),
        ("Inversiones ABC Ltda. y Cía", "Inversiones ABC", "inversiones abc", "ltda y cia"),
        ("Rappi Inc.", "Rappi", "rappi", "inc"),
        ("Ecopetrol", "Ecopetrol", "ecopetrol", None),
    ],
)
def test_legal_forms_are_removed_only_for_comparison(
    raw: str, display: str, normalized: str, legal_form: str | None
) -> None:
    company = normalize_company(raw)
    assert company.original == raw
    assert company.display == display
    assert company.normalized == normalized
    assert company.legal_form == legal_form


def test_legal_form_alone_is_not_stripped() -> None:
    assert normalize_company("SA").normalized == "sa"


def test_distinctive_tokens_skip_generic_words_and_countries() -> None:
    assert normalize_company("Grupo Bimbo, S.A.B. de C.V.").distinctive_tokens == ("bimbo",)
    assert normalize_company("Servicios Integrales SAS").distinctive_tokens == ("integrales",)
    assert normalize_company("AT&T México").distinctive_tokens == ("att",)


@pytest.mark.parametrize("raw", ["Freelance", "Self-employed", "N/A", "Confidencial", "", "-"])
def test_values_that_are_not_companies(raw: str) -> None:
    assert not normalize_company(raw).is_valid


# --- ubicaciones ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("raw", "country", "city", "confidence"),
    [
        ("Bogotá, Distrito Capital, Colombia", "CO", "Bogotá", "high"),
        ("Lima, Perú", "PE", "Lima", "high"),
        ("Monterrey, Nuevo León, México", "MX", "Monterrey", "high"),
        ("San José, Costa Rica", "CR", "San José", "high"),
        ("Córdoba, España", "ES", "Córdoba", "high"),
        ("Santiago, República Dominicana", "DO", "Santiago", "high"),
        ("Miami, Florida, United States", "US", "Miami", "high"),
        ("CO", "CO", None, "high"),
        ("Greater Mexico City Area", "MX", "Ciudad de México", "medium"),
        ("Mexico City Metropolitan Area", "MX", "Ciudad de México", "medium"),
        ("Área metropolitana de Medellín", "CO", "Medellín", "medium"),
        ("Ciudad de Panamá", "PA", "Ciudad de Panamá", "medium"),
        ("Jalisco", "MX", None, "medium"),
        ("Santiago", "CL", "Santiago", "low"),
    ],
)
def test_parse_location(raw: str, country: str, city: str | None, confidence: str) -> None:
    location = parse_location(raw)
    assert location.original == raw
    assert location.country_code == country
    assert location.city == city
    assert location.confidence == confidence


@pytest.mark.parametrize("raw", ["Remote", "Latinoamérica", "", None])
def test_unrecognized_location(raw: str | None) -> None:
    location = parse_location(raw)
    assert not location.recognized
    assert location.confidence is None
