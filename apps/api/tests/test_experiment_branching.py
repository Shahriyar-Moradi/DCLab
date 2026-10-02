"""P2.4-A: branch experiments with typed change sets (ADR 0006 §4).

Real auto-train runs, no engine mocks. Every branch reuses its parent's locked
SplitPlan and source DatasetVersion: the holdout rows and outer folds are
identical (and are never re-derived), each change visibly takes effect, the
diff vs the parent is cached on the child, and invalid changes are refused with
typed errors before anything is written.
"""

from __future__ import annotations

import math
from uuid import uuid4

import numpy as np
import pandas as pd
import pytest
from sqlalchemy import func, select, text
from sqlalchemy.exc import DBAPIError

from app.db.models import (
    DEFAULT_WORKSPACE_ID,
    ClientLabUpload,
    EvaluationMetric,
    ExecutionRequest,
    Experiment,
    ExperimentCandidate,
    MlJob,
    ModelEvaluation,
    ModelSelectionDecision,
    PipelineScientificPlan,
    SplitPlan,
    User,
    UserRole,
    WorkflowRun,
    Workspace,
)
from app.domain.errors import (
    ExperimentComparisonError,
    ExperimentNotBranchableError,
    ExperimentNotFoundError,
    InvalidChangeSetError,
)
from app.engine.lab.auto_prepare import ColumnMissingDecision, MissingValuePlan
from app.services import split_plan_service
from app.services.auth_service import create_user
from app.services.auto_train.branch import (
    BranchRun,
    apply_missing_value_overrides,
    apply_role_overrides,
    forced_datetime_action,
)
from app.services.auto_train_service import run_auto_train_job
from app.services.experiment_branch_service import (
    ParentContext,
    branch_experiment,
    compare_experiments,
    materialize_branch,
    parse_change_set,
)
from app.services.lineage_service import seed_business_domains
from app.services.problem_spec_service import create_problem_spec
from app.services.project_service import get_or_create_labs_project


def _frame(n: int = 240, seed: int = 7) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    plan = rng.choice(["basic", "plus", "pro"], n)
    tenure = rng.integers(1, 72, n).astype(float)
    visits = rng.integers(0, 4, n)
    logit = -0.6 + 0.9 * (plan == "basic") - 0.02 * tenure + 0.3 * visits
    label = rng.binomial(1, 1 / (1 + np.exp(-logit)))
    sparse = rng.normal(0, 1, n)
    sparse[rng.random(n) < 0.6] = np.nan  # > 50% missing: dropped by the rule
    start = pd.Timestamp("2020-01-01")
    signup = [(start + pd.Timedelta(days=int(d))).strftime("%Y-%m-%d") for d in rng.integers(0, 900, n)]
    return pd.DataFrame(
        {
            "tenure": tenure,
            "spend": rng.uniform(20, 120, n),
            "plan": plan,
            "visits": visits,
            "sparse": sparse,
            "signup": signup,  # date text not named like a date: not converted by default
            "leak": np.where(label == 1, "yes", "no"),  # excluded by the leakage plan
            "label": np.where(label == 1, "yes", "no"),
        }
    )


def _parent(auth_client, db, client_user, monkeypatch) -> Experiment:
    monkeypatch.setattr("app.services.client_lab_upload_service.enqueue_auto_train", lambda _id: None)
    monkeypatch.setattr("app.services.auto_train_service.enqueue_auto_train", lambda _id: None)
    seed_business_domains(db)
    project = get_or_create_labs_project(db, workspace_id=DEFAULT_WORKSPACE_ID, actor=client_user)
    spec = create_problem_spec(
        db,
        actor=client_user,
        workspace_id=DEFAULT_WORKSPACE_ID,
        project_id=project.id,
        task_type="binary",
        business_objective="Predict label.",
        target_column="label",
        status="locked",
    )
    db.commit()
    response = auth_client.post(
        "/app/labs/uploads",
        data={"category": "Revenue", "problem_spec_id": str(spec.id)},
        files={"file": ("rows.csv", _frame().to_csv(index=False).encode(), "text/csv")},
    )
    assert response.status_code == 200, response.text
    upload_id = response.json()["run_id"]
    run_auto_train_job(db, upload_id)
    db.expire_all()
    upload = db.get(ClientLabUpload, upload_id)
    assert upload.pipeline_status == "completed", upload.pipeline_log.get("reason")
    parent = db.get(Experiment, upload.experiment_id)
    assert parent.split_plan_id is not None and parent.scientific_evidence_locked_at is not None
    return parent


def _no_rederivation(*_args, **_kwargs):
    raise AssertionError("a branch applies its parent's stored map; it never re-splits")


def _branch(db, actor, parent, changes, monkeypatch, *, intent="try one change") -> Experiment:
    result = branch_experiment(
        db, actor=actor, workspace_id=parent.workspace_id, parent_id=parent.id, changes=changes, intent=intent
    )
    assert result.created and result.experiment.change_set is not None
    with monkeypatch.context() as patch:
        # Neither the holdout lock, the plan stage nor the runner may re-derive
        # the holdout or the outer folds: any call fails the branch run.
        patch.setattr("app.engine.experiments.runner.iter_validation_folds", _no_rederivation)
        patch.setattr("app.engine.experiments.runner.split_train_test_holdout", _no_rederivation)
        patch.setattr("app.services.auto_train.holdout.split_train_test_holdout", _no_rederivation)
        patch.setattr("app.services.auto_train.split_plan.iter_validation_folds", _no_rederivation)
        run_auto_train_job(db, result.upload.id)
    db.expire_all()
    upload = db.get(ClientLabUpload, result.upload.id)
    assert upload.pipeline_status == "completed", upload.pipeline_log.get("reason")
    child = db.get(Experiment, result.experiment.id)
    _assert_same_lineage_and_partition(db, parent, child)
    _assert_comparison(db, parent, child)
    assert all(row["applied"] for row in child.result["branch"]["applied_changes"]), child.result["branch"]
    return child


def _folds(experiment: Experiment, candidate_id: str = "majority") -> dict[int, list[int]]:
    row = next(c for c in experiment.result["candidates"] if c["candidate_id"] == candidate_id)
    return {int(f["fold_number"]): sorted(f["validation_provenance"]) for f in row["folds"]}


def _assert_same_lineage_and_partition(db, parent: Experiment, child: Experiment) -> None:
    assert child.parent_pipeline_run_id == parent.id
    assert child.split_plan_id == parent.split_plan_id
    assert child.source_dataset_id == parent.source_dataset_id
    assert (child.workspace_id, child.project_id) == (parent.workspace_id, parent.project_id)
    assert child.dataset_id != parent.dataset_id  # its own prepared dataset, as every run
    assert child.scientific_evidence_locked_at is not None and child.status == "COMPLETED"
    p_split, c_split = parent.result["split"], child.result["split"]
    assert sorted(c_split["test_source_rows"]) == sorted(p_split["test_source_rows"])
    assert sorted(c_split["train_source_rows"]) == sorted(p_split["train_source_rows"])
    assert c_split["partitioned_by"] == "split_plan_assignment"
    assert _folds(child) == _folds(parent)
    assert db.scalar(select(func.count()).select_from(SplitPlan)) == 1
    plan = db.get(SplitPlan, parent.split_plan_id)
    digests = {
        row.pipeline_run_id: row.holdout_plan_digest
        for row in db.scalars(
            select(PipelineScientificPlan).where(
                PipelineScientificPlan.pipeline_run_id.in_([parent.id, child.id])
            )
        )
    }
    assert digests == {parent.id: plan.holdout_plan_digest, child.id: plan.holdout_plan_digest}
    verification = child.result["deterministic_verification"]
    checks = {c["check_id"]: c["status"] for c in verification["checks"]}
    assert checks["split_plan_consistent"] == "PASS" and not verification["failures"]
    # The threshold saw out-of-fold training rows only; the holdout was scored once.
    oof_rows = set(child.result["decision_threshold"]["oof_source_rows"])
    assert oof_rows and oof_rows.isdisjoint(c_split["test_source_rows"])
    assert child.result["final_test_evaluation"]["evaluation_count"] == 1


def _winner_metrics(db, experiment: Experiment) -> tuple[str, dict[str, float], dict[str, float]]:
    decision = db.scalar(
        select(ModelSelectionDecision).where(ModelSelectionDecision.pipeline_run_id == experiment.id)
    )
    winner = db.get(ExperimentCandidate, decision.selected_candidate_id)
    scopes: dict[str, dict[str, float]] = {"cv_aggregate": {}, "final_holdout": {}}
    rows = db.execute(
        select(ModelEvaluation.evaluation_scope, EvaluationMetric.metric_name, EvaluationMetric.metric_value)
        .join(EvaluationMetric, EvaluationMetric.model_evaluation_id == ModelEvaluation.id)
        .where(ModelEvaluation.candidate_id == winner.id)
    ).all()
    for scope, name, value in rows:
        if scope in scopes:
            scopes[scope][name] = value
    return winner.model_family, scopes["cv_aggregate"], scopes["final_holdout"]


def _assert_comparison(db, parent: Experiment, child: Experiment) -> None:
    diff = child.result["branch_comparison"]
    assert diff["status"] == "compared" and diff["authoritative"] is False
    assert diff["parent_experiment_id"] == str(parent.id)
    assert diff["split_plan_id"] == str(parent.split_plan_id)
    p_family, p_cv, p_holdout = _winner_metrics(db, parent)
    c_family, c_cv, c_holdout = _winner_metrics(db, child)
    assert diff["winner"]["family"] == {"parent": p_family, "child": c_family}
    for section, before, after in (("cv", p_cv, c_cv), ("holdout", p_holdout, c_holdout)):
        expected = (set(before) & set(after)) - {"decision_threshold"}
        assert expected and set(diff[section]) == expected
        for name in expected:
            row = diff[section][name]
            assert row["parent"] == pytest.approx(before[name]) and row["child"] == pytest.approx(after[name])
            assert row["delta"] == pytest.approx(after[name] - before[name])
    assert diff["winner"]["decision_threshold"]["parent"] == parent.result["decision_threshold"]["value"]
    assert diff["winner"]["decision_threshold"]["child"] == child.result["decision_threshold"]["value"]


def _candidates(experiment: Experiment) -> dict[str, dict]:
    return {row["candidate_id"]: row for row in experiment.result["candidates"]}


def _modeled(experiment: Experiment) -> tuple[set[str], set[str]]:
    pre = experiment.result["preprocessing"]
    return set(pre["numeric_columns"]), set(pre["categorical_columns"])


def _check_hyperparameter(parent, child):
    rf, before = _candidates(child)["random_forest"], _candidates(parent)["random_forest"]
    assert rf["status"] == "trained" and rf["hyperparameters"] == {"n_estimators": 37}
    assert before["hyperparameters"] == {} and rf["fingerprint"] != before["fingerprint"]
    assert child.config["branch_overrides"]["hyperparameters"] == {"random_forest": {"n_estimators": 37}}


def _check_exclude(parent, child):
    assert "xgboost" in {row["model_family"] for row in parent.result["candidates"]}
    families = {row["model_family"] for row in child.result["candidates"]}
    assert "xgboost" not in families and "majority" in families  # the baseline always stays
    assert child.config["branch_overrides"]["families_exclude"] == ["xgboost"]


def _check_include(parent, child):
    assert "extra_trees" not in _candidates(parent)
    assert _candidates(child)["extra_trees"]["status"] == "trained"


def _check_balanced(parent, child):
    assert not [cid for cid in _candidates(parent) if "__balanced" in cid]
    balanced = {cid: row for cid, row in _candidates(child).items() if cid.endswith("__balanced")}
    # Every supporting family gets its variant: the count cap never cuts a requested change.
    assert set(balanced) == {
        f"{family}__balanced"
        for family in ("logistic_regression", "random_forest", "xgboost", "lightgbm", "catboost")
    }
    assert balanced["logistic_regression__balanced"]["hyperparameters"] == {"class_weight": "balanced"}
    assert balanced["catboost__balanced"]["hyperparameters"] == {"auto_class_weights": "Balanced"}
    assert all(row["status"] == "trained" for row in balanced.values())


def _check_custom_weights(parent, child):
    weighted = {cid: row for cid, row in _candidates(child).items() if cid.endswith("__weighted")}
    assert set(weighted) == {
        f"{family}__weighted" for family in ("logistic_regression", "random_forest", "lightgbm", "xgboost")
    }
    # Unnamed classes are weighted 1.0 explicitly (label "0" -> code "0").
    assert weighted["random_forest__weighted"]["hyperparameters"] == {"class_weight": {"0": 1.0, "1": 2.5}}
    assert child.config["branch_overrides"]["class_weighting"]["labels"] == {"0": "0", "1": "1"}
    assert weighted["xgboost__weighted"]["hyperparameters"] == {"scale_pos_weight": 2.5}
    # String label keys reach the estimator as integer class codes: they train.
    assert all(row["status"] == "trained" for row in weighted.values())


def _check_metric(parent, child):
    assert parent.result["metric_plan"]["primary_metric"] == "pr_auc"
    assert child.result["metric_plan"]["primary_metric"] == "balanced_accuracy"
    assert child.result["selection"]["selection_metric"] == "balanced_accuracy"
    assert child.config["objective"]["primary_metric"] == "balanced_accuracy"
    assert child.config["objective"]["primary_metric_reason"] == "classes matter equally"


def _check_threshold(parent, child):
    assert parent.result["decision_threshold"]["source"] == "default"
    decision = child.result["decision_threshold"]
    assert decision["source"] == "constraints" and decision["value"] != 0.5
    assert [row["metric"] for row in decision["constraints"]] == ["recall"]
    assert child.config["objective"]["constraints"] == [{"metric": "recall", "op": ">=", "value": 0.75}]


def _check_datetime(parent, child):
    assert "signup" not in set().union(*_modeled(parent))
    assert "signup" in child.result["feature_engineering"]["transformed_features"]
    assert "signup" in _modeled(child)[0]


def _check_categorical(parent, child):
    assert "visits" in _modeled(parent)[0]
    numeric, categorical = _modeled(child)
    assert "visits" in categorical and "visits" not in numeric


def _check_drop_and_undrop(parent, child):
    assert "sparse" in parent.result["cleaning"]["dropped_columns"]
    numeric, _ = _modeled(child)
    assert "sparse" in numeric and "tenure" not in numeric
    assert "tenure" in child.result["cleaning"]["dropped_columns"]


BRANCH_CASES = [
    (
        "hyperparameter_override",
        [{"kind": "hyperparameter_override", "family": "random_forest", "parameters": {"n_estimators": 37}}],
        _check_hyperparameter,
    ),
    ("family_exclude", [{"kind": "family_exclude", "family": "xgboost"}], _check_exclude),
    ("family_include", [{"kind": "family_include", "family": "extra_trees"}], _check_include),
    ("class_weighting_balanced", [{"kind": "class_weighting", "mode": "balanced"}], _check_balanced),
    (
        "class_weighting_custom",
        [{"kind": "class_weighting", "mode": "custom", "weights": {"1": 2.5}}],
        _check_custom_weights,
    ),
    (
        "metric_override",
        [{"kind": "metric_override", "primary_metric": "balanced_accuracy", "reason": "classes matter equally"}],
        _check_metric,
    ),
    (
        "threshold_objective",
        [{"kind": "threshold_objective", "constraints": [{"metric": "recall", "op": ">=", "value": 0.75}]}],
        _check_threshold,
    ),
    (
        "feature_transform_add_datetime",
        [
            {"kind": "feature_transform_add", "column": "signup", "transform": "datetime_extract"},
            {"kind": "feature_transform_add", "column": "signup", "transform": "impute_median"},
        ],
        _check_datetime,
    ),
    (
        "feature_transform_add_categorical",
        [{"kind": "feature_transform_add", "column": "visits", "transform": "impute_most_frequent"}],
        _check_categorical,
    ),
    (
        "feature_transform_remove_and_drop",
        [
            {"kind": "feature_transform_remove", "column": "sparse", "transform": "drop_column"},
            {"kind": "feature_transform_add", "column": "tenure", "transform": "drop_column"},
        ],
        _check_drop_and_undrop,
    ),
]


@pytest.mark.parametrize(
    "changes, check", [case[1:] for case in BRANCH_CASES], ids=[case[0] for case in BRANCH_CASES]
)
def test_branch_applies_change_on_parent_split_plan(
    auth_client, db_session, client_user, monkeypatch, changes, check
):
    db = db_session
    parent = _parent(auth_client, db, client_user, monkeypatch)
    child = _branch(db, client_user, parent, changes, monkeypatch, intent="  why:\x00 test\n ")
    check(parent, child)
    assert child.intent == "why: test"
    assert child.change_set == {"schema_version": 1, "changes": changes}
    request = db.scalar(select(ExecutionRequest).where(ExecutionRequest.pipeline_run_id == child.id))
    assert request.operation == "model_build" and request.workspace_id == parent.workspace_id
    assert request.request_spec["parent_experiment_id"] == str(parent.id)
    assert request.request_spec["change_set_digest"] == child.config["branch_overrides"]["change_set_digest"]
    assert db.scalar(select(MlJob).where(MlJob.pipeline_run_id == child.id)).status is not None
    # The parent's evidence is untouched by its branch.
    assert db.get(Experiment, parent.id).result["split"] == parent.result["split"]


def test_branch_of_a_branch_inherits_materialized_changes(auth_client, db_session, client_user, monkeypatch):
    db = db_session
    parent = _parent(auth_client, db, client_user, monkeypatch)
    child = _branch(db, client_user, parent, [{"kind": "family_exclude", "family": "xgboost"}], monkeypatch)
    grandchild = _branch(
        db,
        client_user,
        child,
        [{"kind": "metric_override", "primary_metric": "roc_auc", "reason": "ranking quality"}],
        monkeypatch,
    )
    # parent ⊕ child ⊕ grandchild, materialized: no ancestor walk at run time.
    assert grandchild.config["branch_overrides"]["families_exclude"] == ["xgboost"]
    assert "xgboost" not in {row["model_family"] for row in grandchild.result["candidates"]}
    assert grandchild.result["metric_plan"]["primary_metric"] == "roc_auc"
    assert sorted(grandchild.result["split"]["test_source_rows"]) == sorted(parent.result["split"]["test_source_rows"])
    assert grandchild.result["branch_comparison"]["parent_experiment_id"] == str(child.id)


NAN, INF = float("nan"), math.inf
INVALID_CASES = [
    ([{"kind": "family_include", "family": "not_a_family"}], "unknown_family", "changes[0].family"),
    ([{"kind": "family_exclude", "family": "majority"}], "baseline_not_excludable", "changes[0]"),
    ([{"kind": "family_exclude", "family": "extra_trees"}], "family_not_in_portfolio", "changes[0].family"),
    ([{"kind": "family_include", "family": "random_forest"}], "family_already_in_portfolio", "changes[0].family"),
    (
        [{"kind": "feature_transform_add", "column": "spend", "transform": "log1p"}],
        "schema",
        "changes[0].transform",
    ),
    (
        [{"kind": "feature_transform_add", "column": "leak", "transform": "keep"}],
        "leakage_excluded_column",
        "changes[0].column",
    ),
    (
        [{"kind": "feature_transform_remove", "column": "leak", "transform": "drop_column"}],
        "leakage_excluded_column",
        "changes[0].column",
    ),
    ([{"kind": "feature_transform_add", "column": "label", "transform": "keep"}], "reserved_column", None),
    ([{"kind": "feature_transform_add", "column": "nope", "transform": "keep"}], "unknown_column", None),
    (
        [{"kind": "feature_transform_remove", "column": "spend", "transform": "drop_column"}],
        "transform_not_applied",
        "changes[0].transform",
    ),
    (
        [{"kind": "feature_transform_add", "column": "plan", "transform": "impute_median"}],
        "transform_not_applicable",
        None,
    ),
    (
        [{"kind": "feature_transform_add", "column": "spend", "transform": "datetime_extract"}],
        "transform_not_applicable",
        "changes[0].transform",
    ),
    (
        [
            {"kind": "feature_transform_add", "column": "spend", "transform": "impute_median"},
            {"kind": "feature_transform_add", "column": "spend", "transform": "impute_most_frequent"},
        ],
        "conflicting_transforms",
        None,
    ),
    (
        [{"kind": "metric_override", "primary_metric": "rmse", "reason": "x"}],
        "invalid_primary_metric",
        "changes[0].primary_metric",
    ),
    (
        [{"kind": "hyperparameter_override", "family": "random_forest", "parameters": {"n_estimators": NAN}}],
        "non_finite_value",
        "changes[0].parameters.n_estimators",
    ),
    (
        [{"kind": "threshold_objective", "constraints": [{"metric": "recall", "op": ">=", "value": INF}]}],
        "non_finite_value",
        "changes[0].constraints[0].value",
    ),
    (
        [{"kind": "hyperparameter_override", "family": "random_forest", "parameters": {"max_depth": 3}}],
        "unknown_hyperparameter",
        "changes[0].parameters.max_depth",
    ),
    (
        [{"kind": "hyperparameter_override", "family": "random_forest", "parameters": {"n_estimators": 0}}],
        "invalid_hyperparameter_value",
        None,
    ),
    (
        [{"kind": "hyperparameter_override", "family": "xgboost_regressor", "parameters": {"n_estimators": 9}}],
        "unknown_family",
        None,
    ),
    ([{"kind": "class_weighting", "mode": "custom", "weights": {"maybe": 2.0}}], "unknown_class_label", None),
    (
        [{"kind": "threshold_objective", "constraints": [{"metric": "rmse", "op": "<=", "value": 1.0}]}],
        "invalid_constraint_metric",
        None,
    ),
    ([{"kind": "threshold_objective", "cost_false_positive": 1.0}], "invalid_cost_matrix", None),
    (
        [{"kind": "family_exclude", "family": "xgboost"}, {"kind": "family_exclude", "family": "xgboost"}],
        "schema",
        None,
    ),
    ([], "schema", None),
    ({"changes": [{"kind": "family_exclude", "family": "xgboost"}], "dataset_id": str(uuid4())}, "new_root_required", "dataset_id"),
    ([{"kind": "target_change", "column": "spend"}], "new_root_required", "changes[0].kind"),
    ([{"kind": "holdout_change", "test_size": 0.3}], "new_root_required", "changes[0].kind"),
    ([{"kind": "family_exclude", "family": "xgboost", "split_plan_id": str(uuid4())}], "new_root_required", None),
]


def test_invalid_changes_are_rejected_with_typed_errors(auth_client, db_session, client_user, monkeypatch):
    db = db_session
    parent = _parent(auth_client, db, client_user, monkeypatch)
    before = db.scalar(select(func.count()).select_from(Experiment))
    uploads = db.scalar(select(func.count()).select_from(ClientLabUpload))
    for changes, reason, path in INVALID_CASES:
        with pytest.raises(InvalidChangeSetError) as caught:
            branch_experiment(
                db,
                actor=client_user,
                workspace_id=parent.workspace_id,
                parent_id=parent.id,
                changes=changes,
                intent="should fail",
            )
        detail = caught.value.public_detail()
        assert (detail["code"], detail["reason"]) == ("invalid_change_set", reason), (changes, detail)
        if path is not None:
            assert detail["path"] == path, (changes, detail)
    with pytest.raises(InvalidChangeSetError) as caught:
        branch_experiment(
            db,
            actor=client_user,
            workspace_id=parent.workspace_id,
            parent_id=parent.id,
            changes=[{"kind": "family_exclude", "family": "xgboost"}],
            intent=" \x00 ",
        )
    assert caught.value.reason == "intent_required"
    # Nothing was written for any rejected change set.
    db.rollback()
    assert db.scalar(select(func.count()).select_from(Experiment)) == before
    assert db.scalar(select(func.count()).select_from(ClientLabUpload)) == uploads


def _branch_run(columns: dict) -> BranchRun:
    return BranchRun(
        experiment_id=uuid4(),
        parent_id=uuid4(),
        split_plan=None,
        target_column="label",
        task_type="binary",
        overrides={"columns": columns},
        objective=None,
    )


def test_runtime_guards_never_reinclude_excluded_or_identifier_columns():
    plan = MissingValuePlan(dropped_columns=[], column_decisions=[ColumnMissingDecision("leak", 0, 0.0, "keep")])
    with pytest.raises(InvalidChangeSetError) as caught:
        apply_missing_value_overrides(_branch_run({"leak": {"treatment": "keep"}}), plan, leakage_excluded={"leak"})
    assert caught.value.reason == "leakage_excluded_column"
    train = pd.DataFrame({"a": [1.0, 2.0, 3.0], "b": ["x", "y", "x"], "when": ["2021-01-01", "bad", "2021-03-01"]})
    for columns, kwargs, reason in (
        ({"a": {"treatment": "numeric"}}, {"allowed_predictors": {"b"}, "identifiers": set()}, "leakage_excluded_column"),
        ({"a": {"treatment": "keep"}}, {"allowed_predictors": {"a"}, "identifiers": {"a"}}, "identifier_column"),
        ({"b": {"treatment": "numeric"}}, {"allowed_predictors": {"b"}, "identifiers": set()}, "transform_not_applicable"),
    ):
        with pytest.raises(InvalidChangeSetError) as caught:
            apply_role_overrides(_branch_run(columns), train, [], [], **kwargs)
        assert caught.value.reason == reason
    # A number is never a date; text must parse as dates on >= 80% of train rows.
    for column in ("a", "when"):
        with pytest.raises(InvalidChangeSetError) as caught:
            forced_datetime_action(train, [column], set())
        assert caught.value.reason == "transform_not_applicable"


def _context(**fields) -> ParentContext:
    defaults = dict(
        experiment=None,
        upload=None,
        workflow_run=None,
        task_type="multiclass",
        target_column="label",
        overrides={},
        objective=None,
        portfolio=["logistic_regression", "random_forest"],
        dataset_columns={"label", "x", "account_id"},
        reserved_columns={"label"},
        leakage_excluded=set(),
        identifiers={"account_id"},
        dropped=set(),
        numeric={"x"},
        categorical=set(),
        datetime_converted=set(),
        numeric_dtype={"x", "account_id"},
        observed_classes=["2", "10", "100"],  # the runner's sorted label order -> codes 0, 1, 2
    )
    return ParentContext(**{**defaults, **fields})


def test_custom_weights_use_the_runner_label_codes_and_identifiers_stay_out():
    overrides, _ = materialize_branch(
        parse_change_set([{"kind": "class_weighting", "mode": "custom", "weights": {"100": 3.0}}]), _context()
    )
    assert overrides["class_weighting"] == {
        "mode": "custom",
        "weights": {"0": 1.0, "1": 1.0, "2": 3.0},
        "labels": {"0": "2", "1": "10", "2": "100"},
    }
    with pytest.raises(InvalidChangeSetError) as caught:
        materialize_branch(
            parse_change_set([{"kind": "feature_transform_add", "column": "account_id", "transform": "keep"}]),
            _context(),
        )
    assert caught.value.reason == "identifier_column"


def _other_tenant(db) -> tuple[Workspace, User]:
    workspace = Workspace(slug=f"branch-other-{uuid4().hex[:8]}", name="Other")
    db.add(workspace)
    db.flush()
    user = create_user(
        db,
        email=f"branch-other-{uuid4().hex}@test.invalid",
        password="test-password",
        role=UserRole.BUSINESS_ADMIN,
        workspace_id=workspace.id,
    )
    db.commit()
    return workspace, user


def test_branch_preconditions_tenancy_and_idempotency(auth_client, db_session, client_user, monkeypatch):
    db = db_session
    parent = _parent(auth_client, db, client_user, monkeypatch)
    changes = [{"kind": "family_exclude", "family": "xgboost"}]
    other_workspace, other_user = _other_tenant(db)
    for actor, workspace_id, parent_id in (
        (other_user, other_workspace.id, parent.id),  # another tenant's experiment
        (client_user, other_workspace.id, parent.id),  # a workspace the actor cannot use
        (client_user, parent.workspace_id, uuid4()),  # no such experiment
    ):
        with pytest.raises(ExperimentNotFoundError):
            branch_experiment(
                db, actor=actor, workspace_id=workspace_id, parent_id=parent_id, changes=changes, intent="x"
            )

    first = branch_experiment(
        db,
        actor=client_user,
        workspace_id=parent.workspace_id,
        parent_id=parent.id,
        changes=changes,
        intent="drop xgboost",
        idempotency_key="branch-key-1",
    )
    replay = branch_experiment(
        db,
        actor=client_user,
        workspace_id=parent.workspace_id,
        parent_id=parent.id,
        changes=changes,
        intent="drop xgboost",
        idempotency_key="branch-key-1",
    )
    assert first.created and not replay.created and replay.experiment.id == first.experiment.id
    with pytest.raises(ExperimentNotBranchableError) as caught:
        branch_experiment(
            db,
            actor=client_user,
            workspace_id=parent.workspace_id,
            parent_id=parent.id,
            changes=[{"kind": "family_exclude", "family": "lightgbm"}],
            intent="other change",
            idempotency_key="branch-key-1",
        )
    assert caught.value.code == "idempotency_key_conflict"

    # The new run envelope lives in the parent's tenant and project, on its source dataset.
    child = db.get(Experiment, first.experiment.id)
    workflow_run = db.get(WorkflowRun, child.workflow_run_id)
    parent_run = db.get(WorkflowRun, parent.workflow_run_id)
    assert child.status == "CREATED" and child.split_plan_id == parent.split_plan_id
    assert first.upload.dataset_id == parent.source_dataset_id
    assert first.upload.workspace_id == workflow_run.workspace_id == parent.workspace_id
    assert workflow_run.project_id == parent.project_id
    assert workflow_run.problem_spec_id == parent_run.problem_spec_id
    assert first.ml_job.workspace_id == parent.workspace_id and first.ml_job.upload_id == first.upload.id
    # Another tenant cannot branch from the new child either.
    with pytest.raises(ExperimentNotFoundError):
        branch_experiment(
            db, actor=other_user, workspace_id=other_workspace.id, parent_id=child.id, changes=changes, intent="x"
        )
    # A parent that has not completed (no locked evidence) cannot be branched.
    with pytest.raises(ExperimentNotBranchableError) as caught:
        branch_experiment(
            db, actor=client_user, workspace_id=parent.workspace_id, parent_id=child.id, changes=changes, intent="x"
        )
    assert caught.value.code == "parent_not_completed"


def test_parent_without_split_plan_cannot_be_branched(auth_client, db_session, client_user, monkeypatch):
    db = db_session

    def unavailable(*_args, **_kwargs):
        raise split_plan_service.SplitPlanUnavailable("forced plan-less run")

    with monkeypatch.context() as patch:
        patch.setattr("app.services.auto_train.split_plan.require_partition_source", unavailable)
        monkeypatch.setattr("app.services.client_lab_upload_service.enqueue_auto_train", lambda _id: None)
        seed_business_domains(db)
        response = auth_client.post(
            "/app/labs/uploads",
            data={"category": "Revenue", "target_column": "label"},
            files={"file": ("rows.csv", _frame().to_csv(index=False).encode(), "text/csv")},
        )
        upload_id = response.json()["run_id"]
        run_auto_train_job(db, upload_id)
    db.expire_all()
    legacy = db.get(Experiment, db.get(ClientLabUpload, upload_id).experiment_id)
    assert legacy.split_plan_id is None and legacy.scientific_evidence_locked_at is not None
    with pytest.raises(ExperimentNotBranchableError) as caught:
        branch_experiment(
            db,
            actor=client_user,
            workspace_id=legacy.workspace_id,
            parent_id=legacy.id,
            changes=[{"kind": "family_exclude", "family": "xgboost"}],
            intent="x",
        )
    assert caught.value.code == "parent_without_split_plan"


def _insert_forged_branch(db, parent: Experiment, *, source_dataset_id, split_plan_id) -> None:
    db.execute(
        text(
            "INSERT INTO experiments (id, workspace_id, project_id, environment_id, dataset_id, "
            "status, config, seed, parent_pipeline_run_id, source_dataset_id, split_plan_id, "
            "change_set, intent) "
            "SELECT :id, workspace_id, project_id, environment_id, dataset_id, 'CREATED', "
            "'{}'::jsonb, 42, id, :source, :split, "
            "CAST('{\"schema_version\": 1, \"changes\": [{\"kind\": \"family_exclude\", "
            "\"family\": \"xgboost\"}]}' AS jsonb), 'forged' FROM experiments WHERE id = :parent"
        ),
        {"id": uuid4(), "parent": parent.id, "source": source_dataset_id, "split": split_plan_id},
    )
    db.commit()


def test_lineage_guard_rejects_forged_branches_at_db_level(auth_client, db_session, client_user, monkeypatch):
    db = db_session
    parent = _parent(auth_client, db, client_user, monkeypatch)
    plan = db.get(SplitPlan, parent.split_plan_id)
    # A second, valid plan in the same project (a new root's plan, e.g. another seed).
    other = SplitPlan(
        **{
            column.key: getattr(plan, column.key)
            for column in SplitPlan.__table__.columns
            if column.key not in {"id", "created_at", "locked_at"}
        }
    )
    other.id, other.version, other.plan_digest = uuid4(), plan.version + 1, "f" * 64
    db.add(other)
    db.commit()
    with pytest.raises(DBAPIError, match="share split_plan_id"):
        _insert_forged_branch(db, parent, source_dataset_id=parent.source_dataset_id, split_plan_id=other.id)
    db.rollback()
    with pytest.raises(DBAPIError, match="share split_plan_id"):
        _insert_forged_branch(db, parent, source_dataset_id=parent.source_dataset_id, split_plan_id=None)
    db.rollback()
    with pytest.raises(DBAPIError, match="share source_dataset_id"):
        _insert_forged_branch(db, parent, source_dataset_id=parent.dataset_id, split_plan_id=plan.id)
    db.rollback()
    # The service's own child passes the guard; its lineage columns are then frozen.
    child = branch_experiment(
        db,
        actor=client_user,
        workspace_id=parent.workspace_id,
        parent_id=parent.id,
        changes=[{"kind": "family_exclude", "family": "xgboost"}],
        intent="guarded",
    ).experiment
    for statement in (
        "UPDATE experiments SET change_set = NULL WHERE id = :id",
        "UPDATE experiments SET intent = 'rewritten' WHERE id = :id",
        f"UPDATE experiments SET split_plan_id = '{other.id}' WHERE id = :id",
    ):
        with pytest.raises(DBAPIError):
            db.execute(text(statement), {"id": child.id})
            db.commit()
        db.rollback()


def test_branch_whose_holdout_or_folds_would_differ_fails_closed(
    auth_client, db_session, client_user, monkeypatch
):
    """A branch never plans: another holdout or validation identity is a failed run, not a new plan."""

    from dataclasses import replace

    from app.services.auto_train import decisions as decisions_stage
    from app.services.auto_train import holdout as holdout_stage

    db = db_session
    parent = _parent(auth_client, db, client_user, monkeypatch)
    real_holdout, real_development = holdout_stage.plan_holdout, decisions_stage.plan_model_development

    def other_seed_holdout(*args, **kwargs):
        return replace(real_holdout(*args, **kwargs), random_state=7)

    def other_seed_folds(*args, **kwargs):
        profile, validation, *rest = real_development(*args, **kwargs)
        return (profile, replace(validation, random_state=7), *rest)

    def unavailable(*_args, **_kwargs):
        raise split_plan_service.SplitPlanUnavailable("forced")

    for target, patched, stage, code in (
        ("app.services.auto_train.holdout.plan_holdout", other_seed_holdout, "holdout", "branch_split_plan_mismatch"),
        (
            "app.services.auto_train.decisions.plan_model_development",
            other_seed_folds,
            "folds",
            "branch_split_plan_mismatch",
        ),
        # Never the root fallback (a per-run split) for a branch.
        ("app.services.auto_train.split_plan.resolve_split_plan", unavailable, "plan", "split_plan_missing"),
    ):
        result = branch_experiment(
            db,
            actor=client_user,
            workspace_id=parent.workspace_id,
            parent_id=parent.id,
            changes=[{"kind": "family_exclude", "family": "xgboost"}],
            intent=f"drifted {stage}",
        )
        with monkeypatch.context() as patch:
            patch.setattr(target, patched)
            run_auto_train_job(db, result.upload.id)
        db.expire_all()
        upload = db.get(ClientLabUpload, result.upload.id)
        child = db.get(Experiment, result.experiment.id)
        assert upload.pipeline_status == "failed", stage
        assert code in upload.pipeline_log["reason"], upload.pipeline_log["reason"]
        assert child.scientific_evidence_locked_at is None and child.split_plan_id == parent.split_plan_id
        assert db.scalar(select(func.count()).select_from(SplitPlan)) == 1


def test_comparison_requires_the_same_split_plan():
    workspace_id = uuid4()
    parent = Experiment(id=uuid4(), workspace_id=workspace_id, split_plan_id=uuid4())
    for other_plan in (uuid4(), None):
        child = Experiment(id=uuid4(), workspace_id=workspace_id, split_plan_id=other_plan)
        with pytest.raises(ExperimentComparisonError) as caught:
            compare_experiments(None, parent, child)
        assert caught.value.code == "split_plan_mismatch"
    stranger = Experiment(id=uuid4(), workspace_id=uuid4(), split_plan_id=parent.split_plan_id)
    with pytest.raises(ExperimentComparisonError) as caught:
        compare_experiments(None, parent, stranger)
    assert caught.value.code == "not_comparable"
