"""ML state graph schema (ADR 0006, Alembic 0063): tables, tenancy FKs, triggers, backfills."""

from __future__ import annotations

import importlib.util
import json
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

import pytest
from sqlalchemy import select, text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import sessionmaker

from _alembic_catalog import dump_constraints_triggers
from app.db.models import (
    ClientLabUpload,
    ExperimentCandidate,
    ModelSelectionDecision,
    ProblemSpec,
    ProjectDecisionRecord,
    SplitPlan,
)
from app.domain.experiment_changes import NON_EXCLUDABLE_FAMILIES, ExperimentChangeSet
from app.services.artifact_service import store_artifact
from app.services.lab_service import ingest_dataset
from app.services.lineage_service import create_pipeline_run, create_workflow_run
from app.services.project_service import create_project
from app.services.winner_record_backfill import backfill_winner_records
from app.storage.local import LocalStorage
from test_data_model_lineage import make_lineage_setup

FIXTURE = Path(__file__).resolve().parent / "fixtures" / "alembic_head_constraints_triggers.json"
MIGRATION = (
    Path(__file__).resolve().parents[1] / "alembic" / "versions" / "0063_state_graph_nodes.py"
)
NEW_TABLES = ("split_plans", "project_refs", "project_decision_records")
NEW_TRIGGERS = (
    "split_plans_immutable",
    "project_decision_records_append_only",
    "project_refs_columns_immutable",
    "project_refs_no_delete",
    "experiments_lineage_guard",
)
NEW_EXPERIMENT_CHECKS = (
    "ck_experiments_split_plan_requires_project",
    "ck_experiments_intent_not_blank",
    "ck_experiments_change_set_object",
    "ck_experiments_change_set_schema_version",
    "ck_experiments_change_set_changes",
    "ck_experiments_change_set_bounded",
    "ck_experiments_change_set_no_secrets",
    "ck_experiments_change_set_requires_parent",
)
PROJECT_SCOPED_TABLES = (
    "problem_specs",
    "datasets",
    "feature_set_versions",
    "model_versions",
    "experiments",
    "experiment_candidates",
)
VALID_CHANGE_SET = {
    "schema_version": 1,
    "changes": [{"kind": "family_include", "family": "xgboost"}],
}


def _rejects(db, sql: str, match: str, **params) -> None:
    with pytest.raises(DBAPIError, match=match):
        db.execute(text(sql), params)
        db.commit()
    db.rollback()


def _source_dataset(db, tmp_path, *, workspace_id, project_id, name: str):
    path = tmp_path / f"{name}.csv"
    path.write_text(f"feature,target\n1,0\n2,1\n{name},0\n", encoding="utf-8")
    from app.db.models import Environment

    env = db.execute(select(Environment)).scalars().first()
    return ingest_dataset(
        db,
        environment=env,
        name=name,
        location=str(path),
        workspace_id=workspace_id,
        project_id=project_id,
    )


def _split_plan(graph, *, dataset_id=None, project_id=None, version=1, digest="1") -> SplitPlan:
    return SplitPlan(
        workspace_id=graph.ws,
        project_id=project_id or graph.project.id,
        dataset_id=dataset_id or graph.source.id,
        version=version,
        task_type="binary",
        target_column="target",
        holdout_strategy="stratified_random",
        holdout_test_size=0.2,
        holdout_seed=42,
        stratified=True,
        validation_strategy="stratified_kfold",
        validation_folds=5,
        validation_seed=42,
        row_count=10,
        train_row_count=8,
        holdout_row_count=2,
        plan_digest=digest * 64,
        assignment_artifact_id=graph.assignment.id,
        assignment_digest=graph.assignment.content_digest,
        holdout_plan_digest="c" * 64,
        holdout_planner_version="holdout.v1",
        validation_planner_version="validation.v1",
        plan_evidence={"holdout_plan": {"strategy": "stratified_random"}},
        reason="stratified holdout for a binary target",
    )


def _record(graph, **overrides) -> ProjectDecisionRecord:
    values = dict(
        workspace_id=graph.ws,
        project_id=graph.project.id,
        decision_type="ref_initialized",
        state="accepted",
        subject_kind="dataset_version",
        dataset_id=graph.source.id,
        actor_kind="rule",
        actor_rule="refs.bootstrap.v1",
        rationale="first locked model; not a comparison",
        rationale_untrusted=False,
        schema_version=1,
        policy_version="dclab.decisions.v1",
    )
    values.update(overrides)
    return ProjectDecisionRecord(**values)


def _insert_branch(db, parent_id, *, source_dataset_id, split_plan_id, change_set=VALID_CHANGE_SET):
    child_id = uuid4()
    db.execute(
        text(
            "INSERT INTO experiments (id, workspace_id, project_id, environment_id, dataset_id, "
            "status, config, seed, parent_pipeline_run_id, source_dataset_id, split_plan_id, "
            "change_set, intent) "
            "SELECT :id, workspace_id, project_id, environment_id, dataset_id, 'CREATED', "
            "'{}'::jsonb, 42, id, :source, :split, CAST(:change_set AS jsonb), 'try xgboost' "
            "FROM experiments WHERE id = :parent"
        ),
        {
            "id": child_id,
            "parent": parent_id,
            "source": source_dataset_id,
            "split": split_plan_id,
            "change_set": json.dumps(change_set) if change_set is not None else None,
        },
    )
    db.commit()
    return child_id


def _new_experiment(db, setup, workflow=None):
    run = create_workflow_run(
        db,
        workspace_id=setup["alpha"].id,
        workflow=workflow or setup["alpha_workflow"],
        requester=setup["alpha_admin"],
        trigger_type="manual",
        source_type="dataset",
    )
    return create_pipeline_run(
        db,
        workflow_run=run,
        environment=setup["env"],
        dataset=setup["alpha_dataset"],
        task=setup["task"],
    )


@pytest.fixture
def graph(db_session, tmp_path):
    setup = make_lineage_setup(db_session, tmp_path)
    ws = setup["alpha"].id
    project = setup["alpha_project"]
    other_project = create_project(
        db_session, actor=setup["alpha_admin"], workspace_id=ws, name="Other", slug="other"
    )
    source = _source_dataset(db_session, tmp_path, workspace_id=ws, project_id=project.id, name="src")
    other_source = _source_dataset(
        db_session, tmp_path, workspace_id=ws, project_id=other_project.id, name="other-src"
    )
    beta_source = _source_dataset(
        db_session,
        tmp_path,
        workspace_id=setup["beta"].id,
        project_id=setup["beta_project"].id,
        name="beta-src",
    )
    experiment = _new_experiment(db_session, setup)
    assert experiment.project_id == project.id
    assignment = store_artifact(
        db_session,
        workspace_id=ws,
        project_id=project.id,
        artifact_type="split_assignment",
        filename="assignment.csv",
        data=b"source_row,partition,fold\n0,train,0\n1,holdout,\n",
        storage=LocalStorage(root=tmp_path / "objects"),
    )
    db_session.commit()
    return SimpleNamespace(
        setup=setup,
        ws=ws,
        project=project,
        other_project=other_project,
        source=source,
        other_source=other_source,
        beta_source=beta_source,
        experiment=experiment,
        assignment=assignment,
        tmp_path=tmp_path,
    )


def _locked_graph(db, graph) -> SimpleNamespace:
    """Root experiment with a split plan, ready to branch."""

    plan = _split_plan(graph)
    db.add(plan)
    db.commit()
    db.execute(
        text("UPDATE experiments SET source_dataset_id = :s, split_plan_id = :p WHERE id = :id"),
        {"s": graph.source.id, "p": plan.id, "id": graph.experiment.id},
    )
    db.commit()
    graph.plan = plan
    return graph


# --- catalog -------------------------------------------------------------------


def test_state_graph_tables_and_project_scoped_uniques_exist(test_engine):
    with test_engine.connect() as connection:
        tables = set(
            connection.execute(
                text("SELECT tablename FROM pg_tables WHERE schemaname = 'public'")
            ).scalars()
        )
        uniques = set(
            connection.execute(
                text("SELECT conname FROM pg_constraint WHERE contype = 'u'")
            ).scalars()
        )
        columns = set(
            connection.execute(
                text(
                    "SELECT column_name FROM information_schema.columns "
                    "WHERE table_name = 'experiments'"
                )
            ).scalars()
        )
        partial = connection.execute(
            text(
                "SELECT indexdef FROM pg_indexes WHERE indexname = "
                "'ix_experiment_candidates_experiment_feature_set_version'"
            )
        ).scalar()
    assert set(NEW_TABLES) <= tables
    for table in PROJECT_SCOPED_TABLES + NEW_TABLES:
        short = "pdr" if table == "project_decision_records" else table
        assert f"uq_{short}_workspace_project_id" in uniques
    assert {"source_dataset_id", "split_plan_id", "intent", "change_set"} <= columns
    assert "WHERE (feature_set_version_id IS NOT NULL)" in partial


def test_create_all_helpers_match_alembic_0063_catalog(test_engine):
    """integrity.py (create_all) and the 0063 literal SQL produce identical objects."""

    def subset(dump: dict) -> dict:
        checks = [
            item
            for item in dump["check_constraints"]
            if item["table"] in NEW_TABLES
            or item["name"] in NEW_EXPERIMENT_CHECKS
            or item["name"] == "ck_artifacts_type_valid"
        ]
        triggers = [item for item in dump["triggers"] if item["name"] in NEW_TRIGGERS]
        functions = [
            item
            for item in dump["functions"]
            if item["name"] == "prevent_experiment_lineage_violation"
        ]
        return {"checks": checks, "triggers": triggers, "functions": functions}

    expected = subset(json.loads(FIXTURE.read_text(encoding="utf-8")))
    assert len(expected["triggers"]) == len(NEW_TRIGGERS)
    assert len(expected["functions"]) == 1
    assert any("split_assignment" in item["definition"] for item in expected["checks"])
    assert subset(dump_constraints_triggers(test_engine)) == expected


# --- split_plans ---------------------------------------------------------------------


def test_split_plan_requires_same_project_and_workspace_dataset(db_session, graph):
    for dataset_id in (graph.other_source.id, graph.beta_source.id):
        db_session.add(_split_plan(graph, dataset_id=dataset_id))
        with pytest.raises(DBAPIError, match="foreign key constraint"):
            db_session.commit()
        db_session.rollback()
    plan = _split_plan(graph)
    db_session.add(plan)
    db_session.commit()
    assert plan.locked_at is not None


def test_split_plans_are_immutable(db_session, graph):
    plan = _split_plan(graph)
    db_session.add(plan)
    db_session.commit()
    _rejects(db_session, "UPDATE split_plans SET reason = 'x' WHERE id = :id", "immutable", id=plan.id)
    _rejects(db_session, "DELETE FROM split_plans WHERE id = :id", "immutable", id=plan.id)


def test_split_plan_checks(db_session, graph):
    for overrides in (
        {"holdout_strategy": "unsupported"},
        {"holdout_test_size": 1.0},
        {"validation_folds": 1},
        {"holdout_row_count": 3},
        {"plan_digest": "Z" * 64},
        {"plan_evidence": {"rows": [1, 2]}},
    ):
        plan = _split_plan(graph)
        for key, value in overrides.items():
            setattr(plan, key, value)
        db_session.add(plan)
        with pytest.raises(DBAPIError, match="check constraint"):
            db_session.commit()
        db_session.rollback()


# --- project_decision_records ----------------------------------------------------


def test_decision_records_are_append_only_with_linear_chains(db_session, graph):
    first = _record(graph)
    db_session.add(first)
    db_session.commit()
    _rejects(
        db_session,
        "UPDATE project_decision_records SET state = 'rejected' WHERE id = :id",
        "immutable",
        id=first.id,
    )
    _rejects(db_session, "DELETE FROM project_decision_records WHERE id = :id", "immutable", id=first.id)
    db_session.add(_record(graph, supersedes_id=first.id))
    db_session.commit()
    db_session.add(_record(graph, supersedes_id=first.id))
    with pytest.raises(DBAPIError, match="uq_pdr_supersedes_id"):
        db_session.commit()
    db_session.rollback()


def test_decision_record_checks(db_session, graph):
    for overrides in (
        {"subject_kind": "project"},  # project subject must have no subject column
        {"subject_kind": "experiment"},  # named column is NULL
        {"actor_kind": "human"},  # human requires a user and no rule
        {"actor_kind": "agent", "actor_rule": None},  # agent requires a run or token
        {"rationale": "   "},
        {"rationale_untrusted": True},
        {"evidence_refs": {"kind": "candidate"}},
        {"facts": {"token": "x"}},
        {"decision_type": "Bad-Type"},
        {"subject_digest": "XYZ"},
    ):
        db_session.add(_record(graph, **overrides))
        with pytest.raises(DBAPIError, match="check constraint"):
            db_session.commit()
        db_session.rollback()


def test_decision_record_subject_must_share_workspace_and_project(db_session, graph):
    db_session.add(_record(graph, dataset_id=graph.other_source.id))
    with pytest.raises(DBAPIError, match="foreign key constraint"):
        db_session.commit()
    db_session.rollback()
    db_session.add(
        _record(
            graph,
            subject_kind="experiment",
            dataset_id=None,
            experiment_id=graph.experiment.id,
            project_id=graph.other_project.id,
        )
    )
    with pytest.raises(DBAPIError, match="foreign key constraint"):
        db_session.commit()
    db_session.rollback()
    db_session.add(_record(graph, subject_kind="experiment", dataset_id=None, experiment_id=graph.experiment.id))
    db_session.commit()


# --- project_refs ----------------------------------------------------------------------


def test_project_refs_move_but_never_change_identity_or_delete(db_session, graph):
    record = _record(graph)
    db_session.add(record)
    db_session.commit()
    ref_id = uuid4()
    insert = (
        "INSERT INTO project_refs (id, workspace_id, project_id, ref_kind, dataset_id, "
        "problem_spec_id, decision_record_id) VALUES (:id, :ws, :project, :kind, :dataset, "
        ":spec, :record)"
    )
    params = dict(
        ws=graph.ws, project=graph.project.id, record=record.id, dataset=graph.source.id, spec=None
    )
    _rejects(db_session, insert, "ck_project_refs_target_matches_kind", id=uuid4(), kind="split_plan", **params)
    _rejects(
        db_session,
        insert,
        "foreign key constraint",
        id=uuid4(),
        kind="dataset",
        **{**params, "dataset": graph.other_source.id},
    )
    db_session.execute(text(insert), {"id": ref_id, "kind": "dataset", **params})
    db_session.commit()
    _rejects(db_session, insert, "uq_project_refs_project_ref_kind", id=uuid4(), kind="dataset", **params)

    second = _source_dataset(
        db_session, graph.tmp_path, workspace_id=graph.ws, project_id=graph.project.id, name="src-v2"
    )
    moved = db_session.execute(
        text(
            "UPDATE project_refs SET dataset_id = :d, version = version + 1, moved_at = now() "
            "WHERE id = :id AND version = 1"
        ),
        {"d": second.id, "id": ref_id},
    )
    db_session.commit()
    assert moved.rowcount == 1
    for column, value in (
        ("ref_kind", "'split_plan'"),
        ("project_id", f"'{graph.other_project.id}'"),
        ("created_at", "now() - interval '1 day'"),
    ):
        _rejects(
            db_session,
            f"UPDATE project_refs SET {column} = {value} WHERE id = :id",
            f"project_refs.{column} is immutable",
            id=ref_id,
        )
    _rejects(db_session, "DELETE FROM project_refs WHERE id = :id", "immutable", id=ref_id)


# --- experiments lineage -------------------------------------------------------------


def test_root_experiment_lineage_columns_are_write_once(db_session, graph):
    graph = _locked_graph(db_session, graph)
    exp_id = graph.experiment.id
    _rejects(
        db_session,
        "UPDATE experiments SET source_dataset_id = :d WHERE id = :id",
        "source_dataset_id is write-once",
        d=graph.other_source.id,
        id=exp_id,
    )
    other_plan = _split_plan(graph, version=2, digest="2")
    db_session.add(other_plan)
    db_session.commit()
    _rejects(
        db_session,
        "UPDATE experiments SET split_plan_id = :p WHERE id = :id",
        "split_plan_id is write-once",
        p=other_plan.id,
        id=exp_id,
    )
    _rejects(
        db_session,
        "UPDATE experiments SET intent = 'later' WHERE id = :id",
        "insert-only",
        id=exp_id,
    )
    other_parent = _new_experiment(db_session, graph.setup)
    _rejects(
        db_session,
        "UPDATE experiments SET parent_pipeline_run_id = :parent WHERE id = :id",
        "insert-only",
        parent=other_parent.id,
        id=exp_id,
    )
    db_session.execute(
        text("UPDATE experiments SET status = 'COMPLETED', result = '{\"ok\": true}' WHERE id = :id"),
        {"id": exp_id},
    )
    db_session.commit()
    status = db_session.execute(
        text("SELECT status FROM experiments WHERE id = :id"), {"id": exp_id}
    ).scalar()
    assert status == "COMPLETED"


def test_split_plan_cannot_be_set_after_lock_or_without_project(db_session, graph):
    plan = _split_plan(graph)
    db_session.add(plan)
    db_session.commit()
    other = _new_experiment(db_session, graph.setup)
    db_session.execute(text("ALTER TABLE experiments DISABLE TRIGGER experiments_evidence_lock_stamp"))
    db_session.execute(
        text("UPDATE experiments SET scientific_evidence_locked_at = now() WHERE id = :id"),
        {"id": other.id},
    )
    db_session.execute(text("ALTER TABLE experiments ENABLE TRIGGER experiments_evidence_lock_stamp"))
    db_session.commit()
    _rejects(
        db_session,
        "UPDATE experiments SET split_plan_id = :p WHERE id = :id",
        "cannot be set after the run is locked",
        p=plan.id,
        id=other.id,
    )
    _rejects(
        db_session,
        "UPDATE experiments SET project_id = NULL, split_plan_id = :p WHERE id = :id",
        "ck_experiments_split_plan_requires_project",
        p=plan.id,
        id=graph.experiment.id,
    )


def test_branch_must_share_source_dataset_and_split_plan(db_session, graph):
    graph = _locked_graph(db_session, graph)
    parent = graph.experiment.id
    child = _insert_branch(
        db_session, parent, source_dataset_id=graph.source.id, split_plan_id=graph.plan.id
    )
    assert child is not None
    with pytest.raises(DBAPIError, match="share source_dataset_id"):
        _insert_branch(
            db_session, parent, source_dataset_id=graph.other_source.id, split_plan_id=graph.plan.id
        )
    db_session.rollback()
    with pytest.raises(DBAPIError, match="share split_plan_id"):
        _insert_branch(db_session, parent, source_dataset_id=graph.source.id, split_plan_id=None)
    db_session.rollback()
    # A parent without a split plan cannot be branched with a change set.
    legacy = _new_experiment(db_session, graph.setup)
    with pytest.raises(DBAPIError, match="share source_dataset_id|share split_plan_id"):
        _insert_branch(db_session, legacy.id, source_dataset_id=graph.source.id, split_plan_id=None)
    db_session.rollback()
    # Legacy branches (no change set) are unaffected.
    legacy_child = _insert_branch(
        db_session, legacy.id, source_dataset_id=None, split_plan_id=None, change_set=None
    )
    assert legacy_child is not None
    _rejects(
        db_session,
        "UPDATE experiments SET change_set = NULL WHERE id = :id",
        "insert-only",
        id=child,
    )


def test_change_set_checks(db_session, graph):
    graph = _locked_graph(db_session, graph)
    stored = ExperimentChangeSet.model_validate(VALID_CHANGE_SET).to_storage()
    assert stored == VALID_CHANGE_SET
    for bad, constraint in (
        ({"changes": [{"kind": "family_include"}]}, "ck_experiments_change_set_schema_version"),
        ({"schema_version": 1, "changes": []}, "ck_experiments_change_set_changes"),
        ({"schema_version": 1, "changes": "x"}, "ck_experiments_change_set_changes"),
        ({**VALID_CHANGE_SET, "token": "x"}, "ck_experiments_change_set_no_secrets"),
        ({**VALID_CHANGE_SET, "pad": "x" * 17000}, "ck_experiments_change_set_bounded"),
    ):
        with pytest.raises(DBAPIError, match=constraint):
            _insert_branch(
                db_session,
                graph.experiment.id,
                source_dataset_id=graph.source.id,
                split_plan_id=graph.plan.id,
                change_set=bad,
            )
        db_session.rollback()
    assert "majority" in NON_EXCLUDABLE_FAMILIES
    from app.engine.search.generator import DUMMY_FAMILIES

    assert set(NON_EXCLUDABLE_FAMILIES) == set(DUMMY_FAMILIES)


def test_source_dataset_backfill_uses_single_upload_link(db_session, graph):
    spec = importlib.util.spec_from_file_location("rev_0063", MIGRATION)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    linked = graph.experiment
    ambiguous = _new_experiment(db_session, graph.setup)
    cross_project = _new_experiment(db_session, graph.setup)
    for experiment, datasets in (
        (linked, [graph.source]),
        (ambiguous, [graph.source, graph.other_source]),
        (cross_project, [graph.other_source]),
    ):
        for dataset in datasets:
            db_session.add(
                ClientLabUpload(
                    workspace_id=graph.ws,
                    category="Revenue",
                    original_filename="u.csv",
                    stored_path="/tmp/u.csv",
                    kind="spreadsheet",
                    record_count=2,
                    fields_noticed=["a"],
                    has_named_fields=True,
                    experiment_id=experiment.id,
                    dataset_id=dataset.id,
                )
            )
    db_session.commit()
    db_session.execute(text(module._SOURCE_DATASET_WORKSPACE_PRECHECK_SQL))
    db_session.execute(text(module._SOURCE_DATASET_BACKFILL_SQL))
    db_session.commit()
    rows = dict(
        db_session.execute(
            text("SELECT id, source_dataset_id FROM experiments WHERE id IN (:a, :b, :c)"),
            {"a": linked.id, "b": ambiguous.id, "c": cross_project.id},
        ).all()
    )
    assert rows[linked.id] == graph.source.id
    assert rows[ambiguous.id] is None
    assert rows[cross_project.id] is None  # dataset belongs to another project


# --- winner_locked backfill -------------------------------------------------------------


def _winner(
    db, graph, experiment, *, project_id, key: str, score: float = 0.81
) -> ModelSelectionDecision:
    candidate = ExperimentCandidate(
        workspace_id=graph.ws,
        project_id=graph.project.id,
        experiment_id=experiment.id,
        candidate_key=key,
        fingerprint=("ab" * 20),
        status="completed",
        payload={},
    )
    db.add(candidate)
    db.flush()
    selection = ModelSelectionDecision(
        workspace_id=graph.ws,
        project_id=project_id,
        pipeline_run_id=experiment.id,
        selected_candidate_id=candidate.id,
        selection_metric="pr_auc",
        selected_score=score,
        selection_policy="cv_mean_then_simplicity",
        reason="highest CV pr_auc",
        locked_at=datetime(2026, 9, 1, tzinfo=UTC),
    )
    db.add(selection)
    db.commit()
    return selection


def test_backfill_winner_records_is_idempotent(db_session, graph, test_engine, monkeypatch, capsys):
    selection = _winner(db_session, graph, graph.experiment, project_id=graph.project.id, key="w")
    orphan_experiment = _new_experiment(db_session, graph.setup)
    _winner(db_session, graph, orphan_experiment, project_id=None, key="o")
    mismatch_experiment = _new_experiment(db_session, graph.setup)
    _winner(
        db_session, graph, mismatch_experiment, project_id=graph.other_project.id, key="m"
    )
    nan_experiment = _new_experiment(db_session, graph.setup)
    nan_selection = _winner(
        db_session, graph, nan_experiment, project_id=graph.project.id, key="n", score=float("nan")
    )

    first = backfill_winner_records(db_session)
    assert first.as_dict() == {
        "created": 2,
        "already_present": 0,
        "skipped_without_project": 1,
        "skipped_project_mismatch": 1,
    }

    from app.cli import main as cli

    monkeypatch.setattr(cli, "_session", sessionmaker(bind=test_engine))
    assert cli.main(["graph", "backfill-winner-records"]) == 0
    assert json.loads(capsys.readouterr().out) == {
        "created": 0,
        "already_present": 2,
        "skipped_without_project": 1,
        "skipped_project_mismatch": 1,
    }

    records = {
        row.candidate_id: row
        for row in db_session.execute(select(ProjectDecisionRecord)).scalars().all()
    }
    assert len(records) == 2
    nan_record = records[nan_selection.selected_candidate_id]
    assert nan_record.facts["selected_score"] is None
    assert nan_record.details["selected_score_non_finite"] is True
    record = records[selection.selected_candidate_id]
    assert record.decision_type == "winner_locked"
    assert record.state == "accepted"
    assert record.subject_kind == "candidate"
    assert record.candidate_id == selection.selected_candidate_id
    assert record.subject_digest == "ab" * 20
    assert record.actor_kind == "rule"
    assert record.actor_rule == "selection.cv_winner.backfill.v1"
    assert record.rationale_untrusted is False
    assert record.event_at == selection.locked_at
    assert record.details == {
        "backfilled": True,
        "model_selection_decision_id": str(selection.id),
    }
    assert record.facts == {
        "selected_score": 0.81,
        "selection_metric": "pr_auc",
        "selection_policy": "cv_mean_then_simplicity",
    }
    assert record.evidence_refs[0]["scope"] == "cv_aggregate"
    assert record.idempotency_key == f"winner_locked:{selection.id}"


def test_problem_spec_ref_target_must_share_project(db_session, graph):
    spec = ProblemSpec(
        workspace_id=graph.ws,
        project_id=graph.other_project.id,
        version=1,
        task_type="binary",
        business_objective="x",
        constraints={},
        success_criteria={},
        content_digest="e" * 64,
        created_by=graph.setup["alpha_admin"].id,
    )
    db_session.add(spec)
    db_session.commit()
    db_session.add(
        _record(graph, subject_kind="problem_spec", dataset_id=None, problem_spec_id=spec.id)
    )
    with pytest.raises(DBAPIError, match="foreign key constraint"):
        db_session.commit()
    db_session.rollback()
