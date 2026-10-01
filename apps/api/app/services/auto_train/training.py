"""Stage: run the open-ingest experiment and assemble its result evidence."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict

from app.db.models import Experiment
from app.domain.lab_run_stages import SPLITTING
from app.engine.modeling.holdout_planner import HoldoutPlan
from app.engine.modeling.leakage_auditor import ModelDevelopmentPlan
from app.engine.models.registry import available_families
from app.services.auto_train.context import RunContext, StageHalt, service_module
from app.services.lab_service import execute_experiment


class TrainingInput(BaseModel):
    model_config = ConfigDict(arbitrary_types_allowed=True)

    experiment: Experiment
    task_type: str
    profile: dict[str, Any]
    cleaning_log: dict[str, Any]
    holdout_plan: HoldoutPlan
    development_plan: ModelDevelopmentPlan
    feature_report: dict[str, Any]
    fe_transformations: list[dict[str, Any]]
    num_cols: list[str]
    cat_cols: list[str]
    combos: list[tuple[str, ...]]
    locked_split: dict[str, Any]
    # Stored SplitPlan fold map (source row -> outer fold); None = derive folds.
    outer_fold_assignment: dict[int, int] | None = None
    # (holdout rows, train rows) of a reused SplitPlan; None = runner re-splits.
    holdout_partition: tuple[frozenset[int], frozenset[int]] | None = None


class TrainingOutput(BaseModel):
    model_config = ConfigDict(arbitrary_types_allowed=True)

    experiment: Experiment
    result: dict[str, Any]
    reuse_locked_run: bool
    boost_family_used: str | None


def run_training(ctx: RunContext, inp: TrainingInput) -> TrainingOutput:
    svc = service_module()
    experiment = inp.experiment
    task_type = inp.task_type
    num_cols = inp.num_cols
    cat_cols = inp.cat_cols
    locked_split = inp.locked_split
    observer = ctx.observer
    on_heartbeat = ctx.on_heartbeat

    def _experiment_stage(stage: str) -> None:
        # The runner repeats the deterministic split from the persisted
        # prepared table; the holdout was already locked before decisions.
        if stage != SPLITTING:
            ctx.stage(stage)

    def _on_model_event(event_type: str, payload: dict[str, Any]) -> None:
        if observer is not None:
            observer.callback(event_type, payload)
        if on_heartbeat is not None and event_type in svc.HEARTBEAT_PROGRESS_EVENTS:
            on_heartbeat()

    reuse_locked_run = experiment.scientific_evidence_locked_at is not None
    if reuse_locked_run and str(experiment.status).upper() != "COMPLETED":
        ctx.fail(
            f"scientific evidence for pipeline run {experiment.id} is locked",
            extra={"experiment_status": experiment.status},
            experiment_id=experiment.id,
        )
        raise StageHalt
    if not reuse_locked_run:
        experiment = execute_experiment(
            ctx.db,
            experiment,
            on_stage=_experiment_stage,
            on_event=_on_model_event,
            persist_scientific=False,
            outer_fold_assignment=inp.outer_fold_assignment,
            holdout_partition=inp.holdout_partition,
        )
        if experiment.status != "COMPLETED":
            result = dict(experiment.result or {})
            reason = result.get("error") or f"experiment ended with status {experiment.status}"
            ctx.fail(
                str(reason),
                extra={"experiment_status": experiment.status},
                experiment_id=experiment.id,
            )
            raise StageHalt

    avail = available_families(task_type)
    boost_family_used = next(
        (
            name
            for name in ("xgboost", "lightgbm", "xgboost_regressor", "lightgbm_regressor")
            if name in avail
        ),
        None,
    )
    result = dict(experiment.result or {})
    result["analysis"] = inp.profile
    result["cleaning"] = inp.cleaning_log
    result.setdefault("holdout_plan", inp.holdout_plan.to_dict())
    result.setdefault("model_development_plan", inp.development_plan.to_dict())
    result["feature_engineering"] = {
        **inp.feature_report,
        "transformations": list(inp.fe_transformations),
        "numerical_cols": num_cols,
        "categorical_cols": cat_cols,
        "group_combinations": [list(item) for item in inp.combos],
    }
    split_meta = dict(result.get("split") or {})
    if (
        not reuse_locked_run
        and split_meta.get("test_source_rows") != locked_split.get("test_source_rows")
    ):
        raise RuntimeError("persisted experiment did not preserve the locked holdout")
    trained = [row for row in (result.get("candidates") or []) if row.get("status") == "trained"]
    winner = dict(result.get("best_single") or {})
    ctx.trace(
        "preprocessing",
        "app.engine.lab.auto_prepare.build_preprocessor",
        numerical_cols=list(winner.get("numerical_cols") or num_cols),
        categorical_cols=list(winner.get("categorical_cols") or cat_cols),
        kind="column_transformer",
    )
    ctx.trace(
        "cross_validation",
        "app.engine.experiments.runner._run_open_ingest_candidates",
        n_folds=(trained[0].get("n_folds") if trained else None),
        cv_strategy=(trained[0].get("cv_strategy") if trained else None),
        families=[row.get("model_family") for row in trained],
    )
    ctx.trace(
        "selection_lock",
        "app.engine.experiments.runner._run_open_ingest_candidates",
        selected_candidate_id=(result.get("selection") or {}).get("selected_candidate_id"),
        selection_metric=(result.get("selection") or {}).get("selection_metric"),
        selection_source=(result.get("selection") or {}).get("selection_source"),
        locked=(result.get("selection") or {}).get("locked"),
    )
    ctx.trace(
        "training",
        "app.engine.models.registry.make_model",
        winner_family=winner.get("model_family"),
        winner_id=winner.get("candidate_id"),
        n_trained=len(trained),
    )
    test_metrics = dict(result.get("test_metrics") or {})
    eval_fn = (
        "app.engine.evaluation.metrics.classification_metrics"
        if task_type == "binary"
        else "app.engine.evaluation.metrics.multiclass_metrics"
        if task_type == "multiclass"
        else "app.engine.evaluation.metrics.regression_metrics"
    )
    ctx.trace(
        "evaluating",
        eval_fn,
        metric_names=sorted(test_metrics.keys()),
        n_test=split_meta.get("n_test"),
    )
    predictions = list(result.get("test_predictions") or [])
    ctx.trace(
        "predicting",
        "app.engine.experiments.runner._prediction_rows",
        n_predictions=len(predictions),
    )
    ctx.finish_stage()
    result["stage_timings"] = [
        *ctx.evidence_stage_timings,
        *list(result.get("execution_stage_timings") or []),
    ]
    return TrainingOutput(
        experiment=experiment,
        result=result,
        reuse_locked_run=reuse_locked_run,
        boost_family_used=boost_family_used,
    )
