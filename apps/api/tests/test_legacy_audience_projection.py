"""S0-P04C response contracts for persisted, pre-redaction diagnostics."""

from __future__ import annotations

from copy import deepcopy
from datetime import UTC, datetime
from uuid import uuid4

from app.db.models import ClientLabRun, DEFAULT_WORKSPACE_ID
from app.services.audience_projection import (
    PUBLIC_FAILURE,
    artifact_read,
    invocation_read,
    public_diagnostic,
)
from dclab_client.types import Artifact as SdkArtifact


def test_public_projection_is_bounded_and_keeps_safe_facts():
    stored = {
        "status": "failed",
        "metric": 0.43,
        "failure_reason": "Traceback (most recent call last): /Users/operator/run.py",
        "details": {
            "handler_key": "internal.worker.run",
            "storage_key": "private/tenant-b/artifact.pkl",
            "system_prompt": "secret instructions",
            "provider_body": {"output_text": "private response"},
            "stack_trace": "traceback",
            "message": "Traceback (most recent call last): /app/worker.py",
            "email": "other-tenant@example.com",
            "safe_count": 3,
        },
    }
    original = deepcopy(stored)
    assert public_diagnostic(stored) == {
        "status": "failed",
        "metric": 0.43,
        "failure_reason": PUBLIC_FAILURE,
        "details": {"email": "[REDACTED]", "message": "[REDACTED]", "safe_count": 3},
    }
    assert stored == original  # read projection never mutates scientific evidence


def test_artifact_sdk_contract_keeps_id_but_omits_storage_key():
    raw = {
        "id": uuid4(),
        "workspace_id": DEFAULT_WORKSPACE_ID,
        "project_id": None,
        "artifact_type": "report",
        "provider": "local",
        "object_key": "workspaces/private/other-tenant/report.json",
        "content_digest": "sha256:test",
        "mime_type": "application/json",
        "size_bytes": 12,
        "created_at": datetime.now(UTC),
    }
    response = artifact_read(raw)
    sdk = SdkArtifact.model_validate(response.model_dump())
    assert sdk.id == raw["id"]
    assert sdk.object_key == ""
    assert raw["object_key"] == "workspaces/private/other-tenant/report.json"


def test_legacy_llm_invocation_snapshot_omits_provider_and_prompt_bodies():
    now = datetime.now(UTC)
    raw = {
        "id": uuid4(),
        "workspace_id": DEFAULT_WORKSPACE_ID,
        "workflow_run_id": uuid4(),
        "experiment_id": uuid4(),
        "purpose": "semantic_target",
        "provider": "openai",
        "model": "test-model",
        "mode": "advisory",
        "prompt_version": "v1",
        "schema_version": "v1",
        "input_evidence_digest": "sha256:test",
        "redaction_summary": {"rows": 4, "storage_key": "private/row.csv"},
        "llm_used": True,
        "reason": "Traceback (most recent call last): /tmp/provider.py",
        "status": "failed",
        "validator_verdict": "reject",
        "safe_output": {"score": 0.7, "system_prompt": "secret instructions"},
        "final_decision": {"decision": "abstain", "provider_body": {"secret": "body"}},
        "latency_ms": 12.0,
        "input_tokens": 4,
        "output_tokens": 2,
        "total_tokens": 6,
        "estimated_cost": 0.01,
        "started_at": now,
        "completed_at": now,
        "created_at": now,
    }
    projected = invocation_read(raw)
    assert projected["reason"] == PUBLIC_FAILURE
    assert projected["redaction_summary"] == {"rows": 4}
    assert projected["safe_output"] == {"score": 0.7}
    assert projected["final_decision"] == {"decision": "abstain"}
    assert raw["safe_output"]["system_prompt"] == "secret instructions"


def test_legacy_client_trial_failure_is_generic_and_storage_unchanged(
    auth_client, db_session, client_user
):
    raw_failure = "Traceback (most recent call last): /Users/operator/private.py"
    row = ClientLabRun(
        workspace_id=DEFAULT_WORKSPACE_ID,
        requested_by=client_user.id,
        use_case="churn",
        category="Revenue",
        data_source="sample",
        row_count=10,
        status="failed",
        failure_reason=raw_failure,
        insights=[],
    )
    db_session.add(row)
    db_session.commit()
    detail = auth_client.get(f"/app/labs/runs/{row.id}")
    assert detail.status_code == 200, detail.text
    assert detail.json()["failure_reason"] == PUBLIC_FAILURE
    listing = auth_client.get("/app/labs/runs")
    assert listing.status_code == 200, listing.text
    assert next(item for item in listing.json() if item["id"] == str(row.id))["failure_reason"] == PUBLIC_FAILURE
    db_session.refresh(row)
    assert row.failure_reason == raw_failure
