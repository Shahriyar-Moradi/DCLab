"""P2.4-B: per-experiment reproducible code export (GET /v1/experiments/{id}/code).

Real auto-train runs (a root run, a branch run and a plan-less legacy run). The
exported script is executed on the stored dataset version and the stored
SplitPlan map, both materialized from object storage into tmp_path; its CV
scores must equal the persisted ones and its holdout/fold rows the stored map.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

import numpy as np
import pandas as pd
import pytest
from sqlalchemy import select

from app.db.models import (
    EvaluationMetric,
    Experiment,
    ExperimentCandidate,
    ModelEvaluation,
    SplitPlan,
    UserRole,
)
from app.domain.model_build_reproduction import DATASET_PATH_ENV, SPLIT_ASSIGNMENT_PATH_ENV
from app.engine.modeling.validation_planner import (
    KFOLD,
    TIME_SERIES_SPLIT,
    ValidationPlan,
    folds_from_assignment,
)
from app.services import split_plan_service
from app.services.auth_service import create_access_token, create_user
from app.services.dataset_materialization import materialize_dataset
from app.engine.evaluation.metrics import (
    classification_metrics,
    multiclass_metrics,
    regression_metrics,
)
from app.engine.experiments.runner import _predict
from app.engine.models.registry import (
    applied_hyperparameters,
    available_families,
    implementation_for_family,
    make_model,
)
from app.services.model_build_codegen import (
    _METRIC_SOURCES,
    _PREDICT_SOURCES,
    SPLIT_ASSIGNMENT_HELPERS,
    _estimator_factory,
    _estimator_params,
)
from app.services.split_plan_service import load_assignment
from app.services.workspace_service import create_business_workspace
from dclab_client import DCLabClient
from test_experiment_branching import _branch, _parent

# Fold-level and aggregate CV scores must agree to this absolute tolerance. The
# script fits the same Pipelines on the same rows in the same order with the
# recorded (per-fold) constructor values, so the measured gap is 0.0.
CV_TOLERANCE = 1e-6
FORBIDDEN_IN_CODE = ("object_key", "/private/", "/Users/", "/tmp/", "Bearer ", "from app", "import app")


def _code(http, experiment: Experiment) -> dict:
    response = http.get(f"/v1/experiments/{experiment.id}/code")
    assert response.status_code == 200, response.text
    return response.json()


def _materialize(db, experiment: Experiment, tmp_path: Path) -> tuple[Path, Path | None]:
    data = tmp_path / f"{experiment.id}-dataset.csv"
    with materialize_dataset(experiment.dataset, db=db) as local:
        data.write_bytes(Path(local).read_bytes())
    if experiment.split_plan_id is None:
        return data, None
    assignment = tmp_path / f"{experiment.id}-assignment.csv"
    assignment.write_bytes(load_assignment(db, db.get(SplitPlan, experiment.split_plan_id)).to_csv_bytes())
    return data, assignment


def _execute(source: str, data: Path, assignment: Path | None, monkeypatch) -> dict:
    monkeypatch.setenv(DATASET_PATH_ENV, str(data))
    if assignment is not None:
        monkeypatch.setenv(SPLIT_ASSIGNMENT_PATH_ENV, str(assignment))
    namespace: dict = {}
    exec(compile(source, "<experiment-code.py>", "exec"), namespace, namespace)  # noqa: S102
    return namespace


def _persisted_scores(db, experiment: Experiment, metric: str) -> dict[str, tuple[dict[int, float], float]]:
    """fingerprint -> ({fold: primary metric}, cv_aggregate primary metric), read from the DB."""

    out: dict[str, tuple[dict[int, float], float]] = {}
    for candidate in db.scalars(
        select(ExperimentCandidate).where(
            ExperimentCandidate.experiment_id == experiment.id, ExperimentCandidate.status == "trained"
        )
    ):
        folds: dict[int, float] = {}
        aggregate = None
        for scope, summary, value in db.execute(
            select(ModelEvaluation.evaluation_scope, ModelEvaluation.summary, EvaluationMetric.metric_value)
            .join(EvaluationMetric, EvaluationMetric.model_evaluation_id == ModelEvaluation.id)
            .where(ModelEvaluation.candidate_id == candidate.id, EvaluationMetric.metric_name == metric)
        ):
            if scope == "cv_fold":
                folds[int(summary["fold_number"])] = value
            elif scope == "cv_aggregate":
                aggregate = value
        out[candidate.fingerprint] = (folds, aggregate)
    return out


def _assert_reproduces_cv(db, experiment: Experiment, body: dict, ns: dict) -> dict[str, float]:
    metric = experiment.result["metric_plan"]["primary_metric"]
    assert ns["PRIMARY_METRIC"] == metric
    persisted = _persisted_scores(db, experiment, metric)
    assert persisted and set(ns["reproduced_fold_scores"]) == set(persisted)
    gaps: dict[str, float] = {}
    for fingerprint, (folds, aggregate) in persisted.items():
        got = ns["reproduced_fold_scores"][fingerprint]
        assert set(got) == set(folds) == set(range(1, experiment.result["validation"]["actual_folds"] + 1))
        gap = max(abs(got[n] - folds[n]) for n in folds)
        gap = max(gap, abs(ns["reproduced_cv_scores"][fingerprint] - aggregate))
        assert gap <= CV_TOLERANCE, (fingerprint, got, folds, aggregate)
        gaps[fingerprint] = gap
    # The winner's primary CV metric and the CV-only selection reproduce too.
    assert ns["REPRODUCED_WINNER_FINGERPRINT"] == ns["WINNER_FINGERPRINT"]
    winner_folds, winner_aggregate = persisted[ns["WINNER_FINGERPRINT"]]
    assert ns["reproduced_cv_scores"][ns["WINNER_FINGERPRINT"]] == pytest.approx(winner_aggregate, abs=CV_TOLERANCE)
    # The locked winner is refit and scored on the holdout once, at the locked threshold.
    holdout = experiment.result["test_metrics"]
    assert ns["reproduced_holdout_metrics"][metric] == pytest.approx(holdout[metric], abs=CV_TOLERANCE)
    assert ns["DECISION_THRESHOLD"] == experiment.result["decision_threshold"]["value"]
    assert body["standalone_cv"] is True
    return gaps


def _assert_rows_follow_map(db, experiment: Experiment, ns: dict) -> None:
    assignment = load_assignment(db, db.get(SplitPlan, experiment.split_plan_id))
    column = ns["SOURCE_ROW_COLUMN"]
    assert set(ns["X_holdout"][column].astype(int)) == set(assignment.holdout_rows)
    train_sources = ns["X_train"][column].astype(int).to_numpy()
    assert set(train_sources) == set(assignment.train_folds)
    # The runner's recorded fold provenance (validation rows per fold) is the map's.
    recorded = next(row for row in experiment.result["candidates"] if row.get("status") == "trained")
    by_fold = {int(f["fold_number"]): set(f["validation_provenance"]) for f in recorded["folds"]}
    for number, train_idx, val_idx in ns["cv_folds"]:
        expected = {row for row, fold in assignment.train_folds.items() if fold == number}
        assert set(train_sources[val_idx]) == expected == by_fold[number]
        assert set(train_sources[train_idx]) == set(assignment.train_folds) - expected
    # Same row order as the run's training pool.
    assert [int(r) for r in train_sources] == [int(r) for r in experiment.result["split"]["train_source_rows"]]


def _assert_holdout_isolated(script: str) -> None:
    """Holdout rows are named only where they are locked and where the winner is scored once."""

    sections: dict[str, str] = {}
    current = ""
    for line in script.splitlines():
        if line.startswith("# === "):
            current = line.split(". ", 1)[1].rstrip(" =")
            continue
        if not line.lstrip().startswith("#"):
            sections[current] = sections.get(current, "") + line + "\n"
    users = {title for title, body in sections.items() if "X_holdout" in body or "y_holdout" in body}
    assert users == {"Holdout creation and lock", "Final holdout exactly once"}, users
    assert script.index("REPRODUCED_WINNER_FINGERPRINT =") < script.index("reproduced_holdout_metrics =")
    assert script.index("pipeline.fit(X_train[WINNER_COLUMNS]") < script.index("holdout_scores =")


def _assert_safe(body: dict, experiment: Experiment) -> None:
    for document in (body["script"]["source"], body["notebook"]["source"]):
        assert experiment.dataset.location not in document
        for fragment in FORBIDDEN_IN_CODE:
            assert fragment not in document, fragment
    names = {item["name"]: item for item in body["inputs"]}
    assert names["dataset"]["content_digest"] == experiment.dataset.content_digest
    assert names["dataset"]["artifact_id"] == str(experiment.dataset.artifact_id)


def test_root_and_branch_scripts_reproduce_cv_on_the_stored_map(
    auth_client, db_session, client_user, monkeypatch, tmp_path
):
    db = db_session
    parent = _parent(auth_client, db, client_user, monkeypatch)
    child = _branch(
        db,
        client_user,
        parent,
        [
            {"kind": "hyperparameter_override", "family": "random_forest", "parameters": {"n_estimators": 37}},
            {"kind": "class_weighting", "mode": "custom", "weights": {"1": 2.5}},
            {"kind": "feature_transform_add", "column": "signup", "transform": "datetime_extract"},
            {"kind": "feature_transform_add", "column": "signup", "transform": "impute_median"},
            {"kind": "metric_override", "primary_metric": "roc_auc", "reason": "ranking quality"},
            {"kind": "threshold_objective", "constraints": [{"metric": "recall", "op": ">=", "value": 0.75}]},
        ],
        monkeypatch,
    )
    plan = db.get(SplitPlan, parent.split_plan_id)
    for experiment in (parent, child):
        body = _code(auth_client, experiment)
        _assert_safe(body, experiment)
        assert body["generator_version"].endswith(".v2")
        assert body["split_plan_id"] == str(plan.id)
        assignment_input = next(item for item in body["inputs"] if item["name"] == "split_assignment")
        assert assignment_input["content_digest"] == plan.assignment_digest
        assert assignment_input["artifact_id"] == str(plan.assignment_artifact_id)
        assert "cv.split(" not in body["script"]["source"]  # folds come from the stored map
        _assert_holdout_isolated(body["script"]["source"])
        data, assignment = _materialize(db, experiment, tmp_path)
        ns = _execute(body["script"]["source"], data, assignment, monkeypatch)
        _assert_rows_follow_map(db, experiment, ns)
        _assert_reproduces_cv(db, experiment, body, ns)

    parent_ns_mode = _code(auth_client, parent)["script"]["source"]
    assert "PARTITIONED_BY = 'holdout_plan_resplit'" in parent_ns_mode
    body = _code(auth_client, child)
    script = body["script"]["source"]
    assert body["is_branch"] is True and body["parent_experiment_id"] == str(parent.id)
    assert "PARTITIONED_BY = 'split_plan_assignment'" in script
    # Branch changes come from what the run recorded, not from ancestors.
    assert f"PARENT_EXPERIMENT_ID = '{parent.id}'" in script
    assert repr(child.config["branch_overrides"]["change_set_digest"]) in script
    assert "'class_weight': {0: 1.0, 1: 2.5}" in script  # class codes, as make_model passes them
    assert "'n_estimators': 37" in script
    assert "PRIMARY_METRIC = 'roc_auc'" in script
    assert child.result["decision_threshold"]["value"] != 0.5
    assert f"DECISION_THRESHOLD = {child.result['decision_threshold']['value']!r}" in script
    assert "'signup'" in script and child.result["decision_threshold"]["source"] == "constraints"
    # The notebook carries the same code.
    assert "read_split_assignment" in body["notebook"]["source"]

    # Fail closed: tampered map bytes never partition the data.
    data, assignment = _materialize(db, child, tmp_path)
    assignment.write_bytes(assignment.read_bytes().replace(b",holdout,", b",train,1", 1))
    with pytest.raises(RuntimeError, match="ASSIGNMENT_DIGEST"):
        _execute(script, data, assignment, monkeypatch)

    # The script runs standalone (no DCLab imports) in a fresh interpreter.
    data, assignment = _materialize(db, child, tmp_path)
    script_path = tmp_path / body["script"]["filename"]
    script_path.write_text(script)
    env = {**os.environ, DATASET_PATH_ENV: str(data), SPLIT_ASSIGNMENT_PATH_ENV: str(assignment)}
    env.pop("PYTHONPATH", None)
    run = subprocess.run([sys.executable, str(script_path)], env=env, capture_output=True, text=True, timeout=300)
    assert run.returncode == 0, run.stderr[-2000:]
    assert "differs from the run" not in run.stdout


def test_sdk_returns_code_and_route_hides_other_tenants(
    client, auth_client, db_session, client_user, client_token, monkeypatch, tmp_path
):
    db = db_session
    parent = _parent(auth_client, db, client_user, monkeypatch)
    api = DCLabClient(
        base_url=str(client.base_url), token=client_token, workspace_id=parent.workspace_id, http=client
    )
    code = api.experiments.code(parent.id)
    assert code.experiment_id == parent.id and code.split_plan_id == parent.split_plan_id
    assert code.script.source.startswith('"""DCLab model-build reproduction script.')
    assert {item.name for item in code.inputs} == {"dataset", "split_assignment"}

    outsider = create_user(
        db,
        email=f"code-outsider-{uuid4().hex}@test.invalid",
        password="test-password",
        role=UserRole.WORKSPACE_OWNER,
        full_name="Outsider",
    )
    other = create_business_workspace(db, owner=outsider, name="Other tenant")
    db.commit()
    foreign = client.get(
        f"/v1/experiments/{parent.id}/code",
        headers={"Authorization": f"Bearer {create_access_token(outsider)}", "X-Workspace-Id": str(other.id)},
    )
    assert foreign.status_code == 404, foreign.text
    missing = client.get(
        f"/v1/experiments/{uuid4()}/code",
        headers={"Authorization": f"Bearer {client_token}", "X-Workspace-Id": str(parent.workspace_id)},
    )
    assert missing.status_code == 404
    client.headers.pop("Authorization", None)
    unauthenticated = client.get(
        f"/v1/experiments/{parent.id}/code", headers={"X-Workspace-Id": str(parent.workspace_id)}
    )
    assert unauthenticated.status_code == 401


def test_legacy_run_without_split_plan_still_exports_and_reproduces(
    auth_client, db_session, client_user, monkeypatch, tmp_path
):
    def unavailable(*_args, **_kwargs):
        raise split_plan_service.SplitPlanUnavailable("forced plan-less run")

    db = db_session
    with monkeypatch.context() as patch:
        patch.setattr("app.services.auto_train.split_plan.require_partition_source", unavailable)
        legacy = _parent_without_plan(auth_client, db, client_user, monkeypatch)
    body = _code(auth_client, legacy)
    _assert_safe(body, legacy)
    assert body["split_plan_id"] is None
    assert [item["name"] for item in body["inputs"]] == ["dataset"]
    script = body["script"]["source"]
    assert "read_split_assignment" not in script and "cv.split(X_train, y_train)" in script
    data, _none = _materialize(db, legacy, tmp_path)
    ns = _execute(script, data, None, monkeypatch)
    _assert_reproduces_cv(db, legacy, body, ns)


def _parent_without_plan(auth_client, db, client_user, monkeypatch) -> Experiment:
    """``_parent``'s upload + auto-train, for a run that ends without a SplitPlan."""

    from app.db.models import ClientLabUpload

    with pytest.raises(AssertionError):  # _parent insists on a split plan
        _parent(auth_client, db, client_user, monkeypatch)
    upload = db.scalars(select(ClientLabUpload).order_by(ClientLabUpload.created_at.desc())).first()
    experiment = db.get(Experiment, upload.experiment_id)
    assert upload.pipeline_status == "completed" and experiment.status == "COMPLETED"
    assert experiment.split_plan_id is None
    return experiment


@pytest.mark.parametrize("strategy", [KFOLD, TIME_SERIES_SPLIT])
def test_emitted_fold_helper_matches_the_engine(strategy):
    rng = np.random.default_rng(3)
    n, folds = 60, 4
    if strategy == TIME_SERIES_SPLIT:
        # Fold 0 = warm-up rows that are never validated (expanding window).
        fold_of = np.repeat(np.arange(0, folds + 1), n // (folds + 1))
    else:
        fold_of = rng.permutation(np.resize(np.arange(1, folds + 1), n))
    plan = ValidationPlan(
        strategy=strategy, requested_folds=folds, actual_folds=folds, shuffle=False, random_state=42,
        group_column=None, time_column=None, stratified=False, reason="test",
    )
    frame = pd.DataFrame({"x": np.arange(len(fold_of))})
    namespace: dict = {"np": np, "hashlib": __import__("hashlib")}
    exec(SPLIT_ASSIGNMENT_HELPERS, namespace)  # noqa: S102
    emitted = namespace["outer_folds"](fold_of, strategy, folds)
    engine = folds_from_assignment(plan, frame, fold_of)
    assert [number for number, _t, _v in emitted] == [fold.fold_number for fold in engine]
    for (_n, train_idx, val_idx), fold in zip(emitted, engine):
        assert np.array_equal(train_idx, fold.train_index)
        assert np.array_equal(val_idx, fold.validation_index)


def _exec_source(source: str, **namespace) -> dict:
    namespace = {"np": np, **namespace}
    exec(source, namespace)  # noqa: S102
    return namespace


def test_emitted_metric_and_predict_helpers_match_the_engine():
    rng = np.random.default_rng(11)
    labels = rng.integers(0, 2, 80)
    scores = np.clip(labels * 0.3 + rng.random(80) * 0.7, 0, 1)
    for threshold in (0.5, 0.37):
        ns = _exec_source("\n".join(_METRIC_SOURCES["binary"]))
        emitted = ns["score_metrics"](labels, scores, threshold=threshold)
        engine = classification_metrics(labels, scores, threshold=threshold)
        assert emitted == pytest.approx({key: engine[key] for key in emitted})
    codes = rng.integers(0, 3, 90)
    proba = rng.dirichlet(np.ones(3), 90)
    ns = _exec_source("\n".join(_METRIC_SOURCES["multiclass"]), N_CLASSES=3)
    emitted = ns["score_metrics"](codes, proba)
    engine = multiclass_metrics(codes, proba, n_classes=3)
    assert emitted == pytest.approx({key: engine[key] for key in emitted})
    actual, predicted = rng.normal(5, 2, 60), rng.normal(5, 2, 60)
    ns = _exec_source("\n".join(_METRIC_SOURCES["regression"]))
    emitted = ns["score_metrics"](actual, predicted)
    assert emitted == pytest.approx({key: regression_metrics(actual, predicted)[key] for key in emitted})

    features = rng.normal(size=(60, 3))
    from sklearn.linear_model import LinearRegression, LogisticRegression

    binary_model = LogisticRegression().fit(features, labels[:60])
    binary_ns = _exec_source(_PREDICT_SOURCES["binary"])
    assert np.allclose(binary_ns["predict_scores"](binary_model, features), _predict(binary_model, features, True))
    # A class absent from the fitted rows keeps a zero column.
    multi_model = LogisticRegression().fit(features, np.where(codes[:60] == 2, 0, codes[:60]) * 2)
    multi_ns = _exec_source(_PREDICT_SOURCES["multiclass"], N_CLASSES=3)
    assert np.allclose(multi_ns["predict_scores"](multi_model, features), _predict(multi_model, features, True, 3))
    regression_model = LinearRegression().fit(features, actual[:60])
    regression_ns = _exec_source(_PREDICT_SOURCES["regression"])
    assert np.allclose(
        regression_ns["predict_scores"](regression_model, features), _predict(regression_model, features, False)
    )


def _describe(estimator) -> object:
    if hasattr(estimator, "steps"):
        return [(name, _describe(step)) for name, step in estimator.steps]
    if type(estimator).__name__ == "ContiguousLabelClassifier":
        return ("ContiguousLabelClassifier", _describe(estimator.estimator))
    return (type(estimator).__name__, estimator.get_params(deep=False))


@pytest.mark.parametrize("task_type", ["binary", "multiclass", "regression"])
def test_emitted_estimators_match_registry_make_model(task_type):
    families = available_families(task_type)
    spec = SimpleNamespace(
        task=SimpleNamespace(task_type=task_type),
        candidates=[
            SimpleNamespace(model_family=family, implementation_class=implementation_for_family(family)[1])
            for family in families
        ],
    )
    lines, _helpers = _estimator_factory(spec)
    build = _exec_source("\n".join(lines))["build_estimator"]
    variants: list[dict] = [{}]
    if task_type != "regression":
        variants += [{"class_weight": "balanced"}, {"class_weight": {"0": 1.0, "1": 2.5}}]
    for family in families:
        for hyperparameters in [*variants, {"n_estimators": 37}]:
            if "class_weight" in hyperparameters and family not in {
                "logistic_regression", "random_forest", "extra_trees", "lightgbm"
            }:
                continue
            applied = _estimator_params(applied_hyperparameters(family, seed=7, hyperparameters=hyperparameters))
            emitted = build(family, applied)
            engine = make_model(family, seed=7, hyperparameters=hyperparameters, task_type=task_type)
            assert _describe(emitted) == _describe(engine), (family, hyperparameters)
    if "lightgbm" in families:
        tuned = {"num_leaves": 20, "learning_rate": 0.05}
        applied = applied_hyperparameters("lightgbm", seed=7, hyperparameters={"tuned": tuned})
        assert _describe(build("lightgbm", applied)) == _describe(
            make_model("lightgbm", seed=7, hyperparameters={"tuned": tuned}, task_type=task_type)
        )
