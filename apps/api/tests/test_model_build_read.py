"""Safe workspace-scoped model-build read model."""

from __future__ import annotations

import hashlib
import json
from types import SimpleNamespace
from uuid import uuid4

import pytest

from adaptive_modeling.fixtures import ordinary_binary
from adaptive_modeling.production import labs_upload_and_train
from app.db.models import (
    DEFAULT_WORKSPACE_ID,
    Dataset,
    DatasetAsset,
    Experiment,
    PredictionTask,
    User,
    UserRole,
    WorkspaceRole,
)
from app.services.auth_service import create_access_token, create_user
from app.services.lab_service import seed_dogfood
from app.services.workspace_service import (
    add_workspace_member,
    create_business_workspace,
    create_personal_workspace,
)


EXPECTED_STAGE_KEYS = [
    "ingestion",
    "profiling_eda",
    "target_task",
    "structural_cleaning",
    "final_holdout_plan",
    "holdout_lock",
    "problem_profile",
    "validation_plan",
    "metric_plan",
    "leakage_audit",
    "missing_value_decisions",
    "feature_engineering",
    "preprocessing",
    "candidate_generation",
    "cv_training",
    "candidate_comparison",
    "winner_lock",
    "final_refit",
    "final_holdout",
    "artifact_reproducibility_persistence",
    "deterministic_verification",
]


@pytest.fixture
def _rule_engine_only(monkeypatch):
    monkeypatch.setattr(
        "app.services.lab_decision_ledger.get_settings",
        lambda: SimpleNamespace(decision_agent_enabled=False, decision_agent_api_key=""),
    )


def _headers(user, workspace_id) -> dict[str, str]:
    return {
        "Authorization": f"Bearer {create_access_token(user)}",
        "X-Workspace-Id": str(workspace_id),
    }


def _stub_pipeline_run(db_session, workspace_id) -> Experiment:
    environment = seed_dogfood(db_session)
    slug = f"model-build-auth-{uuid4().hex[:10]}"
    asset = DatasetAsset(
        workspace_id=workspace_id,
        name=slug,
        slug=slug,
    )
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


def _all_keys(value) -> set[str]:
    if isinstance(value, dict):
        return {str(key).lower() for key in value} | set().union(
            *(_all_keys(item) for item in value.values()), set()
        )
    if isinstance(value, list):
        return set().union(*(_all_keys(item) for item in value), set())
    return set()


def test_model_build_is_ordered_safe_canonical_and_workspace_readable(
    auth_client,
    client,
    db_session,
    monkeypatch,
    client_user,
    _rule_engine_only,
):
    frame = ordinary_binary()
    frame.loc[frame.sample(frac=0.12, random_state=7).index, "income"] = float("nan")
    upload, _workflow_run, experiment, model_version = labs_upload_and_train(
        auth_client,
        db_session,
        monkeypatch,
        frame,
        filename="model-build.csv",
        target="outcome",
    )
    workspace_id = upload.workspace_id

    # Poison compatibility-only data. A current run's normalized evidence must win,
    # and none of these values may cross the response boundary.
    experiment.result = {
        **dict(experiment.result or {}),
        "api_key": "DO-NOT-RETURN",
        "raw_customer_rows": [{"customer": "PRIVATE-ROW"}],
        "raw_llm_prompt": "PRIVATE-PROMPT",
        "selection": {"selected_candidate_id": "WRONG-CANDIDATE"},
        "metric_plan": {"primary_metric": "WRONG-METRIC"},
    }
    db_session.commit()

    viewer_membership = add_workspace_member(
        db_session,
        actor=client_user,
        workspace_id=workspace_id,
        email=f"build-viewer-{uuid4().hex}@test.invalid",
        password="test-password",
        role=WorkspaceRole.VIEWER.value,
        full_name="Build Viewer",
    )
    engineer_membership = add_workspace_member(
        db_session,
        actor=client_user,
        workspace_id=workspace_id,
        email=f"build-engineer-{uuid4().hex}@test.invalid",
        password="test-password",
        role=WorkspaceRole.ML_ENGINEER.value,
        full_name="Build Engineer",
    )
    platform_admin = create_user(
        db_session,
        email=f"build-admin-{uuid4().hex}@dclab.test",
        password="test-password",
        role=UserRole.DCLAB_ADMIN,
        full_name="Build Platform Admin",
    )
    admin_membership = add_workspace_member(
        db_session,
        actor=platform_admin,
        workspace_id=workspace_id,
        email=f"build-ws-admin-{uuid4().hex}@test.invalid",
        password="test-password",
        role=WorkspaceRole.WORKSPACE_ADMIN.value,
        full_name="Build Workspace Admin",
    )
    developer = create_user(
        db_session,
        email=f"build-dev-{uuid4().hex}@dclab.test",
        password="test-password",
        role=UserRole.DCLAB_DEVELOPER,
        full_name="Build Platform Reader",
    )
    foreign_owner = create_user(
        db_session,
        email=f"build-foreign-{uuid4().hex}@test.invalid",
        password="test-password",
        role=UserRole.WORKSPACE_OWNER,
        full_name="Foreign Owner",
    )
    db_session.commit()
    viewer = db_session.get(User, viewer_membership.user_id)
    engineer = db_session.get(User, engineer_membership.user_id)
    workspace_admin = db_session.get(User, admin_membership.user_id)

    path = f"/workspaces/{workspace_id}/pipeline-runs/{experiment.id}/model-build"
    owner_response = auth_client.get(path)
    assert owner_response.status_code == 200, owner_response.text
    body = owner_response.json()

    assert body["workspace_id"] == str(workspace_id)
    assert body["pipeline_run_id"] == str(experiment.id)
    assert body["scientific_evidence_locked_at"] is not None
    assert body["compatibility_fallback_used"] is False
    assert [stage["key"] for stage in body["stages"]] == EXPECTED_STAGE_KEYS
    assert [stage["sequence"] for stage in body["stages"]] == list(range(1, 22))
    assert all(stage["status"] == "completed" for stage in body["stages"])
    assert all(
        {
            "key",
            "sequence",
            "title",
            "status",
            "started_at",
            "completed_at",
            "duration_ms",
            "rows_in",
            "rows_out",
            "decision_summary",
            "reason",
            "configuration",
            "evidence_references",
            "related_candidate_ids",
            "related_fold_ids",
            "code_generation_support_status",
            "generated_code",
        }
        <= set(stage)
        for stage in body["stages"]
    )

    stages = {stage["key"]: stage for stage in body["stages"]}
    assert stages["metric_plan"]["configuration"]["primary_metric"] != "WRONG-METRIC"
    assert (
        stages["winner_lock"]["configuration"]["selected_candidate_id"]
        == str(model_version.selected_candidate_id)
    )
    assert stages["cv_training"]["related_fold_ids"]
    assert stages["candidate_generation"]["related_candidate_ids"]
    assert stages["candidate_generation"]["configuration"]["candidates"]
    assert all("fingerprint" in row for row in stages["candidate_generation"]["configuration"]["candidates"])
    assert stages["feature_engineering"]["configuration"]["features"]
    assert stages["cv_training"]["configuration"]["folds"]
    assert all("metrics" in row for row in stages["cv_training"]["configuration"]["folds"])
    assert stages["winner_lock"]["configuration"]["selection_metric"]
    assert stages["final_holdout"]["configuration"]["evaluations"]
    assert stages["artifact_reproducibility_persistence"]["configuration"]["artifacts"]
    assert stages["ingestion"]["code_generation_support_status"] == "supported"
    assert stages["preprocessing"]["code_generation_support_status"] == "supported"
    assert stages["cv_training"]["code_generation_support_status"] == "supported"
    assert body["generator_version"] == "dclab.model_build_reproduction.v1"
    assert body["reproduction_spec_digest"]
    assert all(stage.get("generated_code") for stage in body["stages"])
    assert all(
        stage["generated_code"]["spec_digest"] == body["reproduction_spec_digest"]
        for stage in body["stages"]
    )
    assert "<authorized-local-dataset-path>" in stages["ingestion"]["generated_code"]["source"]
    assert "SimpleImputer" in stages["preprocessing"]["generated_code"]["source"]
    assert "final holdout must not be used" in stages["cv_training"]["generated_code"]["source"]
    assert "Never read X_holdout" in stages["candidate_comparison"]["generated_code"]["source"]
    assert "X_holdout" not in stages["candidate_generation"]["generated_code"]["source"]
    assert "X_holdout" not in stages["winner_lock"]["generated_code"]["source"]
    assert "Do not change selection" in stages["final_holdout"]["generated_code"]["source"]

    second = auth_client.get(path)
    assert second.status_code == 200, second.text
    assert second.json()["reproduction_spec_digest"] == body["reproduction_spec_digest"]
    assert [stage["generated_code"]["digest"] for stage in second.json()["stages"]] == [
        stage["generated_code"]["digest"] for stage in body["stages"]
    ]
    assert [stage["generated_code"]["source"] for stage in second.json()["stages"]] == [
        stage["generated_code"]["source"] for stage in body["stages"]
    ]

    reproduction = auth_client.get(f"{path}/reproduction")
    assert reproduction.status_code == 200, reproduction.text
    spec = reproduction.json()
    assert spec["generator_version"] == body["generator_version"]
    assert spec["spec_digest"] == body["reproduction_spec_digest"]
    assert spec["task"]["target_column"] == "outcome"
    assert spec["task"]["seed"] == 42
    assert spec["holdout_plan"]["strategy"]
    assert spec["validation_plan"]["strategy"]
    assert spec["metric_plan"]["primary_metric"]
    assert spec["preprocessing"]
    assert spec["candidates"]
    assert spec["winner"]["candidate_id"] == str(model_version.selected_candidate_id)
    assert spec["winner"]["implementation_class"]
    assert spec["final_refit"]["content_digest"]
    assert spec["final_holdout"]["metrics"]
    assert spec["dataset"]["content_digest"]
    assert "location" not in spec["dataset"]
    winner_class = spec["winner"]["implementation_class"].rsplit(".", 1)[-1]
    assert winner_class in stages["final_refit"]["generated_code"]["source"]
    assert winner_class in stages["candidate_generation"]["generated_code"]["source"]

    notebook_meta = body["reproduction_notebook"]
    script_meta = body["reproduction_script"]
    assert notebook_meta is not None
    assert script_meta is not None
    assert notebook_meta["artifact_type"] == "reproduction_notebook"
    assert script_meta["artifact_type"] == "reproduction_script"
    assert notebook_meta["filename"] == f"{experiment.id}-reproduction.ipynb"
    assert script_meta["filename"] == f"{experiment.id}-reproduction.py"
    assert notebook_meta["workspace_id"] == str(workspace_id)
    assert script_meta["workspace_id"] == str(workspace_id)
    assert notebook_meta["project_id"] is not None
    assert notebook_meta["pipeline_run_id"] == str(experiment.id)
    assert notebook_meta["generator_version"] == body["generator_version"]
    assert notebook_meta["spec_digest"] == body["reproduction_spec_digest"]
    assert notebook_meta["role"] == "reproduction_notebook"
    assert script_meta["role"] == "reproduction_script"
    assert "object_key" not in notebook_meta
    assert "bucket" not in notebook_meta
    listed = auth_client.get(f"{path}/reproduction/artifacts")
    assert listed.status_code == 200, listed.text
    assert listed.json()["notebook"]["id"] == notebook_meta["id"]
    assert listed.json()["script"]["id"] == script_meta["id"]
    notebook = client.get(
        f"{path}/reproduction/notebook/download",
        headers=_headers(viewer, workspace_id),
    )
    script = client.get(
        f"{path}/reproduction/script/download",
        headers=_headers(viewer, workspace_id),
    )
    assert notebook.status_code == 200, notebook.text
    assert script.status_code == 200, script.text
    assert notebook.headers["content-type"].startswith("application/x-ipynb+json")
    assert script.headers["content-type"].startswith("text/x-python")
    assert f'filename="{experiment.id}-reproduction.ipynb"' in notebook.headers.get(
        "content-disposition", ""
    )
    notebook_payload = notebook.content.decode("utf-8")
    script_payload = script.content.decode("utf-8")
    parsed_notebook = json.loads(notebook_payload)
    assert parsed_notebook["nbformat"] == 4
    titles = [
        "".join(cell.get("source") or []).split("\n", 1)[0][3:].strip()
        for cell in parsed_notebook["cells"]
        if cell.get("cell_type") == "markdown"
        and "".join(cell.get("source") or []).startswith("## ")
    ]
    assert titles == [
        "Run identity / reproducibility metadata",
        "Imports and versions",
        "Dataset loading placeholder + expected digest",
        "Schema checks",
        "Task / target",
        "Structural cleanup",
        "Holdout creation and lock",
        "TRAIN-only profiling / planning",
        "Leakage exclusions",
        "Feature engineering",
        "Preprocessing",
        "Candidate definitions",
        "CV evaluation",
        "CV-only winner selection",
        "Winner refit on all training data",
        "Final holdout exactly once",
        "Metrics",
        "Saved model / artifact information",
    ]
    assert titles.index("CV evaluation") < titles.index("CV-only winner selection")
    assert titles.index("CV-only winner selection") < titles.index("Final holdout exactly once")
    assert "<authorized-local-dataset-path>" in notebook_payload
    assert "<authorized-local-dataset-path>" in script_payload
    for fragment in ("DO-NOT-RETURN", "PRIVATE-ROW", "PRIVATE-PROMPT", "frame.head("):
        assert fragment not in notebook_payload
        assert fragment not in script_payload
    assert hashlib.sha256(notebook.content).hexdigest() == notebook_meta["content_digest"]
    assert hashlib.sha256(script.content).hexdigest() == script_meta["content_digest"]

    serialized = owner_response.text
    assert "DO-NOT-RETURN" not in serialized
    assert "PRIVATE-ROW" not in serialized
    assert "PRIVATE-PROMPT" not in serialized
    keys = _all_keys(body)
    assert "object_key" not in keys
    assert "bucket" not in keys
    assert "location" not in keys
    assert "raw_customer_rows" not in keys
    assert "raw_llm_prompt" not in keys
    assert "deterministic_checks" not in keys
    assert "test_predictions" not in keys

    for reader in (viewer, workspace_admin, engineer, developer, platform_admin):
        response = client.get(path, headers=_headers(reader, workspace_id))
        assert response.status_code == 200, response.text

    denied = client.get(path, headers=_headers(foreign_owner, workspace_id))
    assert denied.status_code == 404

    missing_run = client.get(
        f"/workspaces/{workspace_id}/pipeline-runs/{uuid4()}/model-build",
        headers=_headers(viewer, workspace_id),
    )
    assert missing_run.status_code == 404

    other_owner = create_user(
        db_session,
        email=f"build-other-owner-{uuid4().hex}@test.invalid",
        password="test-password",
        role=UserRole.WORKSPACE_OWNER,
        full_name="Other Workspace Owner",
    )
    other_workspace = create_business_workspace(
        db_session, owner=other_owner, name="Other Model-Build Co"
    )
    other_run = _stub_pipeline_run(db_session, other_workspace.id)
    # Platform readers may enter any workspace, but a run still has to live in the
    # workspace named by the path.
    wrong_tenant_path = client.get(
        f"/workspaces/{workspace_id}/pipeline-runs/{other_run.id}/model-build",
        headers=_headers(developer, workspace_id),
    )
    assert wrong_tenant_path.status_code == 404
    wrong_workspace_path = client.get(
        f"/workspaces/{other_workspace.id}/pipeline-runs/{experiment.id}/model-build",
        headers=_headers(developer, other_workspace.id),
    )
    assert wrong_workspace_path.status_code == 404


def test_model_build_hides_cross_workspace_run_ids(
    client,
    db_session,
):
    foreign_owner = create_user(
        db_session,
        email=f"model-build-isolated-{uuid4().hex}@test.invalid",
        password="test-password",
        role=UserRole.WORKSPACE_OWNER,
        full_name="Isolated Owner",
    )
    db_session.commit()

    response = client.get(
        f"/workspaces/{uuid4()}/pipeline-runs/{uuid4()}/model-build",
        headers=_headers(foreign_owner, uuid4()),
    )
    assert response.status_code == 404


def test_personal_and_workspace_owners_can_read_their_model_build(
    client,
    db_session,
):
    personal_owner = create_user(
        db_session,
        email=f"personal-build-{uuid4().hex}@test.invalid",
        password="test-password",
        role=UserRole.WORKSPACE_OWNER,
        full_name="Personal Build Owner",
    )
    personal_workspace = create_personal_workspace(
        db_session, owner=personal_owner, name="Personal Model Build"
    )
    personal_run = _stub_pipeline_run(db_session, personal_workspace.id)

    business_owner = create_user(
        db_session,
        email=f"business-build-{uuid4().hex}@test.invalid",
        password="test-password",
        role=UserRole.WORKSPACE_OWNER,
        full_name="Business Build Owner",
    )
    business_workspace = create_business_workspace(
        db_session, owner=business_owner, name="Business Model Build"
    )
    business_run = _stub_pipeline_run(db_session, business_workspace.id)

    personal_path = (
        f"/workspaces/{personal_workspace.id}/pipeline-runs/{personal_run.id}/model-build"
    )
    business_path = (
        f"/workspaces/{business_workspace.id}/pipeline-runs/{business_run.id}/model-build"
    )

    personal = client.get(
        personal_path, headers=_headers(personal_owner, personal_workspace.id)
    )
    assert personal.status_code == 200, personal.text
    assert personal.json()["pipeline_run_id"] == str(personal_run.id)
    assert [stage["key"] for stage in personal.json()["stages"]] == EXPECTED_STAGE_KEYS

    business = client.get(
        business_path, headers=_headers(business_owner, business_workspace.id)
    )
    assert business.status_code == 200, business.text
    assert business.json()["pipeline_run_id"] == str(business_run.id)

    assert (
        client.get(
            personal_path, headers=_headers(business_owner, personal_workspace.id)
        ).status_code
        == 404
    )
    assert (
        client.get(
            business_path, headers=_headers(personal_owner, business_workspace.id)
        ).status_code
        == 404
    )


def test_old_run_uses_only_allowlisted_json_gap_fill(auth_client, db_session):
    environment = seed_dogfood(db_session)
    slug = f"legacy-model-build-{uuid4().hex[:10]}"
    asset = DatasetAsset(
        workspace_id=DEFAULT_WORKSPACE_ID,
        name=slug,
        slug=slug,
    )
    db_session.add(asset)
    db_session.flush()
    dataset = Dataset(
        workspace_id=DEFAULT_WORKSPACE_ID,
        dataset_asset_id=asset.id,
        environment_id=environment.id,
        name=slug,
        source_type="csv",
        location="/private/customer-file.csv",
        version="v1",
        content_digest="a" * 64,
        row_count=20,
        column_count=3,
    )
    task = PredictionTask(
        environment_id=environment.id,
        slug=slug,
        name="Legacy model build",
        task_type="binary",
        spec={
            "target": "outcome",
            "task_type": "binary",
            "raw_rows": [{"private": "SPEC-ROW"}],
        },
    )
    db_session.add_all([dataset, task])
    db_session.flush()
    experiment = Experiment(
        workspace_id=DEFAULT_WORKSPACE_ID,
        environment_id=environment.id,
        task_id=task.id,
        dataset_id=dataset.id,
        status="COMPLETED",
        config={},
        result={
            "task": {
                "target": "outcome",
                "task_type": "binary",
                "evaluation_metric": "roc_auc",
                "raw_rows": [{"private": "TASK-ROW"}],
            },
            "holdout_plan": {
                "strategy": "stratified_random",
                "test_size": 0.2,
                "test_indices": [1, 2, 3],
            },
            "problem_profile": {
                "task_type": "binary",
                "row_count": 16,
                "raw_customer_rows": [{"private": "PROFILE-ROW"}],
            },
            "validation_plan": {"strategy": "StratifiedKFold", "actual_folds": 3},
            "metric_plan": {"primary_metric": "roc_auc"},
            "deterministic_verification": {
                "overall_status": "PASS",
                "checks": [
                    {
                        "status": "PASS",
                        "raw_test_contents": "VERIFY-CONTENTS",
                    }
                ],
            },
            "raw_llm_prompt": "LEGACY-PROMPT",
            "credentials": {"api_key": "LEGACY-KEY"},
            "test_predictions": [{"record_id": "PRIVATE-PREDICTION"}],
        },
    )
    db_session.add(experiment)
    db_session.commit()

    response = auth_client.get(
        f"/workspaces/{DEFAULT_WORKSPACE_ID}/pipeline-runs/{experiment.id}/model-build"
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["compatibility_fallback_used"] is True
    stages = {stage["key"]: stage for stage in body["stages"]}
    assert stages["target_task"]["configuration"]["target_column"] == "outcome"
    assert stages["final_holdout_plan"]["configuration"] == {
        "strategy": "stratified_random",
        "test_size": 0.2,
    }
    assert stages["deterministic_verification"]["configuration"] == {
        "overall_status": "PASS",
        "check_count": 1,
        "failure_count": 0,
        "warning_count": 0,
    }
    serialized = response.text
    for forbidden in (
        "SPEC-ROW",
        "TASK-ROW",
        "PROFILE-ROW",
        "VERIFY-CONTENTS",
        "LEGACY-PROMPT",
        "LEGACY-KEY",
        "PRIVATE-PREDICTION",
        "/private/customer-file.csv",
    ):
        assert forbidden not in serialized
