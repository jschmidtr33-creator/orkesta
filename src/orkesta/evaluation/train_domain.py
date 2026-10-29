"""Entrenamiento y promoción del modelo de dominio a partir de las variables de la evaluación.

Uso: ``python -m orkesta.evaluation.train_domain features1.csv.gz [features2.csv.gz ...]``

- Partición por empresa (``case_id``): una empresa nunca está a la vez en entrenamiento y
  prueba, así que la medición refleja empresas que el modelo no ha visto.
- Umbrales elegidos con validación cruzada por empresa dentro de entrenamiento, nunca en prueba.
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
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from orkesta.engine.domain.features import FEATURE_NAMES
from orkesta.engine.domain.model import ACTIVE_MODEL, DomainModel
from orkesta.engine.domain.scoring import PROBABLE

TEST_SHARE = 0.3
FOLDS = 5
MIN_ACCURACY_FOR_CONFIRMED = 0.95
TARGET_ACCURACY = 0.75  # meta del producto para resultados "probables"
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


def brand_relabel(rows: list[Row]) -> list[Row]:
    """Etiqueta de marca: correcto si comparte marca con el dominio esperado (control).

    Las etiquetas automáticas registran un solo dominio (alpina.com.co) aunque la empresa use
    varios (alpina.com). Se entrena con la etiqueta estricta para aprender la extensión, y la
    exactitud de marca vigila que el modelo no confunda marcas.
    """
    brands: dict[str, set[str]] = defaultdict(set)
    for row in rows:
        if row.label:
            brands[row.case_id].add(row.domain.split(".", 1)[0])
    return [
        replace(row, label=int(row.label or row.domain.split(".", 1)[0] in brands[row.case_id]))
        for row in rows
    ]


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


def _model_from(booster: Any, version: str) -> DomainModel:
    return DomainModel.from_dict(
        {
            "version": version,
            "booster": booster.dump_model(),
            "threshold_confirmed": 0.9,
            "threshold_probable": 0.5,
        }
    )


def out_of_fold_scores(rows: list[Row], version: str) -> dict[tuple[str, str], float]:
    """Probabilidad de cada candidato según un modelo que nunca vio a su empresa (K grupos)."""
    scores: dict[tuple[str, str], float] = {}
    for fold in range(FOLDS):
        held = [r for r in rows if int(bucket(r.case_id, "fold") * FOLDS) % FOLDS == fold]
        fit = [r for r in rows if int(bucket(r.case_id, "fold") * FOLDS) % FOLDS != fold]
        if not held or not fit:
            continue
        model = _model_from(_train_booster(fit), version)
        scores.update(_scores(model, held))
    return scores


def choose_thresholds(rows: list[Row], scores: dict[tuple[str, str], float]) -> tuple[float, float]:
    """Probable: menor umbral con la exactitud meta (75 %); si ninguno la alcanza, el de
    máxima exactitud × cobertura. Confirmado: el menor umbral con ≥ 95 %."""
    grid = [i / 100 for i in range(5, 100)]
    probable = next(
        (t for t in grid if company_metrics(rows, scores, t)["exactitud"] >= TARGET_ACCURACY),
        None,
    ) or max(
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


def should_promote(
    model_m: dict[str, float],
    rules_m: dict[str, float],
    model_brand: dict[str, float] | None = None,
    rules_brand: dict[str, float] | None = None,
) -> bool:
    """Decide con la exactitud estricta; la de marca nunca puede empeorar más de un punto."""
    if model_brand and rules_brand and model_brand["exactitud"] < rules_brand["exactitud"] - 0.01:
        return False
    return _beats(model_m, rules_m)


def _beats(model_m: dict[str, float], rules_m: dict[str, float]) -> bool:
    accuracy_ok = model_m["exactitud"] >= rules_m["exactitud"] - 0.005
    coverage_gain = model_m["cobertura"] > rules_m["cobertura"] + 0.01
    clear_accuracy_gain = (
        model_m["exactitud"] > rules_m["exactitud"] + 0.02
        and model_m["cobertura"] >= rules_m["cobertura"] - 0.02
    )
    return (accuracy_ok and coverage_gain) or clear_accuracy_gain


def train(rows: list[Row], version: str) -> tuple[dict[str, Any], dict[str, Any]]:
    train_rows, test_rows = split(rows, TEST_SHARE, "test")
    brand_test = brand_relabel(test_rows)
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
    # Umbrales con predicciones fuera de grupo sobre todo el entrenamiento: más estables que
    # una sola partición de validación con pocas empresas.
    confirmed, probable = choose_thresholds(train_rows, out_of_fold_scores(train_rows, version))

    # Modelo final: todo el entrenamiento (sin tocar prueba) con esos umbrales.
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
    model_brand = company_metrics(brand_test, _scores(model, brand_test), probable)
    rules_brand = company_metrics(brand_test, _rules_scores(brand_test), PROBABLE)
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
        "modelo_prueba_marca": model_brand,
        "reglas_prueba_marca": rules_brand,
        "promover": should_promote(model_metrics, rules_metrics, model_brand, rules_brand),
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
