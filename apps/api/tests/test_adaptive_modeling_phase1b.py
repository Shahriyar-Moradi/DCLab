"""Phase 1B: prediction-time leakage auditor and ModelDevelopmentPlan."""

from __future__ import annotations

import json
import tempfile
from dataclasses import asdict
from pathlib import Path

import numpy as np
import pandas as pd

from app.engine.experiments.runner import run_experiment
from app.engine.lab.auto_prepare import split_column_roles
from app.engine.lab.decision_validator import validate_leakage_review_decision
from app.engine.lab.llm_client import LeakageReviewDecision
from app.engine.modeling.leakage_auditor import (
    audit_leakage,
    build_model_development_plan,
    plan_model_development,
)
from app.engine.modeling.problem_profile import build_problem_profile
from app.engine.modeling.validation_planner import iter_validation_folds, plan_validation
from app.engine.types import SearchConfig, TaskSpec
from app.engine.validation.splits import SOURCE_ROW_COLUMN


def _roles(frame: pd.DataFrame, target: str) -> tuple[list[str], list[str]]:
    columns = [name for name in frame.columns if name not in {target, SOURCE_ROW_COLUMN}]
    return split_column_roles(frame, columns)


def _task(
    frame: pd.DataFrame,
    *,
    target: str,
    task_type: str,
    metric: str,
    extra_numeric: list[str] | None = None,
    entity_id: str | None = None,
) -> TaskSpec:
    num_cols, cat_cols = _roles(frame, target)
    if extra_numeric:
        num_cols = list(dict.fromkeys([*num_cols, *extra_numeric]))
    return TaskSpec(
        id="phase1b",
        name="phase1b",
        task_type=task_type,
        target=target,
        entity_id=entity_id,
        prediction_time_column=None,
        evaluation_metric=metric,
        feature_groups={"features": num_cols + cat_cols},
        validation_strategy="stratified" if task_type == "binary" else "random",
        column_roles={"numerical": num_cols, "categorical": cat_cols},
    )


def _balanced_binary(n: int = 200, seed: int = 1) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    outcome = np.array([0, 1] * (n // 2) + [0] * (n % 2))
    rng.shuffle(outcome)
    return pd.DataFrame(
        {
            "age": rng.normal(40, 12, n),
            "income": rng.normal(50_000, 8_000, n),
            "region": rng.choice(["N", "S"], n),
            "outcome": outcome,
        }
    )


def _regression_frame(n: int = 180, seed: int = 3) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    tenure = rng.integers(1, 40, n)
    usage = rng.normal(20, 5, n)
    return pd.DataFrame(
        {
            "tenure": tenure,
            "usage": usage,
            "segment": rng.choice(["small", "mid"], n),
            "revenue": 80 + tenure * 3.2 + usage * 1.4 + rng.normal(0, 4, n),
        }
    )


def _repeated_entity_binary(n_entities: int = 20, repeats: int = 5, seed: int = 4) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    rows = []
    for entity in range(n_entities):
        label = int(entity % 4 == 0)
        for visit in range(repeats):
            rows.append(
                {
                    "customer_id": f"C{entity:03d}",
                    "visit": visit,
                    "amount": float(rng.normal(50, 10) + 20 * label),
                    "channel": "web" if visit % 2 == 0 else "store",
                    "outcome": label,
                }
            )
    return pd.DataFrame(rows)


def _candidate_features(result: dict) -> set[str]:
    names: set[str] = set()
    for row in result.get("candidates") or []:
        names.update(row.get("feature_set") or row.get("features") or [])
    groups = (result.get("task") or {}).get("feature_groups") or {}
    for cols in groups.values():
        names.update(cols)
    return names


def _risk(audit, column: str):
    item = audit.risk_for(column)
    assert item is not None
    return item


def test_direct_target_duplicate_is_critical_and_excluded():
    frame = _balanced_binary()
    frame["outcome_copy"] = frame["outcome"]
    audit = audit_leakage(frame, target="outcome", task_type="binary")
    item = _risk(audit, "outcome_copy")
    assert item.risk == "CRITICAL"
    assert item.action == "exclude"
    assert "direct_target_duplicate" in item.reasons
    _, _, _, _, plan = plan_model_development(frame, target="outcome", task_type="binary")
    assert "outcome_copy" not in plan.allowed_features
    assert any(row["column"] == "outcome_copy" and row["risk"] == "CRITICAL" for row in plan.excluded_features)


def test_target_like_proxy_is_detected():
    frame = _balanced_binary()
    frame["result_code"] = frame["outcome"] * 100
    audit = audit_leakage(frame, target="outcome", task_type="binary")
    item = _risk(audit, "result_code")
    assert item.risk == "HIGH"
    assert item.action == "exclude"
    assert "target_proxy" in item.reasons or "strong_single_feature_score" in item.reasons


def test_post_outcome_feature_is_high_when_evidence_supports_it():
    n = 160
    start = pd.Timestamp("2024-01-01")
    as_of = pd.date_range(start, periods=n, freq="D")
    rng = np.random.default_rng(8)
    outcome = np.array([0, 1] * (n // 2))
    frame = pd.DataFrame(
        {
            "as_of_date": as_of,
            "age": rng.normal(40, 12, n),
            "resolved_at": as_of + pd.Timedelta(days=3),
            "outcome": outcome,
        }
    )
    audit = audit_leakage(frame, target="outcome", task_type="binary", time_column="as_of_date")
    item = _risk(audit, "resolved_at")
    assert item.risk == "HIGH"
    assert item.action == "exclude"
    assert item.availability is not None
    assert item.availability.status == "known_after_prediction"


def test_safe_feature_is_retained():
    frame = _balanced_binary()
    _, _, _, audit, plan = plan_model_development(frame, target="outcome", task_type="binary")
    item = _risk(audit, "age")
    assert item.risk in {"NONE", "LOW"}
    assert item.action in {"keep", "keep_with_warning"}
    assert "age" in plan.allowed_features
    assert "income" in plan.allowed_features


def test_name_alone_does_not_exclude():
    frame = _balanced_binary()
    rng = np.random.default_rng(9)
    frame["future_notes"] = rng.choice(["a", "b", "c"], len(frame))
    audit = audit_leakage(frame, target="outcome", task_type="binary")
    item = _risk(audit, "future_notes")
    assert item.action != "exclude"
    assert item.risk in {"LOW", "MEDIUM"}
    _, _, _, _, plan = plan_model_development(frame, target="outcome", task_type="binary")
    if item.action == "keep_with_warning":
        assert "future_notes" in plan.allowed_features


def test_correlation_alone_does_not_exclude():
    frame = _balanced_binary()
    frame["amount"] = frame["outcome"] * 10 + 0.01
    audit = audit_leakage(frame, target="outcome", task_type="binary")
    item = _risk(audit, "amount")
    assert item.action != "exclude"
    assert item.risk == "MEDIUM"
    assert item.action == "requires_review"


def test_identifier_excluded_from_estimator_but_kept_for_grouping():
    frame = _repeated_entity_binary()
    profile, validation_plan, metric_plan, audit, plan = plan_model_development(
        frame,
        target="outcome",
        task_type="binary",
        entity_column="customer_id",
    )
    item = _risk(audit, "customer_id")
    assert item.action == "exclude"
    assert "customer_id" not in plan.allowed_features
    assert plan.group_column == "customer_id"
    assert validation_plan.group_column == "customer_id"
    folds = list(iter_validation_folds(validation_plan, frame, frame["outcome"].to_numpy()))
    assert folds
    for fold in folds:
        train_groups = set(frame.iloc[fold.train_index]["customer_id"])
        val_groups = set(frame.iloc[fold.validation_index]["customer_id"])
        assert train_groups.isdisjoint(val_groups)
    rebuilt = build_model_development_plan(
        problem_profile=profile,
        validation_plan=validation_plan,
        metric_plan=metric_plan,
        audit=audit,
    )
    assert rebuilt.group_column == "customer_id"


def test_high_critical_excluded_never_enter_candidate_features():
    frame = _balanced_binary()
    frame["outcome_copy"] = frame["outcome"]
    frame["result_code"] = frame["outcome"] * 100
    task = _task(frame, target="outcome", task_type="binary", metric="pr_auc")
    with tempfile.TemporaryDirectory() as tmp:
        result = run_experiment(
            frame,
            task,
            SearchConfig(strategy="open_ingest", max_candidates=8, seed=42),
            artifact_dir=Path(tmp),
        )
    assert result["status"] == "COMPLETED"
    features = _candidate_features(result)
    assert "outcome_copy" not in features
    assert "result_code" not in features
    excluded = {row["column"] for row in result["model_development_plan"]["excluded_features"]}
    assert "outcome_copy" in excluded
    assert "result_code" in excluded


def test_identifier_forced_into_roles_is_still_not_a_predictor():
    frame = _repeated_entity_binary()
    task = _task(
        frame,
        target="outcome",
        task_type="binary",
        metric="pr_auc",
        extra_numeric=["customer_id"],
        entity_id="customer_id",
    )
    with tempfile.TemporaryDirectory() as tmp:
        result = run_experiment(
            frame,
            task,
            SearchConfig(strategy="open_ingest", max_candidates=8, seed=42),
            artifact_dir=Path(tmp),
        )
    assert result["status"] == "COMPLETED"
    assert "customer_id" not in _candidate_features(result)
    assert result["validation_plan"]["group_column"] == "customer_id"
    assert result["model_development_plan"]["group_column"] == "customer_id"


def test_ambiguous_llm_path_is_mocked():
    frame = _balanced_binary()
    rng = np.random.default_rng(11)
    score = frame["outcome"].to_numpy(dtype=float).copy()
    flip = rng.choice(len(frame), size=int(0.18 * len(frame)), replace=False)
    score[flip] = 1.0 - score[flip]
    frame["final_score"] = score
    seen: list = []

    def reviewer(evidence):
        seen.append(evidence)
        return LeakageReviewDecision(
            availability_status="unknown",
            risk_level="MEDIUM",
            evidence_field="suspicious_name_tokens",
            rationale="suspicious_name_tokens are present with a moderate single_feature_score",
            confidence=0.82,
        )

    audit = audit_leakage(frame, target="outcome", task_type="binary", reviewer=reviewer)
    item = _risk(audit, "final_score")
    assert seen
    assert item.llm_consulted is True
    assert item.llm_accepted is True
    assert item.action == "requires_review"
    assert item.risk == "MEDIUM"


def test_llm_cannot_directly_override_validator():
    frame = _balanced_binary()
    rng = np.random.default_rng(12)
    frame["future_notes"] = rng.choice(["a", "b", "c"], len(frame))

    def reviewer(evidence):
        return LeakageReviewDecision(
            availability_status="known_after_prediction",
            risk_level="HIGH",
            evidence_field="suspicious_name_tokens",
            rationale="recommend HIGH exclude",
            confidence=0.99,
        )

    evidence_audit = audit_leakage(frame, target="outcome", task_type="binary")
    signals_item = _risk(evidence_audit, "future_notes")
    from app.engine.modeling.leakage_auditor import build_leakage_review_evidence, _collect_signals

    signals = _collect_signals(
        frame,
        "future_notes",
        target="outcome",
        task_type="binary",
        time_column=None,
        identifier_columns=set(),
    )
    evidence = build_leakage_review_evidence(
        signals,
        signals_item.availability,
        target="outcome",
        task_type="binary",
        related_column_names=["age", "outcome"],
    )
    decision = LeakageReviewDecision(
        availability_status="known_after_prediction",
        risk_level="CRITICAL",
        evidence_field="suspicious_name_tokens",
        rationale="drop the column",
        confidence=0.99,
    )
    check = validate_leakage_review_decision(evidence, decision)
    assert check.verdict == "reject"

    audit = audit_leakage(frame, target="outcome", task_type="binary", reviewer=reviewer)
    item = _risk(audit, "future_notes")
    assert item.llm_consulted is True
    assert item.llm_accepted is False
    assert item.action != "exclude"
    assert item.risk != "CRITICAL"


def test_no_raw_csv_sent_to_llm():
    frame = _balanced_binary()
    sentinel = "SENTINEL_CSV_ROW_XYZ"
    notes = np.array(["alpha"] * len(frame), dtype=object)
    notes[0] = sentinel
    frame["final_label"] = notes
    captured = {}

    def reviewer(evidence):
        captured["blob"] = json.dumps(asdict(evidence), default=str)
        captured["keys"] = set(asdict(evidence))
        return None

    audit_leakage(frame, target="outcome", task_type="binary", reviewer=reviewer)
    assert captured
    assert sentinel not in captured["blob"]
    assert "sample_rows" not in captured["keys"]
    assert "sample_values" not in captured["keys"]
    assert "rows" not in captured["keys"]


def test_classification_integration_completes():
    frame = _balanced_binary()
    task = _task(frame, target="outcome", task_type="binary", metric="pr_auc")
    with tempfile.TemporaryDirectory() as tmp:
        result = run_experiment(
            frame,
            task,
            SearchConfig(strategy="open_ingest", max_candidates=8, seed=42),
            artifact_dir=Path(tmp),
        )
    assert result["status"] == "COMPLETED"
    assert result["model_development_plan"]["problem_profile"]["task_type"] == "binary"
    assert "age" in result["model_development_plan"]["allowed_features"]


def test_regression_integration_completes():
    frame = _regression_frame()
    task = _task(frame, target="revenue", task_type="regression", metric="mae")
    with tempfile.TemporaryDirectory() as tmp:
        result = run_experiment(
            frame,
            task,
            SearchConfig(strategy="open_ingest", max_candidates=8, seed=42),
            artifact_dir=Path(tmp),
        )
    assert result["status"] == "COMPLETED"
    assert result["metric_plan"]["primary_metric"] == "mae"
    assert result["model_development_plan"]["plan_version"]


def test_model_development_plan_persists():
    frame = _balanced_binary()
    task = _task(frame, target="outcome", task_type="binary", metric="pr_auc")
    with tempfile.TemporaryDirectory() as tmp:
        artifact_dir = Path(tmp)
        result = run_experiment(
            frame,
            task,
            SearchConfig(strategy="open_ingest", max_candidates=8, seed=42),
            artifact_dir=artifact_dir,
        )
        persisted = json.loads((artifact_dir / "result.json").read_text())
    plan = result["model_development_plan"]
    assert plan["plan_version"]
    assert "feature_availability" in plan
    assert "leakage_assessment" in plan
    assert "allowed_features" in plan
    assert "excluded_features" in plan
    assert "recommended_model_family_hints" in plan
    assert persisted["model_development_plan"]["plan_version"] == plan["plan_version"]


def test_final_holdout_statistics_are_not_used(monkeypatch):
    frame = _balanced_binary()
    frame.insert(0, SOURCE_ROW_COLUMN, np.arange(len(frame)))
    seen: list[pd.DataFrame] = []
    from app.engine.modeling import leakage_auditor as auditor

    original = auditor.audit_leakage

    def spy(train, **kwargs):
        seen.append(train.copy())
        return original(train, **kwargs)

    monkeypatch.setattr(auditor, "audit_leakage", spy)
    task = _task(frame, target="outcome", task_type="binary", metric="pr_auc")
    with tempfile.TemporaryDirectory() as tmp:
        result = run_experiment(
            frame,
            task,
            SearchConfig(strategy="open_ingest", max_candidates=8, seed=42),
            artifact_dir=Path(tmp),
        )
    assert seen
    audited = seen[0]
    train_rows = set(result["split"]["train_source_rows"])
    test_rows = set(result["split"]["test_source_rows"])
    observed = set(audited[SOURCE_ROW_COLUMN].tolist())
    assert observed == train_rows
    assert observed.isdisjoint(test_rows)


def test_repeated_entity_validation_from_phase1a_still_works():
    frame = _repeated_entity_binary()
    profile = build_problem_profile(frame, target="outcome", task_type="binary")
    plan = plan_validation(profile, y=frame["outcome"], frame=frame)
    assert plan.strategy in {"StratifiedGroupKFold", "GroupKFold"}
    assert plan.group_column == "customer_id"
    task = _task(frame, target="outcome", task_type="binary", metric="pr_auc", entity_id="customer_id")
    with tempfile.TemporaryDirectory() as tmp:
        result = run_experiment(
            frame,
            task,
            SearchConfig(strategy="open_ingest", max_candidates=8, seed=42),
            artifact_dir=Path(tmp),
        )
    assert result["status"] == "COMPLETED"
    assert result["validation_plan"]["group_column"] == "customer_id"
    assert "customer_id" not in result["model_development_plan"]["allowed_features"]
    assert "customer_id" not in _candidate_features(result)
