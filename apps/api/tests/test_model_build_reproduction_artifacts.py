"""Persisted reproduction notebook/script artifacts and authorized download."""

from __future__ import annotations

import hashlib
import json
from uuid import uuid4

from sqlalchemy import select

from app.db.models import Artifact, User, UserRole, WorkspaceRole
from app.domain.data_plane import (
    ARTIFACT_TYPES,
    REPRODUCTION_NOTEBOOK_TYPE,
    REPRODUCTION_SCRIPT_TYPE,
)
from app.domain.model_build_reproduction import (
    AUTHORIZED_DATASET_PATH_PLACEHOLDER,
    GENERATOR_VERSION,
)
from app.services.auth_service import create_user
from app.services.model_build_notebook import NOTEBOOK_SECTIONS
from app.services.model_build_reproduction_service import (
    persist_model_build_reproduction_artifacts,
)
from app.services.workspace_service import add_workspace_member, create_business_workspace
from test_model_build_read import _headers, _stub_pipeline_run
from test_model_build_reproduction import FORBIDDEN_FRAGMENTS

DOCUMENT_FORBIDDEN = FORBIDDEN_FRAGMENTS + (
    "frame.head(",
    "y_true =",
    "aws_secret",
)


def test_reproduction_artifact_types_are_registered():
    assert REPRODUCTION_NOTEBOOK_TYPE in ARTIFACT_TYPES
    assert REPRODUCTION_SCRIPT_TYPE in ARTIFACT_TYPES


def _section_titles(notebook: dict) -> list[str]:
    titles: list[str] = []
    for cell in notebook.get("cells") or []:
        if cell.get("cell_type") != "markdown":
            continue
        source = "".join(cell.get("source") or [])
        if source.startswith("## "):
            titles.append(source.split("\n", 1)[0][3:].strip())
    return titles


def test_stub_without_persist_hides_reproduction_downloads(client, db_session):
    owner = create_user(
        db_session,
        email=f"repro-missing-{uuid4().hex}@test.invalid",
        password="test-password",
        role=UserRole.WORKSPACE_OWNER,
        full_name="Missing Reproduction Owner",
    )
    workspace = create_business_workspace(
        db_session, owner=owner, name="Missing Reproduction Workspace"
    )
    run = _stub_pipeline_run(db_session, workspace.id)
    headers = _headers(owner, workspace.id)
    base = f"/workspaces/{workspace.id}/pipeline-runs/{run.id}/model-build/reproduction"
    listed = client.get(f"{base}/artifacts", headers=headers)
    assert listed.status_code == 200, listed.text
    assert listed.json()["notebook"] is None
    assert listed.json()["script"] is None
    assert client.get(f"{base}/notebook", headers=headers).status_code == 404
    assert client.get(f"{base}/script", headers=headers).status_code == 404
    assert client.get(f"{base}/notebook/download", headers=headers).status_code == 404
    assert client.get(f"{base}/script/download", headers=headers).status_code == 404
    build = client.get(
        f"/workspaces/{workspace.id}/pipeline-runs/{run.id}/model-build",
        headers=headers,
    )
    assert build.status_code == 200, build.text
    assert build.json()["reproduction_notebook"] is None
    assert build.json()["reproduction_script"] is None


def test_persisted_reproduction_artifacts_are_safe_idempotent_and_readable(
    client,
    db_session,
):
    owner = create_user(
        db_session,
        email=f"repro-art-owner-{uuid4().hex}@test.invalid",
        password="test-password",
        role=UserRole.WORKSPACE_OWNER,
        full_name="Reproduction Artifact Owner",
    )
    workspace = create_business_workspace(
        db_session, owner=owner, name="Reproduction Artifact Workspace"
    )
    run = _stub_pipeline_run(db_session, workspace.id)
    run.result = {
        "api_key": "DO-NOT-RETURN",
        "raw_customer_rows": [{"customer": "PRIVATE-ROW"}],
        "raw_llm_prompt": "PRIVATE-PROMPT",
        "location": "/private/customer-file.csv",
    }
    db_session.commit()

    first_notebook, first_script = persist_model_build_reproduction_artifacts(db_session, run)
    second_notebook, second_script = persist_model_build_reproduction_artifacts(db_session, run)
    assert first_notebook.id == second_notebook.id
    assert first_script.id == second_script.id
    db_session.flush()

    viewer_membership = add_workspace_member(
        db_session,
        actor=owner,
        workspace_id=workspace.id,
        email=f"repro-art-viewer-{uuid4().hex}@test.invalid",
        password="test-password",
        role=WorkspaceRole.VIEWER.value,
        full_name="Reproduction Viewer",
    )
    foreign_owner = create_user(
        db_session,
        email=f"repro-art-foreign-{uuid4().hex}@test.invalid",
        password="test-password",
        role=UserRole.WORKSPACE_OWNER,
        full_name="Foreign Reproduction Owner",
    )
    db_session.commit()
    viewer = db_session.get(User, viewer_membership.user_id)
    assert viewer is not None

    headers = _headers(viewer, workspace.id)
    base = f"/workspaces/{workspace.id}/pipeline-runs/{run.id}/model-build"
    build = client.get(base, headers=headers)
    assert build.status_code == 200, build.text
    body = build.json()
    notebook_meta = body["reproduction_notebook"]
    script_meta = body["reproduction_script"]
    assert notebook_meta["id"] == str(first_notebook.id)
    assert script_meta["id"] == str(first_script.id)
    assert notebook_meta["artifact_type"] == REPRODUCTION_NOTEBOOK_TYPE
    assert script_meta["artifact_type"] == REPRODUCTION_SCRIPT_TYPE
    assert notebook_meta["filename"] == f"{run.id}-reproduction.ipynb"
    assert script_meta["filename"] == f"{run.id}-reproduction.py"
    assert notebook_meta["workspace_id"] == str(workspace.id)
    assert notebook_meta["pipeline_run_id"] == str(run.id)
    assert notebook_meta["generator_version"] == GENERATOR_VERSION
    assert notebook_meta["spec_digest"] == body["reproduction_spec_digest"]
    assert notebook_meta["role"] == "reproduction_notebook"
    assert script_meta["role"] == "reproduction_script"
    assert "object_key" not in notebook_meta
    assert "bucket" not in notebook_meta
    assert "object_key" not in script_meta

    listed = client.get(f"{base}/reproduction/artifacts", headers=headers)
    assert listed.status_code == 200, listed.text
    listed_body = listed.json()
    assert listed_body["notebook"]["id"] == notebook_meta["id"]
    assert listed_body["script"]["id"] == script_meta["id"]
    assert "object_key" not in json.dumps(listed_body)

    notebook = client.get(f"{base}/reproduction/notebook/download", headers=headers)
    script = client.get(f"{base}/reproduction/script/download", headers=headers)
    assert notebook.status_code == 200, notebook.text
    assert script.status_code == 200, script.text
    assert notebook.headers["content-type"].startswith("application/x-ipynb+json")
    assert script.headers["content-type"].startswith("text/x-python")
    assert f'filename="{run.id}-reproduction.ipynb"' in notebook.headers.get(
        "content-disposition", ""
    )
    assert f'filename="{run.id}-reproduction.py"' in script.headers.get(
        "content-disposition", ""
    )

    notebook_text = notebook.content.decode("utf-8")
    script_text = script.content.decode("utf-8")
    parsed = json.loads(notebook_text)
    assert parsed["nbformat"] == 4
    assert parsed["nbformat_minor"] == 5
    titles = _section_titles(parsed)
    assert titles == [title for _key, title, _stages in NOTEBOOK_SECTIONS]
    assert titles.index("CV evaluation") < titles.index("CV-only winner selection")
    assert titles.index("CV-only winner selection") < titles.index(
        "Final holdout exactly once"
    )
    assert AUTHORIZED_DATASET_PATH_PLACEHOLDER in notebook_text
    assert AUTHORIZED_DATASET_PATH_PLACEHOLDER in script_text
    for fragment in DOCUMENT_FORBIDDEN:
        assert fragment not in notebook_text
        assert fragment not in script_text
    assert hashlib.sha256(notebook.content).hexdigest() == notebook_meta["content_digest"]
    assert hashlib.sha256(script.content).hexdigest() == script_meta["content_digest"]
    assert hashlib.sha256(notebook.content).hexdigest() == first_notebook.content_digest
    assert hashlib.sha256(script.content).hexdigest() == first_script.content_digest

    stored = list(
        db_session.scalars(
            select(Artifact).where(
                Artifact.workspace_id == workspace.id,
                Artifact.pipeline_run_id == run.id,
                Artifact.artifact_type.in_(
                    (REPRODUCTION_NOTEBOOK_TYPE, REPRODUCTION_SCRIPT_TYPE)
                ),
            )
        )
    )
    assert {row.artifact_type for row in stored} == {
        REPRODUCTION_NOTEBOOK_TYPE,
        REPRODUCTION_SCRIPT_TYPE,
    }
    assert len(stored) == 2

    denied = client.get(
        f"{base}/reproduction/notebook/download",
        headers=_headers(foreign_owner, workspace.id),
    )
    assert denied.status_code == 404
    other_workspace = create_business_workspace(
        db_session, owner=foreign_owner, name="Other Reproduction Co"
    )
    wrong_workspace = client.get(
        f"/workspaces/{other_workspace.id}/pipeline-runs/{run.id}/model-build/reproduction/notebook/download",
        headers=_headers(foreign_owner, other_workspace.id),
    )
    assert wrong_workspace.status_code == 404
