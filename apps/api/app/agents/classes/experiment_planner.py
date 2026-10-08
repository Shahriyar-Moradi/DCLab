"""``ExperimentPlannerAgent`` — ``spec.objective`` / ``split.strategy`` /
``training.families_budget`` (AI-before; ADR 0008 §1, §2, § Founder decisions 5).

Answers before the job: the output is an ``ExperimentPlanProposal`` whose payload is
exactly the ``ExperimentPlan`` that ``run_plan_service`` validates. At L1 a person accepts
it (P6.6-A) and a root run consumes it as its ``plan`` input, where every answer is
re-validated against the current graph (``plan_points``: metric for the task, family
subset of the portfolio, tighten-only split, budget ≤ the rule's). Context: the dataset's
column metadata, the project's latest ProblemSpec (task, target, metric) and the code
allowlists — no rows and no results (a plan that could follow results is refused).

Validators: metric, split strategy and families from the code allowlists for the task;
holdout fraction ≥ the code-owned floor; group / time columns exist in the dataset and are
not the spec's target; the task agrees with the project's spec.
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import Field, ValidationError
from sqlalchemy import select

from app.agents.classes.common import Strict, column_names, column_section, dataset_columns, uncited_numbers
from app.agents.runtime.base import (
    AgentClass,
    ContextRequest,
    OutputCheck,
    ProposalDraft,
    RuntimeOutput,
    register_agent_class,
)
from app.agents.tools.shaping import Shaped, code, data_text
from app.domain.run_plans import (
    HOLDOUT_FRACTION_FLOOR,
    PLAN_PROPOSAL_TYPE,
    RULE_TRAINING_SECONDS,
    ExperimentPlan,
    PlanSplit,
)

AGENT_KEY = "experiment_planner"
TASK_TYPES = ("binary", "multiclass", "regression")
SPLIT_STRATEGIES = ("stratified_random", "random", "group_disjoint", "temporal_future")


class ExperimentPlanOutput(Strict):
    task_type: Literal["binary", "multiclass", "regression"]
    primary_metric: str | None = Field(pattern=r"^[a-z][a-z0-9_]{0,63}$")
    split: PlanSplit | None
    families: list[str] | None = Field(max_length=16)
    max_training_seconds: float | None = Field(gt=0, le=RULE_TRAINING_SECONDS)
    rationale: str = Field(max_length=2000)
    confidence: float = Field(ge=0, le=1)


TASK = """You plan one experiment before it runs. You only propose; a person accepts the plan.
Choose the primary metric for the task, a split strategy (a group or time structure only when a
listed column supports it; holdout fraction at least the floor given), a subset of the allowed
model families and a training time budget no larger than the rule's. Use only the allowlists
and column metadata given; leave an answer empty when the rule's default is right."""


def allowed_metrics(task_type: str) -> frozenset[str]:
    from app.engine.modeling.objective import PRIMARY_METRICS

    return PRIMARY_METRICS.get(task_type, frozenset())


def allowed_families(task_type: str) -> list[str]:
    from app.engine.search.generator import open_ingest_families

    return open_ingest_families(task_type)


def latest_spec(db: Any, workspace_id: Any, project_id: Any) -> Any:
    from app.db.models import ProblemSpec

    if project_id is None:
        return None
    return db.scalar(select(ProblemSpec).where(ProblemSpec.workspace_id == workspace_id,
                                               ProblemSpec.project_id == project_id)
                     .order_by(ProblemSpec.version.desc()).limit(1))


def context(request: ContextRequest) -> list[tuple[str, Any]]:
    from app.agents.tools.definitions.reads import ServiceReads, map_service_errors

    if request.subject_kind != "dataset_version" or request.subject_id is None:
        return []
    ctx = request.tool_ctx
    sections: list[tuple[str, Any]] = [
        ("dataset_columns", column_section(request.subject_id, dataset_columns(request, request.subject_id)))]
    if request.project_id is not None:
        with map_service_errors():
            ServiceReads(ctx).project(request.project_id)  # authorizes the spec read
        spec = latest_spec(ctx.db, ctx.workspace_id, request.project_id)
        if spec is not None:
            sections.append(("problem_spec", Shaped({
                "version": spec.version, "task_type": code(spec.task_type), "primary_metric": code(spec.primary_metric),
                "target_column": data_text(spec.target_column, 256)}, source_datasets=(request.subject_id,))))
    sections.append(("plan_allowlists", Shaped({
        "task_types": [code(t) for t in TASK_TYPES], "split_strategies": [code(s) for s in SPLIT_STRATEGIES],
        "metrics": {t: [code(m) for m in sorted(allowed_metrics(t))] for t in TASK_TYPES},
        "families": {t: [code(f) for f in allowed_families(t)] for t in TASK_TYPES},
        "rule_training_seconds": RULE_TRAINING_SECONDS, "split_test_size_floor": HOLDOUT_FRACTION_FLOOR})))
    return sections


def plan_of(out: ExperimentPlanOutput) -> ExperimentPlan:
    return ExperimentPlan(primary_metric=out.primary_metric, split=out.split,
                          families=tuple(out.families) if out.families else None,
                          max_training_seconds=out.max_training_seconds)


def to_output(out: ExperimentPlanOutput) -> RuntimeOutput:
    try:
        payload = plan_of(out).model_dump(mode="json")
    except ValidationError:
        return RuntimeOutput(output=out)  # no proposal: validate_output rejects the run (plan_invalid)
    point = ("spec.objective" if out.primary_metric else "split.strategy" if out.split else "training.families_budget")
    return RuntimeOutput(output=out, proposal=ProposalDraft(
        proposal_type=PLAN_PROPOSAL_TYPE, decision_point_key=point, payload=payload, rationale=out.rationale))


def validate_output(output: RuntimeOutput) -> list[str]:
    out = output.output
    if not isinstance(out, ExperimentPlanOutput):
        return ["output_schema_invalid"]
    try:
        plan = plan_of(out)
    except ValidationError:
        return ["plan_invalid"]
    if not any(getattr(plan, name) is not None for name in ("primary_metric", "split", "families",
                                                              "max_training_seconds")):
        return ["empty_plan"]
    if out.primary_metric is not None and out.primary_metric not in allowed_metrics(out.task_type):
        return ["metric_not_allowed"]
    if out.families is not None and (not set(out.families) <= set(allowed_families(out.task_type))
                                     or len(set(out.families)) != len(out.families)):
        return ["family_not_allowed"]
    split = out.split
    if split is not None:
        if split.test_size is not None and split.test_size < HOLDOUT_FRACTION_FLOOR:
            return ["below_holdout_fraction_floor"]
        if (split.strategy == "group_disjoint") != (split.group_column is not None) or (
                split.strategy == "temporal_future") != (split.time_column is not None):
            return ["split_columns_invalid"]
        if split.strategy == "stratified_random" and out.task_type == "regression":
            return ["split_not_allowed_for_task"]
    if uncited_numbers([out.rationale], [v for v in (out.max_training_seconds, split.test_size if split else None)
                                         if v is not None] + [RULE_TRAINING_SECONDS, HOLDOUT_FRACTION_FLOOR]):
        return ["uncited_number"]
    return []


def check_output(check: OutputCheck, output: RuntimeOutput) -> list[str]:
    out = output.output
    if check.subject_kind != "dataset_version" or check.subject_id is None or not isinstance(
            out, ExperimentPlanOutput):
        return ["subject_not_a_dataset"]
    spec = latest_spec(check.db, check.workspace_id, check.project_id)
    if spec is not None and spec.task_type != out.task_type and not (
            spec.task_type == "classification" and out.task_type in ("binary", "multiclass")):
        return ["task_type_conflicts_with_spec"]
    split = out.split
    if split is not None:
        names = column_names(check.db, check.workspace_id, check.subject_id)
        for column in (split.group_column, split.time_column):
            if column is not None and (column not in names or (spec is not None and column == spec.target_column)):
                return ["unknown_column"]
    return []


CLASS = register_agent_class(AgentClass(
    agent_key=AGENT_KEY, output_schema=ExperimentPlanOutput, task=TASK, method="plan", max_output_tokens=2000,
    to_output=to_output, validate_output=validate_output, check_output=check_output, context=context,
    version="1", prompt_version=1,
))
