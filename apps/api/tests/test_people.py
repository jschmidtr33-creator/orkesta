"""Índice propio de perfiles públicos (con salida permanente) y puntaje de cada posible
cliente. Personas ficticias."""

from __future__ import annotations

from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session, sessionmaker

from orkesta.research import memory, people
from orkesta.research.agent import Candidate, Filters, Prospect
from orkesta.research.models import CompanyDirectory


def _prospect(name: str, title: str, email: str = "", status: str = "", li: str = "") -> Prospect:
    company = Candidate(key="banco andino", name="Banco Andino", domain="bancoandino.co",
                        industry_code="banca_finanzas", employees=800)  # fmt: skip
    first, last = name.split(" ", 1)
    p = Prospect(key=f"banco andino|{name.lower()}", company=company, full_name=name,
                 first_name=first, last_name=last, title=title, linkedin=li,
                 email=email, email_status=status, location="Bogotá, Colombia")  # fmt: skip
    p.sources = [{"type": "perfil", "url": li}]
    return p


def test_index_search_feedback_and_optout(session_factory: sessionmaker[Session]) -> None:
    with session_factory.begin() as session:
        session.execute(pg_insert(CompanyDirectory).values(
            key="CO|860000001", country="CO", name="BANCO ANDINO S.A.", name_key="banco andino",
            industry_code="banca_finanzas", employees=800, search_text="banco andino",
        ))  # fmt: skip
    found = [
        _prospect(
            "Laura Gómez",
            "Vicepresidente de Tecnología",
            "lgomez@bancoandino.co",
            "publicado",
            "https://co.linkedin.com/in/laura-gomez-x1",
        ),
        _prospect("Pedro Ruiz", "Gerente de Riesgo", "pruiz@bancoandino.co", "estimado"),
        _prospect("Sofía Mena", "Analista de datos"),
    ]
    assert people.learn(session_factory, "CO", found) == 3
    # Una búsqueda posterior que lo encuentra con correo estimado no borra el publicado.
    again = _prospect("Laura Gómez", "VP de Tecnología", "laura.gomez@bancoandino.co", "estimado",
                      "https://www.linkedin.com/in/laura-gomez-x1/")  # fmt: skip
    people.learn(session_factory, "CO", [again])

    filters = Filters.from_payload({"country": "CO", "sectors": [], "industries": ["banca_finanzas"],
                                    "seniority": ["vp", "director", "cxo", "gestion_experiencia"]})  # fmt: skip
    with session_factory() as session:
        hits = people.search(session, filters)
        names = {h["person"].full_name: h["person"] for h in hits}
        assert set(names) == {"Laura Gómez", "Pedro Ruiz"}  # el analista no es decisor
        assert names["Laura Gómez"].email == "lgomez@bancoandino.co"
        assert names["Laura Gómez"].seen == 2
        assert people.stats(session) == {"people": 3, "with_email": 2, "verified": 1}

    # Las campañas enseñan: una respuesta confirma el correo.
    with session_factory.begin() as session:
        memory.feedback(session, "pruiz@bancoandino.co", "flast", "replied")
    with session_factory() as session:
        assert people.stats(session)["verified"] == 2

    # Derecho a no figurar: se quita y no vuelve a guardarse.
    with session_factory.begin() as session:
        assert people.remove(session, "https://www.linkedin.com/in/laura-gomez-x1") == 1
    people.learn(session_factory, "CO", [found[0]])
    with session_factory() as session:
        assert "Laura Gómez" not in {h["person"].full_name for h in people.search(session, filters)}


def test_fit_score() -> None:
    filters = Filters.from_payload({"country": "CO", "industries": ["banca_finanzas"],
                                    "functions": ["tecnologia"], "sizes": ["501-1000"]})  # fmt: skip
    top = _prospect("Laura Gómez", "Vicepresidente de Tecnología", "lgomez@bancoandino.co",
                    "publicado", "https://co.linkedin.com/in/laura")  # fmt: skip
    score, reasons = people.fit_score(top, filters)
    assert (
        score >= 85 and "Correo verificado" in reasons and "Empresa del sector buscado" in reasons
    )
    low, _ = people.fit_score(_prospect("Sofía Mena", "Analista de datos"), None)
    assert low < 50
