"""Stage: persist the completed ML evidence, lineage and model version."""

from __future__ import annotations

import logging
import time
from dataclasses import asdict
from datetime import UTC, datetime
from typing import Any

import pandas as pd
from pydantic import BaseModel, ConfigDict
from sqlalchemy import select

from app.db.models import (
    ClientLabUpload,
    Experiment,
    ExperimentCandidate,
    ModelAsset,
    ModelVersion,
    WorkflowRun,
)
from app.engine.lab.auto_prepare import ColumnRoles, MissingValuePlan
from app.engine.modeling.holdout_planner import HoldoutPlan
from app.engine.modeling.leakage_auditor import ModelDevelopmentPlan
from app.engine.validation.split_assignment import SplitAssignment
from app.services.auto_train.context import RunContext, StageHalt
from app.services.evidence_lock_service import (
    lock_scientific_evidence,
    missing_scientific_evidence,
)

logger = logging.getLogger(__name__)


class PersistenceInput(BaseModel):
    model_config = ConfigDict(arbitrary_types_allowed=True)

    experiment: Experiment
    result: dict[str, Any]
    reuse_locked_run: bool
    boost_family_used: str | None
    workflow_run: WorkflowRun | None
    frame: pd.DataFrame
    profile: dict[str, Any]
    quality: dict[str, Any]
    cleaning_log: dict[str, Any]
    target_evidence: dict[str, Any]
    feature_report: dict[str, Any]
    fe_transformations: list[dict[str, Any]]
    num_cols: list[str]
    cat_cols: list[str]
    modeled_cols: list[str]
    initial_roles: ColumnRoles
    final_roles: ColumnRoles
    transformed_datetime: set[str]
    identifier_cols: list[str]
    column_role_evidence: dict[str, Any]
    entity_column: str | None
    missing_plan: MissingValuePlan
    holdout_plan: HoldoutPlan
    development_plan: ModelDevelopmentPlan
    # The run's SplitPlan map (ADR 0006 §3); None for runs without a plan.
    split_assignment: SplitAssignment | None = None


class PersistenceOutput(BaseModel):
    model_config = ConfigDict(arbitrary_types_allowed=True)

    upload: ClientLabUpload
    experiment: Experiment
    result: dict[str, Any]
    log: dict[str, Any]


def _investigation(inp: PersistenceInput, result: dict[str, Any]) -> dict[str, Any]:
    """The five trust checks of this run. Test rows enter only as row hashes of the
    model columns (duplicate check); no check reads a holdout value or label."""

    from app.engine.investigate import (
        investigate,
        investigation_payload,
        row_hashes,
        run_evidence_from_result,
    )
    from app.engine.validation.splits import SOURCE_ROW_COLUMN

    try:
        evidence = run_evidence_from_result(result)
        frame = inp.frame
        split = result.get("split") if isinstance(result.get("split"), dict) else {}
        train_rows, test_rows = split.get("train_source_rows"), split.get("test_source_rows")
        if (train_rows is None or test_rows is None) and inp.split_assignment is not None:
            train_rows, test_rows = list(inp.split_assignment.train_folds), list(inp.split_assignment.holdout_rows)
        target = (result.get("task") or {}).get("target")
        columns = [
            name for name in (evidence.winner_features or tuple(inp.modeled_cols))
            if name in frame.columns and name not in {SOURCE_ROW_COLUMN, target}
        ]
        train_features = test_hashes = None
        if columns and train_rows is not None and test_rows is not None:
            source = frame[SOURCE_ROW_COLUMN] if SOURCE_ROW_COLUMN in frame.columns else frame.index.to_series()
            train_features = frame.loc[source.isin(set(train_rows)).to_numpy(), columns]
            test_hashes = row_hashes(frame.loc[source.isin(set(test_rows)).to_numpy(), columns])
        findings = investigate(evidence, train_features=train_features, test_row_hashes=test_hashes)
        return investigation_payload(findings)
    except Exception:  # noqa: BLE001 - a check bug must not fail a trained run
        logger.exception("trust checks failed for experiment %s", inp.experiment.id)
        return {"version": None, "error": "investigation_failed", "checks": []}


def run_persistence(ctx: RunContext, inp: PersistenceInput) -> PersistenceOutput:
    db = ctx.db
    experiment = inp.experiment
    result = inp.result
    reuse_locked_run = inp.reuse_locked_run
    workflow_run = inp.workflow_run
    frame = inp.frame
    profile = inp.profile
    quality = inp.quality
    cleaning_log = inp.cleaning_log
    fe_transformations = inp.fe_transformations
    num_cols = inp.num_cols
    cat_cols = inp.cat_cols
    initial_roles = inp.initial_roles
    final_roles = inp.final_roles
    transformed_datetime = inp.transformed_datetime
    entity_column = inp.entity_column
    missing_plan = inp.missing_plan
    holdout_plan = inp.holdout_plan
    development_plan = inp.development_plan

    dropped_columns = list(
        dict.fromkeys(list(cleaning_log.get("dropped_columns") or []) + list(missing_plan.dropped_columns))
    )
    cleaning_decisions = {
        item["column"]: item
        for item in (cleaning_log.get("missing_value_plan") or {}).get("column_decisions") or []
    }
    for item in missing_plan.column_decisions:
        cleaning_decisions[item.column] = asdict(item)
    log = {
        "analysis": profile,
        "eda": {
            "row_count": profile["row_count"],
            "column_count": profile["column_count"],
            "duplicate_rows": profile.get("duplicate_rows", profile.get("duplicate_count")),
            "missing_count": profile.get("missing_count"),
            "constant_columns": profile.get("constant_columns"),
            "high_cardinality_columns": profile.get("high_cardinality_columns"),
            "likely_identifier_columns": profile.get("likely_identifier_columns"),
        },
        "quality": quality,
        "cleaning": cleaning_log,
        "feature_engineering": {
            **inp.feature_report,
            "transformations": fe_transformations,
            "numerical_cols": num_cols,
            "categorical_cols": cat_cols,
        },
        "column_roles": {
            "numerical": [name for name in num_cols if name not in transformed_datetime],
            "categorical": [name for name in cat_cols if name not in initial_roles.boolean],
            "boolean": initial_roles.boolean,
            "datetime": sorted(transformed_datetime),
            "identifier": inp.identifier_cols,
            "ignored_free_text": final_roles.ignored_free_text,
        },
        "column_role_evidence": inp.column_role_evidence,
        "preprocessing": {
            "numeric_columns": list(num_cols),
            "categorical_columns": list(cat_cols),
            "numeric_imputer_strategy": "median",
            "numeric_scaler": "StandardScaler",
            "categorical_imputer_strategy": "most_frequent",
            "categorical_encoder": "OneHotEncoder",
            "categorical_encoder_drop": None,
            "handle_unknown": "ignore",
            "fit_partition": "fold_train_only_then_full_train_for_locked_winner",
            "numerical": ["imputer:median", "scaler:standard"],
            "categorical": ["imputer:most_frequent", "onehot:all_categories"],
        },
        "target": inp.target_evidence,
        "entity": {
            "column": entity_column if entity_column in frame.columns else None,
            "source": "deterministic" if entity_column in frame.columns else "none",
            "reason": (
                "strong identifier evidence"
                if entity_column in frame.columns
                else "no identifier was inferred; prediction rows use stable held-out row indexes"
            ),
        },
        "missing_value_decisions": {
            "dropped_columns": dropped_columns,
            "rows_with_missing": missing_plan.rows_with_missing,
            "row_missing_fraction": missing_plan.row_missing_fraction,
            "drop_rows_recommended": missing_plan.drop_rows_recommended,
            "column_decisions": list(cleaning_decisions.values()),
        },
        "numerical_cols": num_cols,
        "categorical_cols": cat_cols,
        "boost_family_used": inp.boost_family_used,
        "model_families": [row.get("model_family") for row in (result.get("candidates") or [])],
        "experiment_status": experiment.status,
        "pipeline_trace": list(ctx.trace_entries),
        "stage_timings": list(result.get("stage_timings") or []),
        "problem_profile": result.get("problem_profile") or {},
        "holdout_plan": result.get("holdout_plan") or holdout_plan.to_dict(),
        "validation_plan": result.get("validation_plan") or {},
        "metric_plan": result.get("metric_plan") or {},
        "model_development_plan": result.get("model_development_plan") or development_plan.to_dict(),
    }
    upload = db.get(ClientLabUpload, ctx.upload_id)
    ctx.upload = upload
    if upload is None:
        raise StageHalt
    # Persist the completed ML evidence before the read-only verifier runs.
    ml_ended_at = datetime.now(UTC)
    ml_execution_total = {
        "stage": "ml_execution_total",
        "started_at": ctx.total_started_at.isoformat(),
        "ended_at": ml_ended_at.isoformat(),
        "duration_ms": max(0.001, (time.perf_counter() - ctx.total_timer) * 1000.0),
        "status": "completed",
        "rows_in": int(upload.record_count),
        "rows_out": ctx.current_rows,
    }
    result["stage_timings"] = [*result["stage_timings"], ml_execution_total]
    log["stage_timings"] = list(result["stage_timings"])
    if not reuse_locked_run:
        # P4.10-A trust checks: stored on the result now and as findings rows with the
        # lineage below, both before the evidence lock.
        result["investigation"] = _investigation(inp, result)
    experiment.result = result
    db.commit()

    if not reuse_locked_run:
        from app.services.scientific_lineage_service import (
            lab_decision_sources_for_upload,
            persist_scientific_lineage_from_result,
        )

        result_prep = (
            result.get("preprocessing")
            if isinstance(result.get("preprocessing"), dict)
            else {}
        )
        evidence = (
            dict(result["scientific_evidence"])
            if isinstance(result.get("scientific_evidence"), dict)
            else {}
        )
        evidence.update(
            {
                "quality": quality,
                "missing_value_plan": missing_plan.to_dict(),
                "leakage_exclusions": list(development_plan.excluded_features),
                "feature_actions": list(fe_transformations),
                "numerical_columns": [
                    str(name)
                    for name in (result_prep.get("numeric_columns") or num_cols)
                ],
                "categorical_columns": [
                    str(name)
                    for name in (result_prep.get("categorical_columns") or cat_cols)
                ],
                "modeled_features": list(inp.modeled_cols),
                "dropped_columns": list(missing_plan.dropped_columns),
                "preprocessing_fit_scope": "fold_train",
            }
        )
        result["scientific_evidence"] = evidence
        persist_scientific_lineage_from_result(
            db,
            experiment,
            result,
            missing_plan=missing_plan,
            lab_decision_sources=lab_decision_sources_for_upload(db, upload.id),
            source_dataset_id=upload.dataset_id,
        )
        if inp.split_assignment is not None:
            from app.services.split_plan_service import verify_run_against_plan

            # Fail closed before the lock: the run used exactly the plan's
            # holdout rows, outer folds and HoldoutPlan, on its source dataset.
            verify_run_against_plan(db, experiment, inp.split_assignment, result)
        from app.services.candidate_modeling_service import (
            link_candidates_to_feature_set_version,
            link_holdout_evaluation_to_model_version,
        )
        from app.services.reproducibility_service import persist_reproducibility

        link_candidates_to_feature_set_version(db, experiment)
        repro = persist_reproducibility(db, experiment, result)
        model_version = None
        if workflow_run is not None:
            from app.services.lineage_service import (
                create_model_asset,
                create_model_version,
            )

            model_asset = db.scalar(
                select(ModelAsset).where(
                    ModelAsset.workflow_id == workflow_run.workflow_id,
                )
            )
            if model_asset is None:
                slug = "client-lab-selected-model"
                taken = db.scalar(
                    select(ModelAsset.id).where(
                        ModelAsset.workspace_id == upload.workspace_id,
                        ModelAsset.slug == slug,
                    )
                )
                if taken is not None:
                    slug = f"client-lab-selected-model-{workflow_run.workflow.slug}"
                model_asset = create_model_asset(
                    db,
                    workspace_id=upload.workspace_id,
                    workflow=workflow_run.workflow,
                    name="Client Lab Selected Model",
                    slug=slug,
                )
            selected_key = (result.get("selection") or {}).get(
                "selected_candidate_id"
            ) or (result.get("best_single") or {}).get("candidate_id")
            selected_candidate = db.scalar(
                select(ExperimentCandidate).where(
                    ExperimentCandidate.experiment_id == experiment.id,
                    ExperimentCandidate.candidate_key == selected_key,
                )
            )
            if selected_candidate is None:
                raise RuntimeError("selected candidate was not persisted")
            version_count = db.query(ModelVersion).filter(
                ModelVersion.model_asset_id == model_asset.id
            ).count()
            model_version = create_model_version(
                db,
                model_asset=model_asset,
                pipeline_run=experiment,
                selected_candidate=selected_candidate,
                version=f"v{version_count + 1}",
                runtime_environment_id=repro.runtime_environment.id,
                code_snapshot_id=(
                    repro.code_snapshot.id if repro.code_snapshot is not None else None
                ),
                model_artifact_id=(
                    repro.model_artifact.id if repro.model_artifact is not None else None
                ),
                preprocessor_artifact_id=(
                    repro.preprocessor_artifact.id
                    if repro.preprocessor_artifact is not None
                    else None
                ),
                feature_manifest_artifact_id=(
                    repro.feature_manifest_artifact.id
                    if repro.feature_manifest_artifact is not None
                    else None
                ),
                feature_set_version_id=repro.feature_set_version_id,
            )
            link_holdout_evaluation_to_model_version(db, experiment, model_version)
        from app.services.model_build_reproduction_service import (
            persist_model_build_reproduction_artifacts,
        )

        persist_model_build_reproduction_artifacts(db, experiment)
        # Last evidence write before the commit: the triggers this arms read
        # the stamp inside this transaction.
        if lock_scientific_evidence(db, experiment) is None:
            missing = ", ".join(missing_scientific_evidence(db, experiment))
            raise RuntimeError(
                "cannot finish pipeline run before scientific evidence is complete: "
                f"{missing}"
            )
        if model_version is not None:
            from app.services.project_ref_service import initialize_refs_on_first_model

            # refs.bootstrap.v1 (ADR 0006 §2): only project_refs and the
            # ref_initialized record are written here, atomically with the lock.
            initialize_refs_on_first_model(db, experiment=experiment, model_version=model_version)
        db.commit()
    return PersistenceOutput(upload=upload, experiment=experiment, result=result, log=log)
