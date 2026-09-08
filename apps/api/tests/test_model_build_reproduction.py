"""Deterministic ModelBuildReproductionSpec and versioned generated Python."""

from __future__ import annotations

from uuid import uuid4

from app.db.models import DEFAULT_WORKSPACE_ID, UserRole
from app.domain.model_build_reproduction import (
    AUTHORIZED_DATASET_PATH_PLACEHOLDER,
    GENERATOR_VERSION,
)
from app.services.auth_service import create_user
from app.services.workspace_service import create_business_workspace
from test_model_build_read import _headers, _stub_pipeline_run

FORBIDDEN_FRAGMENTS = (
    "DO-NOT-RETURN",
    "PRIVATE-ROW",
    "PRIVATE-PROMPT",
    "/private/unused.csv",
    "/private/customer-file.csv",
)


def test_stub_run_reproduction_is_deterministic_and_omits_location(
    client,
    db_session,
):
    owner = create_user(
        db_session,
        email=f"repro-owner-{uuid4().hex}@test.invalid",
        password="test-password",
        role=UserRole.WORKSPACE_OWNER,
        full_name="Reproduction Owner",
    )
    workspace = create_business_workspace(
        db_session, owner=owner, name="Reproduction Workspace"
    )
    run = _stub_pipeline_run(db_session, workspace.id)
    path = f"/workspaces/{workspace.id}/pipeline-runs/{run.id}/model-build/reproduction"
    headers = _headers(owner, workspace.id)

    first = client.get(path, headers=headers)
    second = client.get(path, headers=headers)
    assert first.status_code == 200, first.text
    assert second.status_code == 200, second.text
    body = first.json()
    assert body == second.json()
    assert body["generator_version"] == GENERATOR_VERSION
    assert len(body["spec_digest"]) == 64
    assert body["dataset"]["content_digest"] == "b" * 64
    assert "location" not in body["dataset"]
    assert body["task"]["seed"] == 42
    model_build = client.get(
        f"/workspaces/{workspace.id}/pipeline-runs/{run.id}/model-build",
        headers=headers,
    )
    assert [row["key"] for row in body["stage_code"]] == [
        stage["key"] for stage in model_build.json()["stages"]
    ]
    ingestion = next(row for row in body["stage_code"] if row["key"] == "ingestion")
    assert AUTHORIZED_DATASET_PATH_PLACEHOLDER in ingestion["source"]
    assert ingestion["code_generation_support_status"] == "supported"
    preprocessing = next(row for row in body["stage_code"] if row["key"] == "preprocessing")
    assert preprocessing["code_generation_support_status"] == "not_available"
    serialized = first.text
    for fragment in FORBIDDEN_FRAGMENTS:
        assert fragment not in serialized
    for row in body["stage_code"]:
        if row["key"] in {"holdout_lock", "final_holdout"}:
            continue
        assert "X_holdout" not in row["source"] or "must not" in row["source"].lower() or "never read" in row["source"].lower()


def test_reproduction_hides_cross_workspace_runs(client, db_session):
    owner = create_user(
        db_session,
        email=f"repro-hidden-{uuid4().hex}@test.invalid",
        password="test-password",
        role=UserRole.WORKSPACE_OWNER,
        full_name="Hidden Reproduction Owner",
    )
    db_session.commit()
    response = client.get(
        f"/workspaces/{uuid4()}/pipeline-runs/{uuid4()}/model-build/reproduction",
        headers=_headers(owner, uuid4()),
    )
    assert response.status_code == 404


def test_default_workspace_stub_omits_dataset_location_from_generated_code(
    auth_client, db_session
):
    run = _stub_pipeline_run(db_session, DEFAULT_WORKSPACE_ID)
    response = auth_client.get(
        f"/workspaces/{DEFAULT_WORKSPACE_ID}/pipeline-runs/{run.id}/model-build"
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["generator_version"] == GENERATOR_VERSION
    joined = "\n".join(stage["generated_code"]["source"] for stage in body["stages"])
    assert AUTHORIZED_DATASET_PATH_PLACEHOLDER in joined
    assert "/private/unused.csv" not in joined
    assert body["reproduction_spec_digest"] == body["stages"][0]["generated_code"]["spec_digest"]
