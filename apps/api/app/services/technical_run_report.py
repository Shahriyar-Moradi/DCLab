"""Canonical persisted technical report for an automatic Lab ML run."""

from __future__ import annotations

import json
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models import ClientLabUpload, Experiment, LabDecisionRecord


def _jsonable(value: Any) -> Any:
    return json.loads(json.dumps(value, default=str))


def build_technical_run_report(
    db: Session,
    *,
    upload: ClientLabUpload,
    experiment: Experiment | None,
    result: dict[str, Any],
    pipeline_log: dict[str, Any],
) -> dict[str, Any]:
    """Assemble one backend-owned source of truth from persisted run evidence."""
    decisions = db.scalars(
        select(LabDecisionRecord)
        .where(LabDecisionRecord.upload_id == upload.id)
        .order_by(LabDecisionRecord.column, LabDecisionRecord.created_at)
    ).all()
    candidates = list(result.get("candidates") or [])
    selection = dict(result.get("selection") or {})
    selected_id = selection.get("selected_candidate_id")
    selected = next((row for row in candidates if row.get("candidate_id") == selected_id), None)
    predictions = list(result.get("test_predictions") or [])
    experiment_id = str(experiment.id) if experiment is not None else None
    dataset_id = str(experiment.dataset_id) if experiment is not None else (
        str(upload.dataset_id) if upload.dataset_id is not None else None
    )
    artifact_dir = experiment.artifact_dir if experiment is not None else result.get("artifact_dir")
    artifact_root = str(artifact_dir) if artifact_dir else ""
    result_status = str(result.get("status") or "").lower()
    run_status = (
        "failed"
        if result_status == "failed"
        else (
            str(experiment.status).lower()
            if experiment is not None
            else str(upload.pipeline_status or result_status or "failed").lower()
        )
    )
    stage_history = list(pipeline_log.get("stages") or [])
    return _jsonable(
        {
            "schema_version": 1,
            "run": {
                "run_id": str(upload.id),
                "experiment_id": experiment_id,
                "status": run_status,
                "started_at": experiment.started_at if experiment is not None else None,
                "completed_at": experiment.ended_at if experiment is not None else None,
                "duration_seconds": result.get("duration_seconds"),
                "last_successful_stage": stage_history[-2] if len(stage_history) > 1 else None,
                "failed_stage": pipeline_log.get("failed_at"),
                "failure_reason": pipeline_log.get("reason") or result.get("error"),
            },
            "dataset": {
                "name": upload.original_filename,
                "category": upload.category,
                "record_count": upload.record_count,
                "dataset_id": dataset_id,
            },
            "raw_profile": result.get("analysis") or result.get("profile") or {},
            "data_quality": result.get("quality") or pipeline_log.get("quality") or {},
            "target_decision": pipeline_log.get("target") or {},
            "task": result.get("task") or {},
            "split": result.get("split") or {},
            "holdout_plan": result.get("holdout_plan") or {},
            "problem_profile": result.get("problem_profile") or {},
            "validation_plan": result.get("validation_plan") or {},
            "metric_plan": result.get("metric_plan") or {},
            "model_development_plan": result.get("model_development_plan") or {},
            "leakage": result.get("leakage") or {},
            "cleaning": result.get("cleaning") or pipeline_log.get("cleaning") or {},
            "column_roles": pipeline_log.get("column_roles") or {},
            "column_role_evidence": pipeline_log.get("column_role_evidence") or {},
            "feature_engineering": result.get("feature_engineering") or {},
            "preprocessing": result.get("preprocessing") or {},
            "candidate_models": candidates,
            "expected_candidate_ids": result.get("expected_candidate_ids") or [],
            "selection": selection,
            "final_model": selected,
            "final_fit": result.get("final_fit") or {},
            "final_test_evaluation": result.get("final_test_evaluation") or {},
            "predictions_summary": {
                "count": len(predictions),
                "artifact": "test_predictions.csv" if predictions else None,
            },
            "prediction_evidence": predictions,
            "artifacts": {
                "input": upload.stored_path,
                "model": f"{artifact_root}/model.joblib" if artifact_root else None,
                "result": f"{artifact_root}/result.json" if artifact_root else None,
                "predictions": f"{artifact_root}/test_predictions.csv" if artifact_root else None,
            },
            "stage_timings": pipeline_log.get("stage_timings") or [],
            "timing_semantics": {
                "ml_execution_total": "Job start through persistence of ML artifacts; excludes all verification and report generation.",
                "deterministic_verification": "One read-only deterministic verification pass; never verifies its own timing.",
                "report_generation": "Assembly of the canonical technical report after deterministic verification.",
                "llm_verification": "One requested OpenAI verification attempt, including its bounded provider retry.",
                "workflow_elapsed": "Job start through the latest completed workflow step represented by this report.",
            },
            "deterministic_verification": result.get("deterministic_verification") or {},
            "decision_records": [
                {
                    "id": str(row.id),
                    "column": row.column,
                    "source": row.source,
                    "rule_decision": row.rule_decision,
                    "final_decision": row.final_decision,
                    "fill_value": row.fill_value,
                    "validator_verdict": row.validator_verdict,
                    "prompt_version": row.prompt_version,
                    "evidence_snapshot": row.evidence_snapshot,
                    "raw_llm_output": row.raw_llm_output,
                    "created_at": row.created_at,
                }
                for row in decisions
            ],
        }
    )


def persisted_technical_report(experiment: Experiment | None) -> dict[str, Any] | None:
    if experiment is None or not isinstance(experiment.result, dict):
        return None
    report = experiment.result.get("technical_report")
    return report if isinstance(report, dict) else None
