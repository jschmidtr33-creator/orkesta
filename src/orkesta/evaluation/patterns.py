"""Evaluación autónoma de patrones de correo y aprendizaje de previos, sin revisión humana.

Uso: ``python -m orkesta.evaluation.patterns conjunto1.csv [conjunto2.csv ...] --out dir``

Los conjuntos son los mismos de la evaluación de dominios (empresa, ubicación, dominio esperado).
Para cada dominio se recolectan los correos publicados en su sitio y se convierten en
observaciones. La verdad de referencia sale de los propios datos:

- **Dejar uno fuera:** en empresas con 2+ correos personales, se oculta uno confirmado por
  nombre y se comprueba si el motor deduce su patrón con el resto.
- **Previo:** para empresas sin correos públicos decide el previo; se aprende con una mitad de
  las empresas y se mide con la otra (nunca con las mismas).

Privacidad: los informes contienen solo patrones y conteos por dominio corporativo; nunca
direcciones ni nombres de personas.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import sys
import time
from collections import Counter, defaultdict
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, cast

from orkesta.engine.email.decide import DEFAULT_PRIOR, MODEL_VERSION, decide
from orkesta.engine.email.harvest import harvest
from orkesta.engine.email.observe import Observation, is_role_address, observe
from orkesta.engine.email.patterns import PATTERN_LABELS, PATTERNS
from orkesta.engine.net.dns import DnsError
from orkesta.engine.normalize import parse_location
from orkesta.enrichment.service import EngineDeps, default_engine
from orkesta.evaluation.domains import load_cases

MIN_DOMAINS_FOR_PRIOR = 15
PRIOR_SMOOTHING = 3.0  # peso del previo inicial como pseudo-empresas


@dataclass
class DomainResult:
    domain: str
    country: str | None
    mx_provider: str | None = None
    has_mx: bool = False
    emails: int = 0
    role: int = 0
    observations: list[Observation] = field(default_factory=list)
    email_domain: str | None = None
    pattern: str | None = None
    confidence: str = ""
    blocked: bool = False
    pages: int = 0
    error: str | None = None
    seconds: float = 0.0

    @property
    def named(self) -> list[Observation]:
        return [o for o in self.observations if o.kind == "named"]

    @property
    def dominant(self) -> str | None:
        """Patrón mayoritario observado (con peso), o None si no hay observaciones."""
        votes: Counter[str] = Counter()
        for obs in self.observations:
            votes[obs.pattern] += 3 if obs.kind == "named" else 1
        return votes.most_common(1)[0][0] if votes else None


def evaluate_domain(domain: str, country: str | None, engine: EngineDeps) -> DomainResult:
    started = time.monotonic()
    result = DomainResult(domain, country)
    try:
        mx = engine.resolver.mx(domain)
        result.has_mx, result.mx_provider = mx.has_mx, mx.provider
    except DnsError as exc:
        result.error = f"mx: {exc}"
    found = harvest(domain, engine.fetcher)
    result.blocked, result.pages = found.blocked, len(found.pages_visited)
    result.emails = len(found.emails)
    result.observations = observe(found.emails)
    result.role = sum(1 for e in found.emails if is_role_address(e.local))
    decision = decide(
        domain,
        result.observations,
        country=country,
        mx_provider=result.mx_provider,
        has_mx=result.has_mx,
        harvest=found,
    )
    result.email_domain, result.pattern = decision.email_domain, decision.pattern
    result.confidence = decision.confidence_level
    result.seconds = time.monotonic() - started
    return result


def _half(domain: str) -> str:
    return "train" if int(hashlib.sha256(domain.encode()).hexdigest()[:8], 16) % 2 else "test"


def learn_priors(results: list[DomainResult]) -> dict[str, dict[str, float]]:
    """Distribución del patrón dominante por país, país|proveedor y global, suavizada."""
    groups: dict[str, Counter[str]] = defaultdict(Counter)
    for r in results:
        if r.dominant is None:
            continue
        groups["*"][r.dominant] += 1
        if r.country:
            groups[r.country][r.dominant] += 1
            if r.mx_provider:
                groups[f"{r.country}|{r.mx_provider}"][r.dominant] += 1
    priors: dict[str, dict[str, float]] = {}
    for key, counts in groups.items():
        n = sum(counts.values())
        if n < MIN_DOMAINS_FOR_PRIOR:
            continue
        total = n + PRIOR_SMOOTHING
        priors[key] = {
            p: round((counts.get(p, 0) + PRIOR_SMOOTHING * DEFAULT_PRIOR.get(p, 0.0)) / total, 4)
            for p in PATTERNS
        }
    return priors


def _prior_ranking(
    priors: dict[str, dict[str, float]], country: str | None, provider: str | None
) -> list[str]:
    for key in (f"{country}|{provider}", country or "", "*"):
        if key in priors:
            dist = priors[key]
            break
    else:
        dist = DEFAULT_PRIOR
    return [p for p, _ in sorted(dist.items(), key=lambda kv: (-kv[1], kv[0]))]


def leave_one_out(results: list[DomainResult]) -> dict[str, float | int]:
    """Oculta cada correo con nombre y mide si el resto de evidencia deduce su patrón."""
    trials = hits = 0
    for r in results:
        if len(r.observations) < 2 or not r.named:
            continue
        for i, held in enumerate(r.observations):
            if held.kind != "named":
                continue
            rest = r.observations[:i] + r.observations[i + 1 :]
            decision = decide(
                held.email_domain,
                rest,
                country=r.country,
                mx_provider=r.mx_provider,
                has_mx=True,
            )
            trials += 1
            hits += decision.pattern == held.pattern
    return {
        "pruebas": trials,
        "aciertos": hits,
        "exactitud_pct": round(100 * hits / trials, 1) if trials else 0.0,
    }


def prior_accuracy(results: list[DomainResult]) -> dict[str, float | int]:
    """Exactitud del previo (sin evidencia) en empresas no usadas para aprenderlo."""
    train = [r for r in results if _half(r.domain) == "train"]
    test = [r for r in results if _half(r.domain) == "test" and r.dominant]
    priors = learn_priors(train)
    top1 = top3 = default_top1 = 0
    default_rank = _prior_ranking({}, None, None)
    for r in test:
        ranking = _prior_ranking(priors, r.country, r.mx_provider)
        top1 += ranking[0] == r.dominant
        top3 += r.dominant in ranking[:3]
        default_top1 += default_rank[0] == r.dominant
    n = len(test)

    def pct(x: int) -> float:
        return round(100 * x / n, 1) if n else 0.0

    return {
        "empresas_prueba": n,
        "previo_aprendido_top1_pct": pct(top1),
        "previo_aprendido_top3_pct": pct(top3),
        "previo_inicial_top1_pct": pct(default_top1),
    }


def summarize(results: list[DomainResult]) -> dict[str, object]:
    n = len(results)

    def pct(x: int) -> float:
        return round(100 * x / n, 1) if n else 0.0

    with_emails = sum(1 for r in results if r.emails)
    with_personal = sum(1 for r in results if r.observations)
    with_named = sum(1 for r in results if r.named)
    dominant = Counter(r.dominant for r in results if r.dominant)
    return {
        "modelo": MODEL_VERSION,
        "dominios": n,
        "con_mx_pct": pct(sum(r.has_mx for r in results)),
        "con_correos_publicados_pct": pct(with_emails),
        "con_correos_personales_pct": pct(with_personal),
        "con_nombre_confirmado_pct": pct(with_named),
        "dominio_correo_distinto_del_sitio": sum(
            1 for r in results if r.email_domain and r.email_domain != r.domain
        ),
        "confianza": dict(Counter(r.confidence for r in results if r.confidence)),
        "dejar_uno_fuera": leave_one_out(results),
        "previo": prior_accuracy(results),
        "patrones_dominantes": dict(dominant.most_common()),
    }


def write_outputs(results: list[DomainResult], out: Path) -> dict[str, object]:
    out.mkdir(parents=True, exist_ok=True)
    metrics = summarize(results)
    (out / "metricas.json").write_text(
        json.dumps(metrics, indent=2, ensure_ascii=False), encoding="utf-8"
    )
    priors = learn_priors(results)
    (out / "priors.json").write_text(
        json.dumps(
            {"model_version": MODEL_VERSION, "domains": len(results), "priors": priors},
            indent=2,
        ),
        encoding="utf-8",
    )
    with (out / "dominios.csv").open("w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        writer.writerow(
            ["dominio", "pais", "proveedor_mx", "correos", "genericos", "personales",
             "con_nombre", "patron_dominante", "dominio_correo", "patron", "confianza",
             "paginas", "bloqueado", "error"]
        )  # fmt: skip
        for r in results:
            writer.writerow(
                [r.domain, r.country or "", r.mx_provider or "", r.emails, r.role,
                 len(r.observations), len(r.named), r.dominant or "", r.email_domain or "",
                 r.pattern or "", r.confidence, r.pages, int(r.blocked), r.error or ""]
            )  # fmt: skip
    loo = cast(dict[str, Any], metrics["dejar_uno_fuera"])
    prior = cast(dict[str, Any], metrics["previo"])
    lines = [
        f"# Evaluación de patrones de correo ({MODEL_VERSION})",
        "",
        "| Métrica | Valor |",
        "| --- | --- |",
        f"| Dominios evaluados | {metrics['dominios']} |",
        f"| Con registros MX | {metrics['con_mx_pct']} % |",
        f"| Con correos publicados en su sitio | {metrics['con_correos_publicados_pct']} % |",
        f"| Con correos personales | {metrics['con_correos_personales_pct']} % |",
        f"| Con patrón confirmado por nombre | {metrics['con_nombre_confirmado_pct']} % |",
        f"| **Dejar uno fuera** ({loo['pruebas']} pruebas) | **{loo['exactitud_pct']} %** |",
        f"| Previo aprendido, top 1 ({prior['empresas_prueba']} empresas) | "
        f"{prior['previo_aprendido_top1_pct']} % |",
        f"| Previo aprendido, top 3 | {prior['previo_aprendido_top3_pct']} % |",
        f"| Previo inicial, top 1 | {prior['previo_inicial_top1_pct']} % |",
        f"| Dominio de correo distinto del sitio | "
        f"{metrics['dominio_correo_distinto_del_sitio']} |",
        "",
        "## Patrones dominantes observados",
        "",
        "| Patrón | Empresas |",
        "| --- | --- |",
    ]
    patterns = cast(dict[str, int], metrics["patrones_dominantes"])
    for pattern, count in patterns.items():
        lines.append(f"| {PATTERN_LABELS.get(pattern, pattern)} | {count} |")
    (out / "reporte.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    return metrics


def domains_from(paths: list[Path], limit: int = 0) -> list[tuple[str, str | None]]:
    seen: set[str] = set()
    out: list[tuple[str, str | None]] = []
    for path in paths:
        for case in load_cases(path):
            if not case.expected or case.expected[0] in seen:
                continue
            seen.add(case.expected[0])
            out.append((case.expected[0], parse_location(case.location).country_code))
    return out[:limit] if limit else out


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="orkesta.evaluation.patterns")
    parser.add_argument("datasets", type=Path, nargs="+")
    parser.add_argument("--out", type=Path, default=Path("patrones"))
    parser.add_argument("--workers", type=int, default=8)
    parser.add_argument("--limit", type=int, default=0)
    args = parser.parse_args(argv)

    domains = domains_from(args.datasets, args.limit)
    engine = default_engine()
    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        results = list(pool.map(lambda d: evaluate_domain(d[0], d[1], engine), domains))
    metrics = write_outputs(results, args.out)
    print(json.dumps(metrics, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
