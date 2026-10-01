"""Model-family registry. Optional boosting libraries register only if importable."""

from __future__ import annotations

import logging
from importlib.metadata import PackageNotFoundError, version
from typing import Any

import numpy as np
from sklearn.base import BaseEstimator, ClassifierMixin, clone
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
    classification = task_type in {"binary", "multiclass"}
    names = list(_CLASSIFICATION if classification else _REGRESSION)
    extra = []
    if classification:
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


class ContiguousLabelClassifier(ClassifierMixin, BaseEstimator):
    """Fit the wrapped classifier on labels re-coded to 0..m-1.

    XGBoost rejects non-contiguous labels, which a multiclass training fold
    produces whenever a rare class is absent from it. ``classes_`` keeps the
    original label codes so probabilities stay aligned to the full label set.
    """

    def __init__(self, estimator: Any = None):
        self.estimator = estimator

    def fit(self, X, y, **fit_params):
        self.classes_, codes = np.unique(np.asarray(y), return_inverse=True)
        self.estimator_ = clone(self.estimator).fit(X, codes, **fit_params)
        return self

    def predict_proba(self, X):
        return self.estimator_.predict_proba(X)

    def predict(self, X):
        return self.classes_[np.asarray(self.estimator_.predict(X), dtype=int)]


def make_model(
    family: str,
    *,
    seed: int = 42,
    hyperparameters: dict[str, Any] | None = None,
    task_type: str | None = None,
) -> Any:
    hp = dict(hyperparameters or {})
    tuned = dict(hp.pop("tuned", None) or {})
    model = _make_base_model(family, seed=seed, hp=hp, task_type=task_type)
    return _apply_tuned_params(model, tuned) if tuned else model


def _apply_tuned_params(model: Any, tuned: dict[str, Any]) -> Any:
    """Set tuned constructor values on the estimator, wherever it sits."""
    prefix = ""
    if isinstance(model, ContiguousLabelClassifier):
        prefix = "estimator__"
    elif isinstance(model, Pipeline):
        prefix = f"{model.steps[-1][0]}__"
    model.set_params(**{f"{prefix}{key}": value for key, value in tuned.items()})
    return model


def _make_base_model(family: str, *, seed: int, hp: dict[str, Any], task_type: str | None) -> Any:
    if family == "xgboost" and task_type == "multiclass":
        from xgboost import XGBClassifier

        return ContiguousLabelClassifier(
            XGBClassifier(
                n_estimators=hp.get("n_estimators", 80),
                max_depth=hp.get("max_depth", 4),
                random_state=seed,
                n_jobs=1,
                eval_metric="mlogloss",
            )
        )
    if family == "majority":
        # Class-prior baseline: the "chance" level every learned model must beat.
        return DummyClassifier(strategy="prior")
    if family == "mean":
        return DummyRegressor(strategy="mean")
    if family == "median":
        return DummyRegressor(strategy="median")
    if family == "logistic_regression":
        return Pipeline(
            [
                ("scaler", StandardScaler()),
                (
                    "clf",
                    LogisticRegression(
                        max_iter=hp.get("max_iter", 1000),
                        random_state=seed,
                        class_weight=hp.get("class_weight"),
                    ),
                ),
            ]
        )
    if family == "random_forest":
        return RandomForestClassifier(
            n_estimators=hp.get("n_estimators", 80),
            random_state=seed,
            n_jobs=1,
            class_weight=hp.get("class_weight"),
        )
    if family == "extra_trees":
        return ExtraTreesClassifier(
            n_estimators=hp.get("n_estimators", 80),
            random_state=seed,
            n_jobs=1,
            class_weight=hp.get("class_weight"),
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
            scale_pos_weight=hp.get("scale_pos_weight", 1.0),
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
            n_estimators=hp.get("n_estimators", 80),
            random_state=seed,
            n_jobs=1,
            verbose=-1,
            class_weight=hp.get("class_weight"),
        )
    if family == "lightgbm_regressor":
        from lightgbm import LGBMRegressor

        return LGBMRegressor(
            n_estimators=hp.get("n_estimators", 80), random_state=seed, n_jobs=1, verbose=-1
        )
    if family == "catboost":
        from catboost import CatBoostClassifier

        return CatBoostClassifier(
            iterations=hp.get("n_estimators", 80),
            random_seed=seed,
            thread_count=1,
            allow_writing_files=False,
            verbose=False,
            auto_class_weights=hp.get("auto_class_weights"),
        )
    if family == "catboost_regressor":
        from catboost import CatBoostRegressor

        return CatBoostRegressor(
            iterations=hp.get("n_estimators", 80),
            random_seed=seed,
            thread_count=1,
            allow_writing_files=False,
            verbose=False,
        )
    raise ValueError(f"Unknown model family: {family}")


def applied_hyperparameters(
    family: str, *, seed: int = 42, hyperparameters: dict[str, Any] | None = None
) -> dict[str, Any]:
    """Constructor values `make_model` actually applies. Keep in sync with make_model."""

    hp = dict(hyperparameters or {})
    values = _applied_base(family, seed=seed, hp=hp)
    # Tuned values (P1.4-C) override the defaults they replace.
    values.update(dict(hp.get("tuned") or {}))
    # A tuning plan is not a constructor value; the fingerprint hashes it separately.
    # Imbalance weighting (P1.4-A1) is recorded only when set, so unweighted
    # candidates keep identical evidence.
    for key in ("class_weight", "scale_pos_weight", "auto_class_weights"):
        if hp.get(key) is not None:
            values[key] = hp[key]
    return values


def _applied_base(family: str, *, seed: int, hp: dict[str, Any]) -> dict[str, Any]:
    if family == "majority":
        return {"strategy": "prior"}
    if family == "mean":
        return {"strategy": "mean"}
    if family == "median":
        return {"strategy": "median"}
    if family == "logistic_regression":
        return {"max_iter": hp.get("max_iter", 1000), "random_state": seed}
    if family in {"random_forest", "extra_trees", "random_forest_regressor", "extra_trees_regressor"}:
        return {
            "n_estimators": hp.get("n_estimators", 80),
            "random_state": seed,
            "n_jobs": 1,
        }
    if family in {"gradient_boosting", "gradient_boosting_regressor"}:
        return {"random_state": seed}
    if family in {"linear_regression"}:
        return {"fit_intercept": hp.get("fit_intercept", True)}
    if family in {"ridge", "lasso", "elasticnet"}:
        return {"random_state": seed}
    if family in {"xgboost", "xgboost_regressor"}:
        values: dict[str, Any] = {
            "n_estimators": hp.get("n_estimators", 80),
            "max_depth": hp.get("max_depth", 4),
            "random_state": seed,
            "n_jobs": 1,
        }
        if family == "xgboost":
            values["eval_metric"] = "logloss"
        return values
    if family in {"lightgbm", "lightgbm_regressor"}:
        return {
            "n_estimators": hp.get("n_estimators", 80),
            "random_state": seed,
            "n_jobs": 1,
            "verbose": -1,
        }
    if family in {"catboost", "catboost_regressor"}:
        return {
            "iterations": hp.get("n_estimators", 80),
            "random_seed": seed,
            "thread_count": 1,
            "allow_writing_files": False,
            "verbose": False,
        }
    return dict(hp)


def _package_version(name: str) -> str | None:
    try:
        return version(name)
    except PackageNotFoundError:
        return None


def implementation_for_family(family: str) -> tuple[str, str, str | None]:
    """Return (library, implementation class, library version) for a registry family."""

    mapping = {
        "majority": ("sklearn", "sklearn.dummy.DummyClassifier", "scikit-learn"),
        "mean": ("sklearn", "sklearn.dummy.DummyRegressor", "scikit-learn"),
        "median": ("sklearn", "sklearn.dummy.DummyRegressor", "scikit-learn"),
        "logistic_regression": (
            "sklearn",
            "sklearn.linear_model.LogisticRegression",
            "scikit-learn",
        ),
        "random_forest": ("sklearn", "sklearn.ensemble.RandomForestClassifier", "scikit-learn"),
        "extra_trees": ("sklearn", "sklearn.ensemble.ExtraTreesClassifier", "scikit-learn"),
        "gradient_boosting": (
            "sklearn",
            "sklearn.ensemble.GradientBoostingClassifier",
            "scikit-learn",
        ),
        "linear_regression": ("sklearn", "sklearn.linear_model.LinearRegression", "scikit-learn"),
        "ridge": ("sklearn", "sklearn.linear_model.Ridge", "scikit-learn"),
        "lasso": ("sklearn", "sklearn.linear_model.Lasso", "scikit-learn"),
        "elasticnet": ("sklearn", "sklearn.linear_model.ElasticNet", "scikit-learn"),
        "random_forest_regressor": (
            "sklearn",
            "sklearn.ensemble.RandomForestRegressor",
            "scikit-learn",
        ),
        "extra_trees_regressor": (
            "sklearn",
            "sklearn.ensemble.ExtraTreesRegressor",
            "scikit-learn",
        ),
        "gradient_boosting_regressor": (
            "sklearn",
            "sklearn.ensemble.GradientBoostingRegressor",
            "scikit-learn",
        ),
        "xgboost": ("xgboost", "xgboost.XGBClassifier", "xgboost"),
        "xgboost_regressor": ("xgboost", "xgboost.XGBRegressor", "xgboost"),
        "lightgbm": ("lightgbm", "lightgbm.LGBMClassifier", "lightgbm"),
        "lightgbm_regressor": ("lightgbm", "lightgbm.LGBMRegressor", "lightgbm"),
        "catboost": ("catboost", "catboost.CatBoostClassifier", "catboost"),
        "catboost_regressor": ("catboost", "catboost.CatBoostRegressor", "catboost"),
    }
    library, cls, package = mapping.get(family, ("unknown", family, None))
    return library, cls, _package_version(package) if package else None


def is_classifier(family: str) -> bool:
    return family in _CLASSIFICATION or family in {"xgboost", "lightgbm", "catboost"}


def baseline_families(task_type: str) -> list[str]:
    if task_type in {"binary", "multiclass"}:
        return ["majority", "logistic_regression", "random_forest"]
    return ["mean", "linear_regression", "random_forest_regressor"]


def cheap_families(task_type: str) -> list[str]:
    if task_type in {"binary", "multiclass"}:
        return ["majority", "logistic_regression"]
    return ["mean", "linear_regression", "ridge"]


def strong_families(task_type: str) -> list[str]:
    families = available_families(task_type)
    cheap = set(cheap_families(task_type))
    return [name for name in families if name not in cheap]
