"""Entrenamiento y promoción del modelo de dominio a partir de las variables de la evaluación.

Uso: ``python -m orkesta.evaluation.train_domain features1.csv.gz [features2.csv.gz ...]``

- Partición por empresa (``case_id``): una empresa nunca está a la vez en entrenamiento y
  prueba, así que la medición refleja empresas que el modelo no ha visto.
- Umbrales elegidos en validación (dentro de entrenamiento), nunca en prueba.
- Promoción solo si, en prueba, el modelo iguala o supera la exactitud de las reglas y gana en
  cobertura, o supera claramente la exactitud con una cobertura similar.

Requiere LightGBM (solo para entrenar; producción usa ``engine.domain.model``).
"""

from __future__ import annotations

import argparse
import csv
import gzip
import hashlib
import json
import sys
from collections import defaultdict
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from orkesta.engine.domain.features import FEATURE_NAMES
from orkesta.engine.domain.model import ACTIVE_MODEL, DomainModel
from orkesta.engine.domain.scoring import PROBABLE

TEST_SHARE = 0.3
VALIDATION_SHARE = 0.25
MIN_ACCURACY_FOR_CONFIRMED = 0.95
MIN_TEST_COMPANIES = 30  # con menos, la comparación no es fiable y no se promueve


@dataclass(frozen=True)
class Row:
    case_id: str
    domain: str
    label: int
    rules_score: float
    features: dict[str, float]


def load_rows(paths: list[Path]) -> list[Row]:
    rows: list[Row] = []
    for path in paths:
        with gzip.open(path, "rt", encoding="utf-8") as handle:
            for record in csv.DictReader(handle):
                rows.append(
                    Row(
                        case_id=record["case_id"],
                        domain=record["domain"],
                        label=int(record["label"]),
                        rules_score=float(record["rules_score"]),
                        # Variables nuevas ausentes en informes antiguos: desconocidas (NaN).
                        features={name: float(record.get(name) or "nan") for name in FEATURE_NAMES},
                    )
                )
    return rows


def bucket(case_id: str, salt: str) -> float:
    digest = hashlib.sha256(f"{salt}:{case_id}".encode()).hexdigest()
    return int(digest[:8], 16) / 0xFFFFFFFF


def split(rows: list[Row], share: float, salt: str) -> tuple[list[Row], list[Row]]:
    first = [r for r in rows if bucket(r.case_id, salt) >= share]
    second = [r for r in rows if bucket(r.case_id, salt) < share]
    return first, second


def by_case(rows: list[Row]) -> dict[str, list[Row]]:
    grouped: dict[str, list[Row]] = defaultdict(list)
    for row in rows:
        grouped[row.case_id].append(row)
    return grouped


def company_metrics(
    rows: list[Row], scores: dict[tuple[str, str], float], threshold: float
) -> dict[str, float]:
    """Exactitud y cobertura por empresa eligiendo el candidato de mayor puntuación."""
    answered = correct = 0
    cases = by_case(rows)
    for case_rows in cases.values():
        best = max(case_rows, key=lambda r: (scores[(r.case_id, r.domain)], r.domain))
        if scores[(best.case_id, best.domain)] >= threshold:
            answered += 1
            correct += best.label
    total = len(cases)
    return {
        "empresas": total,
        "con_dominio": answered,
        "aciertos": correct,
        "exactitud": round(correct / answered, 4) if answered else 0.0,
        "cobertura": round(answered / total, 4) if total else 0.0,
    }


def _train_booster(rows: list[Row], seed: int = 7) -> Any:
    import lightgbm as lgb
    import numpy as np

    matrix = np.array([[r.features[n] for n in FEATURE_NAMES] for r in rows], dtype=float)
    labels = np.array([r.label for r in rows])
    positives = max(int(labels.sum()), 1)
    params = {
        "objective": "binary",
        "learning_rate": 0.05,
        "num_leaves": 15,
        "min_data_in_leaf": 20,
        "feature_fraction": 0.9,
        "bagging_fraction": 0.9,
        "bagging_freq": 1,
        "lambda_l2": 1.0,
        "scale_pos_weight": min(10.0, (len(labels) - positives) / positives),
        "seed": seed,
        "deterministic": True,
        "verbosity": -1,
    }
    data = lgb.Dataset(matrix, label=labels, feature_name=list(FEATURE_NAMES))
    return lgb.train(params, data, num_boost_round=250)


def _scores(model: DomainModel, rows: list[Row]) -> dict[tuple[str, str], float]:
    return {(r.case_id, r.domain): model.predict(r.features) for r in rows}


def _rules_scores(rows: list[Row]) -> dict[tuple[str, str], float]:
    return {(r.case_id, r.domain): r.rules_score for r in rows}


def choose_thresholds(rows: list[Row], model: DomainModel) -> tuple[float, float]:
    """Probable: máxima exactitud × cobertura. Confirmado: el menor umbral con ≥ 95 %."""
    scores = _scores(model, rows)
    grid = [i / 100 for i in range(5, 100)]
    probable = max(
        grid,
        key=lambda t: (
            (m := company_metrics(rows, scores, t))["exactitud"] * m["cobertura"],
            t,
        ),
    )
    confirmed = next(
        (
            t
            for t in grid
            if t >= probable
            and company_metrics(rows, scores, t)["exactitud"] >= MIN_ACCURACY_FOR_CONFIRMED
        ),
        0.99,
    )
    return confirmed, probable


def should_promote(model_m: dict[str, float], rules_m: dict[str, float]) -> bool:
    accuracy_ok = model_m["exactitud"] >= rules_m["exactitud"] - 0.005
    coverage_gain = model_m["cobertura"] > rules_m["cobertura"] + 0.01
    clear_accuracy_gain = (
        model_m["exactitud"] > rules_m["exactitud"] + 0.02
        and model_m["cobertura"] >= rules_m["cobertura"] - 0.02
    )
    return (accuracy_ok and coverage_gain) or clear_accuracy_gain


def train(rows: list[Row], version: str) -> tuple[dict[str, Any], dict[str, Any]]:
    train_rows, test_rows = split(rows, TEST_SHARE, "test")
    if len(by_case(test_rows)) < MIN_TEST_COMPANIES or len(by_case(train_rows)) < 60:
        report = {
            "version": version,
            "filas": len(rows),
            "empresas_entrenamiento": len(by_case(train_rows)),
            "empresas_prueba": len(by_case(test_rows)),
            "promover": False,
            "motivo": "muy pocas empresas para entrenar y comparar con fiabilidad",
        }
        return {}, report
    fit_rows, validation_rows = split(train_rows, VALIDATION_SHARE, "validation")

    booster = _train_booster(fit_rows)
    provisional = DomainModel.from_dict(
        {
            "version": version,
            "booster": booster.dump_model(),
            "threshold_confirmed": 0.9,
            "threshold_probable": 0.5,
        }
    )
    confirmed, probable = choose_thresholds(validation_rows, provisional)

    # Modelo final: todo el entrenamiento (sin tocar prueba) con los umbrales de validación.
    final_booster = _train_booster(train_rows)
    data: dict[str, Any] = {
        "version": version,
        "trained_at": datetime.now(UTC).isoformat(),
        "booster": final_booster.dump_model(),
        "threshold_confirmed": confirmed,
        "threshold_probable": probable,
    }
    model = DomainModel.from_dict(data)
    model_metrics = company_metrics(test_rows, _scores(model, test_rows), probable)
    rules_metrics = company_metrics(test_rows, _rules_scores(test_rows), PROBABLE)
    importance = dict(
        zip(FEATURE_NAMES, final_booster.feature_importance("gain").round(1).tolist(), strict=True)
    )
    report = {
        "version": version,
        "filas": len(rows),
        "empresas_entrenamiento": len(by_case(train_rows)),
        "empresas_prueba": len(by_case(test_rows)),
        "umbral_confirmado": confirmed,
        "umbral_probable": probable,
        "modelo_prueba": model_metrics,
        "reglas_prueba": rules_metrics,
        "promover": should_promote(model_metrics, rules_metrics),
        "importancia": dict(sorted(importance.items(), key=lambda kv: -kv[1])),
    }
    data["metrics"] = report
    return data, report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="orkesta.evaluation.train_domain")
    parser.add_argument("features", type=Path, nargs="+")
    parser.add_argument("--version", default=f"lgbm-{datetime.now(UTC):%Y%m%d}")
    parser.add_argument("--out", type=Path, default=ACTIVE_MODEL)
    parser.add_argument("--report", type=Path, default=Path("informe_modelo.json"))
    parser.add_argument("--force", action="store_true", help="Guardar aunque no supere reglas")
    args = parser.parse_args(argv)

    rows = load_rows(args.features)
    data, report = train(rows, args.version)
    args.report.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
    print(
        json.dumps(
            {k: v for k, v in report.items() if k != "importancia"}, indent=2, ensure_ascii=False
        )
    )
    if data and (report["promover"] or args.force):
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(json.dumps(data), encoding="utf-8")
        print(f"Modelo guardado en {args.out}")
    else:
        print("El modelo no supera a las reglas: no se promueve.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
