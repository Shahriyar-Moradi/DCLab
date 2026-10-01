"""Project refs: the only mutable graph pointers (ADR 0006 §2).

P2.2-B ships the bootstrap rule only (``refs.bootstrap.v1``): the first run in
a project that reaches a locked ModelVersion *with* a project source dataset and
a SplitPlan initializes the refs from its own lineage with one accepted
``ref_initialized`` record, in the same transaction. A run that cannot satisfy
``dataset``, ``split_plan`` and ``champion_model`` defers bootstrap to a later
run. ``problem_spec`` / ``feature_recipe`` may still be unsatisfiable; they are
listed in ``details.skipped_refs`` and P2.5-A ``move_ref`` may insert them later
(``from: null``) under an accepted record (ADR 0006 Revision 2). Later runs
never move refs automatically.
"""

from __future__ import annotations

import logging
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.db.models import (
    Dataset,
    Experiment,
    FeatureSetVersion,
    ModelEvaluation,
    ModelVersion,
    ProblemSpec,
    ProjectDecisionRecord,
    ProjectRef,
    SplitPlan,
    WorkflowRun,
)
from app.domain.decision_records import (
    DECISION_REF_INITIALIZED,
    REF_INITIALIZED_SCHEMA_VERSION,
    REFS_BOOTSTRAP_RATIONALE,
    RULE_REFS_BOOTSTRAP,
    ref_initialized_idempotency_key,
)
from app.domain.state_graph import REF_KINDS, REF_TARGET_COLUMNS, REF_TARGET_NODE_KINDS
from app.services.decision_record_service import (
    FINAL_HOLDOUT_SCOPE,
    evidence_ref,
    rule_record,
)
from app.services.split_plan_service import is_unique_race

logger = logging.getLogger(__name__)

# Bootstrap waits for a run whose lineage satisfies these kinds.
BOOTSTRAP_REQUIRED_REF_KINDS = ("dataset", "split_plan", "champion_model")
# A concurrent bootstrap of the same project collides on one of these.
BOOTSTRAP_RACE_CONSTRAINTS = frozenset(
    {"uq_pdr_workspace_idempotency_key", "uq_project_refs_project_ref_kind"}
)


def project_has_refs(db: Session, *, workspace_id: UUID, project_id: UUID) -> bool:
    return (
        db.scalar(
            select(ProjectRef.id)
            .where(ProjectRef.workspace_id == workspace_id, ProjectRef.project_id == project_id)
            .limit(1)
        )
        is not None
    )


def _bootstrap_targets(
    db: Session, experiment: Experiment, model_version: ModelVersion
) -> tuple[dict[str, UUID], list[dict[str, str]]]:
    """Ref kind -> target id from the run's own lineage; unmet kinds are reported."""

    ws, project = experiment.workspace_id, experiment.project_id
    targets: dict[str, UUID] = {}
    skipped: list[dict[str, str]] = []

    workflow_run = (
        db.get(WorkflowRun, experiment.workflow_run_id)
        if experiment.workflow_run_id is not None
        else None
    )
    spec = (
        db.get(ProblemSpec, workflow_run.problem_spec_id)
        if workflow_run is not None and workflow_run.problem_spec_id is not None
        else None
    )
    if spec is None:
        skipped.append({"ref_kind": "problem_spec", "reason": "run has no problem spec"})
    elif spec.workspace_id != ws or spec.project_id != project or spec.status != "locked":
        skipped.append({"ref_kind": "problem_spec", "reason": "problem spec is not locked"})
    else:
        targets["problem_spec"] = spec.id

    dataset = (
        db.get(Dataset, experiment.source_dataset_id)
        if experiment.source_dataset_id is not None
        else None
    )
    if dataset is None or dataset.workspace_id != ws or dataset.project_id != project:
        skipped.append({"ref_kind": "dataset", "reason": "run has no project source dataset"})
    else:
        targets["dataset"] = dataset.id

    plan = db.get(SplitPlan, experiment.split_plan_id) if experiment.split_plan_id else None
    if plan is None or plan.workspace_id != ws or plan.project_id != project:
        skipped.append({"ref_kind": "split_plan", "reason": "run has no split plan"})
    elif targets.get("dataset") != plan.dataset_id:
        skipped.append({"ref_kind": "split_plan", "reason": "split plan dataset is not the dataset ref"})
    else:
        targets["split_plan"] = plan.id

    recipe = (
        db.get(FeatureSetVersion, model_version.feature_set_version_id)
        if model_version.feature_set_version_id is not None
        else None
    )
    if recipe is None or recipe.workspace_id != ws or recipe.project_id != project:
        skipped.append({"ref_kind": "feature_recipe", "reason": "model has no feature recipe"})
    elif recipe.locked_at is None:
        skipped.append({"ref_kind": "feature_recipe", "reason": "feature recipe is not locked"})
    else:
        targets["feature_recipe"] = recipe.id

    targets["champion_model"] = model_version.id
    return targets, skipped


def initialize_refs_on_first_model(
    db: Session, *, experiment: Experiment, model_version: ModelVersion
) -> ProjectDecisionRecord | None:
    """``refs.bootstrap.v1``: initialize refs once per project; never moves existing refs.

    Called after the run's evidence lock, before its commit. Returns the
    ``ref_initialized`` record, or None when the project already has refs, the
    run has no project, or the champion rule cannot be met (no final holdout).
    """

    project_id = experiment.project_id
    if project_id is None or model_version.project_id != project_id:
        return None
    if experiment.scientific_evidence_locked_at is None:
        return None
    if model_version.pipeline_run_id != experiment.id or model_version.workspace_id != experiment.workspace_id:
        return None
    if project_has_refs(db, workspace_id=experiment.workspace_id, project_id=project_id):
        return None
    holdout = db.scalar(
        select(ModelEvaluation)
        .where(
            ModelEvaluation.workspace_id == experiment.workspace_id,
            ModelEvaluation.candidate_id == model_version.selected_candidate_id,
            ModelEvaluation.evaluation_scope == FINAL_HOLDOUT_SCOPE,
        )
        .limit(1)
    )
    if holdout is None:
        return None
    targets, skipped = _bootstrap_targets(db, experiment, model_version)
    if any(kind not in targets for kind in BOOTSTRAP_REQUIRED_REF_KINDS):
        # Defer: a one-shot bootstrap without dataset/split plan would leave
        # the project's refs permanently incomplete.
        return None
    selection_metric = None
    if isinstance(experiment.result, dict):
        selection_metric = (experiment.result.get("selection") or {}).get("selection_metric")
    ref_moves = [
        {
            "ref_kind": kind,
            "from": None,
            "to": {"kind": REF_TARGET_NODE_KINDS[kind], "id": str(targets[kind])},
        }
        for kind in REF_KINDS
        if kind in targets
    ]
    record = rule_record(
        workspace_id=experiment.workspace_id,
        project_id=project_id,
        decision_type=DECISION_REF_INITIALIZED,
        subject_kind="model_version",
        subject_id=model_version.id,
        subject_digest=model_version.content_digest,
        actor_rule=RULE_REFS_BOOTSTRAP,
        rationale=REFS_BOOTSTRAP_RATIONALE,
        facts={
            "experiment_id": str(experiment.id),
            "model_version": model_version.version,
            "final_holdout_evaluation_id": str(holdout.id),
        },
        evidence_refs=[
            evidence_ref(
                "candidate",
                model_version.selected_candidate_id,
                metric=selection_metric,
                scope=FINAL_HOLDOUT_SCOPE,
            )
        ],
        details={"ref_moves": ref_moves, "skipped_refs": skipped},
        schema_version=REF_INITIALIZED_SCHEMA_VERSION,
        idempotency_key=ref_initialized_idempotency_key(project_id),
    )
    try:
        # A concurrent bootstrap collides on the idempotency key or on
        # UNIQUE(project_id, ref_kind); the loser keeps the winner's refs.
        with db.begin_nested():
            db.add(record)
            db.flush()
            for kind in REF_KINDS:
                if kind not in targets:
                    continue
                ref = ProjectRef(
                    workspace_id=experiment.workspace_id,
                    project_id=project_id,
                    ref_kind=kind,
                    version=1,
                    # moved_at = created_at = now() for a bootstrap (ADR §2).
                    decision_record_id=record.id,
                )
                setattr(ref, REF_TARGET_COLUMNS[kind], targets[kind])
                db.add(ref)
            db.flush()
    except IntegrityError as exc:
        if not is_unique_race(exc, BOOTSTRAP_RACE_CONSTRAINTS):
            raise
        logger.info("project %s refs were bootstrapped concurrently; keeping them", project_id)
        return None
    return record
