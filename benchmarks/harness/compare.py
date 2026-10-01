"""Compare a benchmark run with the last accepted baseline (R1-A).

    python -m benchmarks.harness.compare benchmarks/results/<run>.json
    python -m benchmarks.harness.compare benchmarks/results/<run>.json --accept

A task regresses when it errors, no longer completes, stops beating the dummy
baseline, or its holdout primary metric moves the wrong way by more than its
tolerance. ``--accept`` merges the run's tasks into the baseline (a reviewed,
committed decision). Tasks absent from the baseline are reported as new.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

BASELINE = Path(__file__).resolve().parents[1] / "results" / "baseline.json"
# Absolute tolerance for bounded higher-is-better scores (AUC, F1, R²...).
DEFAULT_ABS_TOLERANCE = 0.03
# Relative tolerance for error metrics (MAE, RMSE, log-loss...).
DEFAULT_REL_TOLERANCE = 0.10


def _lower_is_better(metric: str | None) -> bool:
    from app.engine.evaluation.metrics import LOWER_IS_BETTER

    return str(metric or "") in LOWER_IS_BETTER


def compare(results: dict[str, Any], baseline: dict[str, Any]) -> dict[str, list[dict[str, Any]]]:
    report: dict[str, list[dict[str, Any]]] = {"regressions": [], "ok": [], "new": []}
    tasks = results.get("tasks") or {}
    for task_id, base in sorted((baseline.get("tasks") or {}).items()):
        row = tasks.get(task_id)
        if row is None:
            continue  # not part of this run (e.g. the quick suite)
        problem = None
        if row.get("status") == "ERROR":
            problem = f"errored: {row.get('error')}"
        elif row.get("status") != "COMPLETED":
            problem = f"status {row.get('status')!r}"
        elif base.get("beats_baseline") and row.get("beats_baseline") is False:
            problem = "no longer beats the dummy baseline"
        elif row.get("primary_metric") != base.get("primary_metric"):
            problem = f"primary metric changed {base.get('primary_metric')!r} -> {row.get('primary_metric')!r}"
        else:
            new, old = row.get("holdout_primary"), base.get("holdout_primary")
            if isinstance(new, (int, float)) and isinstance(old, (int, float)):
                if _lower_is_better(row.get("primary_metric")):
                    tolerance = float(base.get("rel_tolerance", DEFAULT_REL_TOLERANCE))
                    if new > old * (1.0 + tolerance) + 1e-12:
                        problem = f"{row['primary_metric']} {old:.4g} -> {new:.4g} (> {tolerance:.0%} worse)"
                else:
                    tolerance = float(base.get("abs_tolerance", DEFAULT_ABS_TOLERANCE))
                    if new < old - tolerance:
                        problem = f"{row['primary_metric']} {old:.4g} -> {new:.4g} (> {tolerance} worse)"
            elif old is not None:
                problem = "holdout primary metric missing"
        entry = {"task_id": task_id, "baseline": base.get("holdout_primary"), "current": row.get("holdout_primary")}
        if problem:
            report["regressions"].append({**entry, "problem": problem})
        else:
            report["ok"].append(entry)
    for task_id in sorted(set(tasks) - set(baseline.get("tasks") or {})):
        report["new"].append({"task_id": task_id, "current": tasks[task_id].get("holdout_primary")})
    return report


def accept(results: dict[str, Any], baseline: dict[str, Any]) -> dict[str, Any]:
    merged = dict(baseline.get("tasks") or {})
    for task_id, row in (results.get("tasks") or {}).items():
        if row.get("status") != "COMPLETED":
            continue  # never accept a broken task as the reference
        keep = {key: merged.get(task_id, {}).get(key) for key in ("abs_tolerance", "rel_tolerance")}
        merged[task_id] = {
            key: row.get(key)
            for key in ("task_type", "primary_metric", "holdout_primary", "cv_score", "dummy_margin", "beats_baseline", "rows", "seconds")
        } | {key: value for key, value in keep.items() if value is not None}
    return {
        "schema_version": results.get("schema_version"),
        "accepted_from": results.get("created_at"),
        "environment": {key: results.get(key) for key in ("python", "sklearn", "numpy", "seed")},
        "tasks": dict(sorted(merged.items())),
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("results", type=Path)
    parser.add_argument("--baseline", type=Path, default=BASELINE)
    parser.add_argument("--accept", action="store_true", help="merge completed tasks into the baseline")
    args = parser.parse_args(argv)

    results = json.loads(args.results.read_text())
    baseline = json.loads(args.baseline.read_text()) if args.baseline.exists() else {"tasks": {}}
    if args.accept:
        args.baseline.write_text(json.dumps(accept(results, baseline), indent=1, sort_keys=True) + "\n")
        print(f"accepted {args.results} into {args.baseline}")
        return 0
    report = compare(results, baseline)
    for entry in report["ok"]:
        print(f"ok          {entry['task_id']:24} {entry['baseline']} -> {entry['current']}")
    for entry in report["new"]:
        print(f"new         {entry['task_id']:24} {entry['current']} (not in baseline)")
    for entry in report["regressions"]:
        print(f"REGRESSION  {entry['task_id']:24} {entry['problem']}")
    return 1 if report["regressions"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
