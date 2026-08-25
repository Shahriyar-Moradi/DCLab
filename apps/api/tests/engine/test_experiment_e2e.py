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


def test_fingerprint_cache_skips_retrain(tmp_path):
    frame = make_synthetic_customers(n=400, seed=9)
    cfg = SearchConfig(max_candidates=6, max_feature_group_combinations=4, n_robustness_folds=2, seed=9)
    first = run_experiment(frame, _task("binary", "purchase_within_60d", "pr_auc"), cfg, artifact_dir=tmp_path)
    second = run_experiment(frame, _task("binary", "purchase_within_60d", "pr_auc"), cfg, artifact_dir=tmp_path)
    assert second["funnel"]["cache_hits"] >= 1
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
