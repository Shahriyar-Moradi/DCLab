"""S0-P04A: SimulationRun / Insights are workspace-scoped; orphans are denied."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import uuid4

import pytest
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

from app.db.models import SimulationRun, UserRole
from app.services.auth_service import create_access_token, create_user
from app.services.project_service import create_project
from app.services.workspace_service import create_business_workspace

REPO_ROOT = Path(__file__).resolve().parents[3]
INSIGHT_QUERY = REPO_ROOT / "apps" / "api" / "app" / "services" / "insight_query.py"
HOOKS = REPO_ROOT / "apps" / "web" / "lib" / "application" / "hooks.ts"
SDK_HTTP = REPO_ROOT / "packages" / "dclab_client" / "dclab_client" / "_http.py"
CLI = REPO_ROOT / "apps" / "api" / "app" / "cli" / "main.py"


def _headers(user, workspace_id=None) -> dict[str, str]:
    headers = {"Authorization": f"Bearer {create_access_token(user)}"}
    if workspace_id is not None:
        headers["X-Workspace-Id"] = str(workspace_id)
    return headers


def _owner(db, prefix: str):
    return create_user(
        db,
        email=f"{prefix}-{uuid4().hex}@test.invalid",
        password="test-password",
        role=UserRole.WORKSPACE_OWNER,
        full_name=prefix,
    )


def _member_client(db, workspace, prefix: str):
    return create_user(
        db,
        email=f"{prefix}-{uuid4().hex}@test.invalid",
        password="test-password",
        role=UserRole.CLIENT_USER,
        full_name=prefix,
        workspace_id=workspace.id,
    )


def _hero_payload(use_case: str, external_id: str, *, hours_ago: int = 0) -> dict:
    return {
        "use_case": use_case,
        "model_version": f"{use_case}_sim_v1",
        "policy_version": f"{use_case}_sim_v1",
        "fusion": "single:gb_all",
        "heroes": [
            {
                "external_id": external_id,
                "agreement": 0.88,
                "action_key": "email",
                "recommended_action": "EMAIL",
                "expected_value": 500.0,
                "incremental_value": 120.0,
                "features": {"email_engagement": 0.4},
            }
        ],
        "sample_decisions": [],
    }


def _insert_run(db, *, workspace_id, use_case: str, external_id: str, hours_ago: int = 0, project_id=None):
    payload = _hero_payload(use_case, external_id, hours_ago=hours_ago)
    row = SimulationRun(
        workspace_id=workspace_id,
        project_id=project_id,
        use_case=use_case,
        model_version=payload["model_version"],
        policy_version=payload["policy_version"],
        fusion=payload["fusion"],
        payload=payload,
        created_at=datetime.now(UTC) - timedelta(hours=hours_ago),
    )
    db.add(row)
    db.commit()
    return row


def _subject_ids(response) -> set[str]:
    subjects: set[str] = set()
    for group in response.json()["categories"]:
        for item in group["insights"]:
            subjects.add(item["subject_id"])
    return subjects


def test_two_workspaces_with_same_use_case_and_subject_stay_isolated(
    client, db_session, admin_user
):
    owner_a = _owner(db_session, "sim-a")
    owner_b = _owner(db_session, "sim-b")
    workspace_a = create_business_workspace(db_session, owner=owner_a, name="Insights Alpha")
    workspace_b = create_business_workspace(db_session, owner=owner_b, name="Insights Beta")
    user_a = _member_client(db_session, workspace_a, "client-a")
    user_b = _member_client(db_session, workspace_b, "client-b")
    run_a = _insert_run(
        db_session,
        workspace_id=workspace_a.id,
        use_case="churn",
        external_id="C-SHARED",
    )
    run_b = _insert_run(
        db_session,
        workspace_id=workspace_b.id,
        use_case="churn",
        external_id="C-SHARED",
    )
    db_session.commit()

    alpha = client.get("/app/insights", headers=_headers(user_a, workspace_a.id))
    beta = client.get("/app/insights", headers=_headers(user_b, workspace_b.id))
    assert alpha.status_code == 200, alpha.text
    assert beta.status_code == 200, beta.text
    assert _subject_ids(alpha) == {"C-SHARED"}
    assert _subject_ids(beta) == {"C-SHARED"}

    cross = client.get("/app/insights", headers=_headers(user_a, workspace_b.id))
    assert cross.status_code == 403

    listed_b = client.get(
        "/admin/simulations/runs",
        headers=_headers(admin_user, workspace_b.id),
    )
    assert listed_b.status_code == 200, listed_b.text
    listed_ids = {item["id"] for item in listed_b.json()["items"]}
    assert str(run_b.id) in listed_ids
    assert str(run_a.id) not in listed_ids


def test_cross_workspace_simulation_id_is_404(client, db_session, admin_user):
    owner_a = _owner(db_session, "id-a")
    owner_b = _owner(db_session, "id-b")
    workspace_a = create_business_workspace(db_session, owner=owner_a, name="Id Alpha")
    workspace_b = create_business_workspace(db_session, owner=owner_b, name="Id Beta")
    run_a = _insert_run(
        db_session,
        workspace_id=workspace_a.id,
        use_case="churn",
        external_id="C-ALPHA",
    )
    db_session.commit()

    hidden = client.get(
        f"/admin/simulations/runs/{run_a.id}",
        headers=_headers(admin_user, workspace_b.id),
    )
    assert hidden.status_code == 404
    assert hidden.json()["detail"] == "simulation run not found"

    decision = client.get(
        f"/admin/simulations/runs/{run_a.id}/decisions/C-ALPHA",
        headers=_headers(admin_user, workspace_b.id),
    )
    assert decision.status_code == 404

    visible = client.get(
        f"/admin/simulations/runs/{run_a.id}",
        headers=_headers(admin_user, workspace_a.id),
    )
    assert visible.status_code == 200, visible.text
    assert visible.json()["workspace_id"] == str(workspace_a.id)


def test_unowned_archive_rows_never_reach_insights_or_derivatives(
    client, db_session, admin_user, auth_client
):
    orphan = SimulationRun(
        use_case="churn",
        model_version="churn_sim_v1",
        policy_version="churn_sim_v1",
        fusion="single:gb_all",
        payload=_hero_payload("churn", "C-ORPHAN"),
    )
    db_session.add(orphan)
    db_session.commit()

    insights = auth_client.get("/app/insights")
    assert insights.status_code == 200
    assert "C-ORPHAN" not in _subject_ids(insights)

    models = client.get("/admin/models", headers=_headers(admin_user))
    assert models.status_code == 200
    assert str(orphan.id) not in {row["id"] for row in models.json()}

    monitoring = client.get("/admin/monitoring", headers=_headers(admin_user))
    assert monitoring.status_code == 200
    assert str(orphan.id) not in {row["id"] for row in monitoring.json()["retrain_events"]}


def test_project_lineage_rejects_cross_workspace_project(client, db_session, admin_user, monkeypatch):
    owner_a = _owner(db_session, "proj-a")
    owner_b = _owner(db_session, "proj-b")
    workspace_a = create_business_workspace(db_session, owner=owner_a, name="Proj Alpha")
    workspace_b = create_business_workspace(db_session, owner=owner_b, name="Proj Beta")
    project_b = create_project(
        db_session, actor=owner_b, workspace_id=workspace_b.id, name="Beta case"
    )
    db_session.commit()

    monkeypatch.setattr(
        "app.api.simulations.run_use_case",
        lambda name: _hero_payload(name, "C-PROJ"),
    )
    denied = client.post(
        "/admin/simulations/run",
        json={"use_case": "churn", "project_id": str(project_b.id)},
        headers=_headers(admin_user, workspace_a.id),
    )
    assert denied.status_code == 404


def test_workspace_delete_restricted_while_scoped_run_exists(db_session):
    owner = _owner(db_session, "del")
    workspace = create_business_workspace(db_session, owner=owner, name="Delete Guard")
    _insert_run(
        db_session,
        workspace_id=workspace.id,
        use_case="churn",
        external_id="C-DEL",
    )
    owner.workspace_id = None
    db_session.commit()
    with pytest.raises(IntegrityError):
        db_session.execute(
            text("DELETE FROM workspaces WHERE id = :id"),
            {"id": workspace.id},
        )
        db_session.commit()
    db_session.rollback()


def test_project_delete_clears_project_id_only(db_session):
    owner = _owner(db_session, "pdel")
    workspace = create_business_workspace(db_session, owner=owner, name="Project Delete")
    project = create_project(
        db_session, actor=owner, workspace_id=workspace.id, name="Sim case"
    )
    run = _insert_run(
        db_session,
        workspace_id=workspace.id,
        project_id=project.id,
        use_case="churn",
        external_id="C-PDEL",
    )
    db_session.commit()
    db_session.execute(text("DELETE FROM projects WHERE id = :id"), {"id": project.id})
    db_session.commit()
    db_session.refresh(run)
    assert run.workspace_id == workspace.id
    assert run.project_id is None


def test_cannot_attach_project_without_workspace(db_session):
    owner = _owner(db_session, "ck")
    workspace = create_business_workspace(db_session, owner=owner, name="Check Co")
    project = create_project(
        db_session, actor=owner, workspace_id=workspace.id, name="Checked"
    )
    db_session.commit()
    db_session.add(
        SimulationRun(
            project_id=project.id,
            use_case="churn",
            model_version="churn_sim_v1",
            policy_version="churn_sim_v1",
            fusion="single:gb_all",
            payload={},
        )
    )
    with pytest.raises(IntegrityError):
        db_session.commit()
    db_session.rollback()


def test_source_contract_keeps_workspace_filter_and_no_cli_or_sdk_surface():
    insight_src = INSIGHT_QUERY.read_text(encoding="utf-8")
    assert "SimulationRun.workspace_id == workspace_id" in insight_src
    hooks_src = HOOKS.read_text(encoding="utf-8")
    assert 'workspaceQueryKey("insights")' in hooks_src
    sdk_src = SDK_HTTP.read_text(encoding="utf-8")
    assert "/app/insights" not in sdk_src
    assert "/admin/simulations" not in sdk_src
    cli_src = CLI.read_text(encoding="utf-8")
    assert "simulation_runs" not in cli_src
    assert "SimulationRun" not in cli_src


def test_admin_monitoring_and_registry_hide_other_workspace_simulations(
    client, db_session, admin_user
):
    owner_a = _owner(db_session, "mon-a")
    owner_b = _owner(db_session, "mon-b")
    workspace_a = create_business_workspace(db_session, owner=owner_a, name="Mon Alpha")
    workspace_b = create_business_workspace(db_session, owner=owner_b, name="Mon Beta")
    run_a = _insert_run(
        db_session, workspace_id=workspace_a.id, use_case="churn", external_id="C-MON-A"
    )
    run_b = _insert_run(
        db_session, workspace_id=workspace_b.id, use_case="churn", external_id="C-MON-B"
    )
    db_session.commit()

    models_a = client.get("/admin/models", headers=_headers(admin_user, workspace_a.id))
    assert models_a.status_code == 200
    model_ids = {row["id"] for row in models_a.json() if row["source"] == "simulation"}
    assert str(run_a.id) in model_ids
    assert str(run_b.id) not in model_ids

    monitor_a = client.get("/admin/monitoring", headers=_headers(admin_user, workspace_a.id))
    assert monitor_a.status_code == 200
    event_ids = {
        row["id"]
        for row in monitor_a.json()["retrain_events"]
        if row["source"] == "simulation"
    }
    assert str(run_a.id) in event_ids
    assert str(run_b.id) not in event_ids
