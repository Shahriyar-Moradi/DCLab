"""Admin and customer technical explorer share one query architecture."""

from __future__ import annotations

from types import SimpleNamespace
from uuid import uuid4

import pytest
from sqlalchemy import event

from adaptive_modeling.fixtures import ordinary_binary
from adaptive_modeling.production import labs_upload_and_train
from app.db.models import (
    ExperimentCandidate,
    User,
    UserRole,
    WorkspaceRole,
)
from app.services.auth_service import create_access_token, create_user
from app.services.project_service import create_project
from app.services.workspace_service import add_workspace_member, create_business_workspace

DETAIL_QUERY_BUDGET = 50
LIST_QUERY_BUDGET = 20


@pytest.fixture
def _rule_engine_only(monkeypatch):
    monkeypatch.setattr(
        "app.services.lab_decision_ledger.get_settings",
        lambda: SimpleNamespace(decision_agent_enabled=False, decision_agent_api_key=""),
    )


def _headers(user, workspace_id=None) -> dict[str, str]:
    headers = {"Authorization": f"Bearer {create_access_token(user)}"}
    if workspace_id is not None:
        headers["X-Workspace-Id"] = str(workspace_id)
    return headers


class _QueryCounter:
    def __init__(self, engine):
        self.count = 0
        self.engine = engine
        event.listen(engine, "before_cursor_execute", self._increment)

    def _increment(self, *_args, **_kwargs):
        self.count += 1

    def close(self):
        event.remove(self.engine, "before_cursor_execute", self._increment)


def _count_get(db_session, http, path, headers=None) -> tuple[int, object]:
    db_session.expire_all()
    counter = _QueryCounter(db_session.get_bind())
    try:
        response = http.get(path, headers=headers) if headers else http.get(path)
    finally:
        counter.close()
    return counter.count, response


def _owner(db, prefix: str):
    return create_user(
        db,
        email=f"{prefix}-{uuid4().hex}@test.invalid",
        password="test-password",
        role=UserRole.WORKSPACE_OWNER,
        full_name=prefix,
    )


def test_admin_sees_all_tenants_developer_is_read_only_and_lists_stay_bounded(
    client, admin_client, db_session
):
    owner_a = _owner(db_session, "explorer-a")
    owner_b = _owner(db_session, "explorer-b")
    workspace_a = create_business_workspace(db_session, owner=owner_a, name="Explorer A")
    workspace_b = create_business_workspace(db_session, owner=owner_b, name="Explorer B")
    project_a = create_project(
        db_session, actor=owner_a, workspace_id=workspace_a.id, name="Alpha Case"
    )
    project_b = create_project(
        db_session, actor=owner_b, workspace_id=workspace_b.id, name="Beta Case"
    )
    viewer_membership = add_workspace_member(
        db_session,
        actor=owner_a,
        workspace_id=workspace_a.id,
        email=f"viewer-{uuid4().hex}@test.invalid",
        password="test-password",
        role=WorkspaceRole.VIEWER.value,
        full_name="Viewer",
    )
    db_session.commit()
    viewer = db_session.get(User, viewer_membership.user_id)
    developer = create_user(
        db_session,
        email=f"dev-{uuid4().hex}@dclab.test",
        password="test-password",
        role=UserRole.DCLAB_DEVELOPER,
        full_name="Internal Developer",
    )
    db_session.commit()

    admin_workspaces = admin_client.get("/admin/explorer/workspaces")
    assert admin_workspaces.status_code == 200, admin_workspaces.text
    workspace_ids = {row["id"] for row in admin_workspaces.json()}
    assert str(workspace_a.id) in workspace_ids
    assert str(workspace_b.id) in workspace_ids

    admin_projects = admin_client.get("/admin/explorer/projects")
    assert admin_projects.status_code == 200, admin_projects.text
    by_id = {row["id"]: row for row in admin_projects.json()}
    assert str(project_a.id) in by_id
    assert str(project_b.id) in by_id
    assert by_id[str(project_a.id)]["workspace_id"] == str(workspace_a.id)
    assert by_id[str(project_b.id)]["workspace_id"] == str(workspace_b.id)

    dev_headers = _headers(developer)
    dev_read = client.get("/admin/explorer/projects", headers=dev_headers)
    assert dev_read.status_code == 200, dev_read.text
    assert {row["id"] for row in dev_read.json()} >= {str(project_a.id), str(project_b.id)}
    dev_mutate_explorer = client.post("/admin/explorer/projects", headers=dev_headers)
    assert dev_mutate_explorer.status_code == 405
    dev_mutate_project = client.post(
        f"/workspaces/{workspace_a.id}/projects",
        headers=dev_headers,
        json={"name": "Should Not Create"},
    )
    assert dev_mutate_project.status_code == 403

    own = client.get(
        f"/workspaces/{workspace_a.id}/explorer/projects/{project_a.id}",
        headers=_headers(owner_a, workspace_a.id),
    )
    assert own.status_code == 200, own.text
    assert own.json()["name"] == "Alpha Case"

    substituted = client.get(
        f"/workspaces/{workspace_a.id}/explorer/projects/{project_b.id}",
        headers=_headers(owner_a, workspace_a.id),
    )
    assert substituted.status_code == 404
    foreign_workspace = client.get(
        f"/workspaces/{workspace_b.id}/explorer/projects/{project_b.id}",
        headers=_headers(owner_a, workspace_b.id),
    )
    assert foreign_workspace.status_code == 404

    viewer_headers = _headers(viewer, workspace_a.id)
    viewer_read = client.get(
        f"/workspaces/{workspace_a.id}/explorer/projects",
        headers=viewer_headers,
    )
    assert viewer_read.status_code == 200
    viewer_mutate = client.post(
        f"/workspaces/{workspace_a.id}/projects",
        headers=viewer_headers,
        json={"name": "Viewer Project"},
    )
    assert viewer_mutate.status_code == 403

    small_count, small_response = _count_get(
        db_session, admin_client, "/admin/explorer/projects"
    )
    assert small_response.status_code == 200
    assert small_count <= LIST_QUERY_BUDGET
    for index in range(6):
        create_project(
            db_session,
            actor=owner_a,
            workspace_id=workspace_a.id,
            name=f"Bounded {index}",
        )
    db_session.commit()
    large_count, large_response = _count_get(
        db_session, admin_client, "/admin/explorer/projects"
    )
    assert large_response.status_code == 200
    assert len(large_response.json()) >= len(small_response.json()) + 6
    assert large_count <= LIST_QUERY_BUDGET
    assert large_count <= small_count + 1


def test_pipeline_run_detail_roles_artifacts_and_detail_query_budget(
    auth_client,
    admin_client,
    client,
    db_session,
    monkeypatch,
    client_user,
    _rule_engine_only,
):
    frame = ordinary_binary()
    frame.loc[frame.sample(frac=0.12, random_state=7).index, "income"] = float("nan")
    upload, workflow_run, experiment, model_version = labs_upload_and_train(
        auth_client,
        db_session,
        monkeypatch,
        frame,
        filename="explorer.csv",
        target="outcome",
    )
    workspace_id = upload.workspace_id
    engineer_membership = add_workspace_member(
        db_session,
        actor=client_user,
        workspace_id=workspace_id,
        email=f"ml-{uuid4().hex}@test.invalid",
        password="test-password",
        role=WorkspaceRole.ML_ENGINEER.value,
        full_name="ML Engineer",
    )
    db_session.commit()
    engineer = db_session.get(User, engineer_membership.user_id)
    developer = create_user(
        db_session,
        email=f"dev-{uuid4().hex}@dclab.test",
        password="test-password",
        role=UserRole.DCLAB_DEVELOPER,
        full_name="Internal Developer",
    )
    other_owner = _owner(db_session, "foreign-owner")
    other_workspace = create_business_workspace(
        db_session, owner=other_owner, name="Foreign Explorer"
    )
    db_session.commit()

    detail_count, admin_detail = _count_get(
        db_session,
        admin_client,
        f"/admin/explorer/pipeline-runs/{experiment.id}",
    )
    assert admin_detail.status_code == 200, admin_detail.text
    assert detail_count <= DETAIL_QUERY_BUDGET
    body = admin_detail.json()
    assert body["identity"]["pipeline_run_id"] == str(experiment.id)
    assert body["identity"]["workspace_id"] == str(workspace_id)
    assert body["project"]["id"]
    assert body["datasets"]
    assert body["stage_timeline"]
    assert body["data_quality"] or body["preparation_decisions"]
    assert body["feature_engineering"]["features"]
    assert body["preprocessing"]
    assert body["model_candidates"]
    winner = body["winner_decision"]
    assert winner is not None
    assert winner["selected_candidate_id"]
    assert body["final_model"]["model_version_id"] == str(model_version.id)
    assert body["artifacts"]
    assert body["code_runtime"]["runtime_environment"]
    assert body["code_runtime"]["code_snapshot"]
    assert body["verification"]["overall_status"]
    candidate = body["model_candidates"][0]
    assert candidate["hyperparameters"]
    assert candidate["cv_folds"]
    assert candidate["evaluations"]
    fold = candidate["cv_folds"][0]
    assert fold["metrics"]

    customer = auth_client.get(
        f"/workspaces/{workspace_id}/explorer/pipeline-runs/{experiment.id}"
    )
    assert customer.status_code == 200, customer.text
    assert customer.json()["identity"]["pipeline_run_id"] == str(experiment.id)

    engineer_detail = client.get(
        f"/workspaces/{workspace_id}/explorer/pipeline-runs/{experiment.id}",
        headers=_headers(engineer, workspace_id),
    )
    assert engineer_detail.status_code == 200, engineer_detail.text
    engineer_body = engineer_detail.json()
    assert engineer_body["model_candidates"]
    assert engineer_body["model_candidates"][0]["cv_folds"]
    assert engineer_body["winner_decision"]["selection_metric"]

    candidate_id = engineer_body["model_candidates"][0]["id"]
    candidate_detail = client.get(
        f"/workspaces/{workspace_id}/explorer/candidates/{candidate_id}",
        headers=_headers(engineer, workspace_id),
    )
    assert candidate_detail.status_code == 200, candidate_detail.text
    assert candidate_detail.json()["hyperparameters"]
    assert candidate_detail.json()["cv_folds"]
    assert candidate_detail.json()["evaluations"]

    version_detail = admin_client.get(
        f"/admin/explorer/model-versions/{model_version.id}"
    )
    assert version_detail.status_code == 200, version_detail.text
    assert version_detail.json()["selected_candidate_id"] == str(
        model_version.selected_candidate_id
    )
    assert version_detail.json()["artifacts"]

    workflow_detail = admin_client.get(
        f"/admin/explorer/workflows/{workflow_run.workflow_id}"
    )
    assert workflow_detail.status_code == 200, workflow_detail.text
    assert any(
        row["id"] == str(experiment.id) for row in workflow_detail.json()["runs"]
    )

    project_id = body["project"]["id"]
    project_detail = auth_client.get(
        f"/workspaces/{workspace_id}/explorer/projects/{project_id}"
    )
    assert project_detail.status_code == 200, project_detail.text
    assert any(
        row["id"] == str(experiment.id)
        for row in project_detail.json()["pipeline_runs"]
    )

    developer_read = client.get(
        f"/admin/explorer/pipeline-runs/{experiment.id}",
        headers=_headers(developer),
    )
    assert developer_read.status_code == 200, developer_read.text
    developer_write = client.post(
        f"/workspaces/{workspace_id}/projects",
        headers=_headers(developer),
        json={"name": "Developer Write"},
    )
    assert developer_write.status_code == 403

    cross_object = client.get(
        f"/workspaces/{other_workspace.id}/explorer/pipeline-runs/{experiment.id}",
        headers=_headers(other_owner, other_workspace.id),
    )
    assert cross_object.status_code == 404
    cross_workspace = client.get(
        f"/workspaces/{workspace_id}/explorer/pipeline-runs/{experiment.id}",
        headers=_headers(other_owner, workspace_id),
    )
    assert cross_workspace.status_code == 404

    artifact_id = body["artifacts"][0]["id"]
    signed = auth_client.get(
        f"/workspaces/{workspace_id}/artifacts/{artifact_id}/signed-url"
    )
    assert signed.status_code == 200, signed.text
    signed_text = str(signed.json()).lower()
    assert "aws_secret" not in signed_text
    assert "aws_access_key" not in signed_text
    assert "credential" not in signed_text
    assert signed.json()["artifact_id"] == artifact_id
    foreign_signed = client.get(
        f"/workspaces/{other_workspace.id}/artifacts/{artifact_id}/signed-url",
        headers=_headers(other_owner, other_workspace.id),
    )
    assert foreign_signed.status_code in {403, 404}

    stored = db_session.get(ExperimentCandidate, experiment.candidates[0].id)
    assert stored is not None
    list_count, list_response = _count_get(
        db_session,
        admin_client,
        "/admin/explorer/pipeline-runs",
    )
    assert list_response.status_code == 200
    assert list_count <= LIST_QUERY_BUDGET
    assert any(row["id"] == str(experiment.id) for row in list_response.json())
