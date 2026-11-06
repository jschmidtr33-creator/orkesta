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


def test_sector_engine_and_index_search_uses_inferred_sectors(
    session_factory: sessionmaker[Session],
) -> None:
    from orkesta.engine.taxonomy.titles import classify_title
    from orkesta.research import sectors
    from orkesta.research.agent import company_key
    from orkesta.research.knowledge import Knowledge, apply

    # El motor propio deduce el sector aunque la base no lo traiga.
    assert sectors.infer("colbun spa") == ("energia", "curado")
    assert sectors.infer("Grupo Bimbo S.A.B. de C.V.") == ("alimentos_bebidas", "curado")
    assert sectors.infer("Forestal Río Claro")[0] != "telecomunicaciones"  # «Claro» suelto no
    assert sectors.infer("Consorcio")[0] == "seguros"
    assert sectors.infer("Consorcio Constructor Andino")[0] == "construccion_inmobiliario"
    assert sectors.infer("Agencia X", "agenciax.gob.co") == ("gobierno", "dominio")
    assert sectors.infer("Banco Andino S.A.") == ("banca_finanzas", "nombre")
    assert sectors.infer("Zorya")[0] is None
    assert sectors.from_description("Somos la aseguradora líder en seguros de vida") == "seguros"

    # Una base subida desde el panel (sin sector) queda disponible para búsquedas por sector.
    k = Knowledge()
    titles = ["Gerente de Tecnología", "Directora Comercial", "Jefe de Operaciones", "Analista"]
    for i, (company, title) in enumerate(
        [(c, t) for c in ("Bancolombia", "Colbún S.A.", "Zorya") for t in titles]
    ):
        key = f"n:CO|{company.lower()}|persona {i}"
        k.profiles[key] = {
            "key": key, "country": "CO", "full_name": f"Persona {i}", "first_name": "Persona",
            "last_name": str(i), "title": title, "level": classify_title(title)[0],
            "area": classify_title(title)[1], "company": company,
            "company_key": company_key(company),
            "domain": None, "location": "Bogotá", "linkedin": None,
            "email": f"p{i}@x.co" if i % 2 else None, "email_status": "publicado" if i % 2 else None,
            "phone": None, "source": "bases",
        }  # fmt: skip
        k.companies[("CO", company_key(company))] = {"name": company, "country": "CO",
                                                     "city": "Bogotá", "domain": ""}  # fmt: skip
    stats = apply(session_factory, k)
    assert stats["sectors"] >= 2
    banks = Filters.from_payload({"country": "CO", "industries": ["banca_finanzas"]})
    energy = Filters.from_payload({"country": "CO", "industries": ["energia"],
                                   "areas": ["tecnologia"]})  # fmt: skip
    everyone = Filters.from_payload({"country": "CO"})
    with session_factory() as session:
        hits = people.search(session, banks)
        # Solo decisores de Bancolombia; el analista queda fuera y los con correo van primero.
        assert {h["person"].company for h in hits} == {"Bancolombia"} and len(hits) == 3
        assert hits[0]["person"].email is not None and hits[0]["industry"] == "banca_finanzas"
        tech = people.search(session, energy)
        assert [h["person"].title for h in tech] == ["Gerente de Tecnología"]
        assert len(people.search(session, everyone)) == 9  # incluye la empresa sin sector
