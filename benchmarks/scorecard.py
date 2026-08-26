"""Step 6 — Unified case study scorecard.

Reads the artifacts already produced by Steps 1-5 (nothing here re-runs a
model or re-derives a number — this module only aggregates and renders) and
emits one markdown table across all six case studies with:

  - ML metric delta (Step 1 baseline vs. Step 2 engine, on the case study's
    own declared evaluation metric, held-out test set)
  - Decision-impact delta (Step 3: single-split aggregate realized-value lift)
  - Fold-mean decision-impact delta (Step 5: mean lift across 4 walk-forward
    folds, plus whether the sign is even consistent across those folds)
  - Calibration delta (Step 4: Brier-against-true-probability relative
    improvement — CS4-6 only, "n/a" for real-data case studies which have no
    ground truth)
  - A final verdict, exactly one of three categories, computed from a
    stated, fixed rule (see ``compute_verdict``) — never a subjective read
    of the numbers.

Verdict rule (the ONE rule Step 6 exists to make explicit and code-enforced):
  1. If the Step 5 fold-to-fold lift sign is NOT consistent across all 4
     walk-forward folds, the verdict is "no meaningful difference" —
     regardless of how large Step 3's single-split number looked. An
     advantage that flips sign depending on which time window you measure
     it on is not evidence of anything.
  2. Otherwise, the verdict is driven by the MEAN fold lift (the robust
     number, not the single Step 3 split):
       mean fold lift >= +DECISION_IMPACT_MEANINGFUL_PCT  -> "ensemble meaningfully better"
       mean fold lift <= -DECISION_IMPACT_MEANINGFUL_PCT  -> "baseline preferable"
       otherwise                                          -> "no meaningful difference"

This rule is symmetric by construction: nothing about the code path treats
"ensemble wins" and "baseline wins" differently, so both — and the neutral
outcome — are all genuinely reachable outputs, not just "ensemble wins" or
"tie."
"""

from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from app.config import REPO_ROOT
from app.db.session import get_session_factory
from app.engine.evaluation.metrics import primary_score

from benchmarks.case_studies.predictions import latest_experiment_for
from benchmarks.case_studies.registry import CASE_STUDY_IDS, load_case_study
from benchmarks.case_studies.schema import CaseStudyConfig

ARTIFACT_DIR = REPO_ROOT / "artifacts" / "case_studies"

# The one number this whole scorecard's verdict column turns on. 10 
# percentage points of aggregate realized-value lift, sustained (same sign)
# across all 4 Step 5 walk-forward folds, is required to call either model
# "meaningfully better." Anything smaller, or inconsistent across folds,
# reports as "no meaningful difference" rather than rounding up to a winner.
DECISION_IMPACT_MEANINGFUL_PCT = 10.0

VERDICT_ENSEMBLE_BETTER = "ensemble meaningfully better"
VERDICT_NO_DIFFERENCE = "no meaningful difference"
VERDICT_BASELINE_PREFERABLE = "baseline preferable"


def _read_json(path: Path) -> dict[str, Any] | None:
    if not path.exists():
        return None
    return json.loads(path.read_text())


def ml_metric_delta(config: CaseStudyConfig) -> dict[str, Any]:
    baseline_result = _read_json(ARTIFACT_DIR / config.id / "baseline" / "result.json")
    if baseline_result is None:
        raise FileNotFoundError(f"{config.id}: missing Step 1 baseline artifact — run Step 1 first")
    baseline_metrics = baseline_result["test_metrics"]

    db = get_session_factory()()
    try:
        experiment = latest_experiment_for(db, config.id)
        engine_metrics = (experiment.result or {}).get("test_metrics") or {}
    finally:
        db.close()

    metric = config.target.evaluation_metric
    task_type = config.target.task_type
    baseline_score = primary_score(baseline_metrics, metric, task_type)
    engine_score = primary_score(engine_metrics, metric, task_type)
    delta_pct = ((engine_score - baseline_score) / abs(baseline_score) * 100) if baseline_score else None
    return {
        "metric": metric,
        "baseline_value": baseline_metrics.get(metric),
        "engine_value": engine_metrics.get(metric),
        "baseline_primary_score": baseline_score,
        "engine_primary_score": engine_score,
        "delta_pct": delta_pct,
    }


def decision_impact_delta(config: CaseStudyConfig) -> dict[str, Any]:
    result = _read_json(ARTIFACT_DIR / config.id / "decision_impact" / "result.json")
    if result is None:
        raise FileNotFoundError(f"{config.id}: missing Step 3 decision_impact artifact — run Step 3 first")
    return {
        "lift_rel_pct": result["realized_value"]["lift_rel_pct"],
        "agreement_rate": result["decision_agreement"]["agreement_rate"],
        "engine_better_rate_pct": result["disagreement_analysis"]["engine_better_rate_pct"],
        "baseline_better_rate_pct": result["disagreement_analysis"]["baseline_better_rate_pct"],
    }


def calibration_delta(config: CaseStudyConfig) -> dict[str, Any] | None:
    if config.data_source.kind != "synthetic":
        return None
    result = _read_json(ARTIFACT_DIR / config.id / "calibration" / "result.json")
    if result is None:
        raise FileNotFoundError(f"{config.id}: missing Step 4 calibration artifact — run Step 4 first")
    return {
        "brier_relative_improvement_pct": result["brier_relative_improvement_pct"],
        "verdict": result["verdict"],
    }


def robustness_delta(config: CaseStudyConfig) -> dict[str, Any]:
    result = _read_json(ARTIFACT_DIR / config.id / "segment_comparison" / "result.json")
    if result is None:
        raise FileNotFoundError(f"{config.id}: missing Step 5 segment_comparison artifact — run Step 5 first")
    fold = result["fold_stability"]
    return {
        "mean_fold_lift_pct": fold["mean_lift_pct"],
        "std_fold_lift_pct": fold["std_lift_pct"],
        "sign_consistent_across_folds": fold["sign_consistent_across_folds"],
        "fold_lifts_pct": [f["lift_rel_pct"] for f in fold["folds"]],
        "n_segments_shrinking_ge_10pp": len(result["notable_segment_findings"]),
    }


def compute_verdict(robustness: dict[str, Any]) -> tuple[str, str]:
    mean_lift = robustness["mean_fold_lift_pct"]
    sign_consistent = robustness["sign_consistent_across_folds"]
    fold_lifts = robustness["fold_lifts_pct"]

    if mean_lift is None:
        return VERDICT_NO_DIFFERENCE, "fold lift could not be computed (zero baseline denominator in every fold)"

    if not sign_consistent:
        return (
            VERDICT_NO_DIFFERENCE,
            f"fold-to-fold lift sign is not consistent across the 4 walk-forward folds "
            f"({[f'{v:+.1f}%' for v in fold_lifts]}) — an advantage that flips sign depending on which "
            "window it's measured on is not treated as real, regardless of Step 3's single-split number",
        )

    if mean_lift >= DECISION_IMPACT_MEANINGFUL_PCT:
        return (
            VERDICT_ENSEMBLE_BETTER,
            f"mean fold lift {mean_lift:+.1f}% >= +{DECISION_IMPACT_MEANINGFUL_PCT:.0f}% threshold, "
            f"consistent sign across all 4 folds ({[f'{v:+.1f}%' for v in fold_lifts]})",
        )
    if mean_lift <= -DECISION_IMPACT_MEANINGFUL_PCT:
        return (
            VERDICT_BASELINE_PREFERABLE,
            f"mean fold lift {mean_lift:+.1f}% <= -{DECISION_IMPACT_MEANINGFUL_PCT:.0f}% threshold, "
            f"consistent sign across all 4 folds ({[f'{v:+.1f}%' for v in fold_lifts]})",
        )
    return (
        VERDICT_NO_DIFFERENCE,
        f"mean fold lift {mean_lift:+.1f}% is within +/-{DECISION_IMPACT_MEANINGFUL_PCT:.0f}% "
        f"threshold ({[f'{v:+.1f}%' for v in fold_lifts]})",
    )


def build_row(case_study_id: str) -> dict[str, Any]:
    config = load_case_study(case_study_id)
    ml = ml_metric_delta(config)
    decision = decision_impact_delta(config)
    calibration = calibration_delta(config)
    robustness = robustness_delta(config)
    verdict, verdict_reason = compute_verdict(robustness)
    return {
        "case_study_id": config.id,
        "name": config.name,
        "data_source_kind": config.data_source.kind,
        "honesty_note": config.honesty_note,
        "ml_metric": ml,
        "decision_impact": decision,
        "calibration": calibration,
        "robustness": robustness,
        "verdict": verdict,
        "verdict_reason": verdict_reason,
    }


def _fmt_pct(value: float | None) -> str:
    return "n/a" if value is None else f"{value:+.1f}%"


def render_markdown(rows: list[dict[str, Any]]) -> str:
    lines: list[str] = []
    lines.append("# DCLab Case Study Benchmark — Unified Scorecard")
    lines.append("")
    lines.append(f"_Generated {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}_")
    lines.append("")
    lines.append(
        "One consolidated comparison across all 6 case studies: a single competitively-tuned baseline model "
        "(Step 1) vs. DCLab's full multi-model search + ensemble (Step 2), on ML metrics, actual business "
        "decisions (Step 3), calibration against ground truth where it exists (Step 4), and stability across "
        "segments and walk-forward folds (Step 5)."
    )
    lines.append("")
    lines.append("## Verdict methodology (stated thresholds, not a subjective read)")
    lines.append("")
    lines.append(
        f"1. If the sign of the realized-value lift is **not consistent** across all 4 of Step 5's walk-forward "
        f"folds, the verdict is **\"{VERDICT_NO_DIFFERENCE}\"** regardless of Step 3's single-split number — a "
        "result that flips depending on which time window you measure is not treated as a real advantage."
    )
    lines.append(
        f"2. Otherwise, the verdict is driven by the **mean lift across the 4 folds** (the robust number, not "
        f"the single Step 3 split): >= **+{DECISION_IMPACT_MEANINGFUL_PCT:.0f}%** -> "
        f"\"{VERDICT_ENSEMBLE_BETTER}\"; <= **-{DECISION_IMPACT_MEANINGFUL_PCT:.0f}%** -> "
        f"\"{VERDICT_BASELINE_PREFERABLE}\"; otherwise -> \"{VERDICT_NO_DIFFERENCE}\"."
    )
    lines.append("")
    lines.append(
        "This rule is symmetric — nothing in the code favors one outcome — so all three verdicts are reachable; "
        "which one actually appears below is determined only by what the numbers show."
    )
    lines.append("")

    header = [
        "Case Study",
        "Data",
        "ML Metric Δ",
        "Decision-Impact Δ (Step 3, single split)",
        "Fold-Mean Decision-Impact Δ (Step 5)",
        "Fold Sign Consistent?",
        "Calibration Δ (Brier vs. truth)",
        "Segments Shrinking ≥10pp",
        "Verdict",
    ]
    lines.append("| " + " | ".join(header) + " |")
    lines.append("|" + "|".join(["---"] * len(header)) + "|")
    for row in rows:
        ml = row["ml_metric"]
        decision = row["decision_impact"]
        robustness = row["robustness"]
        calibration = row["calibration"]
        cal_cell = (
            "n/a (real data, no ground truth)"
            if calibration is None
            else f"{_fmt_pct(calibration['brier_relative_improvement_pct'])} ({calibration['verdict']})"
        )
        name = row["name"] + (" *(proxy — see note)*" if row["honesty_note"] else "")
        lines.append(
            "| "
            + " | ".join(
                [
                    name,
                    row["data_source_kind"],
                    f"{_fmt_pct(ml['delta_pct'])} ({ml['metric']})",
                    _fmt_pct(decision["lift_rel_pct"]),
                    _fmt_pct(robustness["mean_fold_lift_pct"]),
                    "yes" if robustness["sign_consistent_across_folds"] else "no",
                    cal_cell,
                    str(robustness["n_segments_shrinking_ge_10pp"]),
                    f"**{row['verdict']}**",
                ]
            )
            + " |"
        )
    lines.append("")

    lines.append("## Per-case-study detail")
    lines.append("")
    for row in rows:
        lines.append(f"### {row['name']} (`{row['case_study_id']}`)")
        lines.append("")
        if row["honesty_note"]:
            lines.append(f"> **Honesty note:** {row['honesty_note'].strip()}")
            lines.append("")
        lines.append(f"- **Verdict:** {row['verdict']} — {row['verdict_reason']}")
        ml = row["ml_metric"]
        lines.append(
            f"- **ML metric ({ml['metric']}):** baseline={ml['baseline_value']}, engine={ml['engine_value']} "
            f"({_fmt_pct(ml['delta_pct'])} relative, higher-is-better primary score basis)"
        )
        decision = row["decision_impact"]
        lines.append(
            f"- **Decision impact (Step 3, single split):** lift={_fmt_pct(decision['lift_rel_pct'])}, "
            f"agreement rate={decision['agreement_rate'] * 100:.1f}%, "
            f"ensemble better in {decision['engine_better_rate_pct']:.1f}% of disagreements, "
            f"baseline better in {decision['baseline_better_rate_pct']:.1f}%"
        )
        robustness = row["robustness"]
        lines.append(
            f"- **Robustness (Step 5):** fold lifts={[f'{v:+.1f}%' for v in robustness['fold_lifts_pct']]}, "
            f"mean={_fmt_pct(robustness['mean_fold_lift_pct'])}, std={robustness['std_fold_lift_pct']:.1f}pp, "
            f"sign consistent={robustness['sign_consistent_across_folds']}, "
            f"{robustness['n_segments_shrinking_ge_10pp']} segment(s) shrink the aggregate lift by >=10pp"
        )
        if row["calibration"] is not None:
            lines.append(
                f"- **Calibration vs. true probability (Step 4):** "
                f"{_fmt_pct(row['calibration']['brier_relative_improvement_pct'])} relative Brier improvement "
                f"from ensemble — {row['calibration']['verdict']}"
            )
        lines.append("")

    verdict_counts: dict[str, int] = {}
    for row in rows:
        verdict_counts[row["verdict"]] = verdict_counts.get(row["verdict"], 0) + 1
    lines.append("## Summary")
    lines.append("")
    for v in (VERDICT_ENSEMBLE_BETTER, VERDICT_NO_DIFFERENCE, VERDICT_BASELINE_PREFERABLE):
        lines.append(f"- **{v}:** {verdict_counts.get(v, 0)} of {len(rows)} case studies")
    lines.append("")

    return "\n".join(lines)


def run_scorecard(out_path: Path | None = None) -> dict[str, Any]:
    rows = [build_row(case_study_id) for case_study_id in CASE_STUDY_IDS]
    markdown = render_markdown(rows)
    if out_path is not None:
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(markdown + "\n")
    return {"rows": rows, "markdown": markdown, "out_path": str(out_path) if out_path else None}


def main() -> None:
    parser = argparse.ArgumentParser(prog="python -m benchmarks.scorecard")
    parser.add_argument("--out", default="reports/case_study_scorecard.md", help="markdown output path")
    args = parser.parse_args()
    out_path = REPO_ROOT / args.out if not Path(args.out).is_absolute() else Path(args.out)
    result = run_scorecard(out_path)
    print(f"scorecard written to {result['out_path']}")
    counts: dict[str, int] = {}
    for row in result["rows"]:
        counts[row["verdict"]] = counts.get(row["verdict"], 0) + 1
    print(json.dumps(counts, indent=2))


if __name__ == "__main__":
    main()
