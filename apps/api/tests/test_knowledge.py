"""Entrenar la IA con bases reales: solo nombre, cargo, empresa, ubicación, correo y teléfono;
se guarda únicamente lo que se aprende de cada empresa (dominio y formato de correo).
Datos sintéticos: ninguna persona real en las pruebas."""

from __future__ import annotations

import json

from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from orkesta.research import knowledge as kn
from orkesta.research import memory
from orkesta.research.models import CompanyDomain, PatternStat
from tests.conftest import register
from tests.test_admin import _role, _unlock

FIRST = ["Ana", "Luis", "Marta", "Pedro", "Sofia", "Diego", "Laura", "Tomas", "Elena", "Raul"]
LAST = ["Rojas", "Pinto", "Vera", "Soto", "Lagos", "Mena", "Fuentes", "Araya", "Reyes", "Silva"]


def _csv() -> bytes:
    lines = ["Nombre,Apellido,Cargo,Empresa,Ciudad,País,Teléfono,E-mail,Industria,email_status"]
    for i in range(40):
        f, last = FIRST[i % 10], LAST[(i // 10 + i) % 10]
        company = ["Minera Andes", "Retail Sur", "Banco Austral", "Viña Alta"][i % 4]
        domain = ["mineraandes.cl", "retailsur.cl", "bancoaustral.cl", "vinaalta.cl"][i % 4]
        local = f"{f[0]}{last}" if i % 4 != 3 else f"{f}.{last}"
        lines.append(f"{f},{last},Gerente,{company},Santiago,Chile,+56912345{i:03d},"
                     f"{local.lower()}@{domain},Minería,valid")  # fmt: skip
    lines.append("Juan,Perez,CEO,Lácteos Sur,Santiago,Chile,,juan.perez@gmail.com,Alimentos,valid")
    return "\n".join(lines).encode()


def test_learn_apply_and_country_pattern(session_factory: sessionmaker[Session]) -> None:
    learned = kn.Knowledge()
    kn.learn_file(learned, _csv(), "wiza.csv")
    assert learned.people == 41 and learned.corporate == 40
    data = learned.to_json()
    # Solo conocimiento de empresas: ningún nombre, correo, teléfono, cargo ni industria.
    text = json.dumps(data)
    assert "Rojas" not in text and "+569" not in text and "Gerente" not in text
    assert "Minería" not in text and "gmail.com" not in text
    assert {k for c in data["companies"] for k in c} <= {"name", "country", "city", "domain"}
    assert data["patterns"]["mineraandes.cl"] == {"flast": [10, 0, 0]}
    assert kn.evaluate(learned)["accuracy"] == 100.0

    stats = kn.apply(session_factory, learned)
    assert stats["domains"] == 4 and stats["with_domain"] == 4
    with session_factory() as session:
        assert session.scalar(select(PatternStat.confirmed).where(
            PatternStat.domain == "retailsur.cl", PatternStat.pattern == "flast")) == 10  # fmt: skip
        assert session.scalar(select(CompanyDomain.domain).where(
            CompanyDomain.key == "CL|minera andes")) == "mineraandes.cl"  # fmt: skip
    # Volver a cargar el mismo archivo no duplica la evidencia.
    kn.apply(session_factory, learned)
    assert memory.known_pattern(session_factory, "retailsur.cl")[0] == "flast"
    # Una empresa chilena nueva: el formato más usado en Chile según lo aprendido.
    assert memory.likely_pattern(session_factory, "empresanueva.cl") == ("flast", 75)
    chain, _ = memory.candidates(session_factory, "empresanueva.cl", "")
    assert chain[:2] == ["flast", "first.last"]


def test_admin_knowledge_endpoint(client: TestClient, database: tuple[str, str]) -> None:
    register(client, "jenn@orkesta.co", company="ORKESTA")
    _role(database, "jenn@orkesta.co", "owner")
    _unlock(client, "jenn@orkesta.co")
    learned = kn.Knowledge()
    kn.learn_file(learned, _csv(), "wiza.csv")
    payload = json.dumps(learned.to_json()).encode()
    r = client.post("/api/admin/knowledge", files=[
        ("files", ("conocimiento-orkesta.json", payload, "application/json")),
        ("files", ("registros.csv", _csv(), "text/csv")),
        ("files", ("malo.json", b'{"kind": "otro"}', "application/json")),
    ])  # fmt: skip
    body = r.json()
    assert r.status_code == 200, body
    assert body["domains"] == 4 and body["people"] == 82
    assert body["errors"] and "malo.json" in body["errors"][0]
    assert client.get("/api/admin/directory").json()["total"] == 5  # 4 con dominio + 1 sin
