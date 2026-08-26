"""Step 1 — Baseline runner.

Trains ONE single, genuinely-tuned gradient-boosted model per case study on
the full declared feature set, using the exact same train/val/test split the
DCLab engine will use for the same case study (both call
``benchmarks.case_studies.data.load_case_study_data``, the one function that
can produce a split for a given case study + seed).

Model family: sklearn's GradientBoostingClassifier / GradientBoostingRegressor
— this is "whichever is already the DCLab default" (see
``app.engine.models.registry.strong_families``): LightGBM/XGBoost/CatBoost are
registered there only if importable, and none of the three is installed in
this environment today, so the gradient-boosted family DCLab actually falls
back to, with or without this harness, is sklearn's GradientBoosting. Using
anything else here would not be "the DCLab default."

Tuning: a genuine random search (default 40 trials, configurable) over a
realistic hyperparameter space, scored on the same validation split the
DCLab engine scores its own candidates on. The untuned, library-default
model is also fit and scored so the report can state, numerically, how much
the tuning budget actually bought.
"""

from __future__ import annotations

import argparse
import json
import math
import random
import time
from pathlib import Path
from typing import Any

import numpy as np
from sklearn.ensemble import GradientBoostingClassifier, GradientBoostingRegressor

from app.config import REPO_ROOT
from app.engine.evaluation.metrics import classification_metrics, primary_score, regression_metrics

from benchmarks.case_studies.data import load_case_study_data
from benchmarks.case_studies.features import available_features, to_matrix
from benchmarks.case_studies.registry import load_case_study
from benchmarks.case_studies.schema import CaseStudyConfig

MODEL_FAMILY = "gradient_boosting"  # matches app.engine.models.registry family name

# A real search space, not a token gesture: depth, shrinkage, row/feature
# subsampling, and leaf-size regularization all vary.
PARAM_SPACE: dict[str, list[Any]] = {
    "n_estimators": [50, 100, 150, 200, 300, 400, 600],
    "max_depth": [2, 3, 4, 5, 6],
    "subsample": [0.6, 0.7, 0.8, 0.9, 1.0],
    "min_samples_leaf": [1, 5, 10, 20, 40],
    "max_features": [None, "sqrt", "log2", 0.5, 0.8],
}
LEARNING_RATE_LOG_RANGE = (0.01, 0.3)


def _sample_params(rng: random.Random) -> dict[str, Any]:
    params = {name: rng.choice(choices) for name, choices in PARAM_SPACE.items()}
    log_lo, log_hi = math.log10(LEARNING_RATE_LOG_RANGE[0]), math.log10(LEARNING_RATE_LOG_RANGE[1])
    params["learning_rate"] = round(10 ** rng.uniform(log_lo, log_hi), 5)
    return params


def _make_model(task_type: str, seed: int, params: dict[str, Any] | None = None):
    params = params or {}
    cls = GradientBoostingClassifier if task_type == "binary" else GradientBoostingRegressor
    return cls(random_state=seed, **params)


def _score(config: CaseStudyConfig, y_true: np.ndarray, pred: np.ndarray) -> tuple[dict[str, Any], float]:
    classifier = config.target.task_type == "binary"
    metrics = classification_metrics(y_true, pred) if classifier else regression_metrics(y_true, pred)
    score = primary_score(metrics, config.target.evaluation_metric, config.target.task_type)
    return metrics, score


def fit_and_predict_with_hyperparameters(
    config: CaseStudyConfig,
    train: Any,
    predict_on: Any,
    *,
    hyperparameters: dict[str, Any],
    seed: int,
) -> tuple[np.ndarray, list[str]]:
    """Refit the same model family on a different train split using a
    FROZEN, already-tuned hyperparameter set (Step 1's ``best_hyperparameters``)
    rather than re-running the 40-trial search.

    Used by Step 5's fold-stability check: re-tuning 40 trials on every one
    of several walk-forward folds, for all three real-data case studies,
    would cost tens of minutes per case study (Step 1's own tuning took up
    to ~870s for one split). Reusing the hyperparameters already validated
    as "genuinely tuned" in Step 1 tests what Step 5 actually asks — does
    this pipeline's advantage hold up across folds — without re-litigating
    whether the tuning itself was real (that's Step 1's job, already done).
    """
    target = config.target.target_column
    features, _dropped = available_features(train, config.feature_groups, exclude={target})
    classifier = config.target.task_type == "binary"
    X_train = to_matrix(train, features)
    y_train = train[target].to_numpy()
    X_pred = to_matrix(predict_on, features)
    model = _make_model(config.target.task_type, seed, hyperparameters)
    model.fit(X_train, y_train)
    pred = model.predict_proba(X_pred)[:, 1] if classifier else model.predict(X_pred)
    return pred, features


def run_baseline(case_study_id: str, *, n_trials: int = 40, seed: int | None = None) -> dict[str, Any]:
    config = load_case_study(case_study_id)
    data = load_case_study_data(config, seed=seed)

    # Self-check: load_case_study_data is the single function every runner in
    # this harness (baseline now, DCLab engine in Step 2) calls to get its
    # split. Calling it a second time, independently, must reproduce the
    # identical split hash — that is the mechanism, not a hope, behind "same
    # split as the DCLab run." Full closure against the *actual* engine run
    # happens in Step 2, once that runner exists.
    replay = load_case_study_data(config, seed=data.seed)
    split_reproducible = replay.split_hash == data.split_hash

    target = config.target.target_column
    features, dropped_features = available_features(data.train, config.feature_groups, exclude={target})
    classifier = config.target.task_type == "binary"

    X_train = to_matrix(data.train, features)
    y_train = data.train[target].to_numpy()
    X_val = to_matrix(data.val, features)
    y_val = data.val[target].to_numpy()
    X_test = to_matrix(data.test, features)
    y_test = data.test[target].to_numpy()

    # 1. Untuned, library-default baseline (same class, same seed, no HPO) —
    # the number the tuning budget has to beat.
    default_model = _make_model(config.target.task_type, data.seed)
    default_model.fit(X_train, y_train)
    default_pred_val = default_model.predict_proba(X_val)[:, 1] if classifier else default_model.predict(X_val)
    default_val_metrics, default_val_score = _score(config, y_val, default_pred_val)

    # 2. Genuine random search: n_trials sampled configurations, each fit on
    # train and scored on the same val split, nothing selected on test.
    rng = random.Random(data.seed)
    trials: list[dict[str, Any]] = []
    best_trial: dict[str, Any] | None = None
    best_model = None
    started = time.time()
    for trial_idx in range(n_trials):
        params = _sample_params(rng)
        model = _make_model(config.target.task_type, data.seed, params)
        t0 = time.time()
        model.fit(X_train, y_train)
        pred_val = model.predict_proba(X_val)[:, 1] if classifier else model.predict(X_val)
        metrics, score = _score(config, y_val, pred_val)
        trial = {
            "trial": trial_idx,
            "params": {**params},
            "score": score,
            "fit_seconds": time.time() - t0,
        }
        trials.append(trial)
        if best_trial is None or score > best_trial["score"]:
            best_trial = trial
            best_model = model
    tuning_seconds = time.time() - started

    assert best_trial is not None and best_model is not None
    best_pred_val = best_model.predict_proba(X_val)[:, 1] if classifier else best_model.predict(X_val)
    best_val_metrics, best_val_score = _score(config, y_val, best_pred_val)

    improvement_abs = best_val_score - default_val_score
    improvement_rel = (improvement_abs / abs(default_val_score)) if default_val_score != 0 else float("nan")

    # 3. Final test metrics, from the train-only-fit best model. Test is
    # touched exactly once, for reporting, never for selection.
    test_pred = best_model.predict_proba(X_test)[:, 1] if classifier else best_model.predict(X_test)
    test_metrics, test_score = _score(config, y_test, test_pred)

    result = {
        "case_study_id": config.id,
        "model_family": MODEL_FAMILY,
        "sklearn_class": type(best_model).__name__,
        "seed": data.seed,
        "features": features,
        "n_features": len(features),
        "dropped_features": dropped_features,
        "split": {
            "meta": data.split_meta,
            "hash": data.split_hash,
            "n_train": len(data.train),
            "n_val": len(data.val),
            "n_test": len(data.test),
            "reproducible_on_replay": split_reproducible,
        },
        "tuning": {
            "budget_trials": n_trials,
            "tuning_seconds": tuning_seconds,
            "param_space": {**PARAM_SPACE, "learning_rate": f"log-uniform{LEARNING_RATE_LOG_RANGE}"},
            "default_hyperparameters": "library defaults (n_estimators=100, max_depth=3, learning_rate=0.1, subsample=1.0)",
            "default_val_metrics": default_val_metrics,
            "default_val_score": default_val_score,
            "best_trial": best_trial["trial"],
            "best_hyperparameters": best_trial["params"],
            "best_val_score": best_val_score,
            "improvement_abs": improvement_abs,
            "improvement_rel_pct": improvement_rel * 100 if improvement_rel == improvement_rel else None,
            "all_trials": trials,
        },
        "best_val_metrics": best_val_metrics,
        "test_metrics": test_metrics,
        "evaluation_metric": config.target.evaluation_metric,
    }

    out_dir = REPO_ROOT / "artifacts" / "case_studies" / config.id / "baseline"
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "result.json").write_text(json.dumps(result, default=str, indent=2) + "\n")

    predictions = data.test[[config.target.entity_id, config.target.prediction_time_column]].copy()
    predictions["y_true"] = y_test
    predictions["y_pred"] = test_pred
    predictions.to_csv(out_dir / "test_predictions.csv", index=False)

    val_predictions = data.val[[config.target.entity_id, config.target.prediction_time_column]].copy()
    val_predictions["y_true"] = y_val
    val_predictions["y_pred"] = best_pred_val
    val_predictions.to_csv(out_dir / "val_predictions.csv", index=False)

    result["_artifact_dir"] = str(out_dir)
    return result


def _print_summary(result: dict[str, Any]) -> None:
    print(f"case study:        {result['case_study_id']}")
    print(f"model family:      {result['model_family']} ({result['sklearn_class']})")
    print(f"features used:     {result['n_features']} -> {result['features']}")
    if result["dropped_features"]:
        print(f"features dropped (not in frame): {result['dropped_features']}")
    split = result["split"]
    print(
        f"split:              strategy={split['meta'].get('strategy')} "
        f"n_train={split['n_train']} n_val={split['n_val']} n_test={split['n_test']}"
    )
    print(f"split hash:         {split['hash']}")
    print(f"split reproducible: {split['reproducible_on_replay']} (independent replay of load_case_study_data)")
    tuning = result["tuning"]
    print(f"\ntuning budget:      {tuning['budget_trials']} trials in {tuning['tuning_seconds']:.1f}s")
    print(f"default val score:  {tuning['default_val_score']:.4f}  ({result['evaluation_metric']})")
    print(f"best val score:     {tuning['best_val_score']:.4f}  (trial {tuning['best_trial']})")
    rel = tuning["improvement_rel_pct"]
    print(f"tuning improvement: {tuning['improvement_abs']:+.4f} absolute" + (f" ({rel:+.1f}%)" if rel is not None else ""))
    print(f"best hyperparameters: {tuning['best_hyperparameters']}")
    print(f"\nvalidation metrics (best model): {json.dumps(result['best_val_metrics'], indent=2)}")
    print(f"\ntest metrics (best model, held out): {json.dumps(result['test_metrics'], indent=2)}")
    print(f"\nartifacts written to: {result['_artifact_dir']}")


def main() -> None:
    parser = argparse.ArgumentParser(prog="python -m benchmarks.baseline_runner")
    parser.add_argument("--case-study", required=True, help="case study id, e.g. purchase_prediction")
    parser.add_argument("--trials", type=int, default=40, help="hyperparameter search budget (default 40)")
    parser.add_argument("--seed", type=int, default=None, help="override the case study's default seed")
    args = parser.parse_args()

    result = run_baseline(args.case_study, n_trials=args.trials, seed=args.seed)
    _print_summary(result)


if __name__ == "__main__":
    main()
