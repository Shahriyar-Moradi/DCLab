"""Build sklearn estimators from layer-config candidate specs."""

from __future__ import annotations

from dataclasses import dataclass

from sklearn.ensemble import GradientBoostingClassifier, RandomForestClassifier
from sklearn.linear_model import LogisticRegression
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import StandardScaler

from app.ml.feature_groups import features_for_groups


@dataclass(frozen=True)
class CandidateSpec:
    id: str
    algorithm: str
    groups: tuple[str, ...]
    features: tuple[str, ...]


def _estimator(algorithm: str):
    if algorithm == "logistic_regression":
        return Pipeline(
            [
                ("scaler", StandardScaler()),
                ("clf", LogisticRegression(max_iter=1000, random_state=42)),
            ]
        )
    if algorithm == "random_forest":
        return RandomForestClassifier(n_estimators=80, random_state=42, n_jobs=1)
    if algorithm == "gradient_boosting":
        return GradientBoostingClassifier(random_state=42)
    raise ValueError(f"Unsupported algorithm: {algorithm}")


def build_candidate_specs(config: dict) -> list[CandidateSpec]:
    specs: list[CandidateSpec] = []
    for raw in config.get("candidates") or []:
        groups = tuple(raw["groups"])
        specs.append(
            CandidateSpec(
                id=str(raw["id"]),
                algorithm=str(raw["algorithm"]),
                groups=groups,
                features=tuple(features_for_groups(config, list(groups))),
            )
        )
    if not specs:
        raise ValueError("Layer config has no candidates")
    cap = int(config.get("max_candidates") or 12)
    if config.get("kind") != "simulation":
        cap = min(cap, 12)
    else:
        cap = min(cap, 20)
    if len(specs) > cap:
        raise ValueError(f"Refusing to evaluate {len(specs)} candidates; cap is {cap}")
    return specs


def make_estimator(spec: CandidateSpec):
    return _estimator(spec.algorithm)
