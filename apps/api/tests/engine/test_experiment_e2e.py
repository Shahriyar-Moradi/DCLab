from app.engine.datasets.synthetic import SYNTHETIC_GROUPS, make_synthetic_customers
from app.engine.experiments.runner import run_experiment
from app.engine.types import SearchConfig, TaskSpec


def _task(task_type: str, target: str, metric: str) -> TaskSpec:
    return TaskSpec(
        id="t",
        name="test",
        task_type=task_type,
        target=target,
        entity_id="entity_id",
        prediction_time_column="as_of_date",
        evaluation_metric=metric,
        feature_groups=SYNTHETIC_GROUPS,
        validation_strategy="time",
    )


def test_end_to_end_synthetic_purchase(tmp_path):
    frame = make_synthetic_customers(n=800, seed=11)
    result = run_experiment(
        frame,
        _task("binary", "purchase_within_60d", "pr_auc"),
        SearchConfig(max_candidates=12, max_feature_group_combinations=8, n_robustness_folds=2, seed=11),
        artifact_dir=tmp_path,
    )
    assert result["status"] == "COMPLETED"
    assert result["funnel"]["generated"] >= 3
    assert result["funnel"]["trained"] >= 1
    assert result["funnel"]["failed"] == 0 or result["funnel"]["trained"] > result["funnel"]["failed"]
    assert result["test_metrics"]["pr_auc"] > 0.55
    assert (tmp_path / "report.md").exists()
    assert result["best_single"]["model_family"] != "majority"


def test_reproducible_seed(tmp_path):
    frame = make_synthetic_customers(n=600, seed=21)
    cfg = SearchConfig(max_candidates=8, max_feature_group_combinations=6, n_robustness_folds=2, seed=21)
    a = run_experiment(frame, _task("binary", "purchase_within_60d", "pr_auc"), cfg, artifact_dir=tmp_path / "a")
    b = run_experiment(frame, _task("binary", "purchase_within_60d", "pr_auc"), cfg, artifact_dir=tmp_path / "b")
    assert abs(a["test_metrics"]["pr_auc"] - b["test_metrics"]["pr_auc"]) < 1e-9


def test_identical_rerun_is_deterministic(tmp_path):
    """The legacy member cache was removed with the legacy runner (P1.2-A);
    the guarantee that matters is that an identical run reproduces exactly."""
    frame = make_synthetic_customers(n=400, seed=9)
    cfg = SearchConfig(max_candidates=6, max_feature_group_combinations=4, n_robustness_folds=2, seed=9)
    first = run_experiment(frame, _task("binary", "purchase_within_60d", "pr_auc"), cfg, artifact_dir=tmp_path / "a")
    second = run_experiment(frame, _task("binary", "purchase_within_60d", "pr_auc"), cfg, artifact_dir=tmp_path / "b")
    assert first["config"]["strategy"] == "open_ingest"
    assert abs(first["test_metrics"]["pr_auc"] - second["test_metrics"]["pr_auc"]) < 1e-9


def test_regression_revenue(tmp_path):
    frame = make_synthetic_customers(n=600, seed=31)
    result = run_experiment(
        frame,
        _task("regression", "revenue_60d", "mae"),
        SearchConfig(max_candidates=8, max_feature_group_combinations=6, n_robustness_folds=2, seed=31),
        artifact_dir=tmp_path,
    )
    assert result["status"] == "COMPLETED"
    assert "mae" in result["test_metrics"]


def _snapshot_panel(n_entities: int = 60, dates: int = 6, seed: int = 4):
    import numpy as np
    import pandas as pd

    rng = np.random.default_rng(seed)
    rows = []
    for d in range(dates):
        as_of = pd.Timestamp("2026-01-01") + pd.DateOffset(months=d)
        for e in range(n_entities):
            spend = rng.gamma(2.0, 30.0)
            visits = rng.integers(0, 12)
            rows.append({
                "entity_id": f"c{e}",
                "as_of_date": as_of.strftime("%Y-%m-%d"),
                "spend": spend,
                "visits": visits,
                "segment": rng.choice(["a", "b", "c"]),
                "purchase_within_60d": int(spend > 60 or rng.random() < 0.1),
            })
    return pd.DataFrame(rows)


def test_declared_time_task_with_few_snapshots_keeps_a_temporal_holdout(tmp_path):
    """Review finding (P1.2-A): legacy "time" tasks must not silently fall back to
    a random split when there are fewer than 8 snapshot dates; panel data
    (repeated entities across dates) must still run."""
    import pandas as pd

    frame = _snapshot_panel()
    result = run_experiment(
        frame,
        _task("binary", "purchase_within_60d", "pr_auc"),
        SearchConfig(max_candidates=6, seed=5),
        artifact_dir=tmp_path,
    )
    assert result["status"] == "COMPLETED"
    plan = result["holdout_plan"]
    assert plan["strategy"] == "temporal_future"
    assert plan["time_column"] == "as_of_date"
    predictions = pd.read_csv(tmp_path / "test_predictions.csv")
    test_rows = set(predictions["source_row_index"].astype(int))
    test_dates = frame.loc[sorted(test_rows), "as_of_date"]
    train_dates = frame.loc[[i for i in frame.index if i not in test_rows], "as_of_date"]
    # Strict chronology: every holdout row is later than every training row.
    assert test_dates.min() > train_dates.max()


def test_declared_time_task_with_one_snapshot_fails_loudly(tmp_path):
    import pytest

    frame = _snapshot_panel(dates=1)
    with pytest.raises(ValueError, match="refusing a random fallback"):
        run_experiment(
            frame,
            _task("binary", "purchase_within_60d", "pr_auc"),
            SearchConfig(max_candidates=4, seed=5),
            artifact_dir=tmp_path,
        )


def test_normalization_leaves_column_roles_to_the_train_partition():
    from app.engine.experiments.runner import _normalize_to_open_ingest

    frame = _snapshot_panel()
    task, config = _normalize_to_open_ingest(
        frame, _task("binary", "purchase_within_60d", "pr_auc"), SearchConfig()
    )
    assert config.strategy == "open_ingest"
    assert task.column_roles == {}
    assert "entity_id" not in task.feature_groups["features"]
    assert "as_of_date" not in task.feature_groups["features"]
    assert "purchase_within_60d" not in task.feature_groups["features"]
