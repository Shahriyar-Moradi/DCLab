"""Stable /v1 application boundary: existing services, legacy adapters stay."""

from __future__ import annotations

from uuid import uuid4

from sqlalchemy import func, select

from app.db.models import (
    DEFAULT_WORKSPACE_ID,
    Dataset,
    DatasetAsset,
    Experiment,
    MlJob,
    Visualization,
)
from app.domain.execution_requests import OPERATION_MODEL_BUILD, REQUEST_ACCEPTED, SOURCE_API
from app.services.artifact_service import store_artifact
from app.services.auth_service import create_access_token
from app.services.lab_service import seed_dogfood
from app.services.lineage_service import create_pipeline_run, create_workflow_run
from app.services.observability_service import append_ml_run_event
from app.services.project_service import create_project
from app.services.visualization_service import persist_visualization
from app.storage.local import LocalStorage
from test_data_model_lineage import make_lineage_setup


def _auth_headers(user, workspace_id) -> dict[str, str]:
    return {
        "Authorization": f"Bearer {create_access_token(user)}",
        "X-Workspace-Id": str(workspace_id),
    }


def _stub_pipeline_run(db_session, workspace_id) -> Experiment:
    environment = seed_dogfood(db_session)
    slug = f"v1-model-build-{uuid4().hex[:10]}"
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


def test_v1_requires_authentication(client):
    assert client.get("/v1/me").status_code == 401
    assert client.get("/v1/workspaces").status_code == 401
    assert client.get("/v1/projects").status_code == 401
    assert client.post("/v1/execution-requests", json={}).status_code == 401


def test_v1_me_and_workspaces_use_existing_auth(auth_client, db_session, client_user):
    me = auth_client.get("/v1/me")
    assert me.status_code == 200, me.text
    body = me.json()
    assert body["id"] == str(client_user.id)
    assert body["email"] == client_user.email
    assert "get_current_user" not in me.text

    listed = auth_client.get("/v1/workspaces")
    assert listed.status_code == 200, listed.text
    ids = {row["id"] for row in listed.json()}
    assert str(DEFAULT_WORKSPACE_ID) in ids


def test_v1_projects_and_datasets_are_workspace_scoped(client, db_session, tmp_path):
    setup = make_lineage_setup(db_session, tmp_path)
    headers = _auth_headers(setup["alpha_admin"], setup["alpha"].id)
    membership = client.get("/v1/workspaces", headers=headers)
    assert membership.status_code == 200, membership.text
    visible_workspaces = {row["id"] for row in membership.json()}
    assert str(setup["alpha"].id) in visible_workspaces
    assert str(setup["beta"].id) not in visible_workspaces

    listed = client.get("/v1/projects", headers=headers)
    assert listed.status_code == 200, listed.text
    project_ids = {row["id"] for row in listed.json()}
    assert str(setup["alpha_project"].id) in project_ids
    assert str(setup["beta_project"].id) not in project_ids

    detail = client.get(f"/v1/projects/{setup['alpha_project'].id}", headers=headers)
    assert detail.status_code == 200
    assert detail.json()["name"] == "Alpha project"

    hidden = client.get(
        f"/v1/projects/{setup['beta_project'].id}",
        headers=headers,
    )
    assert hidden.status_code == 404

    datasets = client.get("/v1/datasets", headers=headers)
    assert datasets.status_code == 200, datasets.text
    dataset_ids = {row["id"] for row in datasets.json()}
    assert str(setup["alpha_dataset"].id) in dataset_ids
    assert str(setup["beta_dataset"].id) not in dataset_ids

    dataset = client.get(f"/v1/datasets/{setup['alpha_dataset'].id}", headers=headers)
    assert dataset.status_code == 200
    assert dataset.json()["id"] == str(setup["alpha_dataset"].id)

    foreign_dataset = client.get(
        f"/v1/datasets/{setup['beta_dataset'].id}",
        headers=headers,
    )
    assert foreign_dataset.status_code == 404


def test_v1_execution_request_returns_id_and_status_without_enqueueing(
    auth_client, db_session, client_user
):
    project = create_project(
        db_session,
        actor=client_user,
        workspace_id=DEFAULT_WORKSPACE_ID,
        name="V1 project",
        slug=f"v1-{uuid4().hex[:8]}",
    )
    db_session.commit()
    before_jobs = db_session.scalar(select(func.count(MlJob.id))) or 0
    created = auth_client.post(
        "/v1/execution-requests",
        json={
            "operation": OPERATION_MODEL_BUILD,
            "project_id": str(project.id),
            "request_spec": {"filename": "customers.csv", "record_count": 2},
            "idempotency_key": "v1-once",
        },
    )
    assert created.status_code == 202, created.text
    body = created.json()
    assert body["id"]
    assert body["status"] == REQUEST_ACCEPTED
    assert body["operation"] == OPERATION_MODEL_BUILD
    assert body["source_surface"] == SOURCE_API
    assert body["pipeline_run_id"] is None
    assert "create_execution_request" not in created.text
    after_jobs = db_session.scalar(select(func.count(MlJob.id))) or 0
    assert after_jobs == before_jobs

    replay = auth_client.post(
        "/v1/execution-requests",
        json={
            "operation": OPERATION_MODEL_BUILD,
            "idempotency_key": "v1-once",
            "request_spec": {"filename": "other.csv"},
        },
    )
    assert replay.status_code == 202
    assert replay.json()["id"] == body["id"]

    fetched = auth_client.get(f"/v1/execution-requests/{body['id']}")
    assert fetched.status_code == 200
    assert fetched.json()["id"] == body["id"]
    assert fetched.json()["status"] == REQUEST_ACCEPTED


def test_v1_model_build_events_paginate_by_sequence(client, db_session, tmp_path):
    setup = make_lineage_setup(db_session, tmp_path)
    headers = _auth_headers(setup["alpha_admin"], setup["alpha"].id)
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
    storage = LocalStorage(root=tmp_path / "v1-objects")
    artifact = store_artifact(
        db_session,
        workspace_id=setup["alpha"].id,
        project_id=setup["alpha_project"].id,
        pipeline_run_id=pipeline.id,
        artifact_type="plot",
        filename="chart.png",
        data=b"\x89PNG\r\n",
        storage=storage,
    )
    db_session.commit()

    first = client.get(
        f"/v1/model-builds/{pipeline.id}/events",
        headers=headers,
        params={"limit": 2},
    )
    assert first.status_code == 200, first.text
    page = first.json()
    assert len(page["items"]) == 2
    assert page["items"][0]["sequence"] == 1
    assert page["items"][1]["sequence"] == 2
    assert page["next_cursor"] == "2"

    second = client.get(
        f"/v1/model-builds/{pipeline.id}/events",
        headers=headers,
        params={"cursor": page["next_cursor"], "limit": 2},
    )
    assert second.status_code == 200
    rest = second.json()
    assert [row["sequence"] for row in rest["items"]] == [3]
    assert rest["next_cursor"] is None

    invalid = client.get(
        f"/v1/model-builds/{pipeline.id}/events",
        headers=headers,
        params={"cursor": "not-a-sequence"},
    )
    assert invalid.status_code == 400

    charts = client.get(
        f"/v1/model-builds/{pipeline.id}/visualizations",
        headers=headers,
    )
    assert charts.status_code == 200
    assert len(charts.json()) == 1
    assert charts.json()[0]["visualization_type"] == "roc_curve"
    assert charts.json()[0]["pipeline_run_id"] == str(pipeline.id)
    viz_count = db_session.scalar(
        select(func.count(Visualization.id)).where(
            Visualization.pipeline_run_id == pipeline.id
        )
    )
    assert viz_count == 1

    artifacts = client.get(
        f"/v1/model-builds/{pipeline.id}/artifacts",
        headers=headers,
    )
    assert artifacts.status_code == 200
    assert {row["id"] for row in artifacts.json()} == {str(artifact.id)}


def test_v1_model_build_hides_cross_workspace_runs(client, db_session, tmp_path):
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
    db_session.commit()
    beta_headers = _auth_headers(setup["beta_admin"], setup["beta"].id)
    hidden = client.get(f"/v1/model-builds/{pipeline.id}", headers=beta_headers)
    assert hidden.status_code == 404
    hidden_events = client.get(
        f"/v1/model-builds/{pipeline.id}/events",
        headers=beta_headers,
    )
    assert hidden_events.status_code == 404


def test_v1_model_build_reads_canonical_timeline(
    admin_client, db_session
):
    run = _stub_pipeline_run(db_session, DEFAULT_WORKSPACE_ID)
    db_session.commit()
    response = admin_client.get(f"/v1/model-builds/{run.id}")
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["pipeline_run_id"] == str(run.id)
    assert body["workspace_id"] == str(DEFAULT_WORKSPACE_ID)
    assert isinstance(body["stages"], list)


def test_legacy_app_route_remains_an_adapter(auth_client):
    from starlette.routing import WebSocketRoute

    from app.main import app

    response = auth_client.get("/app/labs/problems")
    assert response.status_code == 200, response.text
    assert auth_client.get("/auth/me").status_code == 200
    assert auth_client.get("/v1/me").status_code == 200
    assert not any(isinstance(route, WebSocketRoute) for route in app.routes)
