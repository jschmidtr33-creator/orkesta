"""Modelo de dominio propio: ensamble de árboles (LightGBM) evaluado en Python puro.

El entrenamiento usa LightGBM (``orkesta.evaluation.train_domain``) y exporta los árboles a
JSON. En producción se recorren esos árboles aquí, sin dependencias nativas: el contenedor
no necesita LightGBM ni OpenMP, y la predicción es idéntica a la de LightGBM.
"""

from __future__ import annotations

import json
import math
from dataclasses import dataclass, replace
from functools import lru_cache
from pathlib import Path
from typing import Any

from orkesta.engine.domain.features import candidate_features
from orkesta.engine.domain.scoring import ScoredCandidate
from orkesta.engine.normalize.company import CompanyName

MODEL_DIR = Path(__file__).parent / "models"
ACTIVE_MODEL = MODEL_DIR / "domain-model.json"


@dataclass(frozen=True)
class DomainModel:
    version: str
    feature_names: tuple[str, ...]
    trees: tuple[dict[str, Any], ...]
    threshold_confirmed: float
    threshold_probable: float
    metrics: dict[str, Any]

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> DomainModel:
        booster = data["booster"]
        return cls(
            version=data["version"],
            feature_names=tuple(booster["feature_names"]),
            trees=tuple(tree["tree_structure"] for tree in booster["tree_info"]),
            threshold_confirmed=float(data["threshold_confirmed"]),
            threshold_probable=float(data["threshold_probable"]),
            metrics=data.get("metrics", {}),
        )

    @classmethod
    def load(cls, path: Path) -> DomainModel:
        return cls.from_dict(json.loads(path.read_text(encoding="utf-8")))

    def _leaf(self, node: dict[str, Any], row: list[float]) -> float:
        while "leaf_value" not in node:
            value = row[node["split_feature"]]
            if math.isnan(value):
                go_left = bool(node.get("default_left", True))
            elif node.get("decision_type", "<=") == "<=":
                go_left = value <= node["threshold"]
            else:  # "==" para variables categóricas (no se usan, se mantiene por robustez)
                go_left = str(int(value)) in str(node["threshold"]).split("||")
            node = node["left_child"] if go_left else node["right_child"]
        return float(node["leaf_value"])

    def predict(self, features: dict[str, float]) -> float:
        """Probabilidad (0-1) de que el candidato sea el dominio oficial."""
        row = [float(features.get(name, math.nan)) for name in self.feature_names]
        raw = sum(self._leaf(tree, row) for tree in self.trees)
        return 1.0 / (1.0 + math.exp(-raw))


@lru_cache(maxsize=1)
def active_model() -> DomainModel | None:
    """El modelo promovido, o ``None`` si todavía no hay uno (deciden las reglas)."""
    if not ACTIVE_MODEL.exists():
        return None
    return DomainModel.load(ACTIVE_MODEL)


def apply_model(
    model: DomainModel, scored: ScoredCandidate, companies: list[CompanyName], country: str | None
) -> ScoredCandidate:
    """Reemplaza la decisión de reglas por la del modelo, conservando razones y puntaje de reglas.

    Los candidatos rechazados por evidencia dura (no resuelve, aparcado, redirige) siguen
    rechazados: el modelo ordena y decide entre candidatos vivos, no revive descartes.
    """
    if scored.status == "rejected" and scored.score == 0:
        return scored
    probability = model.predict(candidate_features(scored.evidence, companies, country))
    if probability >= model.threshold_confirmed or (
        # Confirmado también cuando ambos coinciden: reglas con evidencia de identidad y
        # modelo por encima de su umbral de probable.
        scored.status == "confirmed" and probability >= model.threshold_probable
    ):
        status = "confirmed"
    elif probability >= model.threshold_probable:
        status = "probable"
    elif probability >= model.threshold_probable / 2:
        status = "candidate"
    else:
        status = "rejected"
    return replace(
        scored,
        score=round(probability * 100),
        status=status,
        reasons=(*scored.reasons, "MODEL_DECISION"),
        rules_score=scored.score,
        model_version=model.version[:40],
    )
