"""Modelo de dominio: inferencia en Python puro idéntica a LightGBM y reglas de promoción."""

from __future__ import annotations

import csv
import gzip
import random
from pathlib import Path

import pytest

from orkesta.engine.domain.features import FEATURE_NAMES
from orkesta.engine.domain.model import DomainModel
from orkesta.evaluation import train_domain
from orkesta.evaluation.train_domain import Row, company_metrics, should_promote, split

lgb = pytest.importorskip("lightgbm")


def _synthetic_rows(n_cases: int = 300, seed: int = 3) -> list[Row]:
    rng = random.Random(seed)  # noqa: S311 - datos sintéticos
    rows: list[Row] = []
    for case in range(n_cases):
        winner = rng.randrange(4)
        for cand in range(4):
            good = cand == winner
            features = {name: 0.0 for name in FEATURE_NAMES}
            features["resolves"] = 1.0
            features["site_ok"] = float(good or rng.random() < 0.4)
            features["name_in_header"] = (
                rng.uniform(0.6, 1) if good else rng.uniform(0, 0.7)
            ) * features["site_ok"]
            features["label_similarity"] = rng.uniform(0.7, 1) if good else rng.uniform(0.3, 0.9)
            features["country_tld"] = float(rng.random() < 0.5)
            rules = 40 * features["name_in_header"] + 30 * features["label_similarity"]
            rows.append(Row(f"c{case}", f"d{cand}.com", int(good), rules, features))
    return rows


def test_pure_python_matches_lightgbm() -> None:
    rows = _synthetic_rows(120)
    booster = train_domain._train_booster(rows)
    model = DomainModel.from_dict(
        {
            "version": "t",
            "booster": booster.dump_model(),
            "threshold_confirmed": 0.9,
            "threshold_probable": 0.5,
        }
    )
    import numpy as np

    matrix = np.array([[r.features[n] for n in FEATURE_NAMES] for r in rows])
    expected = booster.predict(matrix)
    for row, prob in zip(rows, expected, strict=True):
        assert model.predict(row.features) == pytest.approx(prob, abs=1e-9)


def test_split_is_by_company() -> None:
    rows = _synthetic_rows(200)
    first, second = split(rows, 0.3, "test")
    assert {r.case_id for r in first}.isdisjoint({r.case_id for r in second})
    assert len(first) + len(second) == len(rows)


def test_company_metrics_picks_best_candidate() -> None:
    f = dict.fromkeys(FEATURE_NAMES, 0.0)
    rows = [Row("a", "x.com", 1, 0, f), Row("a", "y.com", 0, 0, f), Row("b", "z.com", 0, 0, f)]
    scores = {("a", "x.com"): 0.9, ("a", "y.com"): 0.2, ("b", "z.com"): 0.1}
    m = company_metrics(rows, scores, 0.5)
    assert (m["con_dominio"], m["aciertos"], m["cobertura"]) == (1, 1, 0.5)


def test_promotion_rules() -> None:
    rules = {"exactitud": 0.90, "cobertura": 0.60}
    assert should_promote({"exactitud": 0.90, "cobertura": 0.70}, rules)
    assert should_promote({"exactitud": 0.95, "cobertura": 0.59}, rules)
    assert not should_promote({"exactitud": 0.85, "cobertura": 0.90}, rules)
    assert not should_promote({"exactitud": 0.90, "cobertura": 0.605}, rules)


def test_cli_trains_and_promotes_only_when_better(tmp_path: Path) -> None:
    path = tmp_path / "features.csv.gz"
    with gzip.open(path, "wt", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(["case_id", "country", "domain", "rules_score", "label", *FEATURE_NAMES])
        for r in _synthetic_rows(400):
            writer.writerow(
                [r.case_id, "MX", r.domain, r.rules_score, r.label]
                + [r.features[n] for n in FEATURE_NAMES]
            )
    out = tmp_path / "model.json"
    report = tmp_path / "report.json"
    assert train_domain.main([str(path), "--out", str(out), "--report", str(report)]) == 0
    assert report.exists()
    if out.exists():
        model = DomainModel.load(out)
        assert 0 < model.threshold_probable <= model.threshold_confirmed < 1


def test_too_few_companies_never_promotes() -> None:
    data, report = train_domain.train(_synthetic_rows(40), "t")
    assert data == {}
    assert report["promover"] is False
