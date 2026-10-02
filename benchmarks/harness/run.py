"""Run the pinned benchmark tasks through the open-ingest engine (no database).

    python -m benchmarks.harness.run --suite quick
    python -m benchmarks.harness.run --suite full --out benchmarks/results/2026-10-01.json

The search configuration is the one auto-train ships (``_search_config``), so the
numbers describe the product, not a tuned research setup. A task that errors is
recorded with its error and the run continues.
"""

from __future__ import annotations

import argparse
import json
import platform
import tempfile
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from benchmarks.harness.tasks import SUITES, BenchmarkTask, find

RESULTS_DIR = Path(__file__).resolve().parents[1] / "results"
TARGET = "target"
SCHEMA_VERSION = 1


def load_task_frame(task: BenchmarkTask, *, seed: int) -> pd.DataFrame:
    """A frame with feature columns plus ``target``, subsampled to ``max_rows``."""
    if task.source == "sklearn":
        import sklearn.datasets as datasets

        bunch = getattr(datasets, task.ref)(as_frame=True)
        frame = bunch.frame.rename(columns={bunch.target.name: TARGET})
    elif task.source == "openml":
        from sklearn.datasets import fetch_openml

        bunch = fetch_openml(data_id=int(task.ref), version=task.version, as_frame=True, parser="auto")
        frame = bunch.data.copy()
        frame[TARGET] = bunch.target
    else:
        raise ValueError(f"unknown task source {task.source!r}")
    frame.columns = [str(column) for column in frame.columns]
    if task.task_type != "regression":
        frame[TARGET] = frame[TARGET].astype(str)
    if len(frame) > task.max_rows:
        frame = frame.sample(n=task.max_rows, random_state=seed).reset_index(drop=True)
    return frame


def run_task(task: BenchmarkTask, *, seed: int) -> dict[str, Any]:
    from app.engine.experiments.runner import run_experiment
    from app.engine.lab.auto_prepare import split_column_roles
    from app.engine.types import TaskSpec
    from app.services.auto_train_service import _search_config

    started = time.perf_counter()
    record: dict[str, Any] = {"task_id": task.id, "task_type": task.task_type, "seed": seed}
    try:
        frame = load_task_frame(task, seed=seed)
        record["rows"] = int(len(frame))
        features = [column for column in frame.columns if column != TARGET]
        numerical, categorical = split_column_roles(frame, features)
        spec = TaskSpec(
            id=f"benchmark_{task.id}",
            name=task.id,
            task_type=task.task_type,
            target=TARGET,
            feature_groups={"features": numerical + categorical},
            column_roles={"numerical": numerical, "categorical": categorical},
        )
        config = _search_config()
        config.seed = seed
        with tempfile.TemporaryDirectory() as tmp:
            result = run_experiment(frame, spec, config, artifact_dir=Path(tmp))
        selection = result.get("selection") or {}
        baseline = result.get("baseline_comparison") or {}
        metric = selection.get("selection_metric") or result.get("metric_plan", {}).get("primary_metric")
        test_metrics = {
            key: value
            for key, value in (result.get("test_metrics") or {}).items()
            if isinstance(value, (int, float)) and not isinstance(value, bool)
        }
        record.update(
            {
                "status": result.get("status"),
                "primary_metric": metric,
                "winner": (result.get("best_single") or {}).get("candidate_id"),
                "cv_score": selection.get("cv_score"),
                "holdout": test_metrics,
                "holdout_primary": test_metrics.get(metric),
                "baseline_cv_score": baseline.get("baseline_cv_score"),
                "dummy_margin": baseline.get("margin"),
                "beats_baseline": baseline.get("beats_baseline"),
                "candidates_trained": sum(row.get("status") == "trained" for row in result.get("candidates") or []),
            }
        )
    except Exception as exc:  # noqa: BLE001 - one broken task never aborts the suite
        record.update({"status": "ERROR", "error": f"{type(exc).__name__}: {exc}"[:500]})
    record["seconds"] = round(time.perf_counter() - started, 3)
    return record


def run_suite(tasks: list[BenchmarkTask], *, seed: int) -> dict[str, Any]:
    import sklearn

    started = time.perf_counter()
    rows = [run_task(task, seed=seed) for task in tasks]
    return {
        "schema_version": SCHEMA_VERSION,
        "created_at": datetime.now(UTC).isoformat(),
        "seed": seed,
        "python": platform.python_version(),
        "sklearn": sklearn.__version__,
        "numpy": np.__version__,
        "total_seconds": round(time.perf_counter() - started, 3),
        "tasks": {row["task_id"]: row for row in rows},
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--suite", choices=sorted(SUITES), default="quick")
    parser.add_argument("--task", action="append", default=[], help="run only these task ids")
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--out", type=Path, default=None)
    args = parser.parse_args(argv)

    tasks = [find(task_id) for task_id in args.task] if args.task else list(SUITES[args.suite])
    results = run_suite(tasks, seed=args.seed)
    out = args.out or RESULTS_DIR / f"{datetime.now(UTC).date().isoformat()}-{args.suite}.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(results, indent=1, sort_keys=True, default=str) + "\n")
    for row in results["tasks"].values():
        print(
            f"{row['task_id']:24} {row.get('status', ''):10} "
            f"{row.get('primary_metric') or '':12} holdout={row.get('holdout_primary')} "
            f"margin={row.get('dummy_margin')} {row['seconds']}s"
            + (f"  {row['error']}" if row.get("error") else "")
        )
    print(f"wrote {out}")
    return 1 if any(row.get("status") == "ERROR" for row in results["tasks"].values()) else 0


if __name__ == "__main__":
    raise SystemExit(main())
