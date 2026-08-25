"""Model-family registry. Optional boosting libraries register only if importable."""

from __future__ import annotations

import logging
from typing import Any

from sklearn.dummy import DummyClassifier, DummyRegressor
from sklearn.ensemble import (
    ExtraTreesClassifier,
    ExtraTreesRegressor,
    GradientBoostingClassifier,
    GradientBoostingRegressor,
    RandomForestClassifier,
    RandomForestRegressor,
)
from sklearn.linear_model import ElasticNet, Lasso, LinearRegression, LogisticRegression, Ridge
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import StandardScaler

logger = logging.getLogger(__name__)


def _optional(name: str, factory):
    try:
        return factory()
    except Exception as exc:  # noqa: BLE001
        logger.info("model family %s unavailable: %s", name, exc)
        return None


def available_families(task_type: str) -> list[str]:
    names = list(_CLASSIFICATION if task_type == "binary" else _REGRESSION)
    extra = []
    if task_type == "binary":
        if _optional("xgboost", lambda: __import__("xgboost")):
            extra.append("xgboost")
        if _optional("lightgbm", lambda: __import__("lightgbm")):
            extra.append("lightgbm")
        if _optional("catboost", lambda: __import__("catboost")):
            extra.append("catboost")
    else:
        if _optional("xgboost", lambda: __import__("xgboost")):
            extra.append("xgboost_regressor")
        if _optional("lightgbm", lambda: __import__("lightgbm")):
            extra.append("lightgbm_regressor")
        if _optional("catboost", lambda: __import__("catboost")):
            extra.append("catboost_regressor")
    return names + extra


_CLASSIFICATION = (
    "majority",
    "logistic_regression",
    "random_forest",
    "extra_trees",
    "gradient_boosting",
)

_REGRESSION = (
    "mean",
    "linear_regression",
    "ridge",
    "lasso",
    "elasticnet",
    "random_forest_regressor",
    "extra_trees_regressor",
    "gradient_boosting_regressor",
)


def make_model(family: str, *, seed: int = 42, hyperparameters: dict[str, Any] | None = None) -> Any:
    hp = dict(hyperparameters or {})
    if family == "majority":
        return DummyClassifier(strategy="most_frequent")
    if family == "mean":
        return DummyRegressor(strategy="mean")
    if family == "logistic_regression":
        return Pipeline(
            [
                ("scaler", StandardScaler()),
                ("clf", LogisticRegression(max_iter=hp.get("max_iter", 1000), random_state=seed)),
            ]
        )
    if family == "random_forest":
        return RandomForestClassifier(
            n_estimators=hp.get("n_estimators", 80), random_state=seed, n_jobs=1
        )
    if family == "extra_trees":
        return ExtraTreesClassifier(
            n_estimators=hp.get("n_estimators", 80), random_state=seed, n_jobs=1
        )
    if family == "gradient_boosting":
        return GradientBoostingClassifier(random_state=seed)
    if family == "linear_regression":
        return Pipeline([("scaler", StandardScaler()), ("m", LinearRegression())])
    if family == "ridge":
        return Pipeline([("scaler", StandardScaler()), ("m", Ridge(random_state=seed))])
    if family == "lasso":
        return Pipeline([("scaler", StandardScaler()), ("m", Lasso(random_state=seed))])
    if family == "elasticnet":
        return Pipeline([("scaler", StandardScaler()), ("m", ElasticNet(random_state=seed))])
    if family == "random_forest_regressor":
        return RandomForestRegressor(
            n_estimators=hp.get("n_estimators", 80), random_state=seed, n_jobs=1
        )
    if family == "extra_trees_regressor":
        return ExtraTreesRegressor(
            n_estimators=hp.get("n_estimators", 80), random_state=seed, n_jobs=1
        )
    if family == "gradient_boosting_regressor":
        return GradientBoostingRegressor(random_state=seed)
    if family == "xgboost":
        from xgboost import XGBClassifier

        return XGBClassifier(
            n_estimators=hp.get("n_estimators", 80),
            max_depth=hp.get("max_depth", 4),
            random_state=seed,
            n_jobs=1,
            eval_metric="logloss",
        )
    if family == "xgboost_regressor":
        from xgboost import XGBRegressor

        return XGBRegressor(
            n_estimators=hp.get("n_estimators", 80),
            max_depth=hp.get("max_depth", 4),
            random_state=seed,
            n_jobs=1,
        )
    if family == "lightgbm":
        from lightgbm import LGBMClassifier

        return LGBMClassifier(
            n_estimators=hp.get("n_estimators", 80), random_state=seed, verbose=-1
        )
    if family == "lightgbm_regressor":
        from lightgbm import LGBMRegressor

        return LGBMRegressor(n_estimators=hp.get("n_estimators", 80), random_state=seed, verbose=-1)
    if family == "catboost":
        from catboost import CatBoostClassifier

        return CatBoostClassifier(
            iterations=hp.get("n_estimators", 80), random_seed=seed, verbose=False
        )
    if family == "catboost_regressor":
        from catboost import CatBoostRegressor

        return CatBoostRegressor(
            iterations=hp.get("n_estimators", 80), random_seed=seed, verbose=False
        )
    raise ValueError(f"Unknown model family: {family}")


def is_classifier(family: str) -> bool:
    return family in _CLASSIFICATION or family in {"xgboost", "lightgbm", "catboost"}


def baseline_families(task_type: str) -> list[str]:
    if task_type == "binary":
        return ["majority", "logistic_regression", "random_forest"]
    return ["mean", "linear_regression", "random_forest_regressor"]


def cheap_families(task_type: str) -> list[str]:
    if task_type == "binary":
        return ["majority", "logistic_regression"]
    return ["mean", "linear_regression", "ridge"]


def strong_families(task_type: str) -> list[str]:
    families = available_families(task_type)
    cheap = set(cheap_families(task_type))
    return [name for name in families if name not in cheap]
