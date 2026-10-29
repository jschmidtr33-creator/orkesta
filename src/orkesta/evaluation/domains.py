"""Evaluación en vivo de la resolución de dominios.

Uso: ``python -m orkesta.evaluation.domains data/eval/empresas_mx.csv --out reporte``

Necesita internet (DNS, sitios web y Wikidata). Se ejecuta desde GitHub Actions con el flujo
manual "Evaluación en vivo". No usa base de datos ni datos de personas.

Definiciones (fijadas antes de medir, ver propuesta Etapa 0, sección 9):
- Acierto: el dominio seleccionado coincide con alguna alternativa de ``dominio_esperado``.
- Exactitud: aciertos / empresas etiquetadas con dominio seleccionado.
- Cobertura: empresas etiquetadas con dominio seleccionado / empresas etiquetadas.
- Top 3: el dominio esperado está entre los 3 mejores candidatos no descartados.
- Línea base: nombre sin forma jurídica ni espacios + ".com".
- Equivalencia por redirección: si el dominio esperado redirige hoy a otro (sitio renovado), el
  destino también cuenta como correcto. Se comprueba visitando el dominio esperado.
- Sin fuga: con ``--no-wikidata`` el motor no consulta Wikidata. Es obligatorio cuando las
  etiquetas vienen de Wikidata.
- ``--features`` vuelca una fila por candidato con sus variables y la etiqueta (0/1): son los
  datos de entrenamiento del modelo de dominio.
"""

from __future__ import annotations

import argparse
import csv
import gzip
import json
import sys
import time
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field, replace
from pathlib import Path

from orkesta.engine.domain.features import FEATURE_NAMES, candidate_features
from orkesta.engine.domain.reasons import DOMAIN_STATUS_LABELS, describe_reasons
from orkesta.engine.domain.resolver import (
    DomainResolution,
    TransientResolutionError,
    resolve_domain,
)
from orkesta.engine.domain.scoring import MODEL_VERSION
from orkesta.engine.net.domains import registrable_domain
from orkesta.engine.normalize import normalize_company, parse_location
from orkesta.enrichment.service import EngineDeps, EngineFactory, default_engine


@dataclass(frozen=True)
class Case:
    company: str
    location: str
    expected: tuple[str, ...]
    source: str


@dataclass(frozen=True)
class Outcome:
    case: Case
    country: str | None
    selected: str | None
    status: str
    score: int
    reasons: str
    top3: tuple[str, ...]
    seconds: float
    error: str | None = None
    accepted: tuple[str, ...] = ()  # esperado + destinos de redirección del esperado
    feature_rows: list[dict[str, object]] = field(default_factory=list)

    @property
    def labeled(self) -> bool:
        return bool(self.case.expected)

    @property
    def correct(self) -> bool:
        return self.selected is not None and self.selected in (self.accepted or self.case.expected)

    @property
    def in_top3(self) -> bool:
        return any(d in (self.accepted or self.case.expected) for d in self.top3)


def load_cases(path: Path) -> list[Case]:
    with path.open(encoding="utf-8") as handle:
        return [
            Case(
                company=row["empresa"],
                location=row.get("ubicacion", ""),
                expected=tuple(
                    d
                    for part in row.get("dominio_esperado", "").split("|")
                    if (d := registrable_domain(part.strip()) or "")
                ),
                source=row.get("fuente", ""),
            )
            for row in csv.DictReader(handle)
        ]


def baseline(case: Case) -> str:
    return normalize_company(case.company).normalized.replace(" ", "") + ".com"


def accepted_domains(case: Case, engine: EngineDeps) -> tuple[str, ...]:
    """Dominios esperados más aquellos a los que redirigen hoy."""
    accepted = list(case.expected)
    for domain in case.expected[:2]:
        result = engine.fetcher.get(f"https://{domain}/")
        final = registrable_domain(result.final_url) if result.ok else None
        if final and final not in accepted:
            accepted.append(final)
    return tuple(accepted)


def evaluate_case(case: Case, engine: EngineDeps) -> Outcome:
    country = parse_location(case.location).country_code
    started = time.monotonic()
    names = [normalize_company(case.company)]
    try:
        result: DomainResolution = resolve_domain(
            names,
            country,
            resolver=engine.resolver,
            fetcher=engine.fetcher,
            wikidata=engine.wikidata,
        )
    except TransientResolutionError as exc:
        return Outcome(
            case, country, None, "error", 0, "", (), time.monotonic() - started, str(exc)
        )
    top3 = tuple(c.domain for c in result.candidates if c.status != "rejected")[:3]
    selected = result.selected
    accepted = accepted_domains(case, engine) if case.expected else ()
    rows: list[dict[str, object]] = []
    if case.expected:
        for scored in result.candidates:
            rows.append(
                {
                    "case_id": case.source or case.company,
                    "country": country or "",
                    "domain": scored.domain,
                    "rules_score": scored.score,
                    "label": int(scored.domain in accepted),
                    **candidate_features(scored.evidence, names, country),
                }
            )
    return Outcome(
        case=case,
        country=country,
        selected=selected.domain if selected else None,
        status=result.status,
        score=selected.score if selected else 0,
        reasons=describe_reasons(selected.reasons) if selected else "",
        top3=top3,
        seconds=time.monotonic() - started,
        accepted=accepted,
        feature_rows=rows,
    )


def summarize(outcomes: list[Outcome]) -> dict[str, float | int]:
    labeled = [o for o in outcomes if o.labeled]
    answered = [o for o in labeled if o.selected]
    correct = [o for o in answered if o.correct]
    base_correct = [o for o in labeled if baseline(o.case) in o.case.expected]
    confirmed = [o for o in answered if o.status == "confirmed"]

    def pct(part: int, whole: int) -> float:
        return round(100 * part / whole, 1) if whole else 0.0

    return {
        "empresas": len(outcomes),
        "etiquetadas": len(labeled),
        "con_dominio": len(answered),
        "aciertos": len(correct),
        "exactitud_pct": pct(len(correct), len(answered)),
        "cobertura_pct": pct(len(answered), len(labeled)),
        "top3_pct": pct(sum(o.in_top3 for o in labeled), len(labeled)),
        "exactitud_confirmados_pct": pct(sum(o.correct for o in confirmed), len(confirmed)),
        "confirmados": len(confirmed),
        "linea_base_exactitud_pct": pct(len(base_correct), len(labeled)),
        "errores_red": sum(1 for o in outcomes if o.error),
    }


def write_report(outcomes: list[Outcome], out: Path) -> dict[str, float | int]:
    out.mkdir(parents=True, exist_ok=True)
    metrics = summarize(outcomes)
    statuses = Counter(o.status for o in outcomes)

    with (out / "resultados.csv").open("w", newline="", encoding="utf-8-sig") as handle:
        writer = csv.writer(handle)
        writer.writerow(
            ["empresa", "pais", "dominio_esperado", "dominio_orkesta", "estado", "puntuacion",
             "acierto", "top3", "razones", "segundos", "error"]
        )  # fmt: skip
        for o in outcomes:
            verdict = ("sí" if o.correct else "no") if o.labeled else "sin etiqueta"
            writer.writerow(
                [o.case.company, o.country or "", "|".join(o.case.expected), o.selected or "",
                 DOMAIN_STATUS_LABELS.get(o.status, o.status), o.score, verdict,
                 "|".join(o.top3), o.reasons, f"{o.seconds:.1f}", o.error or ""]
            )  # fmt: skip

    lines = [
        f"# Evaluación en vivo de dominios ({MODEL_VERSION})",
        "",
        "| Métrica | Valor |",
        "| --- | --- |",
        f"| Empresas evaluadas | {metrics['empresas']} |",
        f"| Con dominio esperado (etiquetadas) | {metrics['etiquetadas']} |",
        f"| **Exactitud** (aciertos / con dominio) | **{metrics['exactitud_pct']} %** |",
        f"| Cobertura (etiquetadas con dominio / etiquetadas) | {metrics['cobertura_pct']} % |",
        f"| Exactitud en confirmados ({metrics['confirmados']}) | "
        f"{metrics['exactitud_confirmados_pct']} % |",
        f"| Esperado entre los 3 primeros candidatos | {metrics['top3_pct']} % |",
        f"| Línea base (nombre + .com) | {metrics['linea_base_exactitud_pct']} % |",
        f"| Errores de red | {metrics['errores_red']} |",
        "",
        "Estados: "
        + ", ".join(f"{DOMAIN_STATUS_LABELS.get(k, k)} {v}" for k, v in statuses.most_common()),
        "",
        "## Fallos en empresas etiquetadas",
        "",
        "| Empresa | Esperado | ORKESTA | Estado | Top 3 |",
        "| --- | --- | --- | --- | --- |",
    ]
    for o in outcomes:
        if o.labeled and not o.correct:
            lines.append(
                f"| {o.case.company} | {' / '.join(o.case.expected)} | {o.selected or '—'} | "
                f"{DOMAIN_STATUS_LABELS.get(o.status, o.status)} | {', '.join(o.top3) or '—'} |"
            )
    lines += [
        "",
        "## Empresas sin etiqueta: dominio propuesto para revisión",
        "",
        "| Empresa | ORKESTA | Estado | Puntuación |",
        "| --- | --- | --- | --- |",
    ]
    for o in outcomes:
        if not o.labeled:
            lines.append(
                f"| {o.case.company} | {o.selected or '—'} | "
                f"{DOMAIN_STATUS_LABELS.get(o.status, o.status)} | {o.score} |"
            )
    (out / "reporte.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    (out / "metricas.json").write_text(json.dumps(metrics, indent=2), encoding="utf-8")
    return metrics


def run(
    cases: list[Case],
    engine_factory: EngineFactory,
    workers: int = 4,
    *,
    use_wikidata: bool = True,
) -> list[Outcome]:
    engine = engine_factory()
    if not use_wikidata:
        engine = replace(engine, wikidata=None)
    with ThreadPoolExecutor(max_workers=workers) as pool:
        return list(pool.map(lambda c: evaluate_case(c, engine), cases))


def write_features(outcomes: list[Outcome], path: Path) -> int:
    """Una fila por candidato: variables, puntuación de reglas y etiqueta. CSV comprimido."""
    columns = ["case_id", "country", "domain", "rules_score", "label", *FEATURE_NAMES]
    count = 0
    path.parent.mkdir(parents=True, exist_ok=True)
    with gzip.open(path, "wt", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=columns)
        writer.writeheader()
        for outcome in outcomes:
            for row in outcome.feature_rows:
                writer.writerow(row)
                count += 1
    return count


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="orkesta.evaluation.domains")
    parser.add_argument("dataset", type=Path)
    parser.add_argument("--out", type=Path, default=Path("reporte"))
    parser.add_argument("--workers", type=int, default=4)
    parser.add_argument("--limit", type=int, default=0, help="Solo las primeras N empresas")
    parser.add_argument("--no-wikidata", action="store_true", help="No consultar Wikidata")
    parser.add_argument("--features", type=Path, help="Volcar variables por candidato (.csv.gz)")
    args = parser.parse_args(argv)

    cases = load_cases(args.dataset)
    if args.limit:
        cases = cases[: args.limit]
    outcomes = run(cases, default_engine, args.workers, use_wikidata=not args.no_wikidata)
    metrics = write_report(outcomes, args.out)
    if args.features:
        print(f"Variables: {write_features(outcomes, args.features)} candidatos → {args.features}")
    print(json.dumps(metrics, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
