"""Tenant keys and LLM attribution (ADR 0006 §7, Alembic 0064) on the live schema."""

from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace
from uuid import uuid4

import pytest
from sqlalchemy import select, text
from sqlalchemy.exc import DBAPIError

from app.db.models import ClientLabUpload, LlmInvocation, WorkflowRunInput
from app.db.retention_guard import allow_workspace_deletion
from app.services.lineage_service import create_pipeline_run, create_workflow_run
from test_data_model_lineage import make_lineage_setup


def _rejects(db, sql: str, match: str, **params) -> None:
    with pytest.raises(DBAPIError, match=match):
        db.execute(text(sql), params)
        db.commit()
    db.rollback()


def _run(db, setup, side: str):
    run = create_workflow_run(
        db,
        workspace_id=setup[side].id,
        workflow=setup[f"{side}_workflow"],
        requester=setup[f"{side}_admin"],
        trigger_type="manual",
        source_type="dataset",
    )
    pipeline = create_pipeline_run(
        db,
        workflow_run=run,
        environment=setup["env"],
        dataset=setup[f"{side}_dataset"],
        task=setup["task"],
    )
    upload = ClientLabUpload(
        workspace_id=setup[side].id,
        category="Revenue",
        original_filename=f"{side}.csv",
        stored_path=f"/tmp/{side}.csv",
        kind="spreadsheet",
        record_count=2,
        fields_noticed=["a"],
        has_named_fields=True,
        experiment_id=pipeline.id,
    )
    db.add(upload)
    db.flush()
    invocation = LlmInvocation(
        workspace_id=setup[side].id,
        workflow_run_id=run.id,
        experiment_id=pipeline.id,
        project_id=pipeline.project_id,
        purpose="pipeline_audit_routine",
        mode="routine",
        prompt_version="v1",
        schema_version="1",
        input_evidence_digest="a" * 64,
        redaction_summary={},
        llm_used=False,
        reason="disabled",
        status="not_used",
        validator_verdict="not_run",
        started_at=datetime.now(UTC),
    )
    db.add(invocation)
    db.commit()
    return SimpleNamespace(run=run, pipeline=pipeline, upload=upload, invocation=invocation)


@pytest.fixture
def tenants(db_session, tmp_path):
    setup = make_lineage_setup(db_session, tmp_path)
    return SimpleNamespace(
        setup=setup,
        alpha=_run(db_session, setup, "alpha"),
        beta=_run(db_session, setup, "beta"),
    )


_VERIFICATION_SQL = (
    "INSERT INTO ml_run_verifications (id, workspace_id, run_id, experiment_id, "
    "llm_invocation_id, audit_mode, deterministic_status, deterministic_checks, "
    "deterministic_schema_version, llm_provider, llm_model, llm_status, prompt_version, "
    "schema_version, input_digest, redaction_summary, started_at) VALUES (:id, :ws, :run, "
    ":experiment, :invocation, 'routine', 'PASS', '[]'::jsonb, 1, 'openai', 'm', 'pending', "
    "'v1', 1, :digest, '{}'::jsonb, now())"
)

_INVOCATION_SQL = (
    "INSERT INTO llm_invocations (id, workspace_id, workflow_run_id, experiment_id, "
    "project_id, agent_run_id, provider_kind, purpose, mode, prompt_version, schema_version, "
    "input_evidence_digest, redaction_summary, llm_used, reason, status, validator_verdict, "
    "started_at, data_class, outcome_scope) VALUES (gen_random_uuid(), :ws, :run, :experiment, "
    ":project, :agent, :kind, 'pipeline_audit_routine', 'routine', 'v1', '1', :digest, "
    "'{}'::jsonb, false, 'r', 'not_used', 'not_run', now(), 'aggregates', 'cv')"
)


def test_writers_stamp_workspace_and_project(db_session, tenants):
    inputs = db_session.execute(
        select(WorkflowRunInput).where(WorkflowRunInput.workflow_run_id == tenants.alpha.run.id)
    ).scalars().all()
    assert inputs and {row.workspace_id for row in inputs} == {tenants.setup["alpha"].id}
    assert tenants.alpha.invocation.project_id == tenants.alpha.pipeline.project_id


def test_new_composite_fks_reject_cross_workspace_rows(db_session, tenants):
    a, b = tenants.alpha, tenants.beta
    alpha_ws, beta_ws = tenants.setup["alpha"].id, tenants.setup["beta"].id
    input_sql = (
        "INSERT INTO workflow_run_inputs (id, workspace_id, workflow_run_id, dataset_id, "
        "input_role, position) VALUES (gen_random_uuid(), :ws, :run, :dataset, 'extra', 0)"
    )
    _rejects(
        db_session,
        input_sql,
        "fk_workflow_run_inputs_workspace_dataset",
        ws=alpha_ws,
        run=a.run.id,
        dataset=tenants.setup["beta_dataset"].id,
    )
    _rejects(
        db_session,
        input_sql,
        "fk_workflow_run_inputs_workspace_workflow_run",
        ws=beta_ws,
        run=a.run.id,
        dataset=tenants.setup["beta_dataset"].id,
    )
    _rejects(
        db_session,
        "INSERT INTO experiment_test_predictions (id, workspace_id, experiment_id, row_index, "
        "record_id, predicted_value) VALUES (gen_random_uuid(), :ws, :experiment, 0, 'r', "
        "'1'::jsonb)",
        "fk_experiment_test_predictions_workspace_experiment",
        ws=beta_ws,
        experiment=a.pipeline.id,
    )
    for overrides, constraint in (
        ({"ws": beta_ws}, "fk_ml_run_verifications_workspace_run"),
        ({"experiment": b.pipeline.id}, "fk_ml_run_verifications_workspace_experiment"),
        ({"invocation": b.invocation.id}, "fk_ml_run_verifications_workspace_llm_invocation"),
    ):
        params = {
            "id": uuid4(),
            "ws": alpha_ws,
            "run": a.upload.id,
            "experiment": a.pipeline.id,
            "invocation": None,
            "digest": "b" * 64,
            **overrides,
        }
        _rejects(db_session, _VERIFICATION_SQL, constraint, **params)


def test_llm_invocations_must_be_attributable(db_session, tenants):
    alpha_ws = tenants.setup["alpha"].id
    base = {
        "ws": alpha_ws,
        "run": None,
        "experiment": None,
        "project": None,
        "agent": None,
        "kind": None,
        "digest": "c" * 64,
    }
    _rejects(db_session, _INVOCATION_SQL, "ck_llm_invocations_attributed", **base)
    _rejects(
        db_session,
        _INVOCATION_SQL,
        "ck_llm_invocations_provider_kind",
        **{**base, "project": tenants.alpha.pipeline.project_id, "kind": "openai"},
    )
    _rejects(
        db_session,
        _INVOCATION_SQL,
        "fk_llm_invocations_workspace_project",
        **{**base, "project": tenants.beta.pipeline.project_id},
    )
    # 0071: agent_run_id is a composite FK to agent_runs of the same workspace.
    _rejects(
        db_session,
        _INVOCATION_SQL,
        "fk_llm_invocations_workspace_agent_run",
        **{**base, "agent": uuid4(), "kind": "agent_runtime"},
    )
    agent_run_id = db_session.execute(
        text(
            "INSERT INTO agent_runs (id, workspace_id, project_id, kind, agent_key, agent_version, "
            "runtime, runtime_version, purpose, subject_kind, policy_digest, tool_catalog_digest, "
            "data_class, outcome_scope, status, limits) VALUES (gen_random_uuid(), :ws, :project, "
            "'specialist', 'experiment_critic', '1', 'fake', 'fake==1', 'experiment.review', "
            "'project', repeat('c', 64), repeat('d', 64), 'aggregates', 'cv', 'completed', "
            "'{}'::jsonb) RETURNING id"
        ),
        {"ws": alpha_ws, "project": tenants.alpha.pipeline.project_id},
    ).scalar_one()
    db_session.commit()
    for extra in (
        {"project": tenants.alpha.pipeline.project_id, "kind": "semantic_decision"},
        {"agent": agent_run_id, "kind": "agent_runtime"},
    ):
        db_session.execute(text(_INVOCATION_SQL), {**base, **extra})
        db_session.commit()


def test_verification_keeps_workspace_when_invocation_is_deleted(db_session, tenants):
    a = tenants.alpha
    verification_id = uuid4()
    db_session.execute(
        text(_VERIFICATION_SQL),
        {
            "id": verification_id,
            "ws": tenants.setup["alpha"].id,
            "run": a.upload.id,
            "experiment": a.pipeline.id,
            "invocation": a.invocation.id,
            "digest": "d" * 64,
        },
    )
    db_session.commit()
    # Invocations are retention-guarded (0071): only the deletion GUC permits this.
    allow_workspace_deletion(db_session, tenants.setup["alpha"].id)
    db_session.execute(
        text("DELETE FROM llm_invocations WHERE id = :id"), {"id": a.invocation.id}
    )
    db_session.commit()
    row = db_session.execute(
        text(
            "SELECT workspace_id, llm_invocation_id, experiment_id FROM ml_run_verifications "
            "WHERE id = :id"
        ),
        {"id": verification_id},
    ).one()
    assert row.workspace_id == tenants.setup["alpha"].id
    assert row.llm_invocation_id is None
    assert row.experiment_id == a.pipeline.id


def test_create_llm_invocation_records_project_and_provider_kind(db_session, tenants):
    from app.services.observability_service import create_llm_invocation, provider_kind_for

    assert provider_kind_for(mode="routine", llm_used=False) == "deterministic_fallback"
    assert provider_kind_for(mode="semantic_decision", llm_used=False) == "deterministic_fallback"
    assert provider_kind_for(mode="semantic_decision", llm_used=True) == "semantic_decision"
    assert provider_kind_for(mode="deep", llm_used=True) == "llm_provider"

    expected = {}
    for purpose, mode, llm_used in (
        ("semantic_target", "semantic_decision", True),
        ("semantic_target", "semantic_decision", False),
        ("pipeline_audit_routine", "routine", True),
        ("pipeline_audit_deep", "deep", False),
    ):
        row = create_llm_invocation(
            db_session,
            upload_id=tenants.alpha.upload.id,
            purpose=purpose,
            mode=mode,
            prompt_version="v1",
            schema_version=1,
            evidence={"columns": 2},
            llm_used=llm_used,
            reason="test",
            status="completed" if llm_used else "not_used",
            validator_verdict="ok",
            provider="openai" if llm_used else None,
        )
        expected[row.id] = provider_kind_for(mode=mode, llm_used=llm_used)
    db_session.commit()
    rows = db_session.execute(
        select(LlmInvocation).where(LlmInvocation.id.in_(list(expected)))
    ).scalars().all()
    assert {row.id: row.provider_kind for row in rows} == expected
    assert {row.project_id for row in rows} == {tenants.alpha.pipeline.project_id}
