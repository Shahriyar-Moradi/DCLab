"""R1-A: benchmark harness regression check (pure) and a bundled-task smoke run."""

from __future__ import annotations

from benchmarks.harness import compare as cmp
from benchmarks.harness.tasks import FULL, QUICK, find


def _run(**tasks):
    return {"tasks": {task_id: {"status": "COMPLETED", **row} for task_id, row in tasks.items()}}


def test_task_ids_are_unique_and_quick_needs_no_download():
    ids = [task.id for task in FULL]
    assert len(ids) == len(set(ids)) and 20 <= len(FULL) <= 30
    assert all(task.source == "sklearn" for task in QUICK)
    assert {task.task_type for task in FULL} == {"binary", "multiclass", "regression"}
    assert find("sk-iris").task_type == "multiclass"


def test_higher_is_better_regression_respects_tolerance():
    baseline = {"tasks": {"t": {"primary_metric": "pr_auc", "holdout_primary": 0.90, "beats_baseline": True}}}
    ok = cmp.compare(_run(t={"primary_metric": "pr_auc", "holdout_primary": 0.88, "beats_baseline": True}), baseline)
    assert not ok["regressions"]
    bad = cmp.compare(_run(t={"primary_metric": "pr_auc", "holdout_primary": 0.80, "beats_baseline": True}), baseline)
    assert bad["regressions"] and "pr_auc" in bad["regressions"][0]["problem"]


def test_lower_is_better_uses_relative_tolerance():
    baseline = {"tasks": {"t": {"primary_metric": "mae", "holdout_primary": 10.0}}}
    assert not cmp.compare(_run(t={"primary_metric": "mae", "holdout_primary": 10.9}), baseline)["regressions"]
    assert cmp.compare(_run(t={"primary_metric": "mae", "holdout_primary": 11.5}), baseline)["regressions"]


def test_errors_lost_signal_and_metric_changes_are_regressions():
    baseline = {"tasks": {"t": {"primary_metric": "pr_auc", "holdout_primary": 0.9, "beats_baseline": True}}}
    assert cmp.compare({"tasks": {"t": {"status": "ERROR", "error": "boom"}}}, baseline)["regressions"]
    assert cmp.compare(_run(t={"primary_metric": "pr_auc", "holdout_primary": 0.95, "beats_baseline": False}), baseline)["regressions"]
    assert cmp.compare(_run(t={"primary_metric": "roc_auc", "holdout_primary": 0.95}), baseline)["regressions"]
    assert cmp.compare(_run(u={"holdout_primary": 0.5}), baseline)["new"][0]["task_id"] == "u"


def test_accept_keeps_tolerances_and_skips_broken_tasks():
    baseline = {"tasks": {"t": {"holdout_primary": 0.8, "abs_tolerance": 0.05}}}
    results = {
        "tasks": {
            "t": {"status": "COMPLETED", "primary_metric": "pr_auc", "holdout_primary": 0.85},
            "broken": {"status": "ERROR", "error": "x"},
        }
    }
    merged = cmp.accept(results, baseline)
    assert merged["tasks"]["t"]["holdout_primary"] == 0.85
    assert merged["tasks"]["t"]["abs_tolerance"] == 0.05
    assert "broken" not in merged["tasks"]


def test_bundled_task_runs_end_to_end_without_a_database():
    from benchmarks.harness.run import run_task

    row = run_task(find("sk-iris"), seed=42)
    assert row["status"] == "COMPLETED", row.get("error")
    assert row["primary_metric"] == "macro_f1"
    assert row["holdout_primary"] > 0.8 and row["beats_baseline"] is True
