"""Write tools of the catalog: proposals only (ADR 0008 §6; ADR 0009 §6, §7.4).

From an agent a write tool never acts: the harness validates the arguments here and
stores them in a ``ToolCallProposal`` at L1 under ``decision_point_key``; the command
runs through ``services`` (the functions the ``/v1`` route calls) only when a human
accepts it (P6.6-A), where the validator runs again. Validators are deterministic
and never read holdout: an agent may not cite the final holdout as evidence; on a
champion move DCLab attaches the promoted model's own locked final evaluation itself
(``project_ref_service.champion_final_evaluation``) and only a human accepts. The
harness also checks that every node an argument names is in the run's project. The
MCP-only ``idempotency_key`` salt is not part of these schemas (the proposal id is the key).
"""

from __future__ import annotations

import re
from typing import Annotated, Any, Literal
from uuid import UUID

from pydantic import BaseModel, Field, ValidationError

from app.agents.tools.catalog import ToolError
from app.agents.tools.shaping import names_holdout

Id = Annotated[UUID, Field(description="Resource UUID.")]
Text = Annotated[str, Field(min_length=1, max_length=1000)]
Opt = Annotated[str | None, Field(max_length=200)]
Obj = Annotated[dict[str, Any] | None, Field()]
TaskType = Annotated[str, Field(description="e.g. binary_classification, regression")]


class CreateProblemSpecInput(BaseModel):
    project_id: Id
    task_type: TaskType
    business_objective: Text
    target_column: Opt = None
    primary_metric: Opt = None
    prediction_unit: Opt = None
    prediction_time_column: Opt = None
    prediction_horizon: Opt = None
    constraints: Obj = None
    success_criteria: Obj = None


class ProposeProblemSpecInput(BaseModel):
    project_id: Id
    task_type: TaskType
    business_objective: Text
    rationale: Text
    target_column: Opt = None
    primary_metric: Opt = None
    prediction_unit: Opt = None
    prediction_time_column: Opt = None
    prediction_horizon: Opt = None
    constraints: Obj = None
    success_criteria: Obj = None
    plan: UUID | None = Field(default=None, description="An accepted/applied ExperimentPlanProposal id: its target_column and primary_metric fill the spec (not consumed).")  # noqa: E501


class RunExperimentInput(BaseModel):
    project_id: Id
    dataset_id: Id
    problem_spec_id: UUID | None = None
    target_column: Opt = None
    intent: Annotated[str | None, Field(max_length=500)] = None
    plan: UUID | None = Field(default=None, description="An accepted/applied ExperimentPlanProposal id (single use).")


class BranchExperimentInput(BaseModel):
    experiment_id: Id
    changes: list[dict[str, Any]] = Field(min_length=1, max_length=20, description="Typed changes, e.g. {\"kind\": \"family_exclude\", \"family\": \"xgboost\"}.")  # noqa: E501
    intent: str = Field(min_length=1, max_length=500)


class PredictInput(BaseModel):
    model_version_id: Id
    dataset_id: UUID = Field(description="Dataset UUID of the model's project (uploaded with purpose=scoring).")
    output_format: Literal["csv", "parquet"] = "csv"


class RecordDecisionInput(BaseModel):
    project_id: Id
    rationale: Text
    decision_type: Literal["experiment_accepted", "experiment_rejected"] | None = None
    subject_kind: str | None = Field(default=None, description="e.g. experiment, model_version, project")
    subject_id: UUID | None = None
    evidence_refs: list[dict[str, Any]] | None = Field(default=None, max_length=20, description="[{kind, id, metric?, scope?}]")  # noqa: E501
    ref_moves: dict[str, UUID] | None = Field(default=None, description="Propose moving refs instead: {ref_kind: target_id}. A champion_model move also moves feature_recipe to the model's recipe. Cite CV evidence only: DCLab attaches the promoted model's final evaluation itself, and only a human accepts.")  # noqa: E501
    facts: Obj = None


class RequestAgentReviewInput(BaseModel):
    project_id: Id
    agent: Literal["experiment_critic", "dataset_investigator", "experiment_planner"] = Field(
        description="experiment_critic reviews a completed experiment; the others a dataset version.")
    experiment_id: UUID | None = Field(default=None, description="experiment_critic: the completed experiment.")
    dataset_id: UUID | None = Field(default=None, description="dataset_investigator / experiment_planner: the dataset.")


def _no_holdout(value: Any, where: str) -> None:
    """No key, scope or string value naming the holdout (any spelling: Unicode-folded,
    format/mark characters stripped, homoglyphs and spaced letters folded) anywhere in an
    agent's structured arguments or free text (rejected, never stripped)."""

    if isinstance(value, dict):
        if any(names_holdout(str(key)) for key in value):
            raise ToolError("holdout_not_allowed", f"{where} may not cite or carry the final holdout")
        for item in value.values():
            _no_holdout(item, where)
    elif isinstance(value, list):
        for item in value:
            _no_holdout(item, where)
    elif isinstance(value, str) and names_holdout(value):
        raise ToolError("holdout_not_allowed", f"{where} may not cite or carry the final holdout")


# A dataset column name: one token, no whitespace. A feature change's ``column`` may be
# called anything (``holdout_flag``); a sentence in that slot is free text and is scanned.
_COLUMN_TOKEN = re.compile(r"^\S{1,256}$")


def _branch_changes_to_scan(changes: list[dict[str, Any]]) -> list[dict[str, Any]]:
    from app.domain.experiment_changes import FEATURE_CHANGE_KINDS

    scan: list[dict[str, Any]] = []
    for change in changes:
        if (
            isinstance(change, dict)
            and change.get("kind") in FEATURE_CHANGE_KINDS
            and isinstance(change.get("column"), str)
            and _COLUMN_TOKEN.match(change["column"])
        ):
            scan.append({key: item for key, item in change.items() if key != "column"})
        else:
            scan.append(change)
    return scan


def _review_validator(args: RequestAgentReviewInput) -> RequestAgentReviewInput:
    critic = args.agent == "experiment_critic"
    if (args.experiment_id is None) == (args.dataset_id is None) or (args.experiment_id is not None) != critic:
        raise ToolError("invalid_arguments", "experiment_critic takes experiment_id; the others take dataset_id")
    return args


def _branch_validator(args: BranchExperimentInput) -> BranchExperimentInput:
    from app.domain.experiment_changes import FEATURE_CHANGE_KINDS, ExperimentChangeSet
    from app.engine.features.contract import FeaturePlan, spec_for_transform, validate_feature_plan

    # An agent's branch is proposed on CV evidence only: no argument may cite the holdout
    # (only a feature change's single-token column NAME is exempt; everything else is scanned).
    _no_holdout(_branch_changes_to_scan(args.changes), "changes")
    _no_holdout(args.intent, "intent")
    try:
        change_set = ExperimentChangeSet.model_validate({"changes": args.changes})
    except ValidationError as exc:
        raise ToolError("invalid_change_set", exc.errors(include_url=False)[0]["msg"][:300]) from None
    # P5.0-A: the feature transforms an agent asks for must satisfy the feature contract
    # (a tripwire against a drifting declaration; the branch service checks it again).
    specs = tuple(
        spec_for_transform(change.transform, change.column)
        for change in change_set.changes
        if change.kind in FEATURE_CHANGE_KINDS
    )
    for violation in validate_feature_plan(FeaturePlan(transforms=specs, proposed_by="agent")):
        raise ToolError("invalid_change_set", f"{violation.code}: {violation.message}"[:300])
    return args


def _record_decision_validator(args: RecordDecisionInput) -> RecordDecisionInput:
    from app.domain.project_graph import GraphRefKind

    if not args.ref_moves and (args.decision_type is None or args.subject_kind is None):
        raise ToolError("invalid_arguments", "decision_type and subject_kind are required unless ref_moves is given")
    unknown = set(args.ref_moves or {}) - set(GraphRefKind.__args__)  # type: ignore[attr-defined]
    if unknown:
        raise ToolError("invalid_arguments", "ref_moves keys must be ref kinds")
    for ref in args.evidence_refs or []:
        try:
            UUID(str(ref.get("id")))
        except (TypeError, ValueError):
            raise ToolError("invalid_arguments", "evidence_refs.id must be a UUID") from None
    _no_holdout(args.evidence_refs, "evidence_refs")
    _no_holdout(args.facts, "facts")
    return args


def _spec_holdout_validator(args: BaseModel) -> BaseModel:
    _no_holdout(getattr(args, "constraints", None), "constraints")
    _no_holdout(getattr(args, "success_criteria", None), "success_criteria")
    return args
