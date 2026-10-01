"""Bounded hyperparameter tuning (P1.4-C).

A tuned candidate carries a *tuning plan* (family, bounded search space, trial
budget, seed) instead of fixed values, so its fingerprint covers the search.
The runner tunes it inside every outer CV fold on that fold's training rows
only (one inner stratified split), so the outer CV score stays an honest,
nested estimate; the final fit tunes on the full training pool. The final
holdout is never seen. Optuna is optional: without it no tuned candidate exists.
"""

from __future__ import annotations

import copy
import math
import time
from typing import Any, Callable

import numpy as np

TUNING_VERSION = "dclab.tuning.v1"
INNER_VALIDATION_FRACTION = 0.25

# Bounded, documented search spaces: (kind, low, high[, log]) or ("choice", values).
SEARCH_SPACES: dict[str, dict[str, tuple]] = {
    "lightgbm": {
        "n_estimators": ("int", 50, 300),
        "num_leaves": ("int", 8, 64),
        "learning_rate": ("float", 0.02, 0.3, True),
        "min_child_samples": ("int", 5, 50),
        "colsample_bytree": ("float", 0.6, 1.0),
    },
    "xgboost": {
        "n_estimators": ("int", 50, 300),
        "max_depth": ("int", 2, 8),
        "learning_rate": ("float", 0.02, 0.3, True),
        "subsample": ("float", 0.6, 1.0),
        "colsample_bytree": ("float", 0.6, 1.0),
        "min_child_weight": ("float", 1.0, 10.0, True),
    },
    "catboost": {
        "depth": ("int", 3, 8),
        "learning_rate": ("float", 0.02, 0.3, True),
        "l2_leaf_reg": ("float", 1.0, 10.0, True),
    },
    "random_forest": {
        "n_estimators": ("int", 50, 300),
        "max_depth": ("int", 3, 20),
        "min_samples_leaf": ("int", 1, 10),
        "max_features": ("choice", ["sqrt", 0.5, 1.0]),
    },
}
for _family in ("lightgbm", "xgboost", "catboost", "random_forest"):
    SEARCH_SPACES[f"{_family}_regressor"] = dict(SEARCH_SPACES[_family])

# Strongest-first preference for the single tuned family.
_PREFERENCE = {
    "classification": ("lightgbm", "xgboost", "catboost", "random_forest"),
    "regression": ("lightgbm_regressor", "xgboost_regressor", "catboost_regressor", "random_forest_regressor"),
}


def optuna_available() -> bool:
    try:
        import optuna  # noqa: F401
    except ImportError:
        return False
    return True


def tuning_plan(task_type: str, families: list[str], *, n_trials: int, seed: int) -> dict[str, Any] | None:
    """The tuning spec for one family, or None when tuning is off or unavailable."""
    if n_trials <= 0 or not optuna_available():
        return None
    kind = "classification" if task_type in {"binary", "multiclass"} else "regression"
    family = next((name for name in _PREFERENCE[kind] if name in families), None)
    if family is None:
        return None
    space = copy.deepcopy(SEARCH_SPACES[family])
    return {
        "version": TUNING_VERSION,
        "family": family,
        "search_space": {name: list(spec) for name, spec in space.items()},
        "n_trials": int(n_trials),
        "seed": int(seed),
        "sampler": "TPESampler",
        # Random trials before TPE takes over (stated, not Optuna's default 10).
        "n_startup_trials": max(3, min(10, int(n_trials) // 3)),
        # One inner split of the outer training rows that follows the outer plan:
        # group-disjoint for group CV, latest rows for time CV, else (stratified) random.
        "inner_validation": "plan_aware_holdout",
        "inner_validation_fraction": INNER_VALIDATION_FRACTION,
    }


def _suggest(trial, space: dict[str, list]) -> dict[str, Any]:
    params: dict[str, Any] = {}
    for name, spec in space.items():
        kind = spec[0]
        if kind == "int":
            params[name] = trial.suggest_int(name, int(spec[1]), int(spec[2]))
        elif kind == "float":
            params[name] = trial.suggest_float(name, float(spec[1]), float(spec[2]), log=bool(spec[3]) if len(spec) > 3 else False)
        elif kind == "choice":
            params[name] = trial.suggest_categorical(name, list(spec[1]))
    return params


def _inner_split(
    y: np.ndarray,
    *,
    classification: bool,
    seed: int,
    groups: np.ndarray | None = None,
    time_values: np.ndarray | None = None,
) -> tuple[np.ndarray, np.ndarray, str]:
    from sklearn.model_selection import GroupShuffleSplit, ShuffleSplit, StratifiedShuffleSplit

    index = np.arange(len(y))
    if time_values is not None:
        order = np.argsort(np.asarray(time_values), kind="stable")
        cut = int(round(len(order) * (1.0 - INNER_VALIDATION_FRACTION)))
        return np.sort(order[:cut]), np.sort(order[cut:]), "time_latest"
    if groups is not None and len(np.unique(groups)) >= 2:
        splitter = GroupShuffleSplit(n_splits=1, test_size=INNER_VALIDATION_FRACTION, random_state=seed)
        fit_idx, val_idx = next(splitter.split(index, groups=groups))
        return fit_idx, val_idx, "group_disjoint"
    if classification:
        try:
            splitter = StratifiedShuffleSplit(n_splits=1, test_size=INNER_VALIDATION_FRACTION, random_state=seed)
            fit_idx, val_idx = next(splitter.split(index, y))
            return fit_idx, val_idx, "stratified_random"
        except ValueError:
            pass  # a class too rare to stratify: plain shuffle split
    splitter = ShuffleSplit(n_splits=1, test_size=INNER_VALIDATION_FRACTION, random_state=seed)
    fit_idx, val_idx = next(splitter.split(index))
    return fit_idx, val_idx, "random"


class TuningFailedError(RuntimeError):
    """Every trial raised: a configuration or data bug, not a budget cut."""


def tune(
    plan: dict[str, Any],
    X,
    y: np.ndarray,
    *,
    classification: bool,
    score: Callable[[dict[str, Any], Any, np.ndarray, Any, np.ndarray], float],
    deadline: float | None = None,
    groups: Any = None,
    time_values: Any = None,
) -> dict[str, Any]:
    """Search ``plan`` on (X, y) — training rows only — and return the best params.

    ``score(params, X_fit, y_fit, X_val, y_val)`` fits a fresh pipeline and returns
    a larger-is-better score. ``deadline`` (time.time()) truncates the search; the
    trial count actually run is recorded.
    """
    import optuna

    optuna.logging.set_verbosity(optuna.logging.WARNING)
    fit_idx, val_idx, inner_split = _inner_split(
        np.asarray(y),
        classification=classification,
        seed=int(plan["seed"]),
        groups=None if groups is None else np.asarray(groups),
        time_values=None if time_values is None else np.asarray(time_values),
    )
    X_fit, X_val = X.iloc[fit_idx], X.iloc[val_idx]
    y_fit, y_val = np.asarray(y)[fit_idx], np.asarray(y)[val_idx]
    space = plan["search_space"]

    errors: list[str] = []

    def objective(trial) -> float:
        try:
            value = score(_suggest(trial, space), X_fit, y_fit, X_val, y_val)
        except Exception as exc:  # noqa: BLE001 - recorded, re-raised below if all fail
            errors.append(f"{type(exc).__name__}: {exc}")
            raise
        return value if math.isfinite(value) else float("-inf")

    sampler = optuna.samplers.TPESampler(
        seed=int(plan["seed"]), n_startup_trials=int(plan.get("n_startup_trials", 10))
    )
    study = optuna.create_study(direction="maximize", sampler=sampler)
    timeout = None if deadline is None else max(0.0, deadline - time.time())
    study.optimize(
        objective,
        n_trials=int(plan["n_trials"]),
        timeout=timeout,
        catch=(Exception,),
    )
    completed = [trial for trial in study.trials if trial.state == optuna.trial.TrialState.COMPLETE]
    if not completed and errors:
        raise TuningFailedError(f"every tuning trial failed; first error: {errors[0]}")
    if not completed:
        return {
            "params": {},
            "trials_completed": 0,
            "trials_failed": 0,
            "inner_score": None,
            "inner_split": inner_split,
            "truncated": True,
        }
    best = max(completed, key=lambda trial: trial.value)
    return {
        "params": dict(best.params),
        "trials_completed": len(completed),
        "trials_failed": len(errors),
        "inner_score": float(best.value),
        "inner_split": inner_split,
        "truncated": len(study.trials) < int(plan["n_trials"]),
    }
