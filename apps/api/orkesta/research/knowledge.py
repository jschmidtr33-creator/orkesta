"""Entrenar la IA de ORKESTA con bases de contactos reales (Wiza, registros de eventos,
campañas): de cada archivo se aprende el formato de correo de cada empresa y su dominio oficial.

De cada fila se leen ÚNICAMENTE nombre, cargo, empresa, ubicación, correo y teléfono; cualquier
otra columna se ignora. Y solo se guarda conocimiento de EMPRESAS (dominio, formato de correo y
país): los datos de las personas se usan en memoria y se descartan.
"""

from __future__ import annotations

import json
import re
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from types import SimpleNamespace
from typing import Any

from sqlalchemy import func
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session, sessionmaker

from orkesta.engine.files.contacts import (
    Sheet,
    map_columns,
    read_sheets,
    to_records,
)
from orkesta.engine.normalize.text import fold
from orkesta.research.extract import detect_pattern
from orkesta.research.models import CompanyDomain, PatternStat

# Solo estas columnas se leen de cada base.
ALLOWED_FIELDS = frozenset({"first", "last", "full", "title", "company", "email", "phone",
                            "city", "country", "location"})  # fmt: skip


@dataclass
class Knowledge:
    """Conocimiento agregado (sin datos de personas)."""

    files: int = 0
    people: int = 0
    corporate: int = 0
    # dominio → patrón → [confirmados, vistos, rebotados]
    patterns: dict[str, dict[str, list[int]]] = field(
        default_factory=lambda: defaultdict(lambda: defaultdict(lambda: [0, 0, 0]))
    )
    # (país, clave de empresa) → datos de la empresa
    companies: dict[tuple[str, str], dict[str, Any]] = field(default_factory=dict)
    _domains: dict[tuple[str, str], Counter[str]] = field(
        default_factory=lambda: defaultdict(Counter)
    )

    def to_json(self) -> dict[str, Any]:
        return {
            "kind": "orkesta-knowledge",
            "version": 1,
            "files": self.files,
            "people": self.people,
            "patterns": {d: {p: list(v) for p, v in ps.items()} for d, ps in self.patterns.items()},
            "companies": [
                {**data, "domain": self._best_domain(key) or data.get("domain", "")}
                for key, data in self.companies.items()
            ],
        }

    def _best_domain(self, key: tuple[str, str]) -> str:
        counts = self._domains.get(key)
        return counts.most_common(1)[0][0] if counts else ""


_EMAILISH = re.compile(r"\S*@\S*")


def clean_company(name: str) -> str:
    """Nombre de empresa sin correos ni basura (a veces escriben su correo en «Empresa»)."""
    text = " ".join(_EMAILISH.sub(" ", name or "").replace("/", " ").split()).strip(" -.,;")
    letters = sum(c.isalpha() for c in text)
    return text[:300] if letters >= 2 else ""


def _canonical(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", fold(text).lower()).strip("_")


def _index(headers: list[str], *names: str) -> int | None:
    canon = [_canonical(h) for h in headers]
    for name in names:
        if name in canon:
            return canon.index(name)
    return None


def learn_file(knowledge: Knowledge, content: bytes, filename: str) -> None:
    knowledge.files += 1
    for sheet in read_sheets(content, filename):
        columns = {k: v for k, v in map_columns(sheet).items() if k in ALLOWED_FIELDS}
        if "company" not in columns or not ({"first", "full"} & set(columns)):
            continue
        # Las listas de envío (tienen columna de enviados) pudieron llevar correos deducidos:
        # cuentan como evidencia «vista»; las demás (Wiza, registros) como «confirmada».
        strength = 1 if _index(sheet.headers, "sent", "enviado", "sent_at") is not None else 0
        for row in sheet.rows:
            records = to_records(Sheet(sheet.name, sheet.headers, [row]), columns)
            if not records:
                continue
            r = records[0]
            knowledge.people += 1
            country = r.country_code or ""
            name = clean_company(r.company)
            key = (country, fold(name)[:300])
            if name:
                company = knowledge.companies.setdefault(key, {"name": name, "country": country})
                company.setdefault("city", r.city or "")
            if not r.email or not r.corporate_email or not r.email_domain:
                continue
            knowledge.corporate += 1
            domain = r.email.rsplit("@", 1)[1]
            if name:
                knowledge._domains[key][r.email_domain] += 1
            pattern = detect_pattern(r.first_name, r.last_name, r.email)
            if pattern:
                knowledge.patterns[domain][pattern][strength] += 1


def load_json(knowledge: Knowledge, content: bytes) -> None:
    data = json.loads(content)
    if data.get("kind") != "orkesta-knowledge":
        raise ValueError("Ese archivo no es conocimiento de ORKESTA.")
    knowledge.files += int(data.get("files", 1))
    knowledge.people += int(data.get("people", 0))
    for domain, patterns in data.get("patterns", {}).items():
        for pattern, counts in patterns.items():
            target = knowledge.patterns[str(domain)[:253]][str(pattern)[:40]]
            for i in range(3):
                target[i] += int(counts[i])
    for c in data.get("companies", []):
        name = clean_company(str(c.get("name", "")))
        key = (str(c.get("country", ""))[:2], fold(name)[:300])
        if not name:
            continue
        knowledge.companies[key] = {k: str(c.get(k, "")) for k in
                                    ("country", "city", "domain")} | {"name": name}  # fmt: skip


def apply(factory: sessionmaker[Session], knowledge: Knowledge) -> dict[str, int]:
    """Guarda el conocimiento en la memoria global de ORKESTA (idempotente: volver a cargar
    el mismo archivo no duplica la evidencia)."""
    from orkesta.research import directory, memory
    from orkesta.research.agent import company_key

    data = knowledge.to_json()
    memory._ORDER_CACHE.clear()
    stats = {"domains": 0, "patterns": 0, "companies": 0, "with_domain": 0}
    with factory.begin() as session:
        rows = [
            {"domain": d, "pattern": p, "confirmed": c[0], "seen": c[1], "bounced": c[2]}
            for d, ps in data["patterns"].items()
            for p, c in ps.items()
        ]
        for start in range(0, len(rows), 1000):
            stmt = pg_insert(PatternStat).values(rows[start : start + 1000])
            session.execute(stmt.on_conflict_do_update(
                index_elements=["domain", "pattern"],
                set_={col: func.greatest(getattr(PatternStat, col), stmt.excluded[col])
                      for col in ("confirmed", "seen", "bounced")} | {"updated_at": func.now()},
            ))  # fmt: skip
        stats["domains"], stats["patterns"] = len(data["patterns"]), len(rows)
        domain_rows = {}
        for c in data["companies"]:
            if c["domain"] and c["country"]:
                key = f"{c['country']}|{company_key(c['name'])}"[:320]
                domain_rows[key] = {"key": key, "domain": c["domain"][:253], "source": "bases"}
        if domain_rows:
            stmt = pg_insert(CompanyDomain).values(list(domain_rows.values()))
            session.execute(stmt.on_conflict_do_update(
                index_elements=["key"], set_={"domain": stmt.excluded.domain,
                                              "updated_at": func.now()},
            ))  # fmt: skip
        stats["with_domain"] = len(domain_rows)
    by_country: dict[str, list[Any]] = defaultdict(list)
    for c in data["companies"]:
        if not c["country"]:
            continue
        by_country[c["country"]].append(SimpleNamespace(
            name=c["name"], domain=c["domain"], website=f"https://{c['domain']}" if c["domain"]
            else "", linkedin="", industry_code="", industry="", city=c.get("city", ""),
            employees=None,
        ))  # fmt: skip
    for country, companies in by_country.items():
        stats["companies"] += directory.learn(factory, country, companies)
    return stats


def evaluate(knowledge: Knowledge) -> dict[str, float]:
    """Exactitud del formato de correo dejando a una persona fuera por dominio: con lo que
    ORKESTA sabe del resto de la empresa, ¿acierta el correo de esa persona?"""
    hits = total = fallback_hits = 0
    for patterns in knowledge.patterns.values():
        people = [(p, c[0] + c[1]) for p, c in patterns.items()]
        count = sum(n for _, n in people)
        if count < 2:
            continue
        for pattern, n in people:
            for _ in range(n):
                others = {p: m - (1 if p == pattern else 0) for p, m in people}
                best = max(others, key=lambda p: others[p])
                total += 1
                hits += int(best == pattern and others[best] > 0)
                fallback_hits += int(pattern == "first.last")
    return {
        "evaluated": total,
        "accuracy": round(100 * hits / total, 1) if total else 0.0,
        "baseline": round(100 * fallback_hits / total, 1) if total else 0.0,
    }
