"""Step 3 — Decision impact comparison. The core of the harness.

For each case study: run the SAME decision policy against the Step 1
baseline's test-set predictions and the Step 2 DCLab engine experiment's
test-set predictions (reproduced from its persisted fitted members, not
retrained), on the identical test split (verified in Step 2), and report:

  1. Decision agreement rate.
  2. Realized value comparison — against the real observed outcome for
     CS1-3 (real Olist data), against the true generating probability for
     CS4-6 (synthetic, ground truth known) rather than the noisy label.
  3. On the disagreement subset only: which model's recommendation would
     have realized more value, how often — a real percentage, not a
     qualitative claim.
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
from benchmarks.case_studies.policy import PolicyConfig, decide
from benchmarks.case_studies.predictions import latest_experiment_for, score_frame
from benchmarks.case_studies.registry import load_case_study
from benchmarks.case_studies.schema import CaseStudyConfig

POLICY_DIR = REPO_ROOT / "configs" / "policies" / "case_studies"


def _entity_value(row: pd.Series, policy: PolicyConfig) -> float:
    if policy.value_column:
        try:
            return float(row[policy.value_column])
        except (TypeError, ValueError, KeyError):
            return 0.0
    return policy.flat_value


def build_decision_frame(
    config: CaseStudyConfig, policy: PolicyConfig, data: Any, *, engine_pred: np.ndarray | None = None
) -> tuple[pd.DataFrame, str]:
    """Merge baseline + engine predictions onto the test split and run the
    policy for both, producing one row per test entity with both models'
    action/expected/realized value. This is the one code path Step 3
    (aggregate) and Step 5 (segment + fold breakdowns) both build on, so a
    segment or fold table can never silently diverge from the aggregate
    number it's supposed to be decomposing.

    ``engine_pred`` lets callers (e.g. a fold rerun) supply predictions
    computed against a different model/split than "the latest persisted
    experiment for this case study" — if omitted, that latest experiment is
    loaded and scored on ``data.test`` exactly as Step 3 does.
    """
    test = data.test.reset_index(drop=True)
    entity_col = config.target.entity_id
    time_col = config.target.prediction_time_column
    target_col = config.target.target_column

    baseline_path = REPO_ROOT / "artifacts" / "case_studies" / config.id / "baseline" / "test_predictions.csv"
    if not baseline_path.exists():
        raise FileNotFoundError(f"no Step 1 baseline predictions for {config.id} at {baseline_path} — run Step 1 first")
    baseline_preds = pd.read_csv(baseline_path)

    if engine_pred is None:
        db = get_session_factory()()
        try:
            experiment = latest_experiment_for(db, config.id)
            engine_pred = score_frame(experiment, test, task_type=config.target.task_type)
        finally:
            db.close()

    merged = test[[entity_col, time_col]].copy()
    merged[time_col] = merged[time_col].astype(str)
    merged["y_true"] = test[target_col].to_numpy()
    merged["engine_pred"] = engine_pred
    baseline_join = baseline_preds[[entity_col, time_col, "y_pred"]].rename(columns={"y_pred": "baseline_pred"})
    baseline_join[time_col] = baseline_join[time_col].astype(str)
    merged = merged.merge(baseline_join, on=[entity_col, time_col], how="inner")
    if len(merged) != len(test):
        raise ValueError(
            f"{config.id}: merge dropped rows ({len(merged)} of {len(test)}) "
            "— baseline and engine test sets diverged, this should be impossible given the Step 2 split check"
        )

    if policy.value_column and policy.value_column not in merged.columns:
        value_join = test[[entity_col, time_col, policy.value_column]].copy()
        value_join[time_col] = value_join[time_col].astype(str)
        merged = merged.merge(value_join, on=[entity_col, time_col], how="left")

    ground_truth_col = None
    if data.ground_truth is not None:
        gt = data.ground_truth[["entity_id", "true_probability"]].rename(columns={"entity_id": entity_col})
        merged = merged.merge(gt, on=entity_col, how="left")
        ground_truth_col = "true_probability"

    baseline_actions: list[str] = []
    engine_actions: list[str] = []
    baseline_expected: list[float] = []
    engine_expected: list[float] = []
    baseline_realized: list[float] = []
    engine_realized: list[float] = []

    for _, row in merged.iterrows():
        pred_b = float(row["baseline_pred"])
        pred_e = float(row["engine_pred"])

        if policy.score_basis == "probability":
            entity_val = _entity_value(row, policy)
            value_b, value_e = entity_val, entity_val
        else:
            # Regression: each model's own predicted value is its own dollar baseline.
            value_b, value_e = pred_b, pred_e

        b = decide(score=pred_b, entity_value=value_b, policy=policy)
        e = decide(score=pred_e, entity_value=value_e, policy=policy)
        baseline_actions.append(b["action"])
        engine_actions.append(e["action"])
        baseline_expected.append(b["expected_value"])
        engine_expected.append(e["expected_value"])

        true_outcome = float(row[ground_truth_col]) if ground_truth_col is not None else float(row["y_true"])
        if policy.score_basis == "probability":
            realized_baseline_value = _entity_value(row, policy) * true_outcome
        else:
            realized_baseline_value = true_outcome
        baseline_realized.append(realized_baseline_value * policy.action_uplift.get(b["action"], 0.0))
        engine_realized.append(realized_baseline_value * policy.action_uplift.get(e["action"], 0.0))

    merged["baseline_action"] = baseline_actions
    merged["engine_action"] = engine_actions
    merged["baseline_expected_value"] = baseline_expected
    merged["engine_expected_value"] = engine_expected
    merged["baseline_realized_value"] = baseline_realized
    merged["engine_realized_value"] = engine_realized

    ground_truth_basis = (
        "true generating probability (synthetic ground truth, never used in training)"
        if ground_truth_col is not None
        else "real observed outcome"
    )
    return merged, ground_truth_basis


def run_decision_impact(case_study_id: str, *, seed: int | None = None) -> dict[str, Any]:
    config = load_case_study(case_study_id)
    policy = PolicyConfig.from_yaml(POLICY_DIR / f"{case_study_id}.yaml")
    resolved_seed = seed if seed is not None else resolve_seed(config)

    data = load_case_study_data(config, seed=resolved_seed)
    merged, ground_truth_basis = build_decision_frame(config, policy, data)

    n = len(merged)
    agree_mask = merged["baseline_action"] == merged["engine_action"]
    agreement_rate = float(agree_mask.mean())

    total_baseline_realized = float(merged["baseline_realized_value"].sum())
    total_engine_realized = float(merged["engine_realized_value"].sum())
    realized_lift_abs = total_engine_realized - total_baseline_realized
    realized_lift_rel = (
        (realized_lift_abs / abs(total_baseline_realized)) if total_baseline_realized else float("nan")
    )

    disagree = merged[~agree_mask].copy()
    n_disagree = len(disagree)
    if n_disagree:
        engine_better = int((disagree["engine_realized_value"] > disagree["baseline_realized_value"]).sum())
        baseline_better = int((disagree["baseline_realized_value"] > disagree["engine_realized_value"]).sum())
        tied = n_disagree - engine_better - baseline_better
        engine_better_rate = engine_better / n_disagree
        baseline_better_rate = baseline_better / n_disagree
        tied_rate = tied / n_disagree
    else:
        engine_better = baseline_better = tied = 0
        engine_better_rate = baseline_better_rate = tied_rate = float("nan")

    if n_disagree:
        verdict = (
            f"On {config.id}, baseline and DCLab ensemble agreed on the recommended action for "
            f"{agreement_rate * 100:.1f}% of the {n} test entities ({n_disagree} disagreed). Valuing each model's "
            f"choice against {ground_truth_basis}, the ensemble produced higher realized value in "
            f"{engine_better_rate * 100:.1f}% of disagreement cases, the baseline was better in "
            f"{baseline_better_rate * 100:.1f}%, and {tied_rate * 100:.1f}% were exactly tied. "
            f"Aggregate realized value across all {n} entities: baseline={total_baseline_realized:,.2f}, "
            f"ensemble={total_engine_realized:,.2f} ({realized_lift_abs:+,.2f}, "
            f"{'n/a' if realized_lift_rel != realized_lift_rel else f'{realized_lift_rel * 100:+.1f}%'})."
        )
    else:
        verdict = (
            f"On {config.id}, baseline and DCLab ensemble agreed on the recommended action for all {n} test "
            "entities — there were no disagreement cases to compare who was more often right."
        )

    result: dict[str, Any] = {
        "case_study_id": config.id,
        "policy_version": policy.version,
        "n_test_entities": n,
        "ground_truth_basis": ground_truth_basis,
        "decision_agreement": {
            "agreement_rate": agreement_rate,
            "disagreement_rate": 1.0 - agreement_rate,
            "n_agree": int(agree_mask.sum()),
            "n_disagree": n_disagree,
            "baseline_action_distribution": merged["baseline_action"].value_counts().to_dict(),
            "engine_action_distribution": merged["engine_action"].value_counts().to_dict(),
        },
        "realized_value": {
            "total_baseline_realized_value": total_baseline_realized,
            "total_engine_realized_value": total_engine_realized,
            "lift_abs": realized_lift_abs,
            "lift_rel_pct": realized_lift_rel * 100 if realized_lift_rel == realized_lift_rel else None,
        },
        "disagreement_analysis": {
            "n_disagree": n_disagree,
            "engine_better_count": engine_better,
            "baseline_better_count": baseline_better,
            "tied_count": tied,
            "engine_better_rate_pct": engine_better_rate * 100 if engine_better_rate == engine_better_rate else None,
            "baseline_better_rate_pct": (
                baseline_better_rate * 100 if baseline_better_rate == baseline_better_rate else None
            ),
            "tied_rate_pct": tied_rate * 100 if tied_rate == tied_rate else None,
        },
        "plain_language_verdict": verdict,
    }

    out_dir = REPO_ROOT / "artifacts" / "case_studies" / config.id / "decision_impact"
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "result.json").write_text(json.dumps(result, default=str, indent=2) + "\n")
    merged.to_csv(out_dir / "per_entity_decisions.csv", index=False)
    result["_artifact_dir"] = str(out_dir)
    return result


def main() -> None:
    parser = argparse.ArgumentParser(prog="python -m benchmarks.decision_impact")
    parser.add_argument("--case-study", required=True)
    parser.add_argument("--seed", type=int, default=None)
    args = parser.parse_args()
    result = run_decision_impact(args.case_study, seed=args.seed)
    print(json.dumps(result, default=str, indent=2))


if __name__ == "__main__":
    main()
