"""Scientific PipelineRun branch lineage. Pointers only — no forking."""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from sqlalchemy import select, text
from sqlalchemy.exc import DBAPIError, IntegrityError

from adaptive_modeling.fixtures import ordinary_binary
from adaptive_modeling.production import labs_upload_and_train
from app.db.models import (
    Dataset,
    Environment,
    Experiment,
    ExperimentCandidate,
    PredictionTask,
)
from app.services.lineage_service import (
    LineageError,
    create_pipeline_run,
    create_workflow_run,
)
from test_data_model_lineage import make_lineage_setup


def _pipeline(db_session, setup, *, pipeline_index=0, **branch):
    workflow_run = create_workflow_run(
        db_session,
        workspace_id=setup["alpha"].id,
        workflow=setup["alpha_workflow"],
        requester=setup["alpha_admin"],
        trigger_type="manual",
        source_type="dataset",
    )
    return create_pipeline_run(
        db_session,
        workflow_run=workflow_run,
        environment=setup["env"],
        dataset=setup["alpha_dataset"],
        task=setup["task"],
        pipeline_index=pipeline_index,
        commit=False,
        **branch,
    )


@pytest.fixture
def _rule_engine_only(monkeypatch):
    monkeypatch.setattr(
        "app.services.lab_decision_ledger.get_settings",
        lambda: SimpleNamespace(decision_agent_enabled=False, decision_agent_api_key=""),
    )


def test_same_workspace_branch_lineage_is_a_new_unlocked_run(db_session, tmp_path):
    setup = make_lineage_setup(db_session, tmp_path)
    parent = _pipeline(db_session, setup)
    parent.status = "COMPLETED"
    parent.result = {"selection": {"note": "parent evidence"}}
    db_session.flush()
    parent_status = parent.status
    parent_result = dict(parent.result)
    parent_lock = parent.scientific_evidence_locked_at
    parent_updated = (
        parent.id,
        parent.workspace_id,
        parent.status,
        parent.result,
        parent.scientific_evidence_locked_at,
        parent.parent_pipeline_run_id,
    )
    child = _pipeline(
        db_session,
        setup,
        parent_pipeline_run_id=parent.id,
        branch_key="agent_followup",
        branch_reason="try a different seed",
    )
    db_session.commit()
    stored_parent = db_session.get(Experiment, parent.id)
    stored_child = db_session.get(Experiment, child.id)
    assert stored_child is not None and stored_parent is not None
    assert stored_child.id != stored_parent.id
    assert stored_child.parent_pipeline_run_id == stored_parent.id
    assert stored_child.branch_key == "agent_followup"
    assert stored_child.branch_reason == "try a different seed"
    assert stored_child.scientific_evidence_locked_at is None
    assert stored_child.status == "CREATED"
    assert stored_parent.status == parent_status
    assert stored_parent.result == parent_result
    assert stored_parent.scientific_evidence_locked_at == parent_lock
    assert stored_parent.parent_pipeline_run_id is None
    assert (
        stored_parent.id,
        stored_parent.workspace_id,
        stored_parent.status,
        stored_parent.result,
        stored_parent.scientific_evidence_locked_at,
        stored_parent.parent_pipeline_run_id,
    ) == parent_updated


def test_cross_workspace_parent_is_rejected(db_session, tmp_path):
    setup = make_lineage_setup(db_session, tmp_path)
    alpha_parent = _pipeline(db_session, setup)
    beta_run = create_workflow_run(
        db_session,
        workspace_id=setup["beta"].id,
        workflow=setup["beta_workflow"],
        requester=setup["beta_admin"],
        trigger_type="manual",
        source_type="dataset",
    )
    with pytest.raises(LineageError, match="parent pipeline run"):
        create_pipeline_run(
            db_session,
            workflow_run=beta_run,
            environment=setup["env"],
            dataset=setup["beta_dataset"],
            task=setup["task"],
            commit=False,
            parent_pipeline_run_id=alpha_parent.id,
            branch_key="agent_followup",
        )
    beta_child = create_pipeline_run(
        db_session,
        workflow_run=beta_run,
        environment=setup["env"],
        dataset=setup["beta_dataset"],
        task=setup["task"],
        commit=False,
    )
    db_session.commit()
    with pytest.raises((DBAPIError, IntegrityError), match="foreign key constraint"):
        db_session.execute(
            text(
                "UPDATE experiments SET parent_pipeline_run_id = :parent WHERE id = :id"
            ),
            {"parent": alpha_parent.id, "id": beta_child.id},
        )
        db_session.commit()
    db_session.rollback()


def test_parent_delete_is_no_action_while_children_exist(db_session, tmp_path):
    setup = make_lineage_setup(db_session, tmp_path)
    parent = _pipeline(db_session, setup)
    child = _pipeline(
        db_session,
        setup,
        parent_pipeline_run_id=parent.id,
        branch_key="agent_followup",
    )
    db_session.commit()
    parent_id = parent.id
    child_id = child.id
    with pytest.raises((DBAPIError, IntegrityError), match="foreign key constraint"):
        db_session.execute(
            text("DELETE FROM experiments WHERE id = :id"),
            {"id": parent_id},
        )
        db_session.commit()
    db_session.rollback()
    assert db_session.get(Experiment, parent_id) is not None
    stored_child = db_session.get(Experiment, child_id)
    assert stored_child is not None
    assert stored_child.parent_pipeline_run_id == parent_id
    db_session.execute(text("DELETE FROM experiments WHERE id = :id"), {"id": child_id})
    db_session.commit()
    assert db_session.get(Experiment, child_id) is None
    leftover_parent = db_session.get(Experiment, parent_id)
    assert leftover_parent is not None
    assert leftover_parent.parent_pipeline_run_id is None


def test_locked_parent_evidence_stays_frozen(auth_client, db_session, monkeypatch, _rule_engine_only):
    _upload, workflow_run, experiment, _model_version = labs_upload_and_train(
        auth_client,
        db_session,
        monkeypatch,
        ordinary_binary(),
        filename="branch_parent.csv",
        target="outcome",
    )
    db_session.expire_all()
    parent = db_session.get(Experiment, experiment.id)
    assert parent is not None
    assert parent.scientific_evidence_locked_at is not None
    locked_at = parent.scientific_evidence_locked_at
    parent_status = parent.status
    parent_result = dict(parent.result or {})
    winner = db_session.scalars(
        select(ExperimentCandidate).where(
            ExperimentCandidate.experiment_id == parent.id
        )
    ).first()
    assert winner is not None
    winner_fingerprint = winner.fingerprint
    winner_status = winner.status
    n_candidates = len(
        list(
            db_session.scalars(
                select(ExperimentCandidate).where(
                    ExperimentCandidate.experiment_id == parent.id
                )
            )
        )
    )
    environment = db_session.get(Environment, parent.environment_id)
    dataset = db_session.get(Dataset, parent.dataset_id)
    task = db_session.get(PredictionTask, parent.task_id)
    child = create_pipeline_run(
        db_session,
        workflow_run=workflow_run,
        environment=environment,
        dataset=dataset,
        task=task,
        pipeline_index=1,
        commit=False,
        parent_pipeline_run_id=parent.id,
        branch_key="agent_followup",
        branch_reason="locked parent must stay frozen",
    )
    db_session.commit()
    db_session.expire_all()
    stored_parent = db_session.get(Experiment, parent.id)
    stored_child = db_session.get(Experiment, child.id)
    stored_winner = db_session.get(ExperimentCandidate, winner.id)
    assert stored_parent.scientific_evidence_locked_at == locked_at
    assert stored_parent.status == parent_status
    assert stored_parent.result == parent_result
    assert stored_parent.parent_pipeline_run_id is None
    assert stored_child.scientific_evidence_locked_at is None
    assert stored_child.parent_pipeline_run_id == parent.id
    assert stored_winner.fingerprint == winner_fingerprint
    assert stored_winner.status == winner_status
    assert (
        len(
            list(
                db_session.scalars(
                    select(ExperimentCandidate).where(
                        ExperimentCandidate.experiment_id == parent.id
                    )
                )
            )
        )
        == n_candidates
    )
    with pytest.raises(DBAPIError, match="locked"):
        db_session.execute(
            text(
                "UPDATE experiment_candidates SET status = 'forged' WHERE id = :id"
            ),
            {"id": winner.id},
        )
        db_session.commit()
    db_session.rollback()


def test_self_parent_and_orphan_branch_metadata_are_rejected(db_session, tmp_path):
    setup = make_lineage_setup(db_session, tmp_path)
    parent = _pipeline(db_session, setup)
    db_session.commit()
    with pytest.raises(LineageError, match="require a parent"):
        _pipeline(db_session, setup, branch_key="agent_followup")
    with pytest.raises((DBAPIError, IntegrityError)):
        db_session.execute(
            text("UPDATE experiments SET parent_pipeline_run_id = id WHERE id = :id"),
            {"id": parent.id},
        )
        db_session.commit()
    db_session.rollback()
    with pytest.raises((DBAPIError, IntegrityError)):
        db_session.execute(
            text(
                "UPDATE experiments SET branch_key = 'agent_followup' WHERE id = :id"
            ),
            {"id": parent.id},
        )
        db_session.commit()
    db_session.rollback()
