from app.engine.datasets.synthetic import SYNTHETIC_GROUPS
from app.engine.search.generator import assemble_candidates
from app.engine.types import SearchConfig, TaskSpec


def _task() -> TaskSpec:
    return TaskSpec(
        id="purchase_prediction",
        name="Purchase",
        task_type="binary",
        target="purchase_within_60d",
        feature_groups=SYNTHETIC_GROUPS,
        validation_strategy="time",
    )


def test_progressive_search_includes_baselines_and_trees():
    candidates = assemble_candidates(
        _task(),
        SearchConfig(strategy="progressive", max_candidates=12, max_feature_group_combinations=8, seed=1),
    )
    families = {row.model_family for row in candidates}
    assert "majority" in families
    assert "logistic_regression" in families
    assert "random_forest" in families or "gradient_boosting" in families
    assert any(len(row.feature_groups) > 1 for row in candidates)


def test_candidate_cap_is_respected():
    candidates = assemble_candidates(
        _task(),
        SearchConfig(max_candidates=5, max_feature_group_combinations=16, seed=2),
    )
    assert len(candidates) <= 5
