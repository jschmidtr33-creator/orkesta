"""Medición en vivo del descubrimiento de personas en sitios corporativos.

Uso: ``python -m orkesta.evaluation.people conjunto.csv [...] --out dir --limit 300``

Usa los conjuntos de dominios de la evaluación (columna ``dominio_esperado``). El informe tiene
solo conteos agregados: cuántas empresas publican personas, cuántas por empresa, por qué método,
con qué nivel y área de cargo y cuántos sitios bloquean. Nunca nombres ni cargos individuales.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from statistics import mean, median

from orkesta.catalog.titles import classify_title
from orkesta.engine.people.discover import discover_people
from orkesta.enrichment.service import default_engine
from orkesta.evaluation.domains import load_cases


def measure(domains: list[str], workers: int) -> dict[str, object]:
    engine = default_engine()

    def one(domain: str) -> tuple[int, int, bool, Counter[str], Counter[str], Counter[str]]:
        try:
            result = discover_people(domain, engine.fetcher)
        except Exception:  # un sitio problemático no detiene la medición
            return 0, 0, False, Counter(), Counter(), Counter()
        methods = Counter(p.method for p in result.people)
        levels: Counter[str] = Counter()
        areas: Counter[str] = Counter()
        for person in result.people:
            level, area = classify_title(person.title)
            levels[level or "sin nivel"] += 1
            areas[area or "sin área"] += 1
        return (
            len(result.people), len(result.pages_visited), result.blocked, methods, levels, areas,
        )  # fmt: skip

    with ThreadPoolExecutor(max_workers=workers) as pool:
        results = list(pool.map(one, domains))
    counts = [r[0] for r in results]
    with_people = [c for c in counts if c]
    methods: Counter[str] = Counter()
    levels: Counter[str] = Counter()
    areas: Counter[str] = Counter()
    for r in results:
        methods.update(r[3])
        levels.update(r[4])
        areas.update(r[5])
    return {
        "empresas": len(domains),
        "con_personas": len(with_people),
        "con_personas_pct": round(100 * len(with_people) / max(len(domains), 1), 1),
        "personas_total": sum(counts),
        "personas_por_empresa_con_personas_media": round(mean(with_people), 1)
        if with_people
        else 0,
        "personas_por_empresa_con_personas_mediana": median(with_people) if with_people else 0,
        "con_5_o_mas_pct": round(100 * sum(1 for c in counts if c >= 5) / max(len(domains), 1), 1),
        "sitios_sin_pagina": sum(1 for r in results if r[1] == 0),
        "sitios_con_robots_bloqueando": sum(1 for r in results if r[2]),
        "metodos": dict(methods.most_common()),
        "niveles": dict(levels.most_common()),
        "areas": dict(areas.most_common()),
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="orkesta.evaluation.people")
    parser.add_argument("datasets", type=Path, nargs="+")
    parser.add_argument("--out", type=Path, default=Path("personas"))
    parser.add_argument("--limit", type=int, default=400)
    parser.add_argument("--workers", type=int, default=8)
    args = parser.parse_args(argv)

    domains: list[str] = []
    for dataset in args.datasets:
        for case in load_cases(dataset):
            if case.expected and case.expected[0] not in domains:
                domains.append(case.expected[0])
    domains = domains[: args.limit]
    metrics = measure(domains, args.workers)
    args.out.mkdir(parents=True, exist_ok=True)
    (args.out / "metricas.json").write_text(json.dumps(metrics, indent=2, ensure_ascii=False))
    lines = [
        "# Descubrimiento de personas en sitios corporativos",
        "",
        f"- Empresas exploradas: {metrics['empresas']}",
        f"- **Con al menos una persona publicada: {metrics['con_personas']} "
        f"({metrics['con_personas_pct']} %)**",
        f"- Personas encontradas: {metrics['personas_total']} (media "
        f"{metrics['personas_por_empresa_con_personas_media']}, mediana "
        f"{metrics['personas_por_empresa_con_personas_mediana']} por empresa con personas)",
        f"- Empresas con 5 o más personas: {metrics['con_5_o_mas_pct']} %",
        f"- Sitios sin páginas legibles: {metrics['sitios_sin_pagina']}; con robots.txt "
        f"bloqueando: {metrics['sitios_con_robots_bloqueando']}",
        f"- Métodos: {metrics['metodos']}",
        f"- Niveles: {metrics['niveles']}",
        f"- Áreas: {metrics['areas']}",
    ]
    (args.out / "reporte.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(json.dumps(metrics, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
