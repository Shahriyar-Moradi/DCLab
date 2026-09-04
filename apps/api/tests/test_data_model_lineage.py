from __future__ import annotations

from uuid import uuid4

import pytest
from sqlalchemy import select

from app.db.models import (
    BusinessDomain,
    ClientLabUpload,
    ExperimentCandidate,
    ModelVersion,
    UserRole,
    WorkflowRun,
    Workspace,
)
from app.engine.types import SearchConfig, TaskSpec
from app.services.auth_service import create_user
from app.services.lab_service import ingest_dataset, seed_dogfood, upsert_task
from app.services.lineage_service import (
    LineageError,
    create_dataset_asset,
    create_model_asset,
    create_model_version,
    create_pipeline_run,
    create_workflow,
    create_workflow_run,
    enable_workspace_domain,
    seed_business_domains,
)


@pytest.fixture()
def lineage_setup(db_session, tmp_path):
    alpha = Workspace(slug=f"lineage-alpha-{uuid4().hex}", name="Lineage Alpha")
    beta = Workspace(slug=f"lineage-beta-{uuid4().hex}", name="Lineage Beta")
    db_session.add_all([alpha, beta])
    db_session.flush()
    alpha_admin = create_user(
        db_session,
        email=f"lineage-alpha-{uuid4().hex}@test.invalid",
        password="test-password",
        role=UserRole.BUSINESS_ADMIN,
        workspace_id=alpha.id,
    )
    beta_admin = create_user(
        db_session,
        email=f"lineage-beta-{uuid4().hex}@test.invalid",
        password="test-password",
        role=UserRole.BUSINESS_ADMIN,
        workspace_id=beta.id,
    )
    seeded = seed_business_domains(db_session)
    alpha_domain = enable_workspace_domain(
        db_session,
        workspace_id=alpha.id,
        domain_slug="sales",
        actor=alpha_admin,
    )
    beta_domain = enable_workspace_domain(
        db_session,
        workspace_id=beta.id,
        domain_slug="sales",
        actor=beta_admin,
    )
    alpha_workflow = create_workflow(
        db_session,
        workspace_id=alpha.id,
        workspace_domain=alpha_domain,
        name="Lead conversion",
        slug="lead-conversion",
        description="Score inbound leads.",
        business_objective="Prioritize sales outreach.",
        actor=alpha_admin,
    )
    beta_workflow = create_workflow(
        db_session,
        workspace_id=beta.id,
        workspace_domain=beta_domain,
        name="Lead conversion",
        slug="lead-conversion",
        actor=beta_admin,
    )
    env = seed_dogfood(db_session)
    alpha_path = tmp_path / "alpha.csv"
    beta_path = tmp_path / "beta.csv"
    alpha_path.write_text("feature,target\n1,0\n2,1\n", encoding="utf-8")
    beta_path.write_text("feature,target\n3,0\n4,1\n", encoding="utf-8")
    alpha_asset = create_dataset_asset(
        db_session,
        workspace_id=alpha.id,
        name="Leads",
        slug="leads",
        actor=alpha_admin,
    )
    beta_asset = create_dataset_asset(
        db_session,
        workspace_id=beta.id,
        name="Leads",
        slug="leads",
        actor=beta_admin,
    )
    alpha_dataset = ingest_dataset(
        db_session,
        environment=env,
        name="Leads",
        location=str(alpha_path),
        version="v1",
        workspace_id=alpha.id,
        dataset_asset=alpha_asset,
    )
    beta_dataset = ingest_dataset(
        db_session,
        environment=env,
        name="Leads",
        location=str(beta_path),
        version="v1",
        workspace_id=beta.id,
        dataset_asset=beta_asset,
    )
    task = upsert_task(
        db_session,
        env,
        TaskSpec(
            id=f"lineage_task_{uuid4().hex}",
            name="Lineage task",
            task_type="binary",
            target="target",
            entity_id="entity_id",
            evaluation_metric="pr_auc",
            feature_groups={"base": ["feature"]},
            validation_strategy="stratified",
        ),
    )
    db_session.commit()
    return {
        "alpha": alpha,
        "beta": beta,
        "alpha_admin": alpha_admin,
        "beta_admin": beta_admin,
        "alpha_domain": alpha_domain,
        "beta_domain": beta_domain,
        "alpha_workflow": alpha_workflow,
        "beta_workflow": beta_workflow,
        "alpha_asset": alpha_asset,
        "alpha_dataset": alpha_dataset,
        "beta_dataset": beta_dataset,
        "env": env,
        "task": task,
        "seeded": seeded,
    }


def test_domains_are_configurable_seed_data(db_session, lineage_setup):
    assert {row.slug for row in lineage_setup["seeded"]} == {
        "labs",
        "marketing",
        "sales",
        "revenue",
        "customer",
    }
    db_session.add(
        BusinessDomain(
            slug="operations",
            name="Operations",
            description="Added through data, without a schema change.",
            default_config={"owner": "ops"},
        )
    )
    db_session.commit()
    assert db_session.scalar(
        select(BusinessDomain).where(BusinessDomain.slug == "operations")
    ) is not None


def test_two_businesses_cannot_cross_link_workflows_or_datasets(
    db_session, lineage_setup
):
    setup = lineage_setup
    with pytest.raises(LineageError, match="workspace domain"):
        create_workflow(
            db_session,
            workspace_id=setup["alpha"].id,
            workspace_domain=setup["beta_domain"],
            name="Invalid",
            slug="invalid",
            actor=setup["alpha_admin"],
        )
    with pytest.raises(LineageError, match="workflow belongs"):
        create_workflow_run(
            db_session,
            workspace_id=setup["alpha"].id,
            workflow=setup["beta_workflow"],
            requester=setup["alpha_admin"],
            trigger_type="manual",
            source_type="dataset",
        )
    with pytest.raises(LineageError, match="dataset belongs"):
        create_workflow_run(
            db_session,
            workspace_id=setup["alpha"].id,
            workflow=setup["alpha_workflow"],
            requester=setup["alpha_admin"],
            trigger_type="manual",
            source_type="dataset",
            inputs=[(setup["beta_dataset"], "training")],
        )


def test_same_dataset_can_participate_in_multiple_workflow_runs(
    db_session, lineage_setup
):
    setup = lineage_setup
    runs = [
        create_workflow_run(
            db_session,
            workspace_id=setup["alpha"].id,
            workflow=setup["alpha_workflow"],
            requester=setup["alpha_admin"],
            trigger_type="manual",
            source_type="dataset",
            inputs=[(setup["alpha_dataset"], "training")],
        )
        for _ in range(2)
    ]
    db_session.commit()
    assert runs[0].id != runs[1].id
    assert runs[0].inputs[0].dataset_id == runs[1].inputs[0].dataset_id


def test_repeated_uploads_create_distinct_workflow_runs(
    db_session, lineage_setup, monkeypatch
):
    from app.services import client_lab_upload_service

    monkeypatch.setattr(client_lab_upload_service, "enqueue_auto_train", lambda _run_id: None)
    setup = lineage_setup
    payload = b"feature,target\n1,0\n2,1\n"
    uploads = [
        client_lab_upload_service.save_upload(
            db_session,
            user=setup["alpha_admin"],
            workspace_id=setup["alpha"].id,
            category="Revenue",
            filename="same.csv",
            data=payload,
        )
        for _ in range(2)
    ]
    upload_ids = [row.id for row in uploads]
    persisted_uploads = list(
        db_session.scalars(
            select(ClientLabUpload).where(ClientLabUpload.id.in_(upload_ids))
        )
    )
    workflow_runs = list(
        db_session.scalars(
            select(WorkflowRun).where(WorkflowRun.source_upload_id.in_(upload_ids))
        )
    )
    assert len(persisted_uploads) == 2
    assert len(workflow_runs) == 2
    assert workflow_runs[0].id != workflow_runs[1].id
    assert {row.workflow_id for row in workflow_runs} == {workflow_runs[0].workflow_id}
    assert all(row.experiment_id is not None for row in persisted_uploads)
    assert {row.pipeline_runs[0].id for row in workflow_runs} == {
        row.experiment_id for row in persisted_uploads
    }
    assert all(len(row.pipeline_runs) == 1 for row in workflow_runs)


def test_one_workflow_run_owns_multiple_pipeline_runs(db_session, lineage_setup):
    setup = lineage_setup
    run = create_workflow_run(
        db_session,
        workspace_id=setup["alpha"].id,
        workflow=setup["alpha_workflow"],
        requester=setup["alpha_admin"],
        trigger_type="manual",
        source_type="dataset",
    )
    pipelines = [
        create_pipeline_run(
            db_session,
            workflow_run=run,
            environment=setup["env"],
            dataset=setup["alpha_dataset"],
            task=setup["task"],
            pipeline_name="deterministic_ml",
            pipeline_index=index,
            pipeline_purpose=purpose,
            config=SearchConfig(max_candidates=3),
        )
        for index, purpose in enumerate(("baseline", "challenger"))
    ]
    assert {row.workflow_run_id for row in pipelines} == {run.id}
    assert [row.pipeline_index for row in pipelines] == [0, 1]


def test_selected_model_version_has_exact_pipeline_candidate_and_dataset_lineage(
    db_session, lineage_setup
):
    setup = lineage_setup
    run = create_workflow_run(
        db_session,
        workspace_id=setup["alpha"].id,
        workflow=setup["alpha_workflow"],
        requester=setup["alpha_admin"],
        trigger_type="manual",
        source_type="dataset",
    )
    pipeline = create_pipeline_run(
        db_session,
        workflow_run=run,
        environment=setup["env"],
        dataset=setup["alpha_dataset"],
        task=setup["task"],
        pipeline_index=0,
    )
    winner = ExperimentCandidate(
        experiment_id=pipeline.id,
        candidate_key="winner",
        fingerprint="a" * 40,
        status="trained",
        payload={"test_metrics": {"pr_auc": 0.82}},
    )
    challenger = ExperimentCandidate(
        experiment_id=pipeline.id,
        candidate_key="challenger",
        fingerprint="b" * 40,
        status="trained",
        payload={},
    )
    db_session.add_all([winner, challenger])
    pipeline.result = {
        "selection": {"selected_candidate_id": "winner"},
        "test_metrics": {"pr_auc": 0.82},
    }
    db_session.commit()
    assert len(pipeline.candidates) == 2

    asset = create_model_asset(
        db_session,
        workspace_id=setup["alpha"].id,
        workflow=setup["alpha_workflow"],
        name="Lead conversion model",
        slug="lead-conversion-model",
        actor=setup["alpha_admin"],
    )
    version = create_model_version(
        db_session,
        model_asset=asset,
        pipeline_run=pipeline,
        selected_candidate=winner,
        version="v1",
    )
    db_session.commit()
    assert version.pipeline_run_id == pipeline.id
    assert version.selected_candidate_id == winner.id
    assert version.workflow_run_id == run.id
    assert version.workflow_id == setup["alpha_workflow"].id
    assert version.workspace_id == setup["alpha"].id
    assert version.dataset_id == setup["alpha_dataset"].id
    assert len(version.content_digest) == 64
    assert db_session.scalar(
        select(ModelVersion).where(ModelVersion.pipeline_run_id == pipeline.id)
    ) is version

    with pytest.raises(LineageError, match="selected pipeline winner"):
        create_model_version(
            db_session,
            model_asset=asset,
            pipeline_run=pipeline,
            selected_candidate=challenger,
            version="v2",
        )
    with pytest.raises(LineageError, match="already has"):
        create_model_version(
            db_session,
            model_asset=asset,
            pipeline_run=pipeline,
            selected_candidate=winner,
            version="v2",
        )
    version.metrics = {"pr_auc": 0.99}
    with pytest.raises(ValueError, match="immutable"):
        db_session.commit()
    db_session.rollback()


def test_dataset_asset_has_multiple_immutable_physical_versions(
    db_session, lineage_setup, tmp_path
):
    setup = lineage_setup
    second_path = tmp_path / "alpha-v2.csv"
    second_path.write_text("feature,target\n1,0\n2,1\n3,1\n", encoding="utf-8")
    second = ingest_dataset(
        db_session,
        environment=setup["env"],
        name="Leads",
        location=str(second_path),
        version="v2",
        workspace_id=setup["alpha"].id,
        dataset_asset=setup["alpha_asset"],
    )
    assert second.dataset_asset_id == setup["alpha_dataset"].dataset_asset_id
    assert second.id != setup["alpha_dataset"].id
    assert second.content_digest != setup["alpha_dataset"].content_digest
    second.version = "v3"
    with pytest.raises(ValueError, match="immutable"):
        db_session.commit()
    db_session.rollback()


def test_cross_tenant_pipeline_dataset_is_rejected(db_session, lineage_setup):
    setup = lineage_setup
    run = create_workflow_run(
        db_session,
        workspace_id=setup["alpha"].id,
        workflow=setup["alpha_workflow"],
        requester=setup["alpha_admin"],
        trigger_type="manual",
        source_type="dataset",
    )
    with pytest.raises(LineageError, match="pipeline dataset"):
        create_pipeline_run(
            db_session,
            workflow_run=run,
            environment=setup["env"],
            dataset=setup["beta_dataset"],
            task=setup["task"],
        )
