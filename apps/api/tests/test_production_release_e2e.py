"""Prompt 9 production E2E: personal, business, platform, and scientific lineage."""

from __future__ import annotations

from types import SimpleNamespace
from uuid import uuid4

import pytest
from sqlalchemy import select

from adaptive_modeling.fixtures import ordinary_binary, regression
from adaptive_modeling.production import labs_upload_and_train
from app.db.models import (
    Artifact,
    CodeSnapshot,
    CVFoldRun,
    DataPreparationDecision,
    DataSource,
    Dataset,
    DatasetAsset,
    DatasetColumn,
    EvaluationMetric,
    Experiment,
    ExperimentCandidate,
    Feature,
    FeatureSet,
    FeatureSetVersion,
    IngestionRun,
    ModelEvaluation,
    ModelHyperparameter,
    ModelSelectionDecision,
    ModelVersion,
    Pipeline,
    PipelineStageRun,
    PipelineVersion,
    PreprocessingStep,
    Project,
    RuntimeEnvironment,
    User,
    UserRole,
    WorkflowRun,
    WorkflowVersion,
    WorkspaceKind,
    WorkspaceMembership,
    WorkspaceRole,
)
from app.services.auth_service import create_access_token, create_user
from app.services.workspace_entitlement_service import member_count
from app.services.workspace_service import add_workspace_member, create_business_workspace


@pytest.fixture
def _rule_engine_only(monkeypatch):
    monkeypatch.setattr(
        "app.services.lab_decision_ledger.get_settings",
        lambda: SimpleNamespace(decision_agent_enabled=False, decision_agent_api_key=""),
    )


def _headers(token: str, workspace_id=None) -> dict[str, str]:
    headers = {"Authorization": f"Bearer {token}"}
    if workspace_id is not None:
        headers["X-Workspace-Id"] = str(workspace_id)
    return headers


def _assert_complete_scientific_chain(
    db_session,
    experiment: Experiment,
    model_version: ModelVersion,
    *,
    upload=None,
) -> None:
    if upload is not None:
        assert upload.data_source_id is not None
        assert upload.ingestion_run_id is not None
        assert upload.dataset_id is not None
        assert db_session.get(DataSource, upload.data_source_id) is not None
        assert db_session.get(IngestionRun, upload.ingestion_run_id) is not None
        uploaded = db_session.get(Dataset, upload.dataset_id)
        assert uploaded is not None
        assert db_session.get(DatasetAsset, uploaded.dataset_asset_id) is not None
        assert list(
            db_session.scalars(select(DatasetColumn).where(DatasetColumn.dataset_id == uploaded.id))
        )

    dataset = db_session.get(Dataset, experiment.dataset_id)
    assert dataset is not None
    assert db_session.get(DatasetAsset, dataset.dataset_asset_id) is not None
    ingestion = db_session.get(IngestionRun, dataset.ingestion_run_id)
    assert ingestion is not None
    assert db_session.get(DataSource, ingestion.data_source_id) is not None
    columns = list(
        db_session.scalars(select(DatasetColumn).where(DatasetColumn.dataset_id == dataset.id))
    )
    assert columns

    workflow_run = db_session.get(WorkflowRun, experiment.workflow_run_id)
    assert workflow_run is not None
    assert workflow_run.workflow_version_id is not None
    assert db_session.get(WorkflowVersion, workflow_run.workflow_version_id) is not None
    assert experiment.pipeline_id is not None
    assert experiment.pipeline_version_id is not None
    assert db_session.get(Pipeline, experiment.pipeline_id) is not None
    assert db_session.get(PipelineVersion, experiment.pipeline_version_id) is not None
    stages = list(
        db_session.scalars(
            select(PipelineStageRun).where(PipelineStageRun.pipeline_run_id == experiment.id)
        )
    )
    assert stages
    assert db_session.scalar(
        select(DataPreparationDecision.id).where(
            DataPreparationDecision.pipeline_run_id == experiment.id
        )
    )
    feature_set = db_session.scalar(
        select(FeatureSet).where(FeatureSet.name == f"pipeline-run-{experiment.id}")
    )
    assert feature_set is not None
    version = db_session.scalar(
        select(FeatureSetVersion)
        .where(FeatureSetVersion.feature_set_id == feature_set.id)
        .order_by(FeatureSetVersion.version.desc())
    )
    assert version is not None
    features = list(
        db_session.scalars(select(Feature).where(Feature.feature_set_version_id == version.id))
    )
    assert features
    assert db_session.scalar(
        select(PreprocessingStep.id).where(PreprocessingStep.pipeline_run_id == experiment.id)
    )

    candidates = list(
        db_session.scalars(
            select(ExperimentCandidate).where(ExperimentCandidate.experiment_id == experiment.id)
        )
    )
    assert len(candidates) >= 2
    trained = [row for row in candidates if row.status == "trained"]
    assert trained
    for candidate in trained:
        assert db_session.scalar(
            select(ModelHyperparameter.id).where(ModelHyperparameter.candidate_id == candidate.id)
        )
        folds = list(
            db_session.scalars(select(CVFoldRun).where(CVFoldRun.candidate_id == candidate.id))
        )
        assert folds
        evals = list(
            db_session.scalars(
                select(ModelEvaluation).where(ModelEvaluation.candidate_id == candidate.id)
            )
        )
        assert evals
        metric = db_session.scalar(
            select(EvaluationMetric.id).where(
                EvaluationMetric.model_evaluation_id.in_([row.id for row in evals])
            )
        )
        assert metric is not None

    selection = db_session.scalar(
        select(ModelSelectionDecision).where(
            ModelSelectionDecision.pipeline_run_id == experiment.id
        )
    )
    assert selection is not None
    assert db_session.scalar(
        select(ModelEvaluation.id).where(
            ModelEvaluation.model_version_id == model_version.id,
            ModelEvaluation.evaluation_scope == "final_holdout",
        )
    )
    runtime = db_session.get(RuntimeEnvironment, model_version.runtime_environment_id)
    assert runtime is not None
    snapshot = db_session.get(CodeSnapshot, model_version.code_snapshot_id)
    assert snapshot is not None
    assert db_session.get(Artifact, model_version.model_artifact_id) is not None
    assert model_version.dataset_id == dataset.id
    assert model_version.pipeline_run_id == experiment.id
    assert model_version.selected_candidate_id == selection.selected_candidate_id
    assert model_version.workspace_id == experiment.workspace_id
    assert model_version.project_id == experiment.project_id


def test_personal_signup_project_and_one_ml_core(
    client, db_session, monkeypatch, _rule_engine_only
):
    email = f"personal-{uuid4().hex}@test.invalid"
    registered = client.post(
        "/auth/register",
        json={"email": email, "password": "test-password", "full_name": "Personal Owner"},
    )
    assert registered.status_code == 200, registered.text
    body = registered.json()
    assert body["user"]["role"] == UserRole.WORKSPACE_OWNER.value
    assert body["user"]["workspace_id"] is None
    duplicate = client.post(
        "/auth/register",
        json={"email": email, "password": "test-password"},
    )
    assert duplicate.status_code == 409

    token = body["access_token"]
    created = client.post(
        "/workspaces/personal",
        headers=_headers(token),
        json={"name": "My Lab"},
    )
    assert created.status_code == 200, created.text
    workspace = created.json()
    assert workspace["kind"] == WorkspaceKind.PERSONAL.value
    workspace_id = workspace["id"]
    membership = db_session.scalar(
        select(WorkspaceMembership).where(WorkspaceMembership.workspace_id == workspace_id)
    )
    assert membership is not None
    assert membership.role == WorkspaceRole.WORKSPACE_OWNER.value

    project_response = client.post(
        f"/workspaces/{workspace_id}/projects",
        headers=_headers(token, workspace_id),
        json={"name": "Churn case", "description": "Personal classification"},
    )
    assert project_response.status_code == 200, project_response.text
    project_id = project_response.json()["id"]
    spec_response = client.post(
        f"/workspaces/{workspace_id}/projects/{project_id}/problem-specs",
        headers=_headers(token, workspace_id),
        json={
            "task_type": "classification",
            "business_objective": "Predict the binary outcome",
            "target_column": "outcome",
            "primary_metric": "roc_auc",
            "status": "locked",
        },
    )
    assert spec_response.status_code == 200, spec_response.text
    spec_id = spec_response.json()["id"]

    client.headers.update(_headers(token, workspace_id))
    upload, workflow_run, experiment, model_version = labs_upload_and_train(
        client,
        db_session,
        monkeypatch,
        ordinary_binary(),
        filename="personal_classification.csv",
        target="outcome",
        project_id=project_id,
        problem_spec_id=spec_id,
    )
    assert upload.workspace_id == workflow_run.workspace_id
    dataset = db_session.get(Dataset, upload.dataset_id)
    assert dataset is not None
    assert dataset.project_id == experiment.project_id
    assert str(dataset.project_id) == project_id
    assert workflow_run.problem_spec_id is not None
    assert str(workflow_run.problem_spec_id) == spec_id
    _assert_complete_scientific_chain(db_session, experiment, model_version, upload=upload)

    detail = client.get(
        f"/workspaces/{workspace_id}/explorer/pipeline-runs/{experiment.id}",
        headers=_headers(token, workspace_id),
    )
    assert detail.status_code == 200, detail.text
    payload = detail.json()
    assert payload["identity"]["pipeline_run_id"] == str(experiment.id)
    assert payload["project"]["id"] == project_id
    models = client.get(
        f"/workspaces/{workspace_id}/explorer/model-versions",
        headers=_headers(token, workspace_id),
    )
    assert models.status_code == 200
    assert str(model_version.id) in {row["id"] for row in models.json()}


def test_business_total_member_cap_and_shared_canonical_objects(
    client, db_session, monkeypatch, _rule_engine_only
):
    owner = create_user(
        db_session,
        email=f"biz-owner-{uuid4().hex}@test.invalid",
        password="test-password",
        role=UserRole.WORKSPACE_OWNER,
        full_name="Business Owner",
    )
    db_session.commit()
    workspace = create_business_workspace(db_session, owner=owner, name="Shared Co")
    db_session.commit()
    # Product decision: max_members=5 is 5 total memberships, not 5 engineers plus admins.
    admin = add_workspace_member(
        db_session,
        actor=owner,
        workspace_id=workspace.id,
        email=f"biz-admin-{uuid4().hex}@test.invalid",
        password="test-password",
        role=WorkspaceRole.WORKSPACE_ADMIN.value,
        full_name="Workspace Admin",
    )
    engineers = []
    for index in range(3):
        engineers.append(
            add_workspace_member(
                db_session,
                actor=owner,
                workspace_id=workspace.id,
                email=f"ml-{index}-{uuid4().hex}@test.invalid",
                password="test-password",
                role=WorkspaceRole.ML_ENGINEER.value,
                full_name=f"Engineer {index}",
            )
        )
    db_session.commit()
    assert member_count(db_session, workspace.id) == 5
    sixth = client.post(
        f"/workspaces/{workspace.id}/members",
        headers=_headers(create_access_token(owner), workspace.id),
        json={
            "email": f"overflow-{uuid4().hex}@test.invalid",
            "password": "test-password",
            "role": WorkspaceRole.ML_ENGINEER.value,
        },
    )
    assert sixth.status_code == 409, sixth.text
    assert "all memberships" in sixth.json()["detail"]

    engineer_user = db_session.get(User, engineers[0].user_id)
    other_engineer = db_session.get(User, engineers[1].user_id)
    token = create_access_token(engineer_user)
    project_response = client.post(
        f"/workspaces/{workspace.id}/projects",
        headers=_headers(token, workspace.id),
        json={"name": "Shared case"},
    )
    assert project_response.status_code == 200, project_response.text
    project_id = project_response.json()["id"]

    client.headers.update(_headers(token, workspace.id))
    upload, _workflow_run, experiment, model_version = labs_upload_and_train(
        client,
        db_session,
        monkeypatch,
        ordinary_binary(),
        filename="business_shared.csv",
        target="outcome",
        project_id=project_id,
    )
    other_headers = _headers(create_access_token(other_engineer), workspace.id)
    projects = client.get(f"/workspaces/{workspace.id}/projects", headers=other_headers)
    assert projects.status_code == 200
    assert project_id in {row["id"] for row in projects.json()}
    datasets = client.get(
        f"/workspaces/{workspace.id}/explorer/datasets",
        headers=other_headers,
    )
    assert datasets.status_code == 200
    assert str(upload.dataset_id) in {row["id"] for row in datasets.json()}
    workflows = client.get(
        f"/workspaces/{workspace.id}/explorer/workflows",
        headers=other_headers,
    )
    assert workflows.status_code == 200
    assert workflows.json()
    models = client.get(
        f"/workspaces/{workspace.id}/explorer/model-versions",
        headers=other_headers,
    )
    assert models.status_code == 200
    assert str(model_version.id) in {row["id"] for row in models.json()}
    assert db_session.get(Experiment, experiment.id).workspace_id == workspace.id
    assert db_session.get(Project, experiment.project_id).workspace_id == workspace.id
    admin_user = db_session.get(User, admin.user_id)
    listed = client.get(
        f"/workspaces/{workspace.id}/projects",
        headers=_headers(create_access_token(admin_user), workspace.id),
    )
    assert listed.status_code == 200


def test_platform_admin_writes_developer_is_read_only(client, db_session):
    owner = create_user(
        db_session,
        email=f"tenant-owner-{uuid4().hex}@test.invalid",
        password="test-password",
        role=UserRole.WORKSPACE_OWNER,
    )
    workspace = create_business_workspace(db_session, owner=owner, name="Inspected Co")
    admin = create_user(
        db_session,
        email=f"platform-admin-{uuid4().hex}@test.invalid",
        password="test-password",
        role=UserRole.DCLAB_ADMIN,
    )
    developer = create_user(
        db_session,
        email=f"platform-dev-{uuid4().hex}@test.invalid",
        password="test-password",
        role=UserRole.DCLAB_DEVELOPER,
    )
    db_session.commit()

    admin_headers = _headers(create_access_token(admin))
    developer_headers = _headers(create_access_token(developer))
    workspaces = client.get("/admin/explorer/workspaces", headers=admin_headers)
    assert workspaces.status_code == 200
    assert str(workspace.id) in {row["id"] for row in workspaces.json()}
    assert client.get("/admin/explorer/workspaces", headers=developer_headers).status_code == 200
    assert (
        client.get("/admin/explorer/pipeline-runs", headers=developer_headers).status_code == 200
    )
    assert (
        client.post("/admin/environments/dogfood", headers=developer_headers).status_code == 403
    )
    created = client.post("/admin/environments/dogfood", headers=admin_headers)
    assert created.status_code == 200, created.text
    blocked = client.post(
        "/workspaces/business",
        headers=developer_headers,
        json={"name": "Developer must not create"},
    )
    assert blocked.status_code == 403


def test_scientific_classification_and_regression_have_no_missing_links(
    client, db_session, monkeypatch, _rule_engine_only
):
    owner = create_user(
        db_session,
        email=f"science-{uuid4().hex}@test.invalid",
        password="test-password",
        role=UserRole.WORKSPACE_OWNER,
    )
    db_session.commit()
    token = create_access_token(owner)
    created = client.post(
        "/workspaces/personal",
        headers=_headers(token),
        json={"name": "Science Lab"},
    )
    assert created.status_code == 200, created.text
    workspace_id = created.json()["id"]
    token_headers = _headers(token, workspace_id)
    client.headers.update(token_headers)

    for filename, frame, target, task_type, metric in (
        (
            "science_classification.csv",
            ordinary_binary(),
            "outcome",
            "classification",
            "roc_auc",
        ),
        ("science_regression.csv", regression(), "revenue", "regression", "rmse"),
    ):
        project_response = client.post(
            f"/workspaces/{workspace_id}/projects",
            headers=token_headers,
            json={"name": f"Science {task_type}"},
        )
        assert project_response.status_code == 200, project_response.text
        project_id = project_response.json()["id"]
        spec_response = client.post(
            f"/workspaces/{workspace_id}/projects/{project_id}/problem-specs",
            headers=token_headers,
            json={
                "task_type": task_type,
                "business_objective": f"Prove {task_type} lineage",
                "target_column": target,
                "primary_metric": metric,
                "status": "locked",
            },
        )
        assert spec_response.status_code == 200, spec_response.text
        _upload, _workflow_run, experiment, model_version = labs_upload_and_train(
            client,
            db_session,
            monkeypatch,
            frame,
            filename=filename,
            target=target,
            project_id=project_id,
            problem_spec_id=spec_response.json()["id"],
        )
        _assert_complete_scientific_chain(
            db_session, experiment, model_version, upload=_upload
        )
        db_session.expire_all()
