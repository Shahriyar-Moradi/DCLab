"""Model Build 5 — reproducibility, security, and all-user production gate.

No new product features. One ordinary-binary fixture and one regression fixture
run through POST /app/labs/uploads → run_auto_train_job.
"""

from __future__ import annotations

import ast
import hashlib
import json
import re
from types import SimpleNamespace
from uuid import uuid4

import pytest

from adaptive_modeling.fixtures import ordinary_binary, regression
from adaptive_modeling.production import labs_upload_and_train
from app.db.models import User, UserRole, WorkspaceRole
from app.domain.model_build_reproduction import (
    AUTHORIZED_DATASET_PATH_PLACEHOLDER,
    GENERATOR_VERSION,
)
from app.engine.evaluation.metrics import classification_metrics, regression_metrics
from app.engine.experiments.runner import _predict
from app.services.auth_service import create_access_token, create_user
from app.services.model_build_notebook import NOTEBOOK_SECTIONS
from app.services.workspace_service import (
    add_workspace_member,
    create_business_workspace,
    create_personal_workspace,
)
from test_model_build_read import EXPECTED_STAGE_KEYS, _headers, _stub_pipeline_run

SECTION_TITLES = [title for _key, title, _stages in NOTEBOOK_SECTIONS]
HOLDOUT_LOCK_TITLE = "Holdout creation and lock"
CANDIDATE_TITLE = "Candidate definitions"
CV_TITLE = "CV evaluation"
WINNER_TITLE = "CV-only winner selection"
FINAL_HOLDOUT_TITLE = "Final holdout exactly once"

SECRET_PATTERNS = (
    re.compile(r"sk-(?:proj|live|test|svcacct)-[A-Za-z0-9_-]{8,}"),
    re.compile(r"AKIA[0-9A-Z]{16}"),
    re.compile(r"-----BEGIN (?:RSA |OPENSSH )?PRIVATE KEY-----"),
    re.compile(r"(?i)openai[_-]?api[_-]?key\s*[:=]\s*['\"][^'\"]+['\"]"),
    re.compile(r"(?i)(?:aws_)?secret_access_key\s*[:=]"),
    re.compile(r"(?i)password\s*=\s*['\"][^'\"]{4,}['\"]"),
    re.compile(r"(?i)bearer\s+[A-Za-z0-9._\-]+"),
)

POISON = {
    "api_key": "sk-proj-FAKESECRET-not-for-notebooks",
    "openai_api_key": "sk-live-FAKESECRET-not-for-notebooks",
    "password": "hunter2-storage",
    "token": "ya29.fake-oauth-token",
    "aws_secret_access_key": "wJalrXUtnFEMI/K7MDENG/bPxRfiCYEXAMPLEKEY",
    "raw_customer_rows": [{"customer": "PRIVATE-ROW", "ssn": "999-00-0000"}],
}

DOCUMENT_FORBIDDEN = (
    "PRIVATE-ROW",
    "999-00-0000",
    "hunter2-storage",
    "ya29.fake-oauth-token",
    "sk-proj-FAKESECRET-not-for-notebooks",
    "sk-live-FAKESECRET-not-for-notebooks",
    "wJalrXUtnFEMI/K7MDENG/bPxRfiCYEXAMPLEKEY",
    "frame.head(",
    "y_true =",
)


@pytest.fixture
def _rule_engine_only(monkeypatch):
    monkeypatch.setattr(
        "app.services.lab_decision_ledger.get_settings",
        lambda: SimpleNamespace(decision_agent_enabled=False, decision_agent_api_key=""),
    )


def _cell_text(cell: dict) -> str:
    source = cell.get("source") or []
    if isinstance(source, str):
        return source
    return "".join(source)


def _section_titles(notebook: dict) -> list[str]:
    titles: list[str] = []
    for cell in notebook.get("cells") or []:
        if cell.get("cell_type") != "markdown":
            continue
        text = _cell_text(cell)
        if text.startswith("## "):
            titles.append(text.split("\n", 1)[0][3:].strip())
    return titles


def _code_cells_by_title(notebook: dict) -> dict[str, str]:
    mapping: dict[str, str] = {}
    current = None
    for cell in notebook.get("cells") or []:
        if cell.get("cell_type") == "markdown":
            text = _cell_text(cell)
            if text.startswith("## "):
                current = text.split("\n", 1)[0][3:].strip()
            continue
        if cell.get("cell_type") == "code" and current is not None:
            mapping[current] = _cell_text(cell)
    return mapping


def _assert_valid_notebook(payload: bytes) -> dict:
    text = payload.decode("utf-8")
    notebook = json.loads(text)
    assert notebook["nbformat"] == 4
    assert "cells" in notebook
    assert notebook["cells"]
    try:
        import nbformat
        from nbformat.validator import validate

        validate(nbformat.reads(text, as_version=4))
    except ImportError:
        for cell in notebook["cells"]:
            assert cell["cell_type"] in {"markdown", "code"}
            assert "source" in cell
    for cell in notebook["cells"]:
        if cell.get("cell_type") != "code":
            continue
        ast.parse(_cell_text(cell))
    titles = _section_titles(notebook)
    assert titles == SECTION_TITLES
    assert titles.index(HOLDOUT_LOCK_TITLE) < titles.index(CANDIDATE_TITLE)
    assert titles.index(HOLDOUT_LOCK_TITLE) < titles.index(CV_TITLE)
    assert titles.index(WINNER_TITLE) < titles.index(FINAL_HOLDOUT_TITLE)
    assert titles.index(CV_TITLE) < titles.index(WINNER_TITLE)
    return notebook


def _assert_script_compiles(script: str) -> None:
    ast.parse(script)
    compile(script, "<reproduction.py>", "exec")


def _assert_no_secrets(text: str, *, dataset_location: str | None = None) -> None:
    for fragment in DOCUMENT_FORBIDDEN:
        assert fragment not in text
    for pattern in SECRET_PATTERNS:
        assert pattern.search(text) is None, pattern.pattern
    if dataset_location:
        assert dataset_location not in text
    assert '"object_key"' not in text
    assert "raw_customer_rows" not in text.lower()


def _assert_scientific_fidelity(spec: dict, notebook: dict, script: str) -> None:
    combined = script + "\n" + json.dumps(notebook)
    task = spec["task"]
    assert task["target_column"]
    assert f"TARGET_COLUMN = {task['target_column']!r}" in combined or (
        f"TARGET_COLUMN = '{task['target_column']}'" in combined
    )
    assert f"SEED = {task['seed']}" in combined
    for name in spec["modeled_features"]:
        assert name in combined
    for step in spec["preprocessing"]:
        class_name = (step.get("transformer_class") or "").rsplit(".", 1)[-1]
        if class_name:
            assert class_name in combined
    winner = spec["winner"]
    assert winner["candidate_id"]
    assert str(winner["candidate_id"]) in combined
    assert winner["fingerprint"] in combined
    for key, value in (winner.get("hyperparameters") or {}).items():
        assert key in combined
        assert str(value) in combined or repr(value) in combined
    validation = spec["validation_plan"]
    assert validation["strategy"] in combined
    assert str(validation["actual_folds"]) in combined
    assert spec["metric_plan"]["primary_metric"] in combined
    cells = _code_cells_by_title(notebook)
    holdout_cell = cells[FINAL_HOLDOUT_TITLE]
    metrics_cell = cells["Metrics"]
    winner_id = str(winner["candidate_id"])
    for candidate in spec["candidates"]:
        cid = str(candidate["id"])
        if cid == winner_id:
            continue
        assert cid not in holdout_cell
        assert cid not in metrics_cell
        if candidate["fingerprint"] != winner["fingerprint"]:
            assert candidate["fingerprint"] not in holdout_cell
            assert candidate["fingerprint"] not in metrics_cell


def _assert_api_safe(body: dict, serialized: str, dataset_location: str | None) -> None:
    _assert_no_secrets(serialized, dataset_location=dataset_location)
    dumped = json.dumps(body)
    assert '"object_key"' not in dumped
    assert '"bucket"' not in dumped
    for stage in body["stages"]:
        assert stage["key"] in EXPECTED_STAGE_KEYS
        generated = ((stage.get("generated_code") or {}).get("source") or "")
        if dataset_location:
            assert dataset_location not in generated
    assert [stage["key"] for stage in body["stages"]] == EXPECTED_STAGE_KEYS


def _try_execute_script(script: str, dataset_path: str, spec: dict) -> dict[str, float] | None:
    if "ColumnTransformer" not in script:
        return None
    source = script.replace(AUTHORIZED_DATASET_PATH_PLACEHOLDER, dataset_path)
    namespace: dict = {}
    exec(compile(source, "<reproduction.py>", "exec"), namespace, namespace)  # noqa: S102
    pipeline = namespace.get("pipeline")
    x_holdout = namespace.get("X_holdout")
    y_holdout = namespace.get("y_holdout")
    x_train = namespace.get("X_train")
    if pipeline is None or x_holdout is None or y_holdout is None:
        return None
    exclusions = list(namespace.get("leakage_exclusions") or [])
    frame = x_holdout.drop(columns=[name for name in exclusions if name in x_holdout.columns])
    if x_train is not None:
        keep = [name for name in x_train.columns if name in frame.columns]
        if keep:
            frame = frame[keep]
    task_type = (spec.get("task") or {}).get("task_type") or ""
    scores = _predict(pipeline, frame, classifier=task_type != "regression")
    if task_type == "regression":
        metrics = regression_metrics(y_holdout, scores)
    else:
        metrics = classification_metrics(y_holdout, scores)
    return {
        key: float(value)
        for key, value in metrics.items()
        if isinstance(value, (int, float))
    }


def _assert_metrics_within_tolerance(executed: dict[str, float], expected: dict, primary: str) -> None:
    assert primary in executed
    if primary not in expected:
        return
    got = executed[primary]
    want = float(expected[primary])
    bound = max(0.08, abs(want) * 0.25)
    assert abs(got - want) <= bound, f"{primary}: executed={got} persisted={want}"


def _download_documents(client, headers, workspace_id, run_id) -> tuple[bytes, bytes, dict]:
    base = f"/workspaces/{workspace_id}/pipeline-runs/{run_id}/model-build"
    build = client.get(base, headers=headers)
    assert build.status_code == 200, build.text
    body = build.json()
    notebook = client.get(f"{base}/reproduction/notebook/download", headers=headers)
    script = client.get(f"{base}/reproduction/script/download", headers=headers)
    assert notebook.status_code == 200, notebook.text
    assert script.status_code == 200, script.text
    return notebook.content, script.content, body


def _gate_one_run(
    client,
    db_session,
    *,
    headers,
    experiment,
    model_version,
    expected_task: str,
) -> None:
    db_session.refresh(experiment)
    location = experiment.dataset.location if experiment.dataset is not None else None
    poisoned = dict(experiment.result or {})
    poisoned.update(POISON)
    experiment.result = poisoned
    db_session.commit()

    notebook_bytes, script_bytes, body = _download_documents(
        client, headers, experiment.workspace_id, experiment.id
    )
    _assert_api_safe(body, json.dumps(body), location)
    assert body["generator_version"] == GENERATOR_VERSION
    assert body["reproduction_notebook"] is not None
    assert body["reproduction_script"] is not None
    notebook = _assert_valid_notebook(notebook_bytes)
    script = script_bytes.decode("utf-8")
    _assert_script_compiles(script)
    _assert_no_secrets(script, dataset_location=location)
    _assert_no_secrets(notebook_bytes.decode("utf-8"), dataset_location=location)

    spec_response = client.get(
        f"/workspaces/{experiment.workspace_id}/pipeline-runs/{experiment.id}/model-build/reproduction",
        headers=headers,
    )
    assert spec_response.status_code == 200, spec_response.text
    spec = spec_response.json()
    assert spec["task"]["task_type"] == expected_task
    assert spec["winner"]["candidate_id"] == str(model_version.selected_candidate_id)
    _assert_scientific_fidelity(spec, notebook, script)
    _assert_no_secrets(spec_response.text, dataset_location=location)

    holdout_stage = next(stage for stage in body["stages"] if stage["key"] == "final_holdout")
    holdout_evals = holdout_stage["configuration"].get("evaluations") or []
    winner_id = str(model_version.selected_candidate_id)
    for row in holdout_evals:
        assert row.get("candidate_id") in {winner_id, None}
    rejected = [row for row in spec["candidates"] if str(row["id"]) != winner_id]
    assert rejected
    for row in rejected:
        for evaluation in holdout_evals:
            assert evaluation.get("candidate_id") != str(row["id"])

    assert location, "production Labs path must persist an authorized dataset file"
    executed = _try_execute_script(script, location, spec)
    assert executed is not None, "standalone execution should be supported for this fixture"
    primary = spec["metric_plan"]["primary_metric"]
    _assert_metrics_within_tolerance(executed, spec["final_holdout"]["metrics"], primary)

    assert hashlib.sha256(notebook_bytes).hexdigest() == body["reproduction_notebook"]["content_digest"]
    assert hashlib.sha256(script_bytes).hexdigest() == body["reproduction_script"]["content_digest"]


def test_model_build_gate_binary_and_regression_production_path(
    client,
    db_session,
    monkeypatch,
    _rule_engine_only,
):
    owner = create_user(
        db_session,
        email=f"gate-owner-{uuid4().hex}@test.invalid",
        password="test-password",
        role=UserRole.WORKSPACE_OWNER,
        full_name="Gate Owner",
    )
    workspace = create_business_workspace(db_session, owner=owner, name="Model Build Gate Co")
    db_session.commit()
    client.headers.update(_headers(owner, workspace.id))

    _upload_b, _wf_b, binary_run, binary_version = labs_upload_and_train(
        client,
        db_session,
        monkeypatch,
        ordinary_binary(),
        filename="gate-binary.csv",
        target="outcome",
    )
    _upload_r, _wf_r, regression_run, regression_version = labs_upload_and_train(
        client,
        db_session,
        monkeypatch,
        regression(),
        filename="gate-regression.csv",
        target="revenue",
        category="Customer Value",
    )
    assert binary_run.workspace_id == workspace.id
    assert regression_run.workspace_id == workspace.id
    headers = _headers(owner, workspace.id)
    _gate_one_run(
        client,
        db_session,
        headers=headers,
        experiment=binary_run,
        model_version=binary_version,
        expected_task="binary",
    )
    _gate_one_run(
        client,
        db_session,
        headers=headers,
        experiment=regression_run,
        model_version=regression_version,
        expected_task="regression",
    )


def test_model_build_gate_all_users_and_cross_workspace_hide(
    client,
    db_session,
    monkeypatch,
    _rule_engine_only,
):
    owner = create_user(
        db_session,
        email=f"gate-auth-owner-{uuid4().hex}@test.invalid",
        password="test-password",
        role=UserRole.WORKSPACE_OWNER,
        full_name="Auth Gate Owner",
    )
    workspace = create_business_workspace(db_session, owner=owner, name="Auth Gate Workspace")
    db_session.commit()
    client.headers.update(_headers(owner, workspace.id))
    _upload, _wf, experiment, _version = labs_upload_and_train(
        client,
        db_session,
        monkeypatch,
        ordinary_binary(),
        filename="gate-auth-binary.csv",
        target="outcome",
    )

    business_admin = add_workspace_member(
        db_session,
        actor=owner,
        workspace_id=workspace.id,
        email=f"gate-admin-{uuid4().hex}@test.invalid",
        password="test-password",
        role=WorkspaceRole.WORKSPACE_ADMIN.value,
        full_name="Business Admin",
    )
    engineer = add_workspace_member(
        db_session,
        actor=owner,
        workspace_id=workspace.id,
        email=f"gate-ml-{uuid4().hex}@test.invalid",
        password="test-password",
        role=WorkspaceRole.ML_ENGINEER.value,
        full_name="ML Engineer",
    )
    viewer = add_workspace_member(
        db_session,
        actor=owner,
        workspace_id=workspace.id,
        email=f"gate-viewer-{uuid4().hex}@test.invalid",
        password="test-password",
        role=WorkspaceRole.VIEWER.value,
        full_name="Read-only Viewer",
    )
    legacy_admin = create_user(
        db_session,
        email=f"gate-bizadmin-{uuid4().hex}@test.invalid",
        password="test-password",
        role=UserRole.BUSINESS_ADMIN,
        full_name="Legacy Business Admin",
        workspace_id=workspace.id,
    )
    platform_admin = create_user(
        db_session,
        email=f"gate-dclab-admin-{uuid4().hex}@dclab.test",
        password="test-password",
        role=UserRole.DCLAB_ADMIN,
        full_name="DCLab Admin",
    )
    developer = create_user(
        db_session,
        email=f"gate-dclab-dev-{uuid4().hex}@dclab.test",
        password="test-password",
        role=UserRole.DCLAB_DEVELOPER,
        full_name="DCLab Developer",
    )
    personal_owner = create_user(
        db_session,
        email=f"gate-personal-{uuid4().hex}@test.invalid",
        password="test-password",
        role=UserRole.WORKSPACE_OWNER,
        full_name="Personal Owner",
    )
    personal_workspace = create_personal_workspace(
        db_session, owner=personal_owner, name="Personal Gate Lab"
    )
    personal_run = _stub_pipeline_run(db_session, personal_workspace.id)
    foreign_owner = create_user(
        db_session,
        email=f"gate-foreign-{uuid4().hex}@test.invalid",
        password="test-password",
        role=UserRole.WORKSPACE_OWNER,
        full_name="Workspace B Owner",
    )
    workspace_b = create_business_workspace(
        db_session, owner=foreign_owner, name="Workspace B"
    )
    db_session.commit()

    admin_user = db_session.get(User, business_admin.user_id)
    engineer_user = db_session.get(User, engineer.user_id)
    viewer_user = db_session.get(User, viewer.user_id)
    assert admin_user is not None
    assert engineer_user is not None
    assert viewer_user is not None

    path = f"/workspaces/{workspace.id}/pipeline-runs/{experiment.id}/model-build"
    readers = (
        owner,
        admin_user,
        engineer_user,
        viewer_user,
        legacy_admin,
        platform_admin,
        developer,
    )
    for reader in readers:
        headers = _headers(reader, workspace.id)
        build = client.get(path, headers=headers)
        assert build.status_code == 200, (reader.email, build.text)
        notebook = client.get(f"{path}/reproduction/notebook/download", headers=headers)
        script = client.get(f"{path}/reproduction/script/download", headers=headers)
        assert notebook.status_code == 200, reader.email
        assert script.status_code == 200, reader.email
        assert "object_key" not in notebook.text.lower()

    personal = client.get(
        f"/workspaces/{personal_workspace.id}/pipeline-runs/{personal_run.id}/model-build",
        headers=_headers(personal_owner, personal_workspace.id),
    )
    assert personal.status_code == 200, personal.text
    assert personal.json()["pipeline_run_id"] == str(personal_run.id)

    hidden = (
        client.get(path, headers=_headers(foreign_owner, workspace.id)).status_code,
        client.get(
            f"{path}/reproduction/notebook/download",
            headers=_headers(foreign_owner, workspace.id),
        ).status_code,
        client.get(
            f"/workspaces/{workspace_b.id}/pipeline-runs/{experiment.id}/model-build",
            headers=_headers(foreign_owner, workspace_b.id),
        ).status_code,
        client.get(
            f"/workspaces/{workspace_b.id}/pipeline-runs/{experiment.id}/model-build/reproduction/notebook/download",
            headers=_headers(foreign_owner, workspace_b.id),
        ).status_code,
        client.get(path, headers=_headers(personal_owner, workspace.id)).status_code,
        client.get(
            f"/workspaces/{workspace.id}/pipeline-runs/{personal_run.id}/model-build",
            headers=_headers(owner, workspace.id),
        ).status_code,
    )
    assert hidden == (404, 404, 404, 404, 404, 404)
