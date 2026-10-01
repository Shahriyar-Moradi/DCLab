"""P2.2-B: auto-train persists/reuses SplitPlans, bootstraps refs, writes decision records.

ADR 0006 §2/§3/§5. Holdout isolation: a reused plan never moves a row between
train and holdout; a stored map that disagrees with the run fails closed.
"""

from __future__ import annotations

from dataclasses import replace
from uuid import uuid4

import numpy as np
import pandas as pd
import pytest
from sqlalchemy import func, select

from app.db.models import (
    DEFAULT_WORKSPACE_ID,
    Artifact,
    ClientLabUpload,
    Dataset,
    Experiment,
    ModelSelectionDecision,
    ModelVersion,
    PipelineScientificPlan,
    ProjectDecisionRecord,
    ProjectRef,
    SplitPlan,
    User,
    WorkflowRun,
)
from app.domain.errors import SplitPlanLineageError
from app.engine.modeling.holdout_planner import plan_holdout
from app.engine.modeling.validation_planner import (
    GROUP_KFOLD,
    KFOLD,
    STRATIFIED_GROUP_KFOLD,
    STRATIFIED_KFOLD,
    TIME_SERIES_SPLIT,
    ValidationPlan,
    folds_from_assignment,
    iter_validation_folds,
)
from app.engine.validation.split_assignment import (
    SplitAssignmentMismatchError,
    build_split_assignment,
    folds_for_pool,
    parse_split_assignment,
)
from app.engine.validation.splits import SOURCE_ROW_COLUMN, split_train_test_holdout
from app.services import split_plan_service
from app.services.auto_train_service import run_auto_train_job
from app.services.lab_service import ingest_dataset
from app.services.lineage_service import (
    attach_split_plan,
    create_pipeline_run,
    create_workflow_run,
    seed_business_domains,
)
from app.services.problem_spec_service import create_problem_spec
from app.services.project_service import get_or_create_labs_project
from app.services.winner_record_backfill import backfill_winner_records
from app.storage.factory import storage_for_artifact
from test_data_model_lineage import make_lineage_setup


def _frame(n: int = 220, seed: int = 11) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    contract = rng.choice(["Month-to-month", "One year", "Two year"], n)
    churn = np.where(
        rng.binomial(1, np.where(contract == "Month-to-month", 0.55, 0.18)) == 1, "Yes", "No"
    )
    return pd.DataFrame(
        {
            "tenure": rng.integers(1, 72, n).astype(float),
            "MonthlyCharges": rng.uniform(20, 120, n),
            "contract": contract,
            "churn": churn,
        }
    )


def _upload_and_train(
    auth_client, db, client_user, monkeypatch, *, locked_spec: bool = True
) -> ClientLabUpload:
    monkeypatch.setattr(
        "app.services.client_lab_upload_service.enqueue_auto_train", lambda _id: None
    )
    data = {"category": "Revenue", "target_column": "churn"}
    if locked_spec:
        seed_business_domains(db)
        project = get_or_create_labs_project(
            db, workspace_id=DEFAULT_WORKSPACE_ID, actor=client_user
        )
        spec = create_problem_spec(
            db,
            actor=client_user,
            workspace_id=DEFAULT_WORKSPACE_ID,
            project_id=project.id,
            task_type="binary",
            business_objective="Predict churn.",
            target_column="churn",
            status="locked",
        )
        db.commit()
        data = {"category": "Revenue", "problem_spec_id": str(spec.id)}
    response = auth_client.post(
        "/app/labs/uploads",
        data=data,
        files={"file": ("churn.csv", _frame().to_csv(index=False).encode(), "text/csv")},
    )
    assert response.status_code == 200, response.text
    upload_id = response.json()["run_id"]
    run_auto_train_job(db, upload_id)
    db.expire_all()
    upload = db.get(ClientLabUpload, upload_id)
    assert upload.pipeline_status == "completed", upload.pipeline_log.get("reason")
    return upload


def _second_run_on_same_dataset(db, first: ClientLabUpload) -> ClientLabUpload:
    """A new root run on the *same* published source dataset (same Dataset row)."""

    dataset = db.get(Dataset, first.dataset_id)
    first_run = db.scalar(select(WorkflowRun).where(WorkflowRun.source_upload_id == first.id))
    requester = db.get(User, first.requested_by)
    row = ClientLabUpload(
        workspace_id=first.workspace_id,
        requested_by=first.requested_by,
        category=first.category,
        original_filename=first.original_filename,
        stored_path=first.stored_path,
        kind=first.kind,
        record_count=first.record_count,
        fields_noticed=first.fields_noticed,
        has_named_fields=first.has_named_fields,
        explicit_target_column=first.explicit_target_column,
        pipeline_status="queued",
        client_status="queued",
        dataset_id=dataset.id,
        artifact_id=first.artifact_id,
        data_source_id=first.data_source_id,
        ingestion_run_id=first.ingestion_run_id,
    )
    db.add(row)
    db.flush()
    workflow_run = create_workflow_run(
        db,
        workspace_id=first.workspace_id,
        workflow=first_run.workflow,
        requester=requester,
        trigger_type="upload",
        source_type=row.kind,
        source_upload=row,
        inputs=[(dataset, "reference")],
        problem_spec_id=first_run.problem_spec_id,
    )
    shell = create_pipeline_run(
        db,
        workflow_run=workflow_run,
        environment=dataset.environment,
        dataset=dataset,
        task=None,
        pipeline_name="open_ingest_deterministic_ml",
        pipeline_purpose="training_and_scoring",
        input_role=None,
        commit=False,
        source_dataset_id=dataset.id,
    )
    row.experiment_id = shell.id
    db.commit()
    return row


def _records(db, decision_type: str) -> list[ProjectDecisionRecord]:
    return list(
        db.scalars(
            select(ProjectDecisionRecord).where(ProjectDecisionRecord.decision_type == decision_type)
        )
    )


def _validation_by_fold(result: dict) -> dict[str, dict[int, list[int]]]:
    out: dict[str, dict[int, list[int]]] = {}
    for candidate in result["candidates"]:
        if candidate.get("status") != "trained":
            continue
        out[candidate["candidate_id"]] = {
            int(fold["fold_number"]): sorted(fold["validation_provenance"])
            for fold in candidate["folds"]
        }
    return out


def test_two_runs_on_same_dataset_share_split_plan_refs_bootstrap_once(
    auth_client, db_session, client_user, monkeypatch
):
    db = db_session
    first = _upload_and_train(auth_client, db, client_user, monkeypatch)
    exp1 = db.get(Experiment, first.experiment_id)
    plan = db.scalar(select(SplitPlan))
    assert plan is not None and plan.version == 1
    # Source dataset identity: the published upload dataset, never the prepared one.
    assert exp1.source_dataset_id == first.dataset_id == plan.dataset_id
    assert exp1.dataset_id != first.dataset_id
    assert exp1.split_plan_id == plan.id and exp1.scientific_evidence_locked_at is not None

    artifact = db.get(Artifact, plan.assignment_artifact_id)
    assert artifact.artifact_type == "split_assignment"
    assert artifact.object_key == (
        f"ws/{plan.workspace_id}/projects/{plan.project_id}/split_plans/{plan.id}/assignment.csv"
    )
    stored = storage_for_artifact(artifact).get(artifact.object_key)
    assignment = parse_split_assignment(stored)
    assert assignment.digest() == plan.assignment_digest == artifact.content_digest
    split1 = exp1.result["split"]
    assert assignment.holdout_rows == set(split1["test_source_rows"])
    assert set(assignment.train_folds) == set(split1["train_source_rows"])
    assert (plan.train_row_count, plan.holdout_row_count) == (split1["n_train"], split1["n_test"])
    for folds in _validation_by_fold(exp1.result).values():
        for number, rows in folds.items():
            assert all(assignment.train_folds[row] == number for row in rows)

    sci1 = db.scalar(
        select(PipelineScientificPlan).where(PipelineScientificPlan.pipeline_run_id == exp1.id)
    )
    assert sci1.holdout_plan_digest == plan.holdout_plan_digest
    full_split = sci1.full_plan["split"]
    assert not {"train_source_rows", "test_source_rows", "all_source_rows"} & set(full_split)
    assert full_split["assignment_digest"] == plan.assignment_digest
    assert full_split["n_test"] == plan.holdout_row_count

    created = _records(db, "split_plan_created")
    assert len(created) == 1 and created[0].split_plan_id == plan.id
    assert created[0].actor_rule == "holdout.planner.v1" and created[0].state == "accepted"

    model_version = db.scalar(select(ModelVersion).where(ModelVersion.pipeline_run_id == exp1.id))
    refs = {row.ref_kind: row for row in db.scalars(select(ProjectRef))}
    assert set(refs) == {"problem_spec", "dataset", "split_plan", "feature_recipe", "champion_model"}
    initialized = _records(db, "ref_initialized")
    assert len(initialized) == 1
    record = initialized[0]
    assert record.actor_rule == "refs.bootstrap.v1" and record.model_version_id == model_version.id
    assert record.evidence_refs[0]["scope"] == "final_holdout"
    assert {move["ref_kind"] for move in record.details["ref_moves"]} == set(refs)
    assert all(ref.decision_record_id == record.id and ref.version == 1 for ref in refs.values())
    workflow_run = db.get(WorkflowRun, exp1.workflow_run_id)
    assert refs["problem_spec"].problem_spec_id == workflow_run.problem_spec_id
    assert refs["dataset"].dataset_id == first.dataset_id
    assert refs["split_plan"].split_plan_id == plan.id
    assert refs["feature_recipe"].feature_set_version_id == model_version.feature_set_version_id
    assert refs["champion_model"].model_version_id == model_version.id
    before = {kind: (ref.id, ref.version, ref.updated_at) for kind, ref in refs.items()}

    second = _second_run_on_same_dataset(db, first)

    def no_rederivation(*_args, **_kwargs):
        raise AssertionError("the runner must apply the stored fold map, not re-derive folds")

    # Partition-by-map: neither the holdout lock nor the runner re-splits, and
    # the runner never re-derives outer folds; any of these would fail the run.
    monkeypatch.setattr("app.engine.experiments.runner.iter_validation_folds", no_rederivation)
    monkeypatch.setattr("app.engine.experiments.runner.split_train_test_holdout", no_rederivation)
    monkeypatch.setattr("app.services.auto_train.holdout.split_train_test_holdout", no_rederivation)
    run_auto_train_job(db, second.id)
    db.expire_all()
    second = db.get(ClientLabUpload, second.id)
    assert second.pipeline_status == "completed", second.pipeline_log.get("reason")
    exp2 = db.get(Experiment, second.experiment_id)

    # One plan, one assignment digest, identical holdout and fold provenance.
    assert db.scalar(select(func.count()).select_from(SplitPlan)) == 1
    assert exp2.split_plan_id == plan.id and exp2.source_dataset_id == plan.dataset_id
    split2 = exp2.result["split"]
    assert sorted(split2["test_source_rows"]) == sorted(split1["test_source_rows"])
    assert split2["partitioned_by"] == "split_plan_assignment"
    assert sorted(split2["train_source_rows"]) == sorted(split1["train_source_rows"])
    folds1, folds2 = _validation_by_fold(exp1.result), _validation_by_fold(exp2.result)
    shared = set(folds1) & set(folds2)
    assert shared and all(folds1[key] == folds2[key] for key in shared)
    sci2 = db.scalar(
        select(PipelineScientificPlan).where(PipelineScientificPlan.pipeline_run_id == exp2.id)
    )
    assert sci2.holdout_plan_digest == plan.holdout_plan_digest == sci1.holdout_plan_digest
    assert sci2.full_plan["split"]["assignment_digest"] == plan.assignment_digest
    assert len(_records(db, "split_plan_created")) == 1
    # The deterministic verifier still reads the run's own split provenance.
    verification = exp2.result["deterministic_verification"]
    assert verification["overall_status"] == exp1.result["deterministic_verification"]["overall_status"]
    assert not verification["failures"]
    for run in (exp1, exp2):
        checks = {c["check_id"]: c["status"] for c in run.result["deterministic_verification"]["checks"]}
        assert checks["split_plan_consistent"] == "PASS"

    # Later runs never move refs.
    after = {row.ref_kind: (row.id, row.version, row.updated_at) for row in db.scalars(select(ProjectRef))}
    assert after == before
    assert len(_records(db, "ref_initialized")) == 1

    # winner_locked on every selection, with the backfill's idempotency key.
    selections = list(db.scalars(select(ModelSelectionDecision)))
    winners = _records(db, "winner_locked")
    assert len(selections) == 2 and len(winners) == 2
    assert {row.idempotency_key for row in winners} == {
        f"winner_locked:{row.id}" for row in selections
    }
    assert {row.actor_rule for row in winners} == {"selection.cv_winner.v1"}
    assert {row.candidate_id for row in winners} == {row.selected_candidate_id for row in selections}
    result = backfill_winner_records(db)
    assert result.created == 0 and result.already_present == 2

    # Tenancy: every new write carries the run's workspace and project.
    for row in [plan, *db.scalars(select(ProjectDecisionRecord)), *db.scalars(select(ProjectRef))]:
        assert row.workspace_id == first.workspace_id and row.project_id == plan.project_id


def test_tampered_stored_assignment_fails_closed(auth_client, db_session, client_user, monkeypatch):
    db = db_session
    first = _upload_and_train(auth_client, db, client_user, monkeypatch)
    plan = db.scalar(select(SplitPlan))
    artifact = db.get(Artifact, plan.assignment_artifact_id)
    backend = storage_for_artifact(artifact)
    payload = backend.get(artifact.object_key).decode()
    lines = payload.splitlines()
    # Move one holdout row into training: exactly what reuse must never allow.
    index = next(i for i, line in enumerate(lines) if line.endswith(",holdout,"))
    lines[index] = lines[index].replace(",holdout,", ",train,1")
    backend.put(artifact.object_key, ("\n".join(lines) + "\n").encode())

    second = _second_run_on_same_dataset(db, first)
    run_auto_train_job(db, second.id)
    db.expire_all()
    second = db.get(ClientLabUpload, second.id)
    assert second.pipeline_status == "failed"
    assert "split_assignment_mismatch" in second.pipeline_log["reason"]
    exp2 = db.get(Experiment, second.experiment_id)
    assert exp2.split_plan_id is None and exp2.scientific_evidence_locked_at is None
    assert db.scalar(select(func.count()).select_from(SplitPlan)) == 1


def test_changed_row_set_with_same_identity_fails_closed(
    auth_client, db_session, client_user, monkeypatch
):
    db = db_session
    first = _upload_and_train(auth_client, db, client_user, monkeypatch)
    from app.services.auto_train import cleaning

    real = cleaning.structural_clean_frame

    def silently_drops_a_row(*args, **kwargs):
        frame, log = real(*args, **kwargs)
        return frame.iloc[:-1].reset_index(drop=True), log  # log unchanged: same identity

    monkeypatch.setattr(cleaning, "structural_clean_frame", silently_drops_a_row)
    second = _second_run_on_same_dataset(db, first)
    run_auto_train_job(db, second.id)
    db.expire_all()
    second = db.get(ClientLabUpload, second.id)
    assert second.pipeline_status == "failed"
    assert second.pipeline_log["reason"].startswith("split_assignment_mismatch")
    assert db.scalar(select(func.count()).select_from(SplitPlan)) == 1
    assert db.get(Experiment, second.experiment_id).split_plan_id is None


def test_bootstrap_waits_for_a_run_with_dataset_and_split_plan(
    auth_client, db_session, client_user, monkeypatch
):
    db = db_session

    def unavailable(*_args, **_kwargs):
        raise split_plan_service.SplitPlanUnavailable("forced plan-less run")

    with monkeypatch.context() as patch:
        patch.setattr("app.services.auto_train.split_plan.require_partition_source", unavailable)
        first = _upload_and_train(auth_client, db, client_user, monkeypatch, locked_spec=False)
    exp1 = db.get(Experiment, first.experiment_id)
    assert exp1.split_plan_id is None and exp1.scientific_evidence_locked_at is not None
    # A plan-less first model does not bootstrap (it would be a one-shot dead end).
    assert db.scalar(select(func.count()).select_from(ProjectRef)) == 0
    assert _records(db, "ref_initialized") == []
    assert len(_records(db, "winner_locked")) == 1
    # The verifier's split-plan check does not apply to a run without a plan.
    checks = {c["check_id"] for c in exp1.result["deterministic_verification"]["checks"]}
    assert "split_plan_consistent" not in checks

    second = _second_run_on_same_dataset(db, first)
    run_auto_train_job(db, second.id)
    db.expire_all()
    exp2 = db.get(Experiment, db.get(ClientLabUpload, second.id).experiment_id)
    assert exp2.split_plan_id is not None
    refs = {row.ref_kind: row for row in db.scalars(select(ProjectRef))}
    # No locked ProblemSpec: that kind is reported, not invented.
    assert set(refs) == {"dataset", "split_plan", "feature_recipe", "champion_model"}
    (record,) = _records(db, "ref_initialized")
    assert record.experiment_id is None and record.model_version_id is not None
    assert {item["ref_kind"] for item in record.details["skipped_refs"]} == {"problem_spec"}
    assert refs["split_plan"].split_plan_id == exp2.split_plan_id


def test_verifier_split_plan_check(auth_client, db_session, client_user, monkeypatch):
    from app.services.pipeline_verifier import _verify_split_plan_lineage

    db = db_session
    upload = _upload_and_train(auth_client, db, client_user, monkeypatch)
    exp = db.get(Experiment, upload.experiment_id)

    def run(report, session=db):
        checks: list[tuple[str, str]] = []
        _verify_split_plan_lineage(
            lambda check, _stage, status, *_a: checks.append((check, status)), report, session
        )
        return checks

    report = {"run": {"experiment_id": str(exp.id)}, "split": dict(exp.result["split"])}
    assert run(report) == [("split_plan_consistent", "PASS")]
    report["split"]["n_test"] += 1
    assert run(report) == [("split_plan_consistent", "FAIL")]

    class MissingPlan:
        def get(self, model, key):
            return None if model is SplitPlan else db.get(model, key)

    assert run(report, MissingPlan()) == [("split_plan_consistent", "NOT_VERIFIABLE")]
    assert run({"run": {"experiment_id": None}, "split": {}}) == []


# --- service level -----------------------------------------------------------


def _dataset(db, tmp_path, setup, *, workspace: str = "alpha", name: str, seed: int = 3) -> Dataset:
    rng = np.random.default_rng(seed)
    frame = pd.DataFrame(
        {"feature": rng.normal(size=120).round(6), "target": rng.integers(0, 2, 120)}
    )
    path = tmp_path / f"{name}-{uuid4().hex[:6]}.csv"
    frame.to_csv(path, index=False)
    return ingest_dataset(
        db,
        environment=setup["env"],
        name=name,
        location=str(path),
        workspace_id=setup[workspace].id,
        project_id=setup[f"{workspace}_project"].id,
    )


def _resolve(db, dataset: Dataset, *, strategy: str | None = None, cleaning_log=None, mutate=None):
    frame = pd.read_csv(dataset.location)
    frame[SOURCE_ROW_COLUMN] = np.arange(len(frame))
    holdout = plan_holdout(frame, target="target", task_type="binary", test_size=0.2, random_state=42)
    if strategy is not None:
        holdout = replace(holdout, strategy=strategy, stratified=strategy == "stratified_random")
    train, _val, _test, split = split_train_test_holdout(frame, target="target", plan=holdout)
    if mutate is not None:
        holdout = mutate(holdout)
    validation = ValidationPlan(
        strategy="StratifiedKFold",
        requested_folds=5,
        actual_folds=5,
        shuffle=True,
        random_state=42,
        group_column=None,
        time_column=None,
        stratified=True,
        reason="test plan",
    )

    def derive():
        folds = list(iter_validation_folds(validation, train, train["target"].to_numpy()))
        return build_split_assignment(
            train_source_rows=split["train_source_rows"],
            holdout_source_rows=split["test_source_rows"],
            train_frame=train,
            folds=folds,
        )

    return split_plan_service.resolve_split_plan(
        db,
        source_dataset=dataset,
        target_column="target",
        task_type="binary",
        holdout_plan=holdout,
        validation_plan=validation,
        cleaning_log=cleaning_log
        or {"transformations": [], "rows_in": len(frame), "rows_out": len(frame)},
        holdout_rows=split["test_source_rows"],
        train_rows=split["train_source_rows"],
        derive_assignment=derive,
    )


def test_different_dataset_or_strategy_gets_a_new_plan(db_session, tmp_path):
    db = db_session
    setup = make_lineage_setup(db, tmp_path)
    first_ds = _dataset(db, tmp_path, setup, name="first")
    first = _resolve(db, first_ds)
    db.commit()
    again = _resolve(db, first_ds)
    assert again.reused and again.split_plan.id == first.split_plan.id

    random_plan = _resolve(db, first_ds, strategy="random")
    db.commit()
    assert not random_plan.reused and random_plan.split_plan.version == 2
    assert random_plan.split_plan.plan_digest != first.split_plan.plan_digest

    other = _resolve(db, _dataset(db, tmp_path, setup, name="other", seed=9))
    db.commit()
    assert not other.reused and other.split_plan.version == 3

    # Identical bytes uploaded again are another Dataset row: a new plan, whose
    # deterministic map has the same assignment digest.
    twin = _resolve(db, _dataset(db, tmp_path, setup, name="twin"))
    db.commit()
    assert not twin.reused and twin.split_plan.version == 4
    assert twin.split_plan.assignment_digest == first.split_plan.assignment_digest

    # A changed structural cleaning is another identity: no stored holdout, new plan.
    cleaning = {
        "transformations": [{"step": "drop_duplicate_rows", "rows_removed": 1}],
        "rows_in": 121,
        "rows_out": 120,
    }
    assert (
        split_plan_service.find_stored_holdout(
            db,
            source_dataset=first_ds,
            target_column="target",
            task_type="binary",
            holdout_plan=first.holdout_plan,
            cleaning_log=cleaning,
        )
        is None
    )
    stored = split_plan_service.find_stored_holdout(
        db,
        source_dataset=first_ds,
        target_column="target",
        task_type="binary",
        holdout_plan=first.holdout_plan,
        cleaning_log={"transformations": [], "rows_in": 120, "rows_out": 120},
    )
    assert stored is not None and stored.split_plan.id == first.split_plan.id
    cleaned = _resolve(db, first_ds, cleaning_log=cleaning)
    db.commit()
    assert not cleaned.reused and cleaned.split_plan.version == 5
    assert len(_records(db, "split_plan_created")) == 5


def test_non_race_integrity_error_surfaces_and_cleans_up(db_session, tmp_path):
    from sqlalchemy.exc import IntegrityError

    db = db_session
    setup = make_lineage_setup(db, tmp_path)
    dataset = _dataset(db, tmp_path, setup, name="bad")
    # A CHECK violation (holdout_test_size must be in (0, 1)) is a bug, not a race.
    with pytest.raises(IntegrityError, match="ck_split_plans_holdout_test_size"):
        _resolve(db, dataset, mutate=lambda plan: replace(plan, test_size=1.5))
    db.rollback()
    assert db.scalar(select(func.count()).select_from(SplitPlan)) == 0
    assert db.scalar(
        select(func.count()).select_from(Artifact).where(Artifact.artifact_type == "split_assignment")
    ) == 0


def test_split_plan_must_partition_the_run_source_dataset(db_session, tmp_path):
    db = db_session
    setup = make_lineage_setup(db, tmp_path)
    source = _dataset(db, tmp_path, setup, name="source")
    other = _dataset(db, tmp_path, setup, name="other", seed=5)
    plan = _resolve(db, source).split_plan
    db.commit()
    workflow_run = create_workflow_run(
        db,
        workspace_id=setup["alpha"].id,
        workflow=setup["alpha_workflow"],
        requester=setup["alpha_admin"],
        trigger_type="manual",
        source_type="dataset",
    )
    run = create_pipeline_run(
        db,
        workflow_run=workflow_run,
        environment=setup["env"],
        dataset=other,
        task=setup["task"],
        source_dataset_id=other.id,
    )
    with pytest.raises(SplitPlanLineageError, match="split_plan_dataset_mismatch"):
        attach_split_plan(db, pipeline_run=run, split_plan=plan, source_dataset_id=other.id)
    db.rollback()
    assert db.get(Experiment, run.id).split_plan_id is None

    matching = create_pipeline_run(
        db,
        workflow_run=workflow_run,
        environment=setup["env"],
        dataset=source,
        task=setup["task"],
        pipeline_index=1,
        source_dataset_id=source.id,
    )
    attach_split_plan(db, pipeline_run=matching, split_plan=plan, source_dataset_id=source.id)
    db.commit()
    assert db.get(Experiment, matching.id).split_plan_id == plan.id


def test_split_plan_writes_are_tenant_scoped(db_session, tmp_path):
    db = db_session
    setup = make_lineage_setup(db, tmp_path)
    alpha = _resolve(db, _dataset(db, tmp_path, setup, name="shared"))
    beta_ds = _dataset(db, tmp_path, setup, workspace="beta", name="shared")
    beta = _resolve(db, beta_ds)
    db.commit()
    assert not beta.reused and beta.split_plan.id != alpha.split_plan.id
    assert beta.split_plan.workspace_id == setup["beta"].id and beta.split_plan.version == 1
    assert beta.split_plan.assignment_digest == alpha.split_plan.assignment_digest
    beta_records = [
        row for row in _records(db, "split_plan_created") if row.workspace_id == setup["beta"].id
    ]
    assert [row.split_plan_id for row in beta_records] == [beta.split_plan.id]
    beta_artifact = db.get(Artifact, beta.split_plan.assignment_artifact_id)
    assert beta_artifact.workspace_id == setup["beta"].id
    assert beta_artifact.object_key.startswith(f"ws/{setup['beta'].id}/")

    # A source dataset of another workspace or project is never partitioned.
    with pytest.raises(split_plan_service.SplitPlanUnavailable):
        split_plan_service.require_partition_source(
            db,
            workspace_id=setup["alpha"].id,
            project_id=setup["alpha_project"].id,
            source_dataset_id=beta_ds.id,
        )
    # A plan of another workspace cannot be attached to a run.
    workflow_run = create_workflow_run(
        db,
        workspace_id=setup["beta"].id,
        workflow=setup["beta_workflow"],
        requester=setup["beta_admin"],
        trigger_type="manual",
        source_type="dataset",
    )
    run = create_pipeline_run(
        db,
        workflow_run=workflow_run,
        environment=setup["env"],
        dataset=beta_ds,
        task=setup["task"],
        source_dataset_id=beta_ds.id,
    )
    with pytest.raises(SplitPlanLineageError, match="split_plan_not_found"):
        attach_split_plan(db, pipeline_run=run, split_plan=alpha.split_plan, source_dataset_id=beta_ds.id)
    db.rollback()


def test_assignment_parser_rejects_non_canonical_bytes():
    with pytest.raises(SplitAssignmentMismatchError):
        parse_split_assignment(b"source_row,partition,fold\n1,train,1\n0,holdout,\n")
    with pytest.raises(SplitAssignmentMismatchError):
        parse_split_assignment(b"source_row,partition,fold\n0,holdout,2\n")
    parsed = parse_split_assignment(b"source_row,partition,fold\n0,holdout,\n1,train,2\n")
    assert parsed.holdout_rows == {0} and dict(parsed.train_folds) == {1: 2}


@pytest.mark.parametrize(
    "strategy",
    [STRATIFIED_KFOLD, KFOLD, TIME_SERIES_SPLIT, GROUP_KFOLD, STRATIFIED_GROUP_KFOLD],
)
def test_stored_fold_map_reproduces_planned_folds(strategy):
    rng = np.random.default_rng(4)
    n = 150
    frame = pd.DataFrame(
        {
            SOURCE_ROW_COLUMN: rng.permutation(np.arange(1000, 1000 + n)),
            "entity": rng.integers(0, 30, n),
            "ts": pd.date_range("2024-01-01", periods=n, freq="D")[rng.permutation(n)],
            "target": rng.integers(0, 2, n),
        }
    )
    plan = ValidationPlan(
        strategy=strategy,
        requested_folds=5,
        actual_folds=5,
        shuffle=strategy in {STRATIFIED_KFOLD, KFOLD, STRATIFIED_GROUP_KFOLD},
        random_state=42,
        group_column="entity" if "Group" in strategy else None,
        time_column="ts" if strategy == TIME_SERIES_SPLIT else None,
        stratified=strategy.startswith("Stratified"),
        reason="test",
    )
    planned = list(iter_validation_folds(plan, frame, frame["target"].to_numpy()))
    assignment = build_split_assignment(
        train_source_rows=frame[SOURCE_ROW_COLUMN].tolist(),
        holdout_source_rows=[5000],
        train_frame=frame,
        folds=planned,
    )
    if strategy == TIME_SERIES_SPLIT:
        assert 0 in set(assignment.train_folds.values())  # warm-up rows never validate
    fold_of = np.asarray([assignment.train_folds[int(r)] for r in frame[SOURCE_ROW_COLUMN]])
    rebuilt = folds_from_assignment(plan, frame, fold_of)
    shuffled = frame.sample(frac=1.0, random_state=7).reset_index(drop=True)
    from_pool = folds_for_pool(plan, shuffled, assignment.train_folds)

    def rows(source: pd.DataFrame, index) -> set[int]:
        return set(source.iloc[np.asarray(index)][SOURCE_ROW_COLUMN].astype(int))

    assert len(planned) == len(rebuilt) == len(from_pool) == 5
    for want, got, pooled in zip(planned, rebuilt, from_pool):
        assert rows(frame, got.train_index) == rows(frame, want.train_index)
        assert rows(frame, got.validation_index) == rows(frame, want.validation_index)
        assert rows(shuffled, pooled.train_index) == rows(frame, want.train_index)
        assert rows(shuffled, pooled.validation_index) == rows(frame, want.validation_index)

    # A pool that is not exactly the plan's train partition fails closed.
    with pytest.raises(SplitAssignmentMismatchError):
        folds_for_pool(plan, shuffled.iloc[1:], assignment.train_folds)
