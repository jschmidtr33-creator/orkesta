"""Formato de correo de empresas nuevas: el modelo supera a «el más común del país» cuando el
sector decide el formato, y desde entonces ordena los formatos a probar."""

from __future__ import annotations

from sqlalchemy.orm import Session, sessionmaker

from orkesta.brain import capabilities, registry
from orkesta.research import memory
from orkesta.research.models import CompanyDirectory, PatternStat


def _seed(session: Session) -> None:
    rows = [("tecnologia", "first.last", i) for i in range(36)] + [
        ("salud", "flast", i) for i in range(26)]  # fmt: skip
    for sector, pattern, i in rows:
        domain = f"{sector[:4]}{i}empresa.com.co"
        session.add(PatternStat(domain=domain, pattern=pattern, seen=3, confirmed=1, bounced=0))
        session.add(CompanyDirectory(
            key=f"CO|n:{domain}", country="CO", name=domain, name_key=domain,
            industry_code=sector, employees=40 + i, domain=domain))  # fmt: skip


def test_email_format_model_beats_country_rule_and_ranks(
    session_factory: sessionmaker[Session],
) -> None:
    with session_factory.begin() as session:
        _seed(session)
        session.add(CompanyDirectory(
            key="CO|n:clinicanueva", country="CO", name="Clínica Nueva", name_key="clinica nueva",
            industry_code="salud", employees=80, domain="clinicanueva.com.co"))  # fmt: skip

    # Sin modelo: manda la regla del país (la mayoría usa nombre.apellido).
    memory._MODEL_CACHE.clear()
    memory._ORDER_CACHE.clear()
    assert memory.likely_pattern(session_factory, "clinicanueva.com.co")[0] == "first.last"

    with session_factory.begin() as session:
        row = registry.train(session, "formato_correo")
        assert row.active, row.metrics
        assert capabilities and row.baseline is not None
        assert row.metrics["accuracy"] > row.baseline["accuracy"]

    # Con modelo: una clínica que nunca vio arma los correos como las demás del sector salud.
    memory._MODEL_CACHE.clear()
    memory._ORDER_CACHE.clear()
    pattern, pct = memory.likely_pattern(session_factory, "clinicanueva.com.co")
    assert pattern == "flast" and pct > 50
    tried, _ = memory.candidates(session_factory, "clinicanueva.com.co", "")
    assert tried[0] == "flast"
