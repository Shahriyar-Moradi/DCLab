from __future__ import annotations

import json
from copy import deepcopy
from types import SimpleNamespace
from uuid import uuid4

import numpy as np
import pandas as pd
import pytest
from sqlalchemy import select

from app.db.models import (
    ClientLabUpload,
    Experiment,
    LabDecisionRecord,
    LlmInvocation,
    MlRunEvent,
    MlRunVerification,
    UserRole,
    Workspace,
    WorkspaceCapability,
    WorkflowRun,
)
from app.domain.ml_verification import PipelineAuditReport, PipelineAuditStage
from app.engine.lab.auto_prepare import plan_missing_values
from app.engine.lab.llm_client import MissingValueDecision
from app.engine.types import SearchConfig, TaskSpec
from app.services.auth_service import create_access_token, create_user
from app.services.auto_train_service import run_auto_train_job
from app.services.observability_service import (
    append_ml_run_event,
    create_llm_invocation,
    sanitize_observability_payload,
)
from app.services.pipeline_audit_service import request_pipeline_verification


def _classification_frame(n: int = 120) -> pd.DataFrame:
    rng = np.random.default_rng(90210)
    tenure = rng.integers(1, 72, n)
    monthly = rng.uniform(20, 130, n)
    segment = rng.choice(["smb", "midmarket", "enterprise"], n)
    churn = (monthly + tenure * 0.7 + rng.normal(0, 20, n) > 105).astype(int)
    return pd.DataFrame(
        {
            "tenure": tenure,
            "monthly": monthly,
            "segment": segment,
            "churn": churn,
        }
    )


def _post_and_run(auth_client, db_session, monkeypatch, *, fail_family: str | None = None):
    monkeypatch.setattr(
        "app.services.client_lab_upload_service.enqueue_auto_train", lambda _id: None
    )
    if fail_family is not None:
        from app.engine.experiments import runner

        original = runner.make_model

        def make_model(model_family, *args, **kwargs):
            if model_family == fail_family:
                raise ValueError("forced candidate failure for observability")
            return original(model_family, *args, **kwargs)

        monkeypatch.setattr(runner, "make_model", make_model)
    response = auth_client.post(
        "/app/labs/uploads",
        data={"category": "Revenue", "target_column": "churn"},
        files={
            "file": (
                "observability.csv",
                _classification_frame().to_csv(index=False).encode(),
                "text/csv",
            )
        },
    )
    assert response.status_code == 200, response.text
    upload = db_session.get(ClientLabUpload, response.json()["id"])
    experiment_id = upload.experiment_id
    run_auto_train_job(db_session, upload.id)
    db_session.expire_all()
    upload = db_session.get(ClientLabUpload, upload.id)
    assert upload.experiment_id == experiment_id
    assert upload.pipeline_status == "completed"
    pipeline = db_session.get(Experiment, experiment_id)
    workflow_run = db_session.get(WorkflowRun, pipeline.workflow_run_id)
    return upload, workflow_run, pipeline


class _AuditProvider:
    def audit(self, *, evidence, model):
        self.last_usage = {
            "input_tokens": 120,
            "output_tokens": 30,
            "total_tokens": 150,
        }
        return PipelineAuditReport(
            overall_status="NOT_VERIFIABLE",
            summary="Conservative advisory result for observability testing.",
            stages=[
                PipelineAuditStage(
                    stage="pipeline",
                    status="NOT_VERIFIABLE",
                    summary="The supplied bounded evidence was reviewed.",
                    evidence_refs=[evidence["allowed_evidence_refs"][0]],
                )
            ],
            confidence=0.8,
            warnings=["Additional manual review is appropriate."],
            recommendations=["Retain deterministic checks as authoritative."],
        )


def _audit_settings():
    return SimpleNamespace(
        pipeline_llm_verifier_enabled=True,
        pipeline_llm_verifier_api_key="sk-observability-test-secret",
        pipeline_llm_verifier_model="gpt-5.6-luna",
        pipeline_llm_verifier_deep_model="gpt-5.6-terra",
        pipeline_llm_timeout_seconds=1.0,
    )


def test_real_pipeline_events_llm_contract_and_tenant_apis(
    auth_client,
    admin_client,
    db_session,
    monkeypatch,
):
    upload, workflow_run, pipeline = _post_and_run(
        auth_client, db_session, monkeypatch
    )
    events = list(
        db_session.scalars(
            select(MlRunEvent)
            .where(MlRunEvent.experiment_id == pipeline.id)
            .order_by(MlRunEvent.sequence)
        )
    )
    assert [row.sequence for row in events] == list(range(1, len(events) + 1))
    stages = {row.stage for row in events}
    assert {
        "ingestion",
        "profiling_eda",
        "target_task",
        "structural_cleaning",
        "holdout_lock",
        "train_only_decisions",
        "missing_value_decisions",
        "column_roles",
        "feature_engineering",
        "preprocessing_configuration",
        "candidate_training",
        "cross_validation",
        "model_selection",
        "winner_lock",
        "final_fit",
        "final_test",
        "predictions",
        "artifact_persistence",
        "deterministic_verification",
        "report",
        "terminal",
    } <= stages

    def sequence(event_type: str) -> int:
        return next(row.sequence for row in events if row.event_type == event_type)

    assert sequence("operation_completed") < sequence("cv_fold_started")
    holdout_sequence = next(
        row.sequence
        for row in events
        if row.stage == "holdout_lock" and row.status == "completed"
    )
    assert holdout_sequence < sequence("cv_fold_started")
    assert sequence("winner_locked") < sequence("final_test_started")

    fold_started = [row for row in events if row.event_type == "cv_fold_started"]
    fold_completed = [row for row in events if row.event_type == "cv_fold_completed"]
    assert fold_started
    assert len(fold_started) == len(fold_completed)
    assert {
        (row.payload["candidate_id"], row.payload["fold_number"])
        for row in fold_started
    } == {
        (row.payload["candidate_id"], row.payload["fold_number"])
        for row in fold_completed
    }

    winner = pipeline.result["selection"]["selected_candidate_id"]
    final_tests = [row for row in events if row.event_type == "final_test_completed"]
    assert len(final_tests) == 1
    assert final_tests[0].payload["candidate_id"] == winner
    assert final_tests[0].payload["evaluation_count"] == 1

    semantic = list(
        db_session.scalars(
            select(LlmInvocation).where(
                LlmInvocation.experiment_id == pipeline.id,
                LlmInvocation.purpose.like("semantic_%"),
            )
        )
    )
    assert semantic
    assert any(
        not row.llm_used
        and row.reason == "LLM used: NO — deterministic evidence was sufficient."
        for row in semantic
    )
    assert all(not row.purpose.startswith("pipeline_audit_") for row in semantic)

    from app.services import lab_decision_ledger

    monkeypatch.setattr(lab_decision_ledger, "_agent_configured", lambda: True)
    monkeypatch.setattr(
        lab_decision_ledger,
        "request_decision",
        lambda evidence, prompt_version: MissingValueDecision(
            action="impute_median",
            evidence_field="missing_fraction",
            fill_value=None,
            rationale="Median is robust for this bounded numeric evidence.",
            confidence=0.9,
        ),
    )
    decision_frame = pd.DataFrame(
        {
            "feature": [None] * 10 + list(range(90)),
            "churn": [0, 1] * 50,
        }
    )
    plan = plan_missing_values(decision_frame, ["feature"])
    lab_decision_ledger.record_missing_value_decisions(
        db_session,
        upload.id,
        decision_frame,
        plan,
        "churn",
    )
    db_session.commit()
    decision_record = db_session.scalar(
        select(LabDecisionRecord).where(LabDecisionRecord.upload_id == upload.id)
    )
    used_invocation = db_session.get(
        LlmInvocation, decision_record.llm_invocation_id
    )
    assert used_invocation.llm_used is True
    assert used_invocation.purpose == "semantic_missing_value"
    assert used_invocation.provider == "openai"
    assert used_invocation.validator_verdict == "accept"
    assert used_invocation.final_decision["final_decision"] == "impute_median"

    attempt = request_pipeline_verification(
        db_session,
        upload.id,
        deep=True,
        provider=_AuditProvider(),
        settings=_audit_settings(),
    )
    audit_invocation = db_session.get(LlmInvocation, attempt.llm_invocation_id)
    assert isinstance(attempt, MlRunVerification)
    assert audit_invocation.purpose == "pipeline_audit_deep"
    assert audit_invocation.mode == "deep"
    assert audit_invocation.model == "gpt-5.6-terra"
    assert audit_invocation.purpose not in {
        "semantic_target",
        "semantic_missing_value",
        "semantic_column_type",
    }
    assert audit_invocation.safe_output["deterministic_status"]
    assert audit_invocation.safe_output["advisory_status"] == "NOT_VERIFIABLE"
    assert audit_invocation.safe_output["warnings"]
    assert audit_invocation.safe_output["confidence"] == 0.8
    assert audit_invocation.safe_output["recommendations"]
    assert audit_invocation.safe_output["evidence_digest"] == attempt.input_digest
    assert audit_invocation.safe_output["redaction_summary"] == attempt.redaction_summary
    assert audit_invocation.input_tokens == 120
    assert audit_invocation.output_tokens == 30
    assert audit_invocation.total_tokens == 150

    secret = "sk-this-must-never-be-persisted"
    secret_event = append_ml_run_event(
        db_session,
        workspace_id=pipeline.workspace_id,
        workflow_run_id=workflow_run.id,
        experiment_id=pipeline.id,
        stage="security_test",
        event_type="bounded_payload",
        status="completed",
        payload={
            "api_key": secret,
            "authorization": f"Bearer {secret}",
            "contact": "person@example.com / +1 (415) 555-1234",
            "raw_rows": [{"customer": "real-row"}] * 100,
            "safe_count": 100,
        },
    )
    persisted = db_session.get(MlRunEvent, secret_event.id)
    serialized_event = json.dumps(persisted.payload)
    serialized_invocations = json.dumps(
        [
            {
                "redaction": row.redaction_summary,
                "output": row.safe_output,
                "decision": row.final_decision,
            }
            for row in db_session.scalars(
                select(LlmInvocation).where(LlmInvocation.experiment_id == pipeline.id)
            )
        ]
    )
    assert secret not in serialized_event
    assert "real-row" not in serialized_event
    assert "person@example.com" not in serialized_event
    assert "555-1234" not in serialized_event
    assert _audit_settings().pipeline_llm_verifier_api_key not in serialized_invocations
    secret_event.payload = {"changed": True}
    with pytest.raises(ValueError, match="append-only"):
        db_session.commit()
    db_session.rollback()

    summary = admin_client.get(f"/business/observatory/pipeline-runs/{pipeline.id}/summary")
    assert summary.status_code == 200
    assert summary.json()["pipeline_audit_count"] == 1
    history = admin_client.get(f"/business/observatory/pipeline-runs/{pipeline.id}/events")
    assert history.status_code == 200
    after = history.json()[5]["sequence"]
    incremental = admin_client.get(
        f"/business/observatory/pipeline-runs/{pipeline.id}/events/incremental",
        params={"after_sequence": after},
    )
    assert incremental.status_code == 200
    assert all(row["sequence"] > after for row in incremental.json())
    llm_list = admin_client.get(
        f"/business/observatory/pipeline-runs/{pipeline.id}/llm-invocations"
    )
    assert llm_list.status_code == 200
    assert {row["purpose"] for row in llm_list.json()} >= {
        "semantic_target",
        "pipeline_audit_deep",
    }
    detail = admin_client.get(
        f"/business/observatory/llm-invocations/{audit_invocation.id}"
    )
    assert detail.status_code == 200
    pipelines = admin_client.get(
        f"/business/observatory/workflow-runs/{workflow_run.id}/pipelines"
    )
    assert pipelines.status_code == 200
    assert [row["id"] for row in pipelines.json()] == [str(pipeline.id)]
    assert admin_client.get(
        f"/admin/observatory/pipeline-runs/{pipeline.id}/summary"
    ).status_code == 200
    assert auth_client.get(
        f"/business/observatory/pipeline-runs/{pipeline.id}/summary"
    ).status_code == 403
    assert auth_client.get(
        f"/business/observatory/pipeline-runs/{pipeline.id}/summary",
        headers={"Authorization": ""},
    ).status_code == 401
    assert auth_client.get(
        f"/admin/observatory/pipeline-runs/{pipeline.id}/summary"
    ).status_code == 403

    platform_reader = create_user(
        db_session,
        email=f"observatory-platform-reader-{uuid4().hex}@test.invalid",
        password="test-password",
        role=UserRole.DCLAB_DEVELOPER,
    )
    business_reader = create_user(
        db_session,
        email=f"observatory-business-reader-{uuid4().hex}@test.invalid",
        password="test-password",
        role=UserRole.BUSINESS_DEVELOPER,
        workspace_id=pipeline.workspace_id,
    )
    db_session.add(
        WorkspaceCapability(
            workspace_id=pipeline.workspace_id,
            capability="pipeline_monitor",
            enabled=True,
            configuration={},
        )
    )
    db_session.commit()
    assert auth_client.get(
        f"/admin/observatory/pipeline-runs/{pipeline.id}/summary",
        headers={"Authorization": f"Bearer {create_access_token(platform_reader)}"},
    ).status_code == 200
    assert auth_client.get(
        f"/business/observatory/pipeline-runs/{pipeline.id}/summary",
        headers={"Authorization": f"Bearer {create_access_token(business_reader)}"},
    ).status_code == 200

    other_workspace = Workspace(
        slug=f"observatory-other-{uuid4().hex}", name="Other Observatory Tenant"
    )
    db_session.add(other_workspace)
    db_session.flush()
    other_user = create_user(
        db_session,
        email=f"observatory-other-{uuid4().hex}@test.invalid",
        password="test-password",
        role=UserRole.BUSINESS_ADMIN,
        workspace_id=other_workspace.id,
    )
    db_session.commit()
    foreign = auth_client.get(
        f"/business/observatory/pipeline-runs/{pipeline.id}/summary",
        headers={
            "Authorization": f"Bearer {create_access_token(other_user)}",
            "X-Workspace-Id": str(other_workspace.id),
        },
    )
    assert foreign.status_code == 404


def test_admin_pipeline_events_return_ordered_and_incremental_history(
    auth_client,
    admin_client,
    db_session,
    monkeypatch,
):
    _upload, _workflow_run, pipeline = _post_and_run(
        auth_client, db_session, monkeypatch
    )
    persisted_sequences = list(
        db_session.scalars(
            select(MlRunEvent.sequence)
            .where(MlRunEvent.experiment_id == pipeline.id)
            .order_by(MlRunEvent.sequence)
        )
    )
    assert len(persisted_sequences) > 2

    history = admin_client.get(
        f"/admin/observatory/pipeline-runs/{pipeline.id}/events"
    )
    assert history.status_code == 200, history.text
    assert [row["sequence"] for row in history.json()] == persisted_sequences

    after_sequence = persisted_sequences[-2]
    incremental = admin_client.get(
        f"/admin/observatory/pipeline-runs/{pipeline.id}/events/incremental",
        params={"after_sequence": after_sequence},
    )
    assert incremental.status_code == 200, incremental.text
    assert [row["sequence"] for row in incremental.json()] == [
        sequence for sequence in persisted_sequences if sequence > after_sequence
    ]


def test_failed_candidate_is_emitted_without_failing_pipeline(
    auth_client, db_session, monkeypatch
):
    _upload, _workflow_run, pipeline = _post_and_run(
        auth_client,
        db_session,
        monkeypatch,
        fail_family="random_forest",
    )
    failures = list(
        db_session.scalars(
            select(MlRunEvent).where(
                MlRunEvent.experiment_id == pipeline.id,
                MlRunEvent.event_type == "candidate_failed",
            )
        )
    )
    assert failures
    forced = next(
        row for row in failures if row.payload["model_family"] == "random_forest"
    )
    assert forced.status == "failed"
    assert "forced candidate failure" in forced.payload["reason"]
    assert pipeline.status == "COMPLETED"
    assert pipeline.model_version is not None


def test_safe_payload_is_bounded_and_observer_callback_does_not_change_results(
    tmp_path,
):
    secret = "sk-abcdefghijklmnop"
    safe, summary = sanitize_observability_payload(
        {
            "secret": secret,
            "nested": {"authorization": f"Bearer {secret}"},
            "raw_rows": [{"value": index} for index in range(1000)],
            "message": f"do not persist {secret} person@example.com +1 (415) 555-1234",
        }
    )
    serialized = json.dumps(safe)
    assert secret not in serialized
    assert "person@example.com" not in serialized
    assert "555-1234" not in serialized
    assert "\"value\"" not in serialized
    assert summary["redacted_fields"] >= 2
    assert len(serialized.encode()) < 32_768

    frame = _classification_frame(100)
    task = TaskSpec(
        id="observability-parity",
        name="Observability parity",
        task_type="binary",
        target="churn",
        entity_id=None,
        evaluation_metric="pr_auc",
        feature_groups={"features": ["tenure", "monthly", "segment"]},
        validation_strategy="stratified",
        column_roles={
            "numerical": ["tenure", "monthly"],
            "categorical": ["segment"],
        },
    )
    config = SearchConfig(strategy="open_ingest", seed=42)
    from app.engine.experiments.runner import run_experiment

    without_observer = run_experiment(
        deepcopy(frame),
        task,
        config,
        artifact_dir=tmp_path / "without-observer",
    )
    seen: list[tuple[str, dict]] = []
    with_observer = run_experiment(
        deepcopy(frame),
        task,
        config,
        artifact_dir=tmp_path / "with-observer",
        on_event=lambda event_type, payload: seen.append((event_type, payload)),
    )
    assert seen
    observed_selection = dict(with_observer["selection"])
    baseline_selection = dict(without_observer["selection"])
    observed_selection.pop("locked_at", None)
    baseline_selection.pop("locked_at", None)
    assert observed_selection == baseline_selection
    assert with_observer["test_metrics"] == without_observer["test_metrics"]
    assert with_observer["test_predictions"] == without_observer["test_predictions"]


def test_create_llm_invocation_rejects_mismatched_purpose_and_mode(db_session):
    from datetime import UTC, datetime

    common = dict(
        db=db_session,
        upload_id=uuid4(),
        prompt_version="test",
        schema_version="1",
        evidence={"ok": True},
        llm_used=False,
        reason="contract test",
        status="not_used",
        validator_verdict="not_run",
        started_at=datetime.now(UTC),
    )
    with pytest.raises(ValueError, match="semantic"):
        create_llm_invocation(
            purpose="semantic_target",
            mode="deep",
            **common,
        )
    with pytest.raises(ValueError, match="audit"):
        create_llm_invocation(
            purpose="pipeline_audit_routine",
            mode="semantic_decision",
            **common,
        )
