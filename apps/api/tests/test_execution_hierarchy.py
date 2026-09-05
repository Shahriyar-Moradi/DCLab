from __future__ import annotations

from uuid import uuid4

import pytest
from sqlalchemy.exc import IntegrityError

from app.db.models import (
    MlWorkflow,
    Pipeline,
    PipelineStageRun,
    PipelineVersion,
    UserRole,
    WorkflowVersion,
    Workspace,
)
from app.engine.types import SearchConfig, TaskSpec
from app.services.auth_service import create_user
from app.services.lab_service import ingest_dataset, seed_dogfood, upsert_task
from app.services.lineage_service import (
    LineageError,
    create_dataset_asset,
    create_pipeline_run,
    create_workflow,
    create_workflow_run,
    enable_workspace_domain,
    seed_business_domains,
)
from app.services.problem_spec_service import create_problem_spec
from app.services.project_service import create_project
from app.services.workflow_execution_service import (
    create_pipeline,
    create_pipeline_version,
    create_workflow_version,
    lock_pipeline_version,
    lock_workflow_version,
    replace_pipeline_stage_runs,
)


def _user(db, *, workspace_id, prefix: str = "exec"):
    return create_user(
        db,
        email=f"{prefix}-{uuid4().hex}@test.invalid",
        password="test-password",
        role=UserRole.WORKSPACE_OWNER,
        full_name=prefix,
        workspace_id=workspace_id,
    )


def _workspace(db, name: str) -> Workspace:
    row = Workspace(slug=f"{name}-{uuid4().hex[:10]}", name=name)
    db.add(row)
    db.flush()
    return row


def make_hierarchy(db_session, tmp_path):
    workspace = _workspace(db_session, "hierarchy")
    actor = _user(db_session, workspace_id=workspace.id)
    seed_business_domains(db_session)
    domain = enable_workspace_domain(
        db_session,
        workspace_id=workspace.id,
        domain_slug="sales",
        actor=actor,
    )
    project = create_project(
        db_session,
        actor=actor,
        workspace_id=workspace.id,
        name="Hierarchy project",
        slug="hierarchy-project",
    )
    workflow = create_workflow(
        db_session,
        workspace_id=workspace.id,
        workspace_domain=domain,
        project_id=project.id,
        name="Lead scoring",
        slug="lead-scoring",
        description="Score inbound leads.",
        business_objective="Prioritize outreach.",
        actor=actor,
    )
    env = seed_dogfood(db_session)
    csv_path = tmp_path / "hierarchy.csv"
    csv_path.write_text("feature,target\n1,0\n2,1\n", encoding="utf-8")
    asset = create_dataset_asset(
        db_session,
        workspace_id=workspace.id,
        name="Leads",
        slug="leads",
        actor=actor,
        project_id=project.id,
    )
    dataset = ingest_dataset(
        db_session,
        environment=env,
        name="Leads",
        location=str(csv_path),
        workspace_id=workspace.id,
        dataset_asset=asset,
        created_by=actor.id,
    )
    task = upsert_task(
        db_session,
        env,
        TaskSpec(
            id=f"hierarchy-{uuid4().hex[:8]}",
            name="Hierarchy task",
            task_type="binary",
            target="target",
            entity_id=None,
            evaluation_metric="pr_auc",
            feature_groups={"features": ["feature"]},
            validation_strategy="stratified",
        ),
    )
    db_session.commit()
    return {
        "workspace": workspace,
        "actor": actor,
        "domain": domain,
        "project": project,
        "workflow": workflow,
        "env": env,
        "dataset": dataset,
        "task": task,
    }


@pytest.fixture()
def hierarchy(db_session, tmp_path):
    return make_hierarchy(db_session, tmp_path)


def test_project_owns_multiple_workflows(db_session, hierarchy):
    second = create_workflow(
        db_session,
        workspace_id=hierarchy["workspace"].id,
        workspace_domain=hierarchy["domain"],
        project_id=hierarchy["project"].id,
        name="Churn",
        slug="churn",
        actor=hierarchy["actor"],
    )
    db_session.commit()
    owned = list(
        db_session.query(MlWorkflow).filter(
            MlWorkflow.project_id == hierarchy["project"].id
        )
    )
    assert {row.slug for row in owned} == {"lead-scoring", "churn"}
    assert second.workspace_domain_id == hierarchy["domain"].id
    assert second.workspace_id == hierarchy["workspace"].id


def test_workflow_versions_are_immutable_after_lock(db_session, hierarchy):
    version = create_workflow_version(
        db_session,
        workflow=hierarchy["workflow"],
        actor=hierarchy["actor"],
        lock=True,
    )
    db_session.commit()
    version.definition = {"tampered": True}
    with pytest.raises(ValueError, match="WorkflowVersion is locked and immutable"):
        db_session.flush()
    db_session.rollback()
    stored = db_session.get(WorkflowVersion, version.id)
    assert stored.locked_at is not None
    assert stored.definition["slug"] == "lead-scoring"
    with pytest.raises(ValueError, match="WorkflowVersion is locked and immutable"):
        db_session.delete(stored)
        db_session.flush()
    db_session.rollback()


def test_workflow_run_references_exact_workflow_version(db_session, hierarchy):
    first = create_workflow_version(
        db_session,
        workflow=hierarchy["workflow"],
        actor=hierarchy["actor"],
        lock=True,
    )
    second = create_workflow_version(
        db_session,
        workflow=hierarchy["workflow"],
        actor=hierarchy["actor"],
        lock=True,
    )
    pinned = create_workflow_run(
        db_session,
        workspace_id=hierarchy["workspace"].id,
        workflow=hierarchy["workflow"],
        requester=hierarchy["actor"],
        trigger_type="manual",
        source_type="dataset",
        workflow_version_id=first.id,
    )
    latest = create_workflow_run(
        db_session,
        workspace_id=hierarchy["workspace"].id,
        workflow=hierarchy["workflow"],
        requester=hierarchy["actor"],
        trigger_type="api",
        source_type="dataset",
        initiated_by_type="api",
    )
    db_session.commit()
    assert pinned.workflow_version_id == first.id
    assert pinned.project_id == hierarchy["project"].id
    assert pinned.initiated_by_type == "human"
    assert latest.workflow_version_id == second.id
    assert latest.initiated_by_type == "api"
    assert first.id != second.id


def test_workflow_run_links_problem_spec(db_session, hierarchy):
    spec = create_problem_spec(
        db_session,
        actor=hierarchy["actor"],
        workspace_id=hierarchy["workspace"].id,
        project_id=hierarchy["project"].id,
        task_type="binary",
        business_objective="Identify likely converters.",
        target_column="target",
        primary_metric="pr_auc",
    )
    run = create_workflow_run(
        db_session,
        workspace_id=hierarchy["workspace"].id,
        workflow=hierarchy["workflow"],
        requester=hierarchy["actor"],
        trigger_type="manual",
        source_type="dataset",
        problem_spec_id=spec.id,
    )
    db_session.commit()
    assert run.problem_spec_id == spec.id
    assert run.project_id == spec.project_id


def test_one_workflow_run_has_multiple_canonical_pipeline_runs(db_session, hierarchy):
    run = create_workflow_run(
        db_session,
        workspace_id=hierarchy["workspace"].id,
        workflow=hierarchy["workflow"],
        requester=hierarchy["actor"],
        trigger_type="manual",
        source_type="dataset",
    )
    first = create_pipeline_run(
        db_session,
        workflow_run=run,
        environment=hierarchy["env"],
        dataset=hierarchy["dataset"],
        task=hierarchy["task"],
        pipeline_name="deterministic_ml",
        pipeline_index=0,
        pipeline_purpose="baseline",
        config=SearchConfig(max_candidates=3),
    )
    second = create_pipeline_run(
        db_session,
        workflow_run=run,
        environment=hierarchy["env"],
        dataset=hierarchy["dataset"],
        task=hierarchy["task"],
        pipeline_name="deterministic_ml",
        pipeline_index=1,
        pipeline_purpose="challenger",
        config=SearchConfig(max_candidates=3),
        input_role=None,
    )
    db_session.commit()
    assert first.workflow_run_id == run.id == second.workflow_run_id
    assert first.pipeline_id == second.pipeline_id
    assert first.pipeline_version_id == second.pipeline_version_id
    assert [first.run_number, second.run_number] == [1, 2]
    assert first.project_id == hierarchy["project"].id == second.project_id
    assert first.pipeline_name == "deterministic_ml"
    assert {first.pipeline_index, second.pipeline_index} == {0, 1}
    pipeline = db_session.get(Pipeline, first.pipeline_id)
    version = db_session.get(PipelineVersion, first.pipeline_version_id)
    assert pipeline.workflow_id == hierarchy["workflow"].id
    assert pipeline.project_id == hierarchy["project"].id
    assert version.workflow_version_id == run.workflow_version_id
    assert version.pipeline_id == pipeline.id


def test_pipeline_version_is_immutable_after_lock(db_session, hierarchy):
    run = create_workflow_run(
        db_session,
        workspace_id=hierarchy["workspace"].id,
        workflow=hierarchy["workflow"],
        requester=hierarchy["actor"],
        trigger_type="manual",
        source_type="dataset",
    )
    pipeline = create_pipeline(
        db_session,
        workflow=hierarchy["workflow"],
        name="Training graph",
        slug="training-graph",
        purpose="training",
        actor=hierarchy["actor"],
    )
    version = create_pipeline_version(
        db_session,
        pipeline=pipeline,
        workflow_version=run.workflow_version,
        graph_definition={"nodes": ["ingest"]},
        config={"seed": 1},
        lock=True,
    )
    db_session.commit()
    version.graph_definition = {"nodes": ["tampered"]}
    with pytest.raises(ValueError, match="PipelineVersion is locked and immutable"):
        db_session.flush()
    db_session.rollback()
    stored = db_session.get(PipelineVersion, version.id)
    assert stored.graph_definition == {"nodes": ["ingest"]}
    lock_pipeline_version(db_session, stored)
    db_session.commit()
    assert stored.locked_at is not None


def test_pipeline_run_links_project_workflow_and_pipeline(db_session, hierarchy):
    run = create_workflow_run(
        db_session,
        workspace_id=hierarchy["workspace"].id,
        workflow=hierarchy["workflow"],
        requester=hierarchy["actor"],
        trigger_type="schedule",
        source_type="dataset",
        initiated_by_type="schedule",
    )
    pipeline_run = create_pipeline_run(
        db_session,
        workflow_run=run,
        environment=hierarchy["env"],
        dataset=hierarchy["dataset"],
        task=hierarchy["task"],
        pipeline_name="open_ingest_deterministic_ml",
        pipeline_purpose="training_and_scoring",
    )
    db_session.commit()
    assert pipeline_run.project_id == hierarchy["project"].id
    assert run.workflow_id == hierarchy["workflow"].id
    pipeline = db_session.get(Pipeline, pipeline_run.pipeline_id)
    version = db_session.get(PipelineVersion, pipeline_run.pipeline_version_id)
    assert pipeline.workspace_id == hierarchy["workspace"].id
    assert pipeline.project_id == hierarchy["project"].id
    assert pipeline.workflow_id == hierarchy["workflow"].id
    assert version.project_id == hierarchy["project"].id
    assert version.workflow_version_id == run.workflow_version_id
    assert run.initiated_by_type == "schedule"


def test_stage_run_sequence_is_persisted(db_session, hierarchy):
    run = create_workflow_run(
        db_session,
        workspace_id=hierarchy["workspace"].id,
        workflow=hierarchy["workflow"],
        requester=hierarchy["actor"],
        trigger_type="manual",
        source_type="dataset",
    )
    pipeline_run = create_pipeline_run(
        db_session,
        workflow_run=run,
        environment=hierarchy["env"],
        dataset=hierarchy["dataset"],
        task=hierarchy["task"],
    )
    replace_pipeline_stage_runs(
        db_session,
        pipeline_run,
        [
            {
                "stage": "ingest",
                "status": "completed",
                "duration_ms": 12.5,
                "rows_in": 2,
                "rows_out": 2,
            },
            {
                "stage": "train",
                "status": "failed",
                "failure_code": "fit_error",
                "error": "boom",
                "duration_ms": 4,
            },
        ],
    )
    db_session.commit()
    rows = list(
        db_session.query(PipelineStageRun)
        .filter(PipelineStageRun.pipeline_run_id == pipeline_run.id)
        .order_by(PipelineStageRun.sequence)
    )
    assert [row.sequence for row in rows] == [1, 2]
    assert [row.stage_key for row in rows] == ["ingest", "train"]
    assert rows[0].status == "completed"
    assert rows[1].status == "failed"
    assert rows[1].failure_code == "fit_error"
    assert rows[0].project_id == hierarchy["project"].id
    replace_pipeline_stage_runs(
        db_session,
        pipeline_run,
        [{"stage": "only", "status": "completed", "duration_ms": 1}],
    )
    db_session.commit()
    remaining = list(
        db_session.query(PipelineStageRun).filter(
            PipelineStageRun.pipeline_run_id == pipeline_run.id
        )
    )
    assert len(remaining) == 1
    assert remaining[0].sequence == 1


def test_cross_workspace_execution_links_are_rejected(db_session, hierarchy):
    other = _workspace(db_session, "other")
    other_actor = _user(db_session, workspace_id=other.id, prefix="other")
    seed_business_domains(db_session)
    other_domain = enable_workspace_domain(
        db_session,
        workspace_id=other.id,
        domain_slug="sales",
        actor=other_actor,
    )
    other_project = create_project(
        db_session,
        actor=other_actor,
        workspace_id=other.id,
        name="Other project",
        slug="other-project",
    )
    other_workflow = create_workflow(
        db_session,
        workspace_id=other.id,
        workspace_domain=other_domain,
        project_id=other_project.id,
        name="Other",
        slug="other",
        actor=other_actor,
    )
    other_spec = create_problem_spec(
        db_session,
        actor=other_actor,
        workspace_id=other.id,
        project_id=other_project.id,
        task_type="binary",
        business_objective="Other intent.",
        target_column="target",
    )
    db_session.commit()
    with pytest.raises(LineageError, match="project does not belong"):
        create_workflow(
            db_session,
            workspace_id=hierarchy["workspace"].id,
            workspace_domain=hierarchy["domain"],
            project_id=other_project.id,
            name="Stolen",
            slug="stolen",
            actor=hierarchy["actor"],
        )
    with pytest.raises(LineageError, match="workflow belongs"):
        create_workflow_run(
            db_session,
            workspace_id=hierarchy["workspace"].id,
            workflow=other_workflow,
            requester=hierarchy["actor"],
            trigger_type="manual",
            source_type="dataset",
        )
    with pytest.raises(LineageError, match="problem spec belongs"):
        create_workflow_run(
            db_session,
            workspace_id=hierarchy["workspace"].id,
            workflow=hierarchy["workflow"],
            requester=hierarchy["actor"],
            trigger_type="manual",
            source_type="dataset",
            problem_spec_id=other_spec.id,
        )
    with pytest.raises(LineageError, match="initiated_by_type agent is reserved"):
        create_workflow_run(
            db_session,
            workspace_id=hierarchy["workspace"].id,
            workflow=hierarchy["workflow"],
            requester=hierarchy["actor"],
            trigger_type="manual",
            source_type="dataset",
            initiated_by_type="agent",
        )
    other_run = create_workflow_run(
        db_session,
        workspace_id=other.id,
        workflow=other_workflow,
        requester=other_actor,
        trigger_type="manual",
        source_type="dataset",
    )
    sibling = create_workflow(
        db_session,
        workspace_id=hierarchy["workspace"].id,
        workspace_domain=hierarchy["domain"],
        project_id=hierarchy["project"].id,
        name="Sibling",
        slug="sibling",
        actor=hierarchy["actor"],
    )
    sibling_run = create_workflow_run(
        db_session,
        workspace_id=hierarchy["workspace"].id,
        workflow=sibling,
        requester=hierarchy["actor"],
        trigger_type="manual",
        source_type="dataset",
    )
    pipeline = create_pipeline(
        db_session,
        workflow=hierarchy["workflow"],
        name="Local",
        slug="local",
        actor=hierarchy["actor"],
    )
    with pytest.raises(LineageError, match="crosses workspaces"):
        create_pipeline_version(
            db_session,
            pipeline=pipeline,
            workflow_version=other_run.workflow_version,
        )
    with pytest.raises(LineageError, match="not part of this workflow version"):
        create_pipeline_version(
            db_session,
            pipeline=pipeline,
            workflow_version=sibling_run.workflow_version,
        )


def test_duplicate_workflow_and_pipeline_versions_are_rejected(db_session, hierarchy):
    version = create_workflow_version(
        db_session,
        workflow=hierarchy["workflow"],
        actor=hierarchy["actor"],
        lock=True,
    )
    db_session.commit()
    db_session.add(
        WorkflowVersion(
            workspace_id=version.workspace_id,
            project_id=version.project_id,
            workflow_id=version.workflow_id,
            version=version.version,
            definition={"duplicate": True},
            content_digest="a" * 64,
            created_by=hierarchy["actor"].id,
        )
    )
    with pytest.raises(IntegrityError):
        db_session.flush()
    db_session.rollback()


def test_unlocked_workflow_version_can_be_locked(db_session, hierarchy):
    version = create_workflow_version(
        db_session,
        workflow=hierarchy["workflow"],
        actor=hierarchy["actor"],
        lock=False,
    )
    version.definition = {**version.definition, "note": "edit before lock"}
    db_session.flush()
    lock_workflow_version(db_session, version)
    db_session.commit()
    assert version.locked_at is not None
    version.definition = {"tampered": True}
    with pytest.raises(ValueError, match="locked and immutable"):
        db_session.flush()
    db_session.rollback()
