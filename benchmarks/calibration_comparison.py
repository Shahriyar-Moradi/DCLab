"""Step 4 — Ground-truth calibration comparison (CS4-6 only).

For the three synthetic case studies, compares both the baseline's and the
DCLab ensemble's predicted probabilities against the *true generating
probability* (the sidecar ground truth from
``benchmarks.case_studies.synthetic_generators``, never used in training) —
not the noisy observed label. This tests whether the multi-model
architecture recovers the real underlying relationship better than a single
tuned model, independent of label noise.

Three metrics, all computed against truth:
  - Pearson correlation (predicted probability vs. true probability)
  - A reliability-diagram-style calibration gap (bin by predicted
    probability, compare each bin's mean prediction to its mean true
    probability — the continuous-truth analogue of the engine's own
    ``calibration_gap`` metric, which uses a noisy binary label instead)
  - Brier-against-truth: mean squared error between predicted probability
    and true probability (the same Brier formula, with the true probability
    standing in for the usual noisy 0/1 label)
"""

from __future__ import annotations

import argparse
import json
from typing import Any

import numpy as np
import pandas as pd

from app.config import REPO_ROOT
from app.db.session import get_session_factory

from benchmarks.case_studies.data import load_case_study_data, resolve_seed
from benchmarks.case_studies.predictions import latest_experiment_for, score_frame
from benchmarks.case_studies.registry import load_case_study

# Verdict thresholds, stated here so Step 6/7 can cite them directly rather
# than re-deriving a number: relative improvement in Brier-against-truth
# (lower Brier is better), (baseline_brier - model_brier) / baseline_brier.
MEANINGFUL_IMPROVEMENT_PCT = 5.0


def brier_against_truth(pred: np.ndarray, true_probability: np.ndarray) -> float:
    return float(np.mean((pred - true_probability) ** 2))


def correlation_against_truth(pred: np.ndarray, true_probability: np.ndarray) -> float:
    if np.std(pred) == 0 or np.std(true_probability) == 0:
        return 0.0
    return float(np.corrcoef(pred, true_probability)[0, 1])


def calibration_table_against_truth(pred: np.ndarray, true_probability: np.ndarray, n_bins: int = 10) -> list[dict]:
    order = np.argsort(pred)
    pred_sorted = pred[order]
    true_sorted = true_probability[order]
    bins = np.array_split(np.arange(len(pred)), n_bins)
    rows = []
    for i, idx in enumerate(bins):
        if len(idx) == 0:
            continue
        rows.append(
            {
                "bin": i,
                "n": int(len(idx)),
                "mean_predicted": float(pred_sorted[idx].mean()),
                "mean_true_probability": float(true_sorted[idx].mean()),
                "abs_gap": float(abs(pred_sorted[idx].mean() - true_sorted[idx].mean())),
            }
        )
    return rows


def calibration_gap_against_truth(pred: np.ndarray, true_probability: np.ndarray, n_bins: int = 10) -> float:
    rows = calibration_table_against_truth(pred, true_probability, n_bins)
    weights = np.array([r["n"] for r in rows], dtype=float)
    gaps = np.array([r["abs_gap"] for r in rows], dtype=float)
    return float(np.average(gaps, weights=weights)) if len(rows) else float("nan")


def _verdict(baseline_brier: float, engine_brier: float) -> tuple[str, float]:
    if baseline_brier == 0:
        return "no meaningful calibration difference", 0.0
    relative_improvement_pct = (baseline_brier - engine_brier) / baseline_brier * 100
    if relative_improvement_pct >= MEANINGFUL_IMPROVEMENT_PCT:
        return "ensemble measurably better calibrated", relative_improvement_pct
    if relative_improvement_pct <= -MEANINGFUL_IMPROVEMENT_PCT:
        return "baseline measurably better calibrated", relative_improvement_pct
    return "no meaningful calibration difference", relative_improvement_pct


def run_calibration_comparison(case_study_id: str, *, seed: int | None = None) -> dict[str, Any]:
    config = load_case_study(case_study_id)
    if config.data_source.kind != "synthetic":
        raise ValueError(
            f"{case_study_id!r} is data_source.kind={config.data_source.kind!r} — ground-truth calibration "
            "(Step 4) only applies to the three synthetic case studies (CS4-6), which have a true generating "
            "probability. Real-data case studies (CS1-3) have no such ground truth."
        )
    resolved_seed = seed if seed is not None else resolve_seed(config)

    data = load_case_study_data(config, seed=resolved_seed)
    test = data.test.reset_index(drop=True)
    entity_col = config.target.entity_id
    time_col = config.target.prediction_time_column
    assert data.ground_truth is not None

    baseline_path = REPO_ROOT / "artifacts" / "case_studies" / config.id / "baseline" / "test_predictions.csv"
    baseline_preds = pd.read_csv(baseline_path)
    baseline_preds[time_col] = baseline_preds[time_col].astype(str)

    db = get_session_factory()()
    try:
        experiment = latest_experiment_for(db, config.id)
        engine_pred = score_frame(experiment, test, task_type=config.target.task_type)
    finally:
        db.close()

    merged = test[[entity_col, time_col]].copy()
    merged[time_col] = merged[time_col].astype(str)
    merged["engine_pred"] = engine_pred
    merged = merged.merge(
        baseline_preds[[entity_col, time_col, "y_pred"]].rename(columns={"y_pred": "baseline_pred"}),
        on=[entity_col, time_col],
        how="inner",
    )
    gt = data.ground_truth[["entity_id", "true_probability"]].rename(columns={"entity_id": entity_col})
    merged = merged.merge(gt, on=entity_col, how="left")
    if merged["true_probability"].isna().any():
        raise ValueError(f"{config.id}: {merged['true_probability'].isna().sum()} test rows missing ground truth")
    if len(merged) != len(test):
        raise ValueError(f"{config.id}: merge dropped rows ({len(merged)} of {len(test)})")

    true_p = merged["true_probability"].to_numpy()
    baseline_pred = merged["baseline_pred"].to_numpy()
    engine_pred_arr = merged["engine_pred"].to_numpy()

    baseline_metrics = {
        "brier_against_truth": brier_against_truth(baseline_pred, true_p),
        "correlation_with_truth": correlation_against_truth(baseline_pred, true_p),
        "calibration_gap_against_truth": calibration_gap_against_truth(baseline_pred, true_p),
    }
    engine_metrics = {
        "brier_against_truth": brier_against_truth(engine_pred_arr, true_p),
        "correlation_with_truth": correlation_against_truth(engine_pred_arr, true_p),
        "calibration_gap_against_truth": calibration_gap_against_truth(engine_pred_arr, true_p),
    }
    verdict, relative_improvement_pct = _verdict(
        baseline_metrics["brier_against_truth"], engine_metrics["brier_against_truth"]
    )

    result: dict[str, Any] = {
        "case_study_id": config.id,
        "n_test_entities": len(merged),
        "verdict_threshold_pct": MEANINGFUL_IMPROVEMENT_PCT,
        "baseline": baseline_metrics,
        "ensemble": engine_metrics,
        "brier_relative_improvement_pct": relative_improvement_pct,
        "verdict": verdict,
        "baseline_calibration_table": calibration_table_against_truth(baseline_pred, true_p),
        "ensemble_calibration_table": calibration_table_against_truth(engine_pred_arr, true_p),
        "plain_language_verdict": (
            f"On {config.id}, against the true generating probability (never used in training): baseline Brier="
            f"{baseline_metrics['brier_against_truth']:.5f} (corr={baseline_metrics['correlation_with_truth']:.3f}), "
            f"ensemble Brier={engine_metrics['brier_against_truth']:.5f} "
            f"(corr={engine_metrics['correlation_with_truth']:.3f}). Relative Brier improvement from ensemble: "
            f"{relative_improvement_pct:+.1f}% (threshold for 'meaningful' is +/-{MEANINGFUL_IMPROVEMENT_PCT:.0f}%). "
            f"Verdict: {verdict}."
        ),
    }

    out_dir = REPO_ROOT / "artifacts" / "case_studies" / config.id / "calibration"
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "result.json").write_text(json.dumps(result, default=str, indent=2) + "\n")
    merged.to_csv(out_dir / "per_entity_calibration.csv", index=False)
    result["_artifact_dir"] = str(out_dir)
    return result


def main() -> None:
    parser = argparse.ArgumentParser(prog="python -m benchmarks.calibration_comparison")
    parser.add_argument("--case-study", required=True)
    parser.add_argument("--seed", type=int, default=None)
    args = parser.parse_args()
    result = run_calibration_comparison(args.case_study, seed=args.seed)
    print(json.dumps(result, default=str, indent=2))


if __name__ == "__main__":
    main()
