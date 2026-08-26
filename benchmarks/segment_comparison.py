"""Step 5 — Segment-level and robustness comparison.

Two independent questions, both answered per case study:

1. **Segment breakdown**: does Step 3's decision-impact comparison (realized
   value, agreement rate) hold up when broken down by at least two
   meaningful segments, or does the headline number hide a segment where
   the ensemble's advantage shrinks, disappears, or reverses? Segments are
   declared per case study in ``configs/case_studies/*.yaml``
   (``segment_columns``) — some are literal columns from the source data,
   others (documented in ``DERIVED_SEGMENTS`` below, and in each config's
   comments) are tercile bands derived from a continuous column, because no
   literal second segment existed in the raw data for that case study.

2. **Fold stability**: does the aggregate baseline-vs-ensemble gap hold up
   across several different train/test windows, or was Step 3's number
   produced by one lucky split? Because every case study here is temporal
   (all six declare ``as_of_date``), "folds" are walk-forward windows, not
   random K-fold: fold i trains and validates on the first
   ``FOLD_FRACS[i]`` of the chronologically-sorted data and tests on the
   held-out slice ``make_split`` carves out of that window (see
   ``benchmarks.case_studies.data.load_case_study_data_fold``). The last
   fold (frac=1.0) reproduces Step 1/2/3's exact split, which doubles as a
   consistency check on this module.

   To keep 6 case studies x N folds tractable, each fold reuses Step 1's
   already-tuned baseline hyperparameters (refit, not re-searched) and
   re-runs the DCLab engine's actual candidate-search-and-ensemble pipeline
   on that fold's window (fast: the engine currently has no HPO step of its
   own, so a fold run costs seconds to low tens of seconds, not minutes).
   This is a documented, deliberate scope reduction from "re-run all of
   Step 1 and Step 2 from scratch per fold" — re-tuning 40 trials per fold
   across three real-data case studies would cost tens of minutes each; see
   ``baseline_runner.fit_and_predict_with_hyperparameters``.
"""

from __future__ import annotations

import argparse
import json
from typing import Any

import numpy as np
import pandas as pd

from app.config import REPO_ROOT
from app.db.session import get_session_factory

from benchmarks.baseline_runner import fit_and_predict_with_hyperparameters
from benchmarks.case_studies.data import CaseStudyData, load_case_study_data, load_case_study_data_fold, resolve_seed
from benchmarks.case_studies.policy import PolicyConfig, decide
from benchmarks.case_studies.predictions import score_frame
from benchmarks.case_studies.registry import load_case_study
from benchmarks.case_studies.schema import CaseStudyConfig
from benchmarks.decision_impact import POLICY_DIR, _entity_value, build_decision_frame
from benchmarks.dclab_runner import run_experiment_on_frame

# (base_column, n_bands, labels) for segment_columns declared in a config
# that are not literal columns in the source frame. Documented per case
# study in configs/case_studies/*.yaml alongside the segment_columns list.
DERIVED_SEGMENTS: dict[str, tuple[str, list[str]]] = {
    "order_count_band": ("order_count", ["Low", "Medium", "High"]),
    "dormancy_band": ("days_since_last_order", ["Recently dormant", "Moderately dormant", "Long dormant"]),
    "order_value_band": ("avg_order_value", ["Low value", "Medium value", "High value"]),
    "usage_score_band": ("usage_score", ["Low usage", "Medium usage", "High usage"]),
}

# Walk-forward fold windows: fraction of the chronologically-sorted full
# frame used as the pool make_split's own 70/15/15 time-split is applied
# to. frac=1.0 (last fold) is the exact Step 1/2/3 split.
FOLD_FRACS: list[float] = [0.4, 0.6, 0.8, 1.0]

# A segment is flagged as a notable finding if the ensemble's relative
# realized-value lift there is at least this many percentage points worse
# than the case study's own aggregate lift (i.e. the segment materially
# undercuts the headline number) — stated here so Step 6/7 can cite a
# concrete number rather than "looks smaller."
SEGMENT_SHRINK_THRESHOLD_PP = 10.0


def _segment_series(config: CaseStudyConfig, frame: pd.DataFrame, seg_col: str) -> pd.Series:
    if seg_col in frame.columns:
        return frame[seg_col]
    if seg_col in DERIVED_SEGMENTS:
        base_col, labels = DERIVED_SEGMENTS[seg_col]
        if base_col not in frame.columns:
            raise ValueError(f"{config.id}: derived segment {seg_col!r} needs base column {base_col!r}, not in frame")
        values = pd.to_numeric(frame[base_col], errors="coerce")
        try:
            return pd.qcut(values, q=len(labels), labels=labels, duplicates="drop")
        except ValueError:
            # Degenerate distribution (e.g. too many ties for len(labels) distinct
            # quantile edges) — fall back to as many bands as the data actually
            # supports rather than crashing the whole comparison.
            return pd.qcut(values, q=len(labels), labels=None, duplicates="drop")
    raise ValueError(
        f"{config.id}: segment column {seg_col!r} is neither a literal frame column nor a known derived "
        f"segment ({sorted(DERIVED_SEGMENTS)})"
    )


def _segment_table(merged: pd.DataFrame, seg_values: pd.Series, aggregate_lift_pct: float | None) -> list[dict]:
    rows = []
    frame = merged.copy()
    frame["_segment"] = seg_values.reset_index(drop=True).values
    for level, group in frame.groupby("_segment", observed=True):
        n = len(group)
        if n == 0:
            continue
        agreement_rate = float((group["baseline_action"] == group["engine_action"]).mean())
        total_baseline = float(group["baseline_realized_value"].sum())
        total_engine = float(group["engine_realized_value"].sum())
        lift_abs = total_engine - total_baseline
        lift_pct = (lift_abs / abs(total_baseline) * 100) if total_baseline else None
        shrinks_vs_aggregate = (
            aggregate_lift_pct is not None and lift_pct is not None and (aggregate_lift_pct - lift_pct) >= SEGMENT_SHRINK_THRESHOLD_PP
        )
        rows.append(
            {
                "segment": str(level),
                "n": n,
                "agreement_rate": agreement_rate,
                "total_baseline_realized_value": total_baseline,
                "total_engine_realized_value": total_engine,
                "lift_abs": lift_abs,
                "lift_rel_pct": lift_pct,
                "shrinks_vs_aggregate_by_ge_10pp": shrinks_vs_aggregate,
            }
        )
    rows.sort(key=lambda r: r["segment"])
    return rows


def run_segment_breakdown(config: CaseStudyConfig, policy: PolicyConfig, data: CaseStudyData) -> dict[str, Any]:
    merged, ground_truth_basis = build_decision_frame(config, policy, data)
    total_baseline = float(merged["baseline_realized_value"].sum())
    total_engine = float(merged["engine_realized_value"].sum())
    aggregate_lift_pct = ((total_engine - total_baseline) / abs(total_baseline) * 100) if total_baseline else None

    entity_col = config.target.entity_id
    test = data.test.reset_index(drop=True)
    test[entity_col] = test[entity_col].astype(str)
    merged_keyed = merged.copy()
    merged_keyed[entity_col] = merged_keyed[entity_col].astype(str)
    aligned = merged_keyed.merge(
        test[[entity_col] + [c for c in test.columns if c not in merged_keyed.columns]],
        on=entity_col,
        how="left",
    )

    breakdown: dict[str, list[dict]] = {}
    for seg_col in config.segment_columns:
        seg_values = _segment_series(config, aligned, seg_col)
        breakdown[seg_col] = _segment_table(merged, seg_values, aggregate_lift_pct)

    return {
        "aggregate_lift_pct": aggregate_lift_pct,
        "ground_truth_basis": ground_truth_basis,
        "segments": breakdown,
    }


def _run_fold(config: CaseStudyConfig, policy: PolicyConfig, *, frac: float, seed: int) -> dict[str, Any]:
    fold_data = load_case_study_data_fold(config, seed=seed, frac=frac)

    baseline_result_path = REPO_ROOT / "artifacts" / "case_studies" / config.id / "baseline" / "result.json"
    best_hyperparameters = json.loads(baseline_result_path.read_text())["tuning"]["best_hyperparameters"]
    baseline_pred, _features = fit_and_predict_with_hyperparameters(
        config, fold_data.train, fold_data.test, hyperparameters=best_hyperparameters, seed=seed
    )

    db = get_session_factory()()
    try:
        experiment = run_experiment_on_frame(
            db, config, fold_data.frame, seed=seed, dataset_suffix=f"_fold_{int(frac * 100)}"
        )
        engine_pred = score_frame(experiment, fold_data.test, task_type=config.target.task_type)
    finally:
        db.close()

    entity_col = config.target.entity_id
    time_col = config.target.prediction_time_column
    test = fold_data.test.reset_index(drop=True)
    merged = test[[entity_col, time_col]].copy()
    merged["baseline_pred"] = baseline_pred
    merged["engine_pred"] = engine_pred
    merged["y_true"] = test[config.target.target_column].to_numpy()
    if policy.value_column and policy.value_column in test.columns:
        merged[policy.value_column] = test[policy.value_column].to_numpy()
    ground_truth_col = None
    if fold_data.ground_truth is not None:
        gt = fold_data.ground_truth[["entity_id", "true_probability"]].rename(columns={"entity_id": entity_col})
        merged[entity_col] = merged[entity_col].astype(str)
        gt[entity_col] = gt[entity_col].astype(str)
        merged = merged.merge(gt, on=entity_col, how="left")
        ground_truth_col = "true_probability"

    baseline_realized: list[float] = []
    engine_realized: list[float] = []
    baseline_actions: list[str] = []
    engine_actions: list[str] = []
    for _, row in merged.iterrows():
        pred_b = float(row["baseline_pred"])
        pred_e = float(row["engine_pred"])
        if policy.score_basis == "probability":
            entity_val = _entity_value(row, policy)
            value_b, value_e = entity_val, entity_val
        else:
            value_b, value_e = pred_b, pred_e
        b = decide(score=pred_b, entity_value=value_b, policy=policy)
        e = decide(score=pred_e, entity_value=value_e, policy=policy)
        baseline_actions.append(b["action"])
        engine_actions.append(e["action"])
        true_outcome = float(row[ground_truth_col]) if ground_truth_col is not None else float(row["y_true"])
        realized_base_value = _entity_value(row, policy) * true_outcome if policy.score_basis == "probability" else true_outcome
        baseline_realized.append(realized_base_value * policy.action_uplift.get(b["action"], 0.0))
        engine_realized.append(realized_base_value * policy.action_uplift.get(e["action"], 0.0))

    merged["baseline_action"] = baseline_actions
    merged["engine_action"] = engine_actions
    merged["baseline_realized_value"] = baseline_realized
    merged["engine_realized_value"] = engine_realized

    total_baseline = float(merged["baseline_realized_value"].sum())
    total_engine = float(merged["engine_realized_value"].sum())
    lift_pct = ((total_engine - total_baseline) / abs(total_baseline) * 100) if total_baseline else None
    agreement_rate = float((merged["baseline_action"] == merged["engine_action"]).mean())

    return {
        "frac": frac,
        "n_train": len(fold_data.train),
        "n_test": len(fold_data.test),
        "train_window_end": str(fold_data.frame[config.target.prediction_time_column].max()),
        "agreement_rate": agreement_rate,
        "total_baseline_realized_value": total_baseline,
        "total_engine_realized_value": total_engine,
        "lift_rel_pct": lift_pct,
    }


def run_fold_stability(config: CaseStudyConfig, policy: PolicyConfig, *, seed: int) -> dict[str, Any]:
    folds = [_run_fold(config, policy, frac=frac, seed=seed) for frac in FOLD_FRACS]
    lifts = [f["lift_rel_pct"] for f in folds if f["lift_rel_pct"] is not None]
    if lifts:
        signs = {1 if v > 0 else (-1 if v < 0 else 0) for v in lifts}
        sign_consistent = len(signs) == 1
        mean_lift = float(np.mean(lifts))
        std_lift = float(np.std(lifts))
    else:
        sign_consistent = False
        mean_lift = std_lift = None

    if not lifts:
        stability_verdict = "no fold produced a non-zero baseline denominator; lift could not be computed."
    elif sign_consistent and std_lift is not None and abs(std_lift) <= abs(mean_lift) * 0.5 + 1e-9:
        stability_verdict = (
            f"stable: the {'ensemble' if mean_lift > 0 else 'baseline'} advantage holds sign across all "
            f"{len(lifts)} folds (mean lift {mean_lift:+.1f}%, std {std_lift:.1f}pp) — not one lucky split."
        )
    elif sign_consistent:
        stability_verdict = (
            f"directionally stable but noisy: sign is consistent across all {len(lifts)} folds "
            f"(mean lift {mean_lift:+.1f}%) but the spread is large (std {std_lift:.1f}pp)."
        )
    else:
        stability_verdict = (
            f"unstable: which model wins flips sign across folds (lifts: {[f'{v:+.1f}%' for v in lifts]}) — "
            "Step 3's headline number is sensitive to which window it was measured on."
        )

    return {
        "folds": folds,
        "mean_lift_pct": mean_lift,
        "std_lift_pct": std_lift,
        "sign_consistent_across_folds": sign_consistent,
        "stability_verdict": stability_verdict,
    }


def run_segment_comparison(case_study_id: str, *, seed: int | None = None) -> dict[str, Any]:
    config = load_case_study(case_study_id)
    policy = PolicyConfig.from_yaml(POLICY_DIR / f"{case_study_id}.yaml")
    resolved_seed = seed if seed is not None else resolve_seed(config)
    data = load_case_study_data(config, seed=resolved_seed)

    segment_result = run_segment_breakdown(config, policy, data)
    fold_result = run_fold_stability(config, policy, seed=resolved_seed)

    notable_findings = [
        f"segment_columns={seg_col!r}, segment={row['segment']!r} (n={row['n']}): lift {row['lift_rel_pct']:+.1f}% "
        f"vs. aggregate {segment_result['aggregate_lift_pct']:+.1f}% — shrinks by "
        f"{segment_result['aggregate_lift_pct'] - row['lift_rel_pct']:.1f}pp"
        for seg_col, rows in segment_result["segments"].items()
        for row in rows
        if row["shrinks_vs_aggregate_by_ge_10pp"]
    ]

    result: dict[str, Any] = {
        "case_study_id": config.id,
        "segment_breakdown": segment_result,
        "fold_stability": fold_result,
        "notable_segment_findings": notable_findings,
        "plain_language_verdict": (
            f"On {config.id} (aggregate ensemble lift {segment_result['aggregate_lift_pct']:+.1f}%): "
            + (
                f"{len(notable_findings)} segment(s) show the ensemble's advantage shrinking by >= "
                f"{SEGMENT_SHRINK_THRESHOLD_PP:.0f}pp vs. the aggregate — " + "; ".join(notable_findings) + ". "
                if notable_findings
                else "no segment's lift shrinks by >= "
                f"{SEGMENT_SHRINK_THRESHOLD_PP:.0f}pp vs. the aggregate — the headline number is not being "
                "carried by one segment. "
            )
            + f"Fold stability: {fold_result['stability_verdict']}"
        ),
    }

    out_dir = REPO_ROOT / "artifacts" / "case_studies" / config.id / "segment_comparison"
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "result.json").write_text(json.dumps(result, default=str, indent=2) + "\n")
    result["_artifact_dir"] = str(out_dir)
    return result


def main() -> None:
    parser = argparse.ArgumentParser(prog="python -m benchmarks.segment_comparison")
    parser.add_argument("--case-study", required=True)
    parser.add_argument("--seed", type=int, default=None)
    args = parser.parse_args()
    result = run_segment_comparison(args.case_study, seed=args.seed)
    print(json.dumps(result, default=str, indent=2))


if __name__ == "__main__":
    main()
