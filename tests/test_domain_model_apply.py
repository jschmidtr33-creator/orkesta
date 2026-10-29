"""Aplicación del modelo sobre candidatos puntuados por reglas (sin LightGBM)."""

from __future__ import annotations

from orkesta.engine.domain.features import FEATURE_NAMES
from orkesta.engine.domain.model import DomainModel, apply_model
from orkesta.engine.domain.scoring import CandidateEvidence, score
from orkesta.engine.normalize import normalize_company


def _model(leaf: float) -> DomainModel:
    """Un árbol: 'site_ok' > 0.5 → +leaf, si no → -leaf."""
    split = FEATURE_NAMES.index("site_ok")
    tree = {
        "split_feature": split,
        "threshold": 0.5,
        "decision_type": "<=",
        "default_left": True,
        "left_child": {"leaf_value": -leaf},
        "right_child": {"leaf_value": leaf},
    }
    return DomainModel.from_dict(
        {
            "version": "lgbm-test",
            "booster": {
                "feature_names": list(FEATURE_NAMES),
                "tree_info": [{"tree_structure": tree}],
            },
            "threshold_confirmed": 0.9,
            "threshold_probable": 0.6,
        }
    )


def test_model_decides_and_keeps_rules_score() -> None:
    names = [normalize_company("Acme Logística")]
    alive = CandidateEvidence("acme.mx", "match_key", resolves=True, site_ok=False)
    rules = score(alive, names, "MX")
    decided = apply_model(_model(4.0), rules, names, "MX")
    assert decided.status == "rejected"  # sin sitio: el modelo lo descarta
    assert decided.rules_score == rules.score
    assert decided.model_version == "lgbm-test"
    assert decided.reasons[-1] == "MODEL_DECISION"


def test_model_does_not_revive_hard_rejections() -> None:
    names = [normalize_company("Acme")]
    dead = CandidateEvidence("acme.mx", "match_key", resolves=False)
    rules = score(dead, names, "MX")
    assert apply_model(_model(4.0), rules, names, "MX") is rules
