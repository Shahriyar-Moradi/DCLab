"""DCLabClient against the real FastAPI /v1 app (ASGI TestClient)."""

from __future__ import annotations

from uuid import uuid4

import pytest
from sqlalchemy import func, select

from dclab_client import DCLabAPIError, DCLabClient
from app.db.models import DEFAULT_WORKSPACE_ID, Dataset, DatasetAsset, Experiment, MlJob
from app.services.artifact_service import store_artifact
from app.services.auth_service import create_access_token
from app.services.lab_service import seed_dogfood
from app.services.lineage_service import create_pipeline_run, create_workflow_run
from app.services.observability_service import append_ml_run_event
from app.services.project_service import create_project
from app.services.visualization_service import persist_visualization
from app.storage.local import LocalStorage
from test_data_model_lineage import make_lineage_setup


def _client(http, token: str, workspace_id=None, **kwargs) -> DCLabClient:
    return DCLabClient(
        base_url=str(http.base_url),
        token=token,
        workspace_id=workspace_id,
        http=http,
        **kwargs,
    )


def _stub_pipeline_run(db_session, workspace_id) -> Experiment:
    environment = seed_dogfood(db_session)
    slug = f"dclab-client-{uuid4().hex[:10]}"
    asset = DatasetAsset(workspace_id=workspace_id, name=slug, slug=slug)
    db_session.add(asset)
    db_session.flush()
    dataset = Dataset(
        workspace_id=workspace_id,
        dataset_asset_id=asset.id,
        environment_id=environment.id,
        name=slug,
        source_type="csv",
        location="/private/unused.csv",
        version="v1",
        content_digest="b" * 64,
        row_count=8,
        column_count=3,
    )
    db_session.add(dataset)
    db_session.flush()
    experiment = Experiment(
        workspace_id=workspace_id,
        environment_id=environment.id,
        dataset_id=dataset.id,
        status="COMPLETED",
        config={},
        result={},
    )
    db_session.add(experiment)
    db_session.commit()
    return experiment


def test_client_identity_and_workspaces_hit_v1(auth_client, client_user, client_token):
    api = _client(auth_client, client_token)
    me = api.identity.me()
    assert me.id == client_user.id
    assert me.email == client_user.email
    workspaces = api.workspaces.list()
    assert any(row.id == DEFAULT_WORKSPACE_ID for row in workspaces)


def test_client_unauthenticated_is_structured_error(client):
    api = DCLabClient(base_url=str(client.base_url), http=client)
    with pytest.raises(DCLabAPIError) as caught:
        api.identity.me()
    assert caught.value.status_code == 401
    assert caught.value.path == "/v1/me"
    assert caught.value.detail


def test_client_projects_and_datasets_are_workspace_scoped(client, db_session, tmp_path):
    setup = make_lineage_setup(db_session, tmp_path)
    api = _client(
        client,
        create_access_token(setup["alpha_admin"]),
        workspace_id=setup["alpha"].id,
    )
    workspace_ids = {row.id for row in api.workspaces.list()}
    assert setup["alpha"].id in workspace_ids
    assert setup["beta"].id not in workspace_ids

    projects = api.projects.list()
    assert setup["alpha_project"].id in {row.id for row in projects}
    assert setup["beta_project"].id not in {row.id for row in projects}
    detail = api.projects.get(setup["alpha_project"].id)
    assert detail.name == "Alpha project"
    with pytest.raises(DCLabAPIError) as hidden:
        api.projects.get(setup["beta_project"].id)
    assert hidden.value.status_code == 404

    datasets = api.datasets.list()
    assert setup["alpha_dataset"].id in {row.id for row in datasets}
    assert setup["beta_dataset"].id not in {row.id for row in datasets}
    dataset = api.datasets.get(setup["alpha_dataset"].id)
    assert dataset.id == setup["alpha_dataset"].id
    with pytest.raises(DCLabAPIError) as hidden_dataset:
        api.datasets.get(setup["beta_dataset"].id)
    assert hidden_dataset.value.status_code == 404


def test_client_execution_request_returns_id_and_status(
    auth_client, db_session, client_user, client_token
):
    project = create_project(
        db_session,
        actor=client_user,
        workspace_id=DEFAULT_WORKSPACE_ID,
        name="Client project",
        slug=f"client-{uuid4().hex[:8]}",
    )
    db_session.commit()
    api = _client(auth_client, client_token, request_id="client-trace")
    before_jobs = db_session.scalar(select(func.count(MlJob.id))) or 0
    created = api.execution_requests.create(
        project_id=project.id,
        request_spec={"filename": "customers.csv", "record_count": 2},
        idempotency_key="client-once",
    )
    assert created.status == "accepted"
    assert created.operation == "model_build"
    assert created.source_surface == "api"
    assert created.pipeline_run_id is None
    after_jobs = db_session.scalar(select(func.count(MlJob.id))) or 0
    assert after_jobs == before_jobs

    replay = api.execution_requests.create(
        idempotency_key="client-once",
        request_spec={"filename": "other.csv"},
    )
    assert replay.id == created.id
    fetched = api.execution_requests.get(created.id)
    assert fetched.id == created.id
    assert fetched.status == "accepted"


def test_client_model_build_events_visualizations_and_artifacts(
    client, db_session, tmp_path
):
    setup = make_lineage_setup(db_session, tmp_path)
    workflow_run = create_workflow_run(
        db_session,
        workspace_id=setup["alpha"].id,
        workflow=setup["alpha_workflow"],
        requester=setup["alpha_admin"],
        trigger_type="manual",
        source_type="dataset",
    )
    pipeline = create_pipeline_run(
        db_session,
        workflow_run=workflow_run,
        environment=setup["env"],
        dataset=setup["alpha_dataset"],
        task=setup["task"],
        commit=False,
    )
    for index in range(3):
        append_ml_run_event(
            db_session,
            workspace_id=setup["alpha"].id,
            workflow_run_id=workflow_run.id,
            experiment_id=pipeline.id,
            stage="training",
            event_type=f"step_{index}",
            status="completed",
            commit=False,
        )
    persist_visualization(
        db_session,
        workspace_id=setup["alpha"].id,
        pipeline_run_id=pipeline.id,
        visualization_type="roc_curve",
        spec={"title": "declared only"},
    )
    artifact = store_artifact(
        db_session,
        workspace_id=setup["alpha"].id,
        project_id=setup["alpha_project"].id,
        pipeline_run_id=pipeline.id,
        artifact_type="plot",
        filename="chart.png",
        data=b"\x89PNG\r\n",
        storage=LocalStorage(root=tmp_path / "client-objects"),
    )
    db_session.commit()

    api = _client(
        client,
        create_access_token(setup["alpha_admin"]),
        workspace_id=setup["alpha"].id,
    )
    build = api.model_builds.get(pipeline.id)
    assert build.pipeline_run_id == pipeline.id
    assert isinstance(build.stages, list)

    page = api.model_builds.events(pipeline.id, limit=2)
    assert [row.sequence for row in page.items] == [1, 2]
    assert page.next_cursor == "2"
    rest = api.model_builds.events(pipeline.id, cursor=page.next_cursor, limit=2)
    assert [row.sequence for row in rest.items] == [3]
    assert rest.next_cursor is None

    charts = api.visualizations.list(pipeline.id)
    assert len(charts) == 1
    assert charts[0].visualization_type == "roc_curve"
    blobs = api.artifacts.list(pipeline.id)
    assert {row.id for row in blobs} == {artifact.id}

    beta = _client(
        client,
        create_access_token(setup["beta_admin"]),
        workspace_id=setup["beta"].id,
    )
    with pytest.raises(DCLabAPIError) as hidden:
        beta.model_builds.get(pipeline.id)
    assert hidden.value.status_code == 404


def test_client_model_build_on_default_workspace(admin_client, admin_token, db_session):
    run = _stub_pipeline_run(db_session, DEFAULT_WORKSPACE_ID)
    api = _client(admin_client, admin_token)
    body = api.model_builds.get(run.id)
    assert body.pipeline_run_id == run.id
    assert body.workspace_id == DEFAULT_WORKSPACE_ID
