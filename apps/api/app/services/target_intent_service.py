"""Canonical target-intent resolution for DCLab executions.

ProblemSpec.target_column is the persisted scientific intent. Compatibility
fields still exist for older Labs clients and must not silently contradict it:

* ClientLabUpload.explicit_target_column  (legacy form field)
* WorkflowRun.explicit_target             (run-level copy of the request)
* ExecutionRequest.request_spec.target_column

Precedence:

A. Linked ProblemSpec.target_column is set → authoritative. Validate against
   the selected DatasetVersion schema. Do not run heuristic inference or
   semantic target assistance.
B. A request supplies a target and ProblemSpec has no target → validate and
   use it as explicit intent. An unlocked draft ProblemSpec may be populated.
C. Request target conflicts with an authoritative ProblemSpec target →
   TARGET_INTENT_CONFLICT (409). Both names are returned; no dataset values.
D. No explicit intent → deterministic inference, then optional semantic
   assistance. An unresolved result is not a scientific pipeline failure.

Do not lower TARGET_CONFIDENCE_THRESHOLD or TARGET_MARGIN_THRESHOLD, and do
not pick the first tied candidate.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any
from uuid import UUID

import pandas as pd
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models import Dataset, DatasetColumn, ExecutionRequest, ProblemSpec, WorkflowRun
from app.domain.errors import TargetIntentConflictError, TargetNotInDatasetError
from app.domain.execution_requests import TARGET_CONFIRMATION_REQUIRED
from app.engine.lab.schema_inference import (
    TargetChoice,
    choose_target_deterministically,
    metric_for_task,
    profile_named_target,
)
from app.services.lab_decision_ledger import resolve_target_selection

UNRESOLVED_TARGET_STATUS = "unresolved"
TARGET_SOURCE_USER = "user"
TARGET_SOURCE_SYSTEM = "system"
TARGET_SOURCE_SEMANTIC = "semantic"
_RAW_VALUE_KEYS = frozenset(
    {
        "sample_values",
        "samples",
        "rows",
        "records",
        "dataset_rows",
        "csv",
        "contents",
        "file_bytes",
    }
)


def normalize_target_name(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def strip_raw_dataset_values(payload: Any) -> Any:
    """Drop raw cell samples from persisted or error payloads. Names stay."""

    if isinstance(payload, dict):
        return {
            key: strip_raw_dataset_values(value)
            for key, value in payload.items()
            if key not in _RAW_VALUE_KEYS
        }
    if isinstance(payload, list):
        return [strip_raw_dataset_values(item) for item in payload]
    return payload


def public_target_payload(
    choice: TargetChoice, *, status: str | None = None
) -> dict[str, Any]:
    payload = strip_raw_dataset_values(choice.audit_dict())
    payload.pop("raw_llm_output", None)
    payload["target_column"] = choice.column
    if status is not None:
        payload["status"] = status
    elif choice.column:
        payload["status"] = "resolved"
    else:
        payload["status"] = UNRESOLVED_TARGET_STATUS
    return payload


def dataset_schema_column_names(
    dataset: Dataset | None,
    *,
    frame_columns: Sequence[str] | None = None,
) -> list[str]:
    names: list[str] = []
    if dataset is not None:
        stored = list(dataset.columns or [])
        if stored:
            names = [row.name for row in stored if row.name]
        schema = dataset.schema_json if isinstance(dataset.schema_json, dict) else {}
        if not names:
            names = [
                str(item.get("name"))
                for item in list(schema.get("columns") or [])
                if item.get("name")
            ]
    if names:
        return names
    return [str(name) for name in list(frame_columns or []) if str(name).strip()]


def coalesce_request_targets(values: Sequence[Any]) -> str | None:
    """One request-side name. Distinct non-empty values are a typed conflict."""

    seen: list[str] = []
    for raw in values:
        name = normalize_target_name(raw)
        if name is None:
            continue
        if name not in seen:
            seen.append(name)
    if not seen:
        return None
    if len(seen) > 1:
        raise TargetIntentConflictError(
            problem_spec_target=None,
            requested_target=seen[0],
            conflicting_target=seen[1],
        )
    return seen[0]


def load_workspace_problem_spec(
    db: Session,
    *,
    workspace_id: UUID,
    problem_spec_id: UUID | None,
    project_id: UUID | None = None,
) -> ProblemSpec | None:
    """Return the spec only when it belongs to this workspace (and project)."""

    if problem_spec_id is None:
        return None
    spec = db.get(ProblemSpec, problem_spec_id)
    if spec is None or spec.workspace_id != workspace_id:
        return None
    if project_id is not None and spec.project_id != project_id:
        return None
    return spec


def requested_target_from_execution(
    db: Session,
    *,
    upload_explicit_target: str | None,
    workflow_run: WorkflowRun | None,
) -> str | None:
    values: list[Any] = [
        workflow_run.explicit_target if workflow_run is not None else None,
        upload_explicit_target,
    ]
    if workflow_run is not None:
        request = db.scalar(
            select(ExecutionRequest).where(
                ExecutionRequest.workspace_id == workflow_run.workspace_id,
                ExecutionRequest.workflow_run_id == workflow_run.id,
            )
        )
        if request is not None and isinstance(request.request_spec, dict):
            values.append(request.request_spec.get("target_column"))
    return coalesce_request_targets(values)


def validate_declared_target_intent(
    *,
    schema_names: Sequence[str],
    problem_spec: ProblemSpec | None,
    requested_target: str | None,
) -> None:
    """Enforce Case A–C without inference. Safe to call at ingest time."""

    spec_target = normalize_target_name(
        problem_spec.target_column if problem_spec is not None else None
    )
    request_target = normalize_target_name(requested_target)
    if spec_target and request_target and spec_target != request_target:
        raise TargetIntentConflictError(
            problem_spec_target=spec_target,
            requested_target=request_target,
        )
    chosen = spec_target or request_target
    names = [str(name) for name in schema_names if str(name).strip()]
    if chosen and names and chosen not in names:
        raise TargetNotInDatasetError(chosen)


def validate_request_spec_target_intent(
    db: Session,
    *,
    workspace_id: UUID,
    request_spec: dict[str, Any],
    project_id: UUID | None = None,
) -> None:
    spec_id = normalize_target_name(request_spec.get("problem_spec_id"))
    problem_spec_id: UUID | None = None
    if spec_id is not None:
        try:
            problem_spec_id = UUID(spec_id)
        except ValueError:
            problem_spec_id = None
    spec = load_workspace_problem_spec(
        db,
        workspace_id=workspace_id,
        problem_spec_id=problem_spec_id,
        project_id=project_id,
    )
    validate_declared_target_intent(
        schema_names=[],
        problem_spec=spec,
        requested_target=normalize_target_name(request_spec.get("target_column")),
    )


def upload_has_unresolved_target(upload: Any) -> bool:
    if str(getattr(upload, "pipeline_status", "") or "") == "needs_input":
        return True
    log = upload.pipeline_log if isinstance(getattr(upload, "pipeline_log", None), dict) else {}
    target = log.get("target") if isinstance(log.get("target"), dict) else {}
    return (
        target.get("status") == UNRESOLVED_TARGET_STATUS
        and target.get("column") is None
        and target.get("target_column") is None
    )


def audit_source_for_choice(choice: TargetChoice) -> str:
    if choice.source == "llm" or choice.intent_source == "llm":
        return TARGET_SOURCE_SEMANTIC
    return TARGET_SOURCE_SYSTEM


def target_confirmation_required_payload(choice: TargetChoice) -> dict[str, Any]:
    """Structured wait payload. Column names and scores only — no row values."""

    candidates: list[dict[str, Any]] = []
    for item in list(choice.candidates or []):
        name = normalize_target_name(getattr(item, "column", None))
        if name is None:
            continue
        try:
            confidence = float(getattr(item, "confidence", 0.0) or 0.0)
        except (TypeError, ValueError):
            confidence = 0.0
        candidates.append({"name": name, "confidence": confidence})
    recommended = candidates[0]["name"] if candidates else None
    margin = 0.0
    if len(candidates) >= 2:
        margin = float(candidates[0]["confidence"]) - float(candidates[1]["confidence"])
    try:
        confidence = float(choice.confidence or 0.0)
    except (TypeError, ValueError):
        confidence = float(candidates[0]["confidence"]) if candidates else 0.0
    if confidence == 0.0 and candidates:
        confidence = float(candidates[0]["confidence"])
    reason = "Multiple possible prediction targets were detected."
    return strip_raw_dataset_values(
        {
            "code": TARGET_CONFIRMATION_REQUIRED,
            "reason": reason,
            "recommended_column": recommended,
            "confidence": confidence,
            "margin": margin,
            "possible_columns": candidates,
        }
    )


def confirmed_target_from_request(request: ExecutionRequest | None) -> str | None:
    if request is None:
        return None
    summary = request.result_summary if isinstance(request.result_summary, dict) else {}
    spec = request.request_spec if isinstance(request.request_spec, dict) else {}
    return coalesce_request_targets(
        [
            summary.get("target_column"),
            summary.get("confirmed_target"),
            spec.get("target_column"),
        ]
    )


def _choice_for_canonical_column(
    frame: pd.DataFrame,
    column: str,
    *,
    intent_source: str,
    reason: str,
) -> TargetChoice:
    task_type, evidence = profile_named_target(frame, column)
    return TargetChoice(
        column=column,
        reason=reason,
        task_type=task_type,
        evaluation_metric=metric_for_task(task_type) if task_type else None,
        confidence=1.0,
        source="explicit",
        intent_source=intent_source,
        evidence=evidence,
        candidates=[],
        validator_verdict="not_run",
    )


def _require_column_in_schema(column: str, schema_names: Sequence[str], frame: pd.DataFrame) -> None:
    names = list(schema_names)
    if names and column not in names:
        raise TargetNotInDatasetError(column)
    if column not in frame.columns:
        raise TargetNotInDatasetError(column)


def resolve_execution_target(
    db: Session,
    *,
    workspace_id: UUID,
    frame: pd.DataFrame,
    columns: Sequence[str],
    dataset: Dataset | None = None,
    problem_spec_id: UUID | None = None,
    project_id: UUID | None = None,
    requested_target: str | None = None,
    upload_id: UUID | None = None,
) -> TargetChoice:
    """Single target-intent resolver used by auto-train and related callers."""

    spec = load_workspace_problem_spec(
        db,
        workspace_id=workspace_id,
        problem_spec_id=problem_spec_id,
        project_id=project_id,
    )
    spec_target = normalize_target_name(spec.target_column if spec is not None else None)
    request_target = normalize_target_name(requested_target)
    schema_names = dataset_schema_column_names(dataset, frame_columns=columns)
    validate_declared_target_intent(
        schema_names=schema_names,
        problem_spec=spec,
        requested_target=request_target,
    )

    if spec_target is not None:
        _require_column_in_schema(spec_target, schema_names, frame)
        return _choice_for_canonical_column(
            frame,
            spec_target,
            intent_source="problem_spec",
            reason="canonical ProblemSpec target",
        )

    if request_target is not None:
        _require_column_in_schema(request_target, schema_names, frame)
        choice = choose_target_deterministically(
            frame, list(columns), explicit_target=request_target
        )
        choice.intent_source = "request"
        if choice.column is None and "not present" in choice.reason:
            raise TargetNotInDatasetError(request_target)
        return choice

    choice = resolve_target_selection(
        frame,
        list(columns),
        explicit_target=None,
        db=db,
        upload_id=upload_id,
    )
    if choice.column is None:
        choice.intent_source = "unresolved"
    elif choice.source == "llm":
        choice.intent_source = "llm"
    elif not choice.intent_source:
        choice.intent_source = "rule"
    return choice


def column_names_from_dataset_id(db: Session, dataset_id: UUID | None) -> list[str]:
    if dataset_id is None:
        return []
    dataset = db.get(Dataset, dataset_id)
    if dataset is None:
        return []
    stored = list(
        db.scalars(
            select(DatasetColumn.name).where(DatasetColumn.dataset_id == dataset.id)
        )
    )
    if stored:
        return [str(name) for name in stored]
    return dataset_schema_column_names(dataset)
