"""P4.10-A: the five trust checks, pure (no database). One synthetic positive and one
negative per check, plus orientation, duplicate hashing and holdout isolation."""

from __future__ import annotations

import inspect
from dataclasses import fields, replace

import numpy as np
import pandas as pd
import pytest

from app.domain.findings import FINDING_CHECKS, MESSAGE_TEMPLATES, findings_read, render_message
from app.engine.investigate import (
    RunEvidence,
    check_class_imbalance,
    check_duplicate_rows,
    check_implausible_score,
    check_overfit_gap,
    check_target_leakage,
    investigate,
    investigation_payload,
    row_hashes,
    run_evidence_from_result,
)

BASE = RunEvidence(task_type="binary", primary_metric="roc_auc", winner_family="random_forest",
                   winner_features=("a", "b", "c"), winner_cv={"roc_auc": 0.80}, winner_cv_std={"roc_auc": 0.02},
                   train_metrics={"roc_auc": 0.84}, baseline_family="majority", baseline_cv={"roc_auc": 0.5},
                   class_distribution={"0": 60, "1": 40}, minority_class_fraction=0.4, imbalance_ratio=1.5)


def _risk(column: str, risk: str, action: str, *reasons: str) -> dict:
    return {"column": column, "risk": risk, "action": action, "reasons": list(reasons),
            "evidence": {"single_feature_score": 0.97, "partition": "train"}}


# --- target leakage ---------------------------------------------------------------------


def test_leakage_negative_clean_audit_passes():
    finding = check_target_leakage(BASE)
    assert (finding.status, finding.severity, finding.recommendation_kind) == ("pass", "info", None)
    assert "3 columns" in finding.message


def test_leakage_positive_excluded_and_flagged_columns_warn():
    leak = _risk("refund_issued", "CRITICAL", "exclude", "direct_target_duplicate")
    ident = _risk("row_id", "NONE", "exclude", "identifier_not_a_predictor")
    named = _risk("b", "LOW", "keep_with_warning", "suspicious_name")
    ev = replace(BASE, leakage_risks=(leak, ident, named),
                 leakage_exclusions=({k: leak[k] for k in ("column", "risk", "action", "reasons")},
                                     {k: ident[k] for k in ("column", "risk", "action", "reasons")}))
    finding = check_target_leakage(ev)
    assert (finding.status, finding.severity, finding.recommendation_kind) == ("warning", "warning", "review_columns")
    assert finding.evidence["excluded_columns"] == ["refund_issued"]  # identifiers are not leakage
    assert finding.evidence["flagged_columns"] == ["b"]
    assert "refund_issued" in finding.message and "b" in finding.message


def test_leakage_kept_risky_column_fails():
    ev = replace(BASE, leakage_risks=(_risk("a", "MEDIUM", "requires_review", "high_correlation_alone"),))
    finding = check_target_leakage(ev)
    assert (finding.status, finding.severity) == ("fail", "error")
    assert finding.evidence["risky_columns"] == ["a"]


# --- overfit gap ------------------------------------------------------------------------


def test_overfit_negative_small_gap_passes():
    finding = check_overfit_gap(BASE)
    assert finding.status == "pass" and finding.evidence["absolute_gap"] == pytest.approx(0.04)


def test_overfit_positive_large_gap_warns():
    finding = check_overfit_gap(replace(BASE, train_metrics={"roc_auc": 1.0}))
    assert (finding.status, finding.recommendation_kind) == ("warning", "regularize")
    assert finding.evidence["absolute_gap"] == pytest.approx(0.20)
    assert "ROC AUC 1 on the rows it trained on but 0.8" in finding.message


def test_overfit_lower_is_better_metric_is_oriented():
    # RMSE: training error far below CV error is the gap; never negated / mixed scales.
    ev = replace(BASE, task_type="regression", primary_metric="rmse", winner_cv={"rmse": 10.0},
                 winner_cv_std={"rmse": 0.5}, train_metrics={"rmse": 4.0}, baseline_cv={"rmse": 20.0})
    finding = check_overfit_gap(ev)
    assert finding.status == "warning" and finding.evidence["direction"] == "lower_is_better"
    assert finding.evidence["absolute_gap"] == pytest.approx(6.0)
    assert finding.evidence["relative_gap"] == pytest.approx(0.6)
    # Training error above CV error is not overfitting.
    assert check_overfit_gap(replace(ev, train_metrics={"rmse": 11.0})).status == "pass"


def test_overfit_binary_threshold_metric_compares_a_threshold_free_metric():
    # Training rows are scored at the locked decision threshold, CV folds at 0.5: F1
    # values are not comparable, ROC AUC is.
    ev = replace(BASE, primary_metric="f1", winner_cv={"f1": 0.40, "roc_auc": 0.80},
                 train_metrics={"f1": 0.75, "roc_auc": 0.83})
    finding = check_overfit_gap(ev)
    assert finding.status == "pass" and finding.evidence["metric"] == "roc_auc"
    assert finding.evidence["primary_metric"] == "f1"


def test_overfit_skips_without_train_metrics():
    finding = check_overfit_gap(replace(BASE, train_metrics={}))
    assert finding.status == "pass" and finding.evidence["skipped_reason"] == "train_or_cv_metric_missing"
    assert finding.message.startswith("Not checked")


# --- duplicate rows ---------------------------------------------------------------------


def _features(n: int, seed: int) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    return pd.DataFrame({"amount": rng.normal(100, 30, n).round(4), "plan": rng.choice(["a", "b", "c"], n),
                         "visits": rng.integers(0, 5, n)})


def test_duplicates_negative_distinct_rows_pass():
    train, test = _features(300, 1), _features(80, 2)
    finding = check_duplicate_rows(train, row_hashes(test))
    assert finding.status == "pass"
    assert finding.evidence["train_duplicate_rows"] == 0 and finding.evidence["train_test_duplicate_rows"] == 0


def test_duplicates_positive_rows_differing_only_in_an_id_column():
    frame = _features(300, 3)
    frame = pd.concat([frame, frame.iloc[:30]], ignore_index=True)
    frame.insert(0, "record_id", [f"r{i}" for i in range(len(frame))])  # unique: rows "differ" only here
    model_columns = ["amount", "plan", "visits"]
    train = frame.iloc[:270]
    test = frame.iloc[270:]  # holds the 30 copies of training rows
    assert check_duplicate_rows(train, row_hashes(test[["record_id", *model_columns]])).status == "pass"
    finding = check_duplicate_rows(train[model_columns], row_hashes(test[model_columns]))
    assert (finding.status, finding.recommendation_kind) == ("fail", "deduplicate")
    assert finding.evidence["train_test_duplicate_rows"] == 30
    assert finding.message_keys == ("duplicate_rows.across_split",)

    within = pd.concat([train[model_columns], train[model_columns].iloc[:10]], ignore_index=True)
    finding = check_duplicate_rows(within, row_hashes(_features(50, 9)))
    assert finding.status == "warning" and finding.evidence["train_duplicate_rows"] == 10


def test_duplicates_expected_by_chance_on_low_cardinality_columns_pass():
    rng = np.random.default_rng(4)
    train = pd.DataFrame({"x": rng.integers(0, 2, 400), "y": rng.integers(0, 3, 400)})  # 6 combinations
    test = pd.DataFrame({"x": rng.integers(0, 2, 100), "y": rng.integers(0, 3, 100)})
    finding = check_duplicate_rows(train, row_hashes(test))
    assert finding.evidence["train_duplicate_rows"] > 350
    assert finding.status == "pass"


def test_row_hashes_ignore_index_and_column_order():
    frame = _features(20, 5)
    shuffled = frame[["visits", "amount", "plan"]].set_index(pd.Index(range(100, 120)))
    assert (row_hashes(frame) == row_hashes(shuffled)).all()


# --- class imbalance --------------------------------------------------------------------


def test_imbalance_negative_balanced_passes():
    assert check_class_imbalance(BASE).status == "pass"
    regression = check_class_imbalance(replace(BASE, task_type="regression"))
    assert regression.status == "pass" and regression.message_keys == ("class_imbalance.not_applicable",)


def test_imbalance_positive_warns_and_fails_with_weight_note():
    ev = replace(BASE, class_distribution={"0": 930, "1": 70}, minority_class_fraction=0.07, imbalance_ratio=13.3)
    finding = check_class_imbalance(ev)
    assert (finding.status, finding.recommendation_kind) == ("warning", "class_weights")
    assert "7.0%" in finding.message and "70 rows" in finding.message
    weighted = check_class_imbalance(replace(ev, class_weighted_candidates=2, minority_class_fraction=0.015,
                                             class_distribution={"0": 985, "1": 15}))
    assert (weighted.status, weighted.severity, weighted.recommendation_kind) == ("fail", "error", "collect_more_data")
    assert "already trained" in weighted.message


def test_imbalance_multiclass_uses_fair_share():
    # 10 balanced classes are 10% each: not imbalanced.
    balanced = replace(BASE, task_type="multiclass", class_distribution={str(i): 100 for i in range(10)},
                       minority_class_fraction=0.10, imbalance_ratio=1.0)
    assert check_class_imbalance(balanced).status == "pass"


# --- too good to be true ----------------------------------------------------------------


def test_implausible_negative_believable_score_passes():
    assert check_implausible_score(BASE).status == "pass"


def test_implausible_positive_near_perfect_and_error_ratio():
    finding = check_implausible_score(replace(BASE, winner_cv={"roc_auc": 0.995}))
    assert (finding.status, finding.recommendation_kind) == ("warning", "investigate_leakage")
    assert check_implausible_score(replace(BASE, winner_cv={"roc_auc": 1.0})).status == "fail"
    regression = replace(BASE, task_type="regression", primary_metric="mae", winner_cv={"mae": 0.3},
                         baseline_cv={"mae": 12.0})
    finding = check_implausible_score(regression)
    assert finding.status == "warning" and finding.evidence["error_ratio"] == pytest.approx(0.025)
    assert check_implausible_score(replace(regression, winner_cv={"mae": 6.0})).status == "pass"


def test_implausible_trivial_when_the_baseline_is_already_near_perfect():
    ev = replace(BASE, primary_metric="accuracy", winner_cv={"accuracy": 0.995}, baseline_cv={"accuracy": 0.992})
    finding = check_implausible_score(ev)
    assert finding.status == "pass" and finding.message_keys == ("implausible_score.trivial",)


# --- evidence extraction, holdout isolation, messages --------------------------------------


def _result() -> dict:
    return {
        "task": {"target": "label", "task_type": "binary"},
        "metric_plan": {"primary_metric": "roc_auc"},
        "problem_profile": {"task_type": "binary", "class_distribution": {"0": 80, "1": 20},
                            "minority_class_fraction": 0.2, "imbalance_ratio": 4.0},
        "model_development_plan": {"allowed_features": ["a", "b"], "excluded_features": [],
                                   "leakage_assessment": {"findings": []}},
        "candidates": [
            {"candidate_id": "base", "model_family": "majority", "status": "trained", "cv_mean": {"roc_auc": 0.5}},
            {"candidate_id": "rf", "model_family": "random_forest", "status": "trained",
             "hyperparameters": {"class_weight": "balanced"}, "cv_mean": {"roc_auc": 0.81}},
        ],
        "best_single": {"candidate_id": "rf", "model_family": "random_forest", "features": ["a", "b"],
                        "hyperparameters": {"class_weight": "balanced"}, "score": 0.81,
                        "cv_mean": {"roc_auc": 0.81}, "cv_std": {"roc_auc": 0.03},
                        "test_metrics": {"roc_auc": 0.62}, "n_test_rows": 50},
        "baseline_comparison": {"metric": "roc_auc", "baseline_candidate_id": "base", "baseline_cv_score": 0.5,
                                "winner_cv_score": 0.81},
        "train_metrics": {"roc_auc": 0.86},
        "test_metrics": {"roc_auc": 0.62},
        "final_test_evaluation": {"metrics": {"roc_auc": 0.62}},
        "test_predictions": [{"row": 1, "prediction": 1}],
    }


def test_evidence_extraction_reads_cv_train_and_baseline():
    ev = run_evidence_from_result(_result())
    assert ev.primary_metric == "roc_auc" and ev.winner_cv["roc_auc"] == 0.81
    assert ev.baseline_cv == {"roc_auc": 0.5} and ev.train_metrics == {"roc_auc": 0.86}
    assert ev.winner_class_weighted and ev.class_weighted_candidates == 1


def test_no_holdout_input_reaches_any_check():
    names = {item.name for item in fields(RunEvidence)}
    names |= set(inspect.signature(investigate).parameters)
    assert not {name for name in names if "holdout" in name or "final" in name or name.startswith("test_metric")}
    # Changing every holdout value of the run changes no finding.
    result = _result()
    baseline = investigation_payload(investigate(run_evidence_from_result(result)))
    for key in ("test_metrics", "final_test_evaluation"):
        result[key] = {"roc_auc": 0.999, "metrics": {"roc_auc": 0.999}}
    result["best_single"]["test_metrics"] = {"roc_auc": 0.999}
    result["test_predictions"] = []
    assert investigation_payload(investigate(run_evidence_from_result(result))) == baseline
    assert "0.62" not in str(baseline)


def test_investigate_returns_every_check_in_order_and_read_model_renders_messages():
    from uuid import uuid4

    payload = investigation_payload(investigate(run_evidence_from_result(_result())))
    assert [row["check"] for row in payload["checks"]] == list(FINDING_CHECKS)
    body = findings_read(uuid4(), payload)
    assert body.investigated and len(body.checks) == 5
    assert body.summary.passed + body.summary.warnings + body.summary.failures == 5
    assert all(item.message for item in body.checks)
    assert findings_read(uuid4(), None).investigated is False


def test_messages_are_deterministic_and_every_key_renders():
    evidence = {"metric": "rmse", "train_score": 1.23456, "cv_score": 2.5, "absolute_gap": 1.26544,
                "relative_gap": 0.506, "excluded_columns": [f"c{i}" for i in range(8)], "excluded_count": 8}
    first = render_message(("overfit_gap.gap", "target_leakage.excluded"), evidence)
    assert first == render_message(("overfit_gap.gap", "target_leakage.excluded"), evidence)
    assert "RMSE 1.23" in first and "50.6%" in first and "c4 and 3 more" in first
    for key in MESSAGE_TEMPLATES:
        assert render_message((key,), {})  # missing numbers render as n/a, never raise
