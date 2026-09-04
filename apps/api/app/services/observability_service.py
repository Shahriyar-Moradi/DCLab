"""Safe append-only observability for workflow-scoped ML pipeline runs."""

from __future__ import annotations

import hashlib
import json
import logging
import re
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.db.models import (
    ClientLabUpload,
    Experiment,
    LlmInvocation,
    MlRunEvent,
    ModelVersion,
    WorkflowRun,
)
from app.db.session import get_session_factory

logger = logging.getLogger(__name__)

SEMANTIC_PURPOSES = frozenset(
    {"semantic_target", "semantic_missing_value", "semantic_column_type"}
)
AUDIT_PURPOSES = frozenset({"pipeline_audit_routine", "pipeline_audit_deep"})
LLM_PURPOSES = SEMANTIC_PURPOSES | AUDIT_PURPOSES

_BLOCKED_KEY_PARTS = (
    "api_key",
    "apikey",
    "authorization",
    "password",
    "secret",
    "access_token",
    "refresh_token",
    "raw_rows",
    "sample_rows",
    "train_provenance",
    "validation_provenance",
    "test_source_rows",
    "train_source_rows",
    "prediction_evidence",
    "raw_llm_output",
    "fill_value",
    "stored_path",
)
_SECRET_PATTERNS = (
    re.compile(r"\bsk-[A-Za-z0-9_-]{8,}\b"),
    re.compile(r"(?i)\bbearer\s+[A-Za-z0-9._~+/=-]{8,}"),
    re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}"),
    re.compile(r"\+?\d[\d()\-\s.]{8,}\d"),
)
_MAX_STRING = 1000
_MAX_LIST = 50
_MAX_KEYS = 100
_MAX_DEPTH = 6
_MAX_PAYLOAD_BYTES = 32_768


def _redaction_summary() -> dict[str, Any]:
    return {
        "redacted_fields": 0,
        "redacted_strings": 0,
        "truncated_strings": 0,
        "truncated_lists": 0,
        "truncated_objects": 0,
        "raw_rows_stored": False,
        "secrets_stored": False,
    }


def sanitize_observability_payload(value: Any) -> tuple[dict[str, Any], dict[str, Any]]:
    """Return bounded JSON and a summary. Secret/provenance keys never survive."""

    summary = _redaction_summary()

    def clean(item: Any, depth: int) -> Any:
        if depth > _MAX_DEPTH:
            summary["truncated_objects"] += 1
            return "[MAX_DEPTH]"
        if isinstance(item, dict):
            result: dict[str, Any] = {}
            for index, (raw_key, raw_value) in enumerate(item.items()):
                if index >= _MAX_KEYS:
                    summary["truncated_objects"] += 1
                    break
                key = str(raw_key)[:128]
                lowered = key.lower()
                safe_redaction_counter = (
                    lowered.endswith("_redacted")
                    and isinstance(raw_value, (bool, int, float))
                )
                if (
                    any(part in lowered for part in _BLOCKED_KEY_PARTS)
                    and not safe_redaction_counter
                ):
                    summary["redacted_fields"] += 1
                    result[key] = "[REDACTED]"
                else:
                    result[key] = clean(raw_value, depth + 1)
            return result
        if isinstance(item, (list, tuple, set)):
            values = list(item)
            if len(values) > _MAX_LIST:
                summary["truncated_lists"] += 1
                values = values[:_MAX_LIST]
            return [clean(entry, depth + 1) for entry in values]
        if isinstance(item, str):
            text = item
            for pattern in _SECRET_PATTERNS:
                text, count = pattern.subn("[REDACTED]", text)
                summary["redacted_strings"] += count
            if len(text) > _MAX_STRING:
                summary["truncated_strings"] += 1
                text = text[:_MAX_STRING] + "…"
            return text
        if item is None or isinstance(item, (bool, int, float)):
            return item
        return clean(str(item), depth + 1)

    cleaned = clean(value if isinstance(value, dict) else {"value": value}, 0)
    encoded = json.dumps(cleaned, sort_keys=True, default=str).encode("utf-8")
    if len(encoded) > _MAX_PAYLOAD_BYTES:
        summary["truncated_objects"] += 1
        cleaned = {
            "summary": "Payload exceeded the observability size limit.",
            "sha256": hashlib.sha256(encoded).hexdigest(),
            "original_size_bytes": len(encoded),
        }
    return cleaned, summary


def evidence_digest(value: Any) -> str:
    encoded = json.dumps(value, sort_keys=True, default=str, separators=(",", ":")).encode()
    return hashlib.sha256(encoded).hexdigest()


def pipeline_context_for_upload(
    db: Session, upload_id: UUID
) -> tuple[ClientLabUpload, WorkflowRun, Experiment] | None:
    upload = db.get(ClientLabUpload, upload_id)
    if upload is None or upload.experiment_id is None:
        return None
    pipeline = db.get(Experiment, upload.experiment_id)
    if pipeline is None or pipeline.workflow_run_id is None:
        return None
    workflow_run = db.get(WorkflowRun, pipeline.workflow_run_id)
    if workflow_run is None:
        return None
    return upload, workflow_run, pipeline


def append_ml_run_event(
    db: Session,
    *,
    workspace_id: UUID,
    workflow_run_id: UUID,
    experiment_id: UUID,
    stage: str,
    event_type: str,
    status: str,
    payload: dict[str, Any] | None = None,
    duration_ms: float | None = None,
    timestamp: datetime | None = None,
    commit: bool = True,
) -> MlRunEvent:
    pipeline = db.scalar(
        select(Experiment)
        .where(Experiment.id == experiment_id)
        .with_for_update()
    )
    if pipeline is None:
        raise LookupError("pipeline run not found")
    if (
        pipeline.workspace_id != workspace_id
        or pipeline.workflow_run_id != workflow_run_id
    ):
        raise ValueError("event lineage does not match pipeline run")
    last_sequence = db.scalar(
        select(func.max(MlRunEvent.sequence)).where(
            MlRunEvent.experiment_id == experiment_id
        )
    )
    safe_payload, redaction = sanitize_observability_payload(payload or {})
    if any(redaction.values()):
        safe_payload["_redaction"] = redaction
    row = MlRunEvent(
        workspace_id=workspace_id,
        workflow_run_id=workflow_run_id,
        experiment_id=experiment_id,
        sequence=int(last_sequence or 0) + 1,
        stage=stage[:80],
        event_type=event_type[:80],
        status=status[:32],
        timestamp=timestamp or datetime.now(UTC),
        duration_ms=duration_ms,
        payload=safe_payload,
    )
    db.add(row)
    if commit:
        db.commit()
        db.refresh(row)
    else:
        db.flush()
    return row


class PipelineRunObserver:
    """Failure-isolated event callback suitable for long-running ML work."""

    def __init__(self, workspace_id: UUID, workflow_run_id: UUID, experiment_id: UUID):
        self.workspace_id = workspace_id
        self.workflow_run_id = workflow_run_id
        self.experiment_id = experiment_id

    @classmethod
    def for_upload(cls, db: Session, upload_id: UUID) -> PipelineRunObserver | None:
        context = pipeline_context_for_upload(db, upload_id)
        if context is None:
            return None
        _upload, workflow_run, pipeline = context
        return cls(pipeline.workspace_id, workflow_run.id, pipeline.id)

    def emit(
        self,
        stage: str,
        event_type: str,
        status: str,
        payload: dict[str, Any] | None = None,
        duration_ms: float | None = None,
    ) -> None:
        session = get_session_factory()()
        try:
            append_ml_run_event(
                session,
                workspace_id=self.workspace_id,
                workflow_run_id=self.workflow_run_id,
                experiment_id=self.experiment_id,
                stage=stage,
                event_type=event_type,
                status=status,
                payload=payload,
                duration_ms=duration_ms,
            )
        except Exception:  # observability cannot change ML behavior
            session.rollback()
            logger.exception("could not persist ML run event for %s", self.experiment_id)
        finally:
            session.close()

    def callback(self, event_type: str, payload: dict[str, Any]) -> None:
        data = dict(payload)
        stage = str(data.pop("stage", event_type))
        status = str(data.pop("status", "completed"))
        duration_ms = data.pop("duration_ms", None)
        self.emit(stage, event_type, status, data, duration_ms)


def create_llm_invocation(
    db: Session,
    *,
    upload_id: UUID,
    purpose: str,
    mode: str,
    prompt_version: str,
    schema_version: str | int,
    evidence: Any,
    llm_used: bool,
    reason: str,
    status: str,
    validator_verdict: str,
    provider: str | None = None,
    model: str | None = None,
    safe_output: dict[str, Any] | None = None,
    final_decision: dict[str, Any] | None = None,
    started_at: datetime | None = None,
    completed_at: datetime | None = None,
    latency_ms: float | None = None,
) -> LlmInvocation | None:
    if purpose not in LLM_PURPOSES:
        raise ValueError("unsupported LLM invocation purpose")
    if purpose in SEMANTIC_PURPOSES and mode != "semantic_decision":
        raise ValueError("semantic LLM purpose requires semantic_decision mode")
    if purpose in AUDIT_PURPOSES and mode not in {"routine", "deep", "pipeline_audit"}:
        raise ValueError("pipeline audit purpose requires an audit mode")
    context = pipeline_context_for_upload(db, upload_id)
    if context is None:
        return None
    _upload, workflow_run, pipeline = context
    safe_result = None
    result_redaction = _redaction_summary()
    if safe_output is not None:
        safe_result, result_redaction = sanitize_observability_payload(safe_output)
    safe_decision = None
    decision_redaction = _redaction_summary()
    if final_decision is not None:
        safe_decision, decision_redaction = sanitize_observability_payload(final_decision)
    redaction = {
        "input_evidence_persisted": False,
        "raw_rows_stored": False,
        "secrets_stored": False,
        "safe_output": result_redaction,
        "final_decision": decision_redaction,
    }
    row = LlmInvocation(
        workspace_id=pipeline.workspace_id,
        workflow_run_id=workflow_run.id,
        experiment_id=pipeline.id,
        purpose=purpose,
        provider=provider if llm_used else None,
        model=model if llm_used else None,
        mode=mode,
        prompt_version=prompt_version,
        schema_version=str(schema_version),
        input_evidence_digest=evidence_digest(evidence),
        redaction_summary=redaction,
        llm_used=llm_used,
        reason=reason[:1024],
        status=status,
        validator_verdict=validator_verdict[:1024],
        safe_output=safe_result,
        final_decision=safe_decision,
        latency_ms=latency_ms,
        started_at=started_at or datetime.now(UTC),
        completed_at=completed_at,
    )
    db.add(row)
    db.flush()
    return row


def finalize_llm_invocation(
    invocation: LlmInvocation,
    *,
    status: str,
    validator_verdict: str,
    reason: str,
    safe_output: dict[str, Any] | None,
    final_decision: dict[str, Any] | None = None,
    latency_ms: float | None = None,
) -> None:
    cleaned_output = None
    output_redaction = _redaction_summary()
    if safe_output is not None:
        cleaned_output, output_redaction = sanitize_observability_payload(safe_output)
    cleaned_decision = None
    decision_redaction = _redaction_summary()
    if final_decision is not None:
        cleaned_decision, decision_redaction = sanitize_observability_payload(final_decision)
    invocation.status = status
    invocation.validator_verdict = validator_verdict[:1024]
    invocation.reason = reason[:1024]
    invocation.safe_output = cleaned_output
    invocation.final_decision = cleaned_decision
    invocation.latency_ms = latency_ms
    invocation.completed_at = datetime.now(UTC)
    invocation.redaction_summary = {
        **dict(invocation.redaction_summary or {}),
        "safe_output": output_redaction,
        "final_decision": decision_redaction,
    }


def pipeline_summary(db: Session, experiment: Experiment) -> dict[str, Any]:
    latest_sequence = db.scalar(
        select(func.max(MlRunEvent.sequence)).where(
            MlRunEvent.experiment_id == experiment.id
        )
    )
    event_count = db.scalar(
        select(func.count(MlRunEvent.id)).where(
            MlRunEvent.experiment_id == experiment.id
        )
    )
    llm_rows = list(
        db.scalars(
            select(LlmInvocation)
            .where(LlmInvocation.experiment_id == experiment.id)
            .order_by(LlmInvocation.created_at, LlmInvocation.id)
        )
    )
    model_version = db.scalar(
        select(ModelVersion).where(ModelVersion.pipeline_run_id == experiment.id)
    )
    return {
        "id": experiment.id,
        "workspace_id": experiment.workspace_id,
        "workflow_run_id": experiment.workflow_run_id,
        "pipeline_name": experiment.pipeline_name,
        "pipeline_index": experiment.pipeline_index,
        "pipeline_purpose": experiment.pipeline_purpose,
        "status": experiment.status,
        "failure_reason": experiment.failure_reason,
        "started_at": experiment.started_at,
        "ended_at": experiment.ended_at,
        "latest_sequence": int(latest_sequence or 0),
        "event_count": int(event_count or 0),
        "candidate_count": len(experiment.candidates),
        "model_version_id": model_version.id if model_version is not None else None,
        "semantic_llm_count": sum(row.purpose in SEMANTIC_PURPOSES for row in llm_rows),
        "pipeline_audit_count": sum(row.purpose in AUDIT_PURPOSES for row in llm_rows),
    }
