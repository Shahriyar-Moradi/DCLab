"""P1.4-A2: multiclass classification end to end through the open-ingest runner."""

from __future__ import annotations

import tempfile
from pathlib import Path

import numpy as np
import pandas as pd

from app.engine.evaluation.metrics import multiclass_metrics
from app.engine.experiments.runner import run_experiment
from app.engine.lab.auto_prepare import split_column_roles
from app.engine.modeling.holdout_planner import RANDOM, STRATIFIED_RANDOM, plan_holdout
from app.engine.modeling.metric_planner import plan_metrics
from app.engine.modeling.problem_profile import build_problem_profile
from app.engine.models.registry import ContiguousLabelClassifier, available_families, make_model
from app.engine.search.generator import balanced_variants
from app.engine.types import SearchConfig, TaskSpec

SPECIES = ("bristle", "fern", "moss")


def _flowers(n: int = 240, seed: int = 11, weights=(1 / 3, 1 / 3, 1 / 3)) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    label = rng.choice(len(SPECIES), size=n, p=list(weights))
    centers = np.array([[1.0, 0.5], [3.0, 1.5], [5.0, 2.5]])
    xy = centers[label] + rng.normal(0, 0.45, size=(n, 2))
    soil = np.where(label == 2, rng.choice(["clay", "loam"], n), rng.choice(["sand", "loam"], n))
    return pd.DataFrame(
        {
            "petal_len": xy[:, 0],
            "petal_wid": xy[:, 1],
            "noise": rng.normal(size=n),
            "soil": soil,
            "species": [SPECIES[i] for i in label],
        }
    )


def _task(frame: pd.DataFrame, target: str = "species") -> TaskSpec:
    columns = [c for c in frame.columns if c != target]
    num_cols, cat_cols = split_column_roles(frame, columns)
    return TaskSpec(
        id="multiclass_test",
        name="multiclass",
        task_type="multiclass",
        target=target,
        entity_id=None,
        prediction_time_column=None,
        evaluation_metric="macro_f1",
        feature_groups={"features": num_cols + cat_cols},
        validation_strategy="stratified",
        column_roles={"numerical": num_cols, "categorical": cat_cols},
    )


def _run(frame: pd.DataFrame, target: str = "species") -> dict:
    config = SearchConfig(strategy="open_ingest", max_candidates=8, seed=42)
    with tempfile.TemporaryDirectory() as tmp:
        return run_experiment(frame, _task(frame, target), config, artifact_dir=Path(tmp))


def test_multiclass_run_completes_with_macro_f1_and_original_labels():
    result = _run(_flowers())

    assert result["status"] == "COMPLETED"
    assert result["class_labels"] == list(SPECIES)
    assert result["metric_plan"]["primary_metric"] == "macro_f1"
    assert result["validation_plan"]["strategy"] == "StratifiedKFold"
    assert result["holdout_plan"]["strategy"] == STRATIFIED_RANDOM

    trained = [row for row in result["candidates"] if row["status"] == "trained"]
    assert {row["model_family"] for row in trained} >= {"majority", "logistic_regression"}
    assert not [row for row in result["candidates"] if row["status"] == "FAILED"]
    assert result["baseline_comparison"]["beats_baseline"] is True
    assert result["best_single"]["model_family"] != "majority"

    test_metrics = result["test_metrics"]
    for key in ("macro_f1", "balanced_accuracy", "log_loss", "roc_auc_ovr", "accuracy"):
        assert key in test_metrics
    assert test_metrics["macro_f1"] > 0.8
    assert len(test_metrics["confusion_matrix"]) == 3

    rows = result["test_predictions"]
    assert rows and {row["y_true"] for row in rows} <= set(SPECIES)
    assert {row["y_pred"] for row in rows} <= set(SPECIES)
    assert all(0.0 <= row["probability"] <= 1.0 for row in rows)
    # Holdout isolation: predictions cover exactly the locked test rows.
    assert sorted(row["source_row_index"] for row in rows) == sorted(result["split"]["test_source_rows"])


def test_multiclass_numeric_labels_keep_numeric_order():
    frame = _flowers()
    frame["grade"] = frame.pop("species").map({"bristle": 10, "fern": 2, "moss": 7})
    result = _run(frame, target="grade")
    assert result["status"] == "COMPLETED"
    # Sorted numerically (2, 7, 10 = fern, moss, bristle), not in label-name order:
    # a misaligned probability column would wreck these scores.
    assert result["class_labels"] == [2, 7, 10]
    assert result["test_metrics"]["macro_f1"] > 0.8
    rows = result["test_predictions"]
    assert {row["y_true"] for row in rows} <= {2, 7, 10}
    assert sum(row["y_pred"] == row["y_true"] for row in rows) >= 0.8 * len(rows)


def test_rare_class_uses_random_holdout_and_still_trains():
    frame = _flowers(n=200)
    frame.loc[0, "species"] = "orchid"  # one row: cannot be stratified
    result = _run(frame)

    assert result["status"] == "COMPLETED"
    assert "orchid" in result["class_labels"]
    assert result["holdout_plan"]["strategy"] == RANDOM
    assert "too few to stratify" in result["holdout_plan"]["reason"]
    failed = [row for row in result["candidates"] if row["status"] == "FAILED"]
    assert not failed, [row["failure_reason"] for row in failed]
    validation = result["validation_plan"]
    # One rare row never collapses CV for the whole table.
    assert validation["strategy"] == "StratifiedKFold"
    assert validation["actual_folds"] == 5
    if "orchid" in result["problem_profile"]["class_distribution"]:
        assert "rarest class" in validation["fallback_reason"]
    else:
        # Holdout-only class: never predicted, so scored as misclassified.
        orchid_rows = [row for row in result["test_predictions"] if row["y_true"] == "orchid"]
        assert orchid_rows and all(row["y_pred"] != "orchid" for row in orchid_rows)


def test_multiclass_metrics_score_unseen_class_as_misclassified():
    y = np.array([0, 1, 2, 2])
    # Column 2 is all zeros: the model never saw class 2.
    proba = np.array([[0.9, 0.1, 0.0], [0.2, 0.8, 0.0], [0.6, 0.4, 0.0], [0.3, 0.7, 0.0]])
    metrics = multiclass_metrics(y, proba, n_classes=3)
    assert metrics["accuracy"] == 0.5
    assert np.isfinite(metrics["log_loss"])
    assert metrics["confusion_matrix"][2] == [1, 1, 0]


def test_planners_treat_multiclass_as_classification():
    frame = _flowers()
    profile = build_problem_profile(frame, target="species", task_type="multiclass")
    assert set(profile.class_distribution) == set(SPECIES)
    assert plan_metrics(profile).primary_metric == "macro_f1"
    assert plan_holdout(frame, target="species", task_type="multiclass").strategy == STRATIFIED_RANDOM
    assert "logistic_regression" in available_families("multiclass")


def test_imbalanced_multiclass_gets_balanced_variants_but_not_scale_pos_weight():
    frame = _flowers(weights=(0.8, 0.15, 0.05))
    profile = build_problem_profile(frame, target="species", task_type="multiclass")
    plan = {"problem_profile": profile.to_dict()}
    variants = balanced_variants("multiclass", ["logistic_regression", "random_forest", "xgboost"], plan)
    assert ("logistic_regression", {"class_weight": "balanced"}) in variants
    assert all("scale_pos_weight" not in hp for _, hp in variants)
    balanced = _flowers()
    profile = build_problem_profile(balanced, target="species", task_type="multiclass")
    assert balanced_variants("multiclass", ["logistic_regression"], {"problem_profile": profile.to_dict()}) == []


def test_multiclass_xgboost_tolerates_a_fold_missing_a_class():
    try:
        import xgboost  # noqa: F401
    except ImportError:
        return
    model = make_model("xgboost", task_type="multiclass")
    assert isinstance(model, ContiguousLabelClassifier)
    X = np.random.default_rng(0).normal(size=(40, 2))
    y = np.array([0, 2] * 20)  # class 1 absent from this fit
    model.fit(X, y)
    assert list(model.classes_) == [0, 2]
    from app.engine.experiments.runner import _class_probabilities

    proba = _class_probabilities(model, X, 3)
    assert proba.shape == (40, 3)
    assert np.all(proba[:, 1] == 0.0)
    assert np.allclose(proba.sum(axis=1), 1.0)


def test_multiclass_run_is_deterministic():
    first, second = _run(_flowers()), _run(_flowers())
    assert first["selection"]["selected_candidate_id"] == second["selection"]["selected_candidate_id"]
    assert first["test_metrics"]["macro_f1"] == second["test_metrics"]["macro_f1"]


def _modeled(result: dict) -> set[str]:
    return set(result["preprocessing"]["numeric_columns"]) | set(result["preprocessing"]["categorical_columns"])


def test_numeric_copy_of_integer_target_is_excluded():
    frame = _flowers()
    frame["grade"] = frame.pop("species").map({"bristle": 1, "fern": 2, "moss": 3})
    frame["grade_copy"] = frame["grade"]
    result = _run(frame, target="grade")
    assert "grade_copy" not in _modeled(result)
    assert {"petal_len", "petal_wid"} <= _modeled(result)


def test_permuted_code_of_string_target_is_excluded():
    frame = _flowers()
    frame["perm"] = frame["species"].map({"bristle": 2, "fern": 0, "moss": 1})
    frame["perm_text"] = frame["species"].map({"bristle": "q", "fern": "r", "moss": "s"})
    result = _run(frame)
    assert not {"perm", "perm_text"} & _modeled(result)
    assert {"petal_len", "petal_wid"} <= _modeled(result)


def test_macro_metrics_share_a_denominator_when_a_class_is_absent():
    y = np.array([0, 0, 1, 1])
    absent = np.array([[0.9, 0.05, 0.05], [0.9, 0.05, 0.05], [0.1, 0.8, 0.1], [0.1, 0.1, 0.8]])
    present = np.array([[0.9, 0.05, 0.05], [0.9, 0.05, 0.05], [0.1, 0.8, 0.1], [0.8, 0.1, 0.1]])
    # One error either way: predicting the absent class 2 must not cost more.
    assert multiclass_metrics(y, absent, n_classes=3)["macro_f1"] >= multiclass_metrics(
        y, present, n_classes=3
    )["macro_f1"]
