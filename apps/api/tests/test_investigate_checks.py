"""P4.10-A: the five trust checks, pure (no database). Synthetic positive and negative
cases per check, not_evaluated paths, orientation, duplicate hashing, holdout isolation."""

from __future__ import annotations

import inspect
from dataclasses import fields, replace
from uuid import uuid4

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
    investigate_result,
    investigation_payload,
    row_hashes,
    run_evidence_from_result,
)

BASE = RunEvidence(task_type="binary", primary_metric="roc_auc", winner_family="random_forest",
                   winner_features=("a", "b", "c"), winner_cv={"roc_auc": 0.80}, winner_cv_std={"roc_auc": 0.02},
                   train_metrics={"roc_auc": 0.84}, baseline_family="majority", baseline_cv={"roc_auc": 0.5},
                   n_folds=5, class_distribution={"0": 600, "1": 400}, minority_class_fraction=0.4,
                   imbalance_ratio=1.5, leakage_audited=True)


def _risk(column: str, risk: str, action: str, *reasons: str) -> dict:
    return {"column": column, "risk": risk, "action": action, "reasons": list(reasons),
            "evidence": {"single_feature_score": 0.97, "partition": "train"}}


# --- target leakage ---------------------------------------------------------------------


def test_leakage_negative_clean_audit_passes_and_missing_audit_is_not_evaluated():
    finding = check_target_leakage(BASE)
    assert (finding.status, finding.severity, finding.recommendation_kind) == ("pass", "info", None)
    missing = check_target_leakage(replace(BASE, leakage_audited=False))
    assert missing.status == "not_evaluated" and missing.evidence["not_evaluated_reason"] == "audit_missing"


def test_leakage_positive_excluded_and_flagged_columns_warn():
    leak = _risk("refund_issued", "CRITICAL", "exclude", "direct_target_duplicate")
    ident = _risk("row_id", "NONE", "exclude", "identifier_not_a_predictor")
    named = _risk("b", "LOW", "keep_with_warning", "suspicious_name")
    ev = replace(BASE, leakage_risks=(leak, ident, named),
                 leakage_exclusions=({k: leak[k] for k in ("column", "risk", "action", "reasons")},
                                     {k: ident[k] for k in ("column", "risk", "action", "reasons")}))
    finding = check_target_leakage(ev)
    assert (finding.status, finding.recommendation_kind) == ("warning", "review_columns")
    assert finding.evidence["excluded_columns"] == ["refund_issued"]  # identifiers are not leakage
    assert finding.evidence["flagged_columns"] == ["b"] and "refund_issued" in finding.message


def test_leakage_kept_risky_column_fails():
    ev = replace(BASE, leakage_risks=(_risk("a", "MEDIUM", "requires_review", "high_correlation_alone"),))
    finding = check_target_leakage(ev)
    assert (finding.status, finding.severity, finding.evidence["risky_columns"]) == ("fail", "error", ["a"])


# --- overfit gap ------------------------------------------------------------------------

GAP = replace(BASE, train_metrics={"roc_auc": 1.0})  # gap 0.20 = 10 fold standard deviations


def test_overfit_negative_small_gap_or_within_noise_passes():
    assert check_overfit_gap(BASE).status == "pass"
    noisy = check_overfit_gap(replace(GAP, winner_cv_std={"roc_auc": 0.08}))  # 0.20 < 3 x 0.08
    assert noisy.status == "pass" and noisy.message_keys == ("overfit_gap.within_noise",)


def test_overfit_warns_when_a_simpler_model_matches_in_cv():
    ev = replace(GAP, simple_family="logistic_regression", simple_cv={"roc_auc": 0.79})
    finding = check_overfit_gap(ev)
    assert (finding.status, finding.recommendation_kind) == ("warning", "simpler_model")
    assert "logistic regression scores about the same" in finding.message
    assert "ROC AUC 1 on the rows it trained on but 0.8" in finding.message


def test_overfit_flexible_winner_that_beats_simpler_models_passes():
    ev = replace(GAP, simple_family="logistic_regression", simple_cv={"roc_auc": 0.70})
    finding = check_overfit_gap(ev)
    assert finding.status == "pass" and finding.message_keys == ("overfit_gap.flexible_by_design",)
    assert "honest estimate" in finding.message
    # Fully grown forests fit training rows by construction; other families still warn.
    assert check_overfit_gap(GAP).message_keys == ("overfit_gap.fit_by_design",)
    boosted = check_overfit_gap(replace(GAP, winner_family="xgboost", final_fit_retuned=True))
    assert (boosted.status, boosted.recommendation_kind) == ("warning", "regularize")
    assert "re-tuned on all training rows" in boosted.message


def test_overfit_lower_is_better_metric_is_oriented():
    ev = replace(BASE, task_type="regression", primary_metric="rmse", winner_family="xgboost_regressor",
                 winner_cv={"rmse": 10.0}, winner_cv_std={"rmse": 0.5}, train_metrics={"rmse": 4.0},
                 baseline_cv={"rmse": 20.0})
    finding = check_overfit_gap(ev)
    assert finding.status == "warning" and finding.evidence["direction"] == "lower_is_better"
    assert finding.evidence["absolute_gap"] == pytest.approx(6.0)
    assert finding.evidence["relative_gap"] == pytest.approx(0.6)
    assert check_overfit_gap(replace(ev, train_metrics={"rmse": 11.0})).status == "pass"


def test_overfit_binary_threshold_metric_compares_a_threshold_free_metric():
    # Training rows are scored at the locked decision threshold, CV folds at 0.5.
    ev = replace(BASE, primary_metric="f1", winner_cv={"f1": 0.40, "roc_auc": 0.80},
                 train_metrics={"f1": 0.75, "roc_auc": 0.83})
    finding = check_overfit_gap(ev)
    assert finding.status == "pass" and finding.evidence["metric"] == "roc_auc"
    swapped = check_overfit_gap(replace(ev, winner_family="xgboost", train_metrics={"f1": 0.99, "roc_auc": 1.0}))
    assert swapped.status == "warning" and swapped.evidence["primary_metric"] == "f1"
    assert "F1 is not compared here" in swapped.message and "ROC AUC does not depend" in swapped.message
    none = check_overfit_gap(replace(ev, train_metrics={"f1": 0.75}))
    assert none.status == "not_evaluated" and none.evidence["not_evaluated_reason"] == "no_comparable_metric"


def test_overfit_not_evaluated_names_what_is_missing():
    finding = check_overfit_gap(replace(BASE, train_metrics={}))
    assert finding.status == "not_evaluated" and finding.evidence["not_evaluated_reason"] == "train_score_missing"
    assert finding.message == "Not evaluated: the winning model's ROC AUC on its own training rows was not recorded."


# --- duplicate rows ---------------------------------------------------------------------


def _features(n: int, seed: int) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    return pd.DataFrame({"amount": rng.normal(100, 30, n).round(4), "plan": rng.choice(["a", "b", "c"], n),
                         "visits": rng.integers(0, 5, n)})


def test_duplicates_negative_distinct_rows_pass():
    finding = check_duplicate_rows(_features(300, 1), row_hashes(_features(80, 2)))
    assert finding.status == "pass" and finding.message_keys == ("duplicate_rows.pass",)
    assert finding.evidence["train_duplicate_rows"] == finding.evidence["holdout_duplicate_rows"] == 0


def test_duplicates_positive_rows_differing_only_in_an_id_column():
    frame = _features(300, 3)
    frame = pd.concat([frame, frame.iloc[:30]], ignore_index=True)
    frame.insert(0, "record_id", [f"r{i}" for i in range(len(frame))])  # unique: rows "differ" only here
    model_columns = ["amount", "plan", "visits"]
    train, test = frame.iloc[:270][model_columns], frame.iloc[270:][model_columns]  # test: 30 copies
    finding = check_duplicate_rows(train, row_hashes(test))
    assert (finding.status, finding.recommendation_kind) == ("fail", "deduplicate")
    assert finding.evidence["holdout_duplicate_rows"] == 30 and finding.evidence["fail_eligible"]
    assert finding.evidence["near_unique_columns"] == ["amount"]
    within = pd.concat([train, train.iloc[:10]], ignore_index=True)
    finding = check_duplicate_rows(within, row_hashes(_features(50, 9)))
    assert finding.status == "warning" and finding.evidence["train_duplicate_rows"] == 10


def test_duplicates_on_correlated_categoricals_never_fail():
    # city -> region -> country are nested; repeats are natural, not duplicate records.
    rng = np.random.default_rng(8)

    def rows(n: int) -> pd.DataFrame:
        city = rng.integers(0, 50, n)
        return pd.DataFrame({"city": [f"c{v}" for v in city], "region": [f"r{v // 5}" for v in city],
                             "country": [f"k{v // 10}" for v in city], "tier": rng.choice(["a", "b", "c"], n)})

    finding = check_duplicate_rows(rows(3000), row_hashes(rows(750)))
    assert finding.status == "warning" and not finding.evidence["fail_eligible"]
    assert finding.message_keys[-1] == "duplicate_rows.independence_caveat"
    assert "assumes the model columns vary independently" in finding.message


def test_duplicates_expected_by_chance_pass_and_minor_excess_is_worded_honestly():
    rng = np.random.default_rng(4)
    train = pd.DataFrame({"x": rng.integers(0, 2, 400), "y": rng.integers(0, 3, 400)})  # 6 combinations
    test = pd.DataFrame({"x": rng.integers(0, 2, 100), "y": rng.integers(0, 3, 100)})
    assert check_duplicate_rows(train, row_hashes(test)).status == "pass"
    minor = _features(1000, 6)
    minor = pd.concat([minor, minor.iloc[:3]], ignore_index=True)  # 0.3% repeats: real but small
    finding = check_duplicate_rows(minor, row_hashes(_features(100, 7)))
    assert finding.status == "pass" and finding.message_keys == ("duplicate_rows.pass_minor",)
    assert "more than chance alone would produce" in finding.message


def test_duplicates_not_evaluated_without_partition_and_hashes_ignore_index_and_order():
    finding = check_duplicate_rows(None, None)
    assert finding.status == "not_evaluated" and finding.evidence["not_evaluated_reason"] == "partition_unavailable"
    frame = _features(20, 5)
    shuffled = frame[["visits", "amount", "plan"]].set_index(pd.Index(range(100, 120)))
    assert (row_hashes(frame) == row_hashes(shuffled)).all()


# --- class imbalance --------------------------------------------------------------------


def test_imbalance_negative_balanced_passes_and_regression_is_not_evaluated():
    assert check_class_imbalance(BASE).status == "pass"
    regression = check_class_imbalance(replace(BASE, task_type="regression"))
    assert regression.status == "not_evaluated" and regression.message_keys == ("class_imbalance.not_applicable",)
    missing = check_class_imbalance(replace(BASE, class_distribution=None, minority_class_fraction=None))
    assert missing.status == "not_evaluated" and "reasonably balanced" not in missing.message


def test_imbalance_warns_on_fraction_and_fails_only_on_too_few_minority_rows():
    ev = replace(BASE, class_distribution={"0": 930, "1": 70}, minority_class_fraction=0.07, imbalance_ratio=13.3)
    finding = check_class_imbalance(ev)
    assert (finding.status, finding.recommendation_kind) == ("warning", "class_weights")
    assert "7.0%" in finding.message and "70 rows" in finding.message
    plenty = check_class_imbalance(replace(ev, class_distribution={"0": 49_000, "1": 1_000},
                                           minority_class_fraction=0.02))
    assert plenty.status == "warning"  # 2% but 200 rows per fold: measurable
    rare = check_class_imbalance(replace(ev, class_weighted_candidates=2, minority_class_fraction=0.015,
                                         class_distribution={"0": 985, "1": 15}))
    assert (rare.status, rare.severity, rare.recommendation_kind) == ("fail", "error", "collect_more_data")
    assert "about 3 rows of that class per fold" in rare.message and "already trained" in rare.message


def test_imbalance_multiclass_uses_fair_share():
    balanced = replace(BASE, task_type="multiclass", class_distribution={str(i): 100 for i in range(10)},
                       minority_class_fraction=0.10, imbalance_ratio=1.0)
    assert check_class_imbalance(balanced).status == "pass"
    skewed = {**{str(i): 220 for i in range(9)}, "9": 20}
    finding = check_class_imbalance(replace(balanced, class_distribution=skewed, minority_class_fraction=0.01,
                                            imbalance_ratio=11.0, n_folds=2))
    assert finding.status == "warning" and finding.evidence["minority_fair_share_fraction"] == pytest.approx(0.1)


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
    assert check_implausible_score(replace(regression, winner_cv={"mae": 0.1})).status == "fail"  # <= 1%
    assert check_implausible_score(replace(regression, winner_cv={"mae": 6.0})).status == "pass"


def test_implausible_trivial_exemption_uses_the_residual_error_ratio():
    trivial = replace(BASE, primary_metric="accuracy", winner_cv={"accuracy": 0.995}, baseline_cv={"accuracy": 0.992})
    finding = check_implausible_score(trivial)
    assert finding.status == "pass" and finding.message_keys == ("implausible_score.trivial",)
    assert finding.evidence["residual_error_ratio"] == pytest.approx(0.625)
    # A 95% baseline does not explain 99.5%: 90% of its errors are gone.
    assert check_implausible_score(replace(trivial, baseline_cv={"accuracy": 0.95})).status == "warning"


def test_implausible_not_evaluated_without_cv_or_error_baseline():
    assert check_implausible_score(replace(BASE, winner_cv={})).status == "not_evaluated"
    regression = replace(BASE, primary_metric="mae", winner_cv={"mae": 0.3}, baseline_cv={})
    finding = check_implausible_score(regression)
    assert finding.status == "not_evaluated" and finding.evidence["not_evaluated_reason"] == "baseline_missing"


# --- evidence extraction, orchestration, holdout isolation, messages ------------------------


def _result() -> dict:
    return {
        "task": {"target": "label", "task_type": "binary"},
        "metric_plan": {"primary_metric": "roc_auc"},
        "problem_profile": {"task_type": "binary", "class_distribution": {"0": 160, "1": 40},
                            "minority_class_fraction": 0.2, "imbalance_ratio": 4.0},
        "model_development_plan": {"allowed_features": ["a", "b"], "excluded_features": [],
                                   "leakage_assessment": {"findings": []}},
        "candidates": [
            {"candidate_id": "base", "model_family": "majority", "status": "trained", "score": 0.5,
             "cv_mean": {"roc_auc": 0.5}},
            {"candidate_id": "lr", "model_family": "logistic_regression", "status": "trained", "score": 0.74,
             "cv_mean": {"roc_auc": 0.74}},
            {"candidate_id": "rf", "model_family": "random_forest", "status": "trained", "score": 0.81,
             "hyperparameters": {"class_weight": "balanced"}, "cv_mean": {"roc_auc": 0.81}},
        ],
        "best_single": {"candidate_id": "rf", "model_family": "random_forest", "features": ["a", "b"],
                        "hyperparameters": {"class_weight": "balanced"}, "score": 0.81, "n_folds": 5,
                        "cv_mean": {"roc_auc": 0.81}, "cv_std": {"roc_auc": 0.03},
                        "test_metrics": {"roc_auc": 0.62}, "n_test_rows": 50},
        "baseline_comparison": {"metric": "roc_auc", "baseline_candidate_id": "base", "baseline_cv_score": 0.5,
                                "winner_cv_score": 0.81},
        "train_metrics": {"roc_auc": 0.86},
        "test_metrics": {"roc_auc": 0.62},
        "final_test_evaluation": {"metrics": {"roc_auc": 0.62}},
        "test_predictions": [{"row": 1, "prediction": 1}],
    }


def test_evidence_extraction_reads_cv_train_baseline_and_simple_model():
    ev = run_evidence_from_result(_result())
    assert ev.primary_metric == "roc_auc" and ev.winner_cv["roc_auc"] == 0.81 and ev.n_folds == 5
    assert ev.baseline_cv == {"roc_auc": 0.5} and ev.train_metrics == {"roc_auc": 0.86}
    assert (ev.simple_family, ev.simple_cv) == ("logistic_regression", {"roc_auc": 0.74})
    assert ev.winner_class_weighted and ev.class_weighted_candidates == 1 and ev.leakage_audited


def test_no_holdout_input_reaches_any_check_including_duplicates():
    names = {item.name for item in fields(RunEvidence)}
    names |= set(inspect.signature(investigate).parameters) | set(inspect.signature(check_duplicate_rows).parameters)
    assert not {name for name in names if "holdout" in name or "final" in name.replace("final_fit", "")}
    train, test = _features(200, 11), _features(50, 12)
    test.iloc[:5] = train.iloc[:5].to_numpy()

    def payload(result: dict) -> dict:
        return investigation_payload(investigate_result(result, partition=lambda: (train, row_hashes(test))))

    result = _result()
    baseline = payload(result)
    assert baseline["checks"][2]["evidence"]["holdout_duplicate_rows"] == 5
    for key in ("test_metrics", "final_test_evaluation"):
        result[key] = {"roc_auc": 0.999, "metrics": {"roc_auc": 0.999}}
    result["best_single"]["test_metrics"] = {"roc_auc": 0.999}
    result["test_predictions"] = []
    assert payload(result) == baseline
    assert "0.62" not in str(baseline)


def test_missing_evidence_is_not_evaluated_never_passed():
    body = findings_read(uuid4(), investigation_payload(investigate_result({})))
    total = len(FINDING_CHECKS)  # P5.1-A: 15 checks (deliberately updated from 5)
    assert body.investigated and [item.status for item in body.checks] == ["not_evaluated"] * total
    assert (body.summary.passed, body.summary.not_evaluated) == (0, total)
    assert all(item.message.startswith(("Not evaluated", "Not applicable")) for item in body.checks)


def test_a_crashing_check_or_partition_is_not_evaluated_and_the_rest_still_run(monkeypatch):
    from app.engine.investigate import checks

    def boom(*_args):
        raise ValueError("bad column")

    findings = investigate_result(_result(), partition=boom)
    assert findings[2].status == "not_evaluated" and findings[2].evidence == {
        "error_type": "ValueError", "not_evaluated_reason": "check_error"}
    assert "could not run (ValueError)" in findings[2].message
    # The other core checks still run (the P5.1-A checks have no evidence in this fixture).
    assert [f.status for f in findings[:5] if f.check != "duplicate_rows"].count("not_evaluated") == 0
    monkeypatch.setattr(checks, "check_class_imbalance", boom)
    assert investigate_result(_result())[3].evidence["not_evaluated_reason"] == "check_error"
    monkeypatch.setattr(checks, "run_evidence_from_result", boom)
    assert {f.evidence["not_evaluated_reason"] for f in investigate_result(_result())} == {"check_error"}


def test_read_model_orders_checks_renders_messages_and_counts():
    payload = investigation_payload(investigate_result(_result(), partition=lambda: (
        _features(200, 1), row_hashes(_features(50, 2)))))
    assert [row["check"] for row in payload["checks"]] == list(FINDING_CHECKS)
    body = findings_read(uuid4(), payload)
    assert body.investigated and len(body.checks) == len(FINDING_CHECKS) and all(item.message for item in body.checks)
    summary = body.summary
    assert summary.passed + summary.warnings + summary.failures + summary.not_evaluated == len(FINDING_CHECKS)
    assert [item.status for item in body.checks[:5]].count("not_evaluated") == 0  # the core five
    assert findings_read(uuid4(), None).investigated is False


def test_messages_are_deterministic_and_every_key_renders():
    evidence = {"metric": "rmse", "train_score": 1.23456, "cv_score": 2.5, "absolute_gap": 1.26544,
                "gap_in_cv_std": 4.2, "excluded_columns": [f"c{i}" for i in range(8)], "excluded_count": 8}
    first = render_message(("overfit_gap.gap", "target_leakage.excluded"), evidence)
    assert first == render_message(("overfit_gap.gap", "target_leakage.excluded"), evidence)
    assert "RMSE 1.23" in first and "4.2 times" in first and "c4 and 3 more" in first
    for key in MESSAGE_TEMPLATES:
        assert render_message((key,), {})  # missing numbers render as n/a, never raise
