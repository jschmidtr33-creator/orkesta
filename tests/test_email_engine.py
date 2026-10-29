"""Motor de patrones de correo: catálogo, recolección, observación, decisión y generación.

Todos los nombres y correos son inventados.
"""

from __future__ import annotations

import pytest

from fakes import FakeFetcher, page
from orkesta.engine.email.decide import (
    DEFAULT_PRIOR,
    choose_email_domain,
    decide,
    generate_emails,
)
from orkesta.engine.email.harvest import Harvest, deobfuscate, extract_emails, harvest
from orkesta.engine.email.observe import (
    Observation,
    is_role_address,
    observe,
    structural_pattern,
)
from orkesta.engine.email.patterns import PATTERNS, matching_patterns, render
from orkesta.engine.normalize import normalize_person

# --- catálogo ----------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("pattern", "expected"),
    [
        ("first.last", "maria.garcia"),
        ("flast", "mgarcia"),
        ("flastl2", "mgarciap"),
        ("first.last.last2", "maria.garcia.perez"),
        ("last.first", "garcia.maria"),
        ("first", "maria"),
    ],
)
def test_render(pattern: str, expected: str) -> None:
    assert render(pattern, normalize_person("María José", "García Pérez")) == expected


def test_render_without_second_surname() -> None:
    assert render("flastl2", normalize_person("Ana", "Núñez")) is None


def test_matching_handles_compound_names_and_particles() -> None:
    person = normalize_person("María José", "de la Torre")
    assert "first.last" in matching_patterns("mariajose.torre", person)
    assert "first.last" in matching_patterns("maria.delatorre", person)
    assert matching_patterns("pedro.torre", person) == []


def test_every_pattern_roundtrips() -> None:
    person = normalize_person("Juan", "Pérez Gómez")
    for pattern in PATTERNS:
        local = render(pattern, person)
        assert local is not None
        assert pattern in matching_patterns(local, person)


# --- recolección -------------------------------------------------------------------------


def test_deobfuscate() -> None:
    assert deobfuscate("juan [arroba] acme [punto] mx") == "juan@acme.mx"
    assert deobfuscate("juan(at)acme.mx") == "juan@acme.mx"


def test_extract_emails_keeps_only_company_addresses() -> None:
    html = """
    <p>Escríbenos a <a href="mailto:ventas@acme.com.mx?subject=hola">aquí</a></p>
    <p>Lic. Laura Méndez — lmendez@acme.com.mx</p>
    <p>Agencia: contacto@agencia.com · imagen logo@2x.png</p>
    <a href="/aviso-de-privacidad">Aviso de privacidad</a>
    <a href="https://otra.com/contacto">otra</a>
    """
    emails, links = extract_emails(html, "https://acme.com.mx/", "acme.com.mx")
    assert {e.address for e in emails} == {"ventas@acme.com.mx", "lmendez@acme.com.mx"}
    assert links == ["https://acme.com.mx/aviso-de-privacidad"]
    named = next(e for e in emails if e.local == "lmendez")
    assert "Laura Méndez" in named.context


def test_extract_accepts_same_brand_other_extension() -> None:
    emails, _ = extract_emails("<p>juan.perez@acme.com</p>", "https://acme.com.mx/", "acme.com.mx")
    assert [e.domain for e in emails] == ["acme.com"]


def test_harvest_follows_contact_pages_and_respects_robots() -> None:
    home = '<a href="/contacto">Contacto</a><a href="/equipo">Equipo</a><p>info@acme.mx</p>'
    fetcher = FakeFetcher(
        pages={
            "https://acme.mx/": page("https://acme.mx/", home),
            "https://acme.mx/contacto": page(
                "https://acme.mx/contacto", "<p>Pedro Ramírez pramirez@acme.mx</p>"
            ),
            "https://acme.mx/equipo": page("https://acme.mx/equipo", "<p>x@acme.mx</p>"),
        },
        disallowed={"https://acme.mx/equipo"},
    )
    result = harvest("acme.mx", fetcher)
    assert {e.address for e in result.emails} == {"info@acme.mx", "pramirez@acme.mx"}
    assert result.blocked is True
    assert "https://www.acme.mx/" not in fetcher.requested


# --- observación -------------------------------------------------------------------------


@pytest.mark.parametrize(
    "local", ["ventas", "rrhh", "atencion.clientes", "contacto-mx", "ventas01", "mx12345"]
)
def test_role_addresses(local: str) -> None:
    assert is_role_address(local)


@pytest.mark.parametrize(
    ("local", "pattern"),
    [
        ("maria.lopez", "first.last"),
        ("carlos_ruiz", "first_last"),
        ("jperez", "flast"),
        ("juanperez", "firstlast"),
        ("andrea", "first"),
        ("lopez.maria", "last.first"),
        ("j.perez", "f.last"),
        ("x1y", None),
    ],
)
def test_structural_pattern(local: str, pattern: str | None) -> None:
    assert structural_pattern(local) == pattern


def test_observe_prefers_named_evidence() -> None:
    emails, _ = extract_emails(
        "<p>Gerente: Pedro Ramírez Soto · pramirezs@acme.mx</p><p>ventas@acme.mx</p>",
        "https://acme.mx/",
        "acme.mx",
    )
    assert observe(emails) == [Observation("flastl2", "named", "acme.mx")]


# --- decisión y generación ---------------------------------------------------------------


def _obs(pattern: str, kind: str = "named", domain: str = "acme.mx") -> Observation:
    return Observation(pattern, kind, domain)


def test_decide_with_named_evidence_is_high_confidence() -> None:
    decision = decide(
        "acme.mx",
        [_obs("flast"), _obs("flast"), _obs("first.last", "structural")],
        country="MX",
        mx_provider="microsoft_365",
        has_mx=True,
    )
    assert decision.pattern == "flast"
    assert decision.confidence_level == "high"
    assert decision.exceptions == 1
    assert decision.origin == "observed"


def test_decide_without_evidence_uses_prior_with_low_confidence() -> None:
    decision = decide("acme.mx", [], country="MX", mx_provider=None, has_mx=True)
    assert decision.pattern == max(DEFAULT_PRIOR, key=lambda p: DEFAULT_PRIOR[p])
    assert decision.confidence_level == "low"
    assert decision.origin == "learned"


def test_no_mx_and_no_published_emails_means_no_email_domain() -> None:
    decision = decide("acme.mx", [], country="MX", mx_provider=None, has_mx=False)
    assert decision.email_domain is None
    assert generate_emails(normalize_person("Ana", "Ruiz"), decision) == []


def test_email_domain_follows_published_addresses() -> None:
    emails, _ = extract_emails(
        "<p>ana.ruiz@acme.com</p><p>luis.diaz@acme.com</p>", "https://acme.com.mx/", "acme.com.mx"
    )
    published = Harvest("acme.com.mx", emails)
    assert choose_email_domain("acme.com.mx", published, has_mx=True) == "acme.com"
    assert choose_email_domain("acme.com.mx", None, has_mx=True) == "acme.com.mx"


def test_generate_emails_ranks_and_flags_published() -> None:
    decision = decide("acme.mx", [_obs("flast"), _obs("flast")], country="MX", mx_provider=None,
                      has_mx=True)  # fmt: skip
    person = normalize_person("Laura", "Méndez Ortiz")
    emails = generate_emails(person, decision)
    assert emails[0].address == "lmendez@acme.mx"
    assert len(emails) == 3
    assert emails[0].confidence_level == "high"
    assert all(e.confidence_score <= emails[0].confidence_score for e in emails)
    found = generate_emails(person, decision, published={"laura.mendez@acme.mx"})
    assert found[0].address == "laura.mendez@acme.mx"
    assert found[0].label == "publicly_found"


def test_names_next_to_punctuation_and_particles_are_recognized() -> None:
    from orkesta.engine.email.harvest import FoundEmail
    from orkesta.engine.email.observe import named_patterns

    def found(local: str, context: str) -> FoundEmail:
        return FoundEmail(local, "empresa.com", context, "https://empresa.com/contacto")

    assert named_patterns(found("mariana.torres", "Ventas Mariana Torres, Gerente:")) == [
        "first.last"
    ]
    assert "flast" in named_patterns(found("jdelacruz", "Ing. José de la Cruz: jdelacruz@"))
    assert named_patterns(found("mtorres", "Contacto general: mtorres@")) == []
