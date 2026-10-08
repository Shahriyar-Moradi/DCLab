"""``DatasetInvestigatorAgent`` — ``target.column`` / ``column.missing_value_action``
(AI-before; ADR 0008 §1, §2: answers are produced before the job and travel as typed
inputs; caps L1 here: a target is always a person's choice, any exclusion is L1).

Context: the dataset version (generic subject read) and its column metadata only — name,
dtype family, recorded semantic type / role, a has-missing flag; no rows, no sample values
and no whole-file statistics. Output: a ``DatasetInvestigationProposal`` (ranked target
candidates, missing-value actions from the executed v3 action set, leakage explanations,
questions for the user). Validators: every cited column exists in the dataset version; an
action outside ``impute_median`` / ``impute_most_frequent`` / ``drop_column`` is refused,
and so is an action for a column without missing values; free text may not introduce new
statistics (the context carries none).
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import Field

from app.agents.classes.common import (
    NAME_MAX,
    Strict,
    column_names,
    column_section,
    dataset_columns,
    uncited_numbers,
)
from app.agents.runtime.base import (
    AgentClass,
    ContextRequest,
    OutputCheck,
    ProposalDraft,
    RuntimeOutput,
    register_agent_class,
)
from app.domain.run_plans import IMPUTE_ACTIONS

AGENT_KEY = "dataset_investigator"
PROPOSAL_TYPE = "DatasetInvestigationProposal"
MISSING_VALUE_ACTIONS = (*IMPUTE_ACTIONS, "drop_column")  # missing_value prompt v3: executed actions only


class TargetCandidate(Strict):
    column: str = Field(min_length=1, max_length=NAME_MAX)
    rank: int = Field(ge=1, le=10)
    reason: str = Field(max_length=300)


class MissingValueAction(Strict):
    column: str = Field(min_length=1, max_length=NAME_MAX)
    action: Literal["impute_median", "impute_most_frequent", "drop_column"]
    reason: str = Field(max_length=300)


class LeakageExplanation(Strict):
    column: str = Field(min_length=1, max_length=NAME_MAX)
    explanation: str = Field(max_length=500)


class DatasetInvestigation(Strict):
    target_candidates: list[TargetCandidate] = Field(max_length=10)
    missing_values: list[MissingValueAction] = Field(max_length=64)
    leakage_suspects: list[LeakageExplanation] = Field(max_length=20)
    questions: list[str] = Field(max_length=5)
    confidence: float = Field(ge=0, le=1)


TASK = """You investigate one dataset version before any experiment runs. You only propose.
Rank the columns most likely to be the prediction target, propose a missing-value action
(impute_median, impute_most_frequent or drop_column) only for columns that have missing
values, explain columns that could leak the target, and ask the user what you cannot decide.
Use only the column metadata given; never invent statistics or column names."""


def context(request: ContextRequest) -> list[tuple[str, Any]]:
    if request.subject_kind != "dataset_version" or request.subject_id is None:
        return []
    return [("dataset_columns", column_section(request.subject_id, dataset_columns(request, request.subject_id)))]


def to_output(out: DatasetInvestigation) -> RuntimeOutput:
    point = "target.column" if out.target_candidates else "column.missing_value_action"
    return RuntimeOutput(output=out, proposal=ProposalDraft(
        proposal_type=PROPOSAL_TYPE, decision_point_key=point,
        payload={"schema_version": 1, **out.model_dump(mode="json")},
        rationale="; ".join(item.reason for item in out.target_candidates[:3]) or None))


def _cited(out: DatasetInvestigation) -> list[str]:
    return [item.column for group in (out.target_candidates, out.missing_values, out.leakage_suspects)
            for item in group]


def validate_output(output: RuntimeOutput) -> list[str]:
    out = output.output
    if not isinstance(out, DatasetInvestigation):
        return ["output_schema_invalid"]
    if any(item.action not in MISSING_VALUE_ACTIONS for item in out.missing_values):
        return ["action_not_allowed"]
    for group in (out.target_candidates, out.missing_values, out.leakage_suspects):
        if len({item.column for item in group}) != len(group):
            return ["duplicate_column"]
    if sorted(item.rank for item in out.target_candidates) != list(range(1, len(out.target_candidates) + 1)):
        return ["rank_invalid"]
    texts = [*(i.reason for i in out.target_candidates), *(i.reason for i in out.missing_values),
             *(i.explanation for i in out.leakage_suspects), *out.questions]
    if uncited_numbers(texts, ()):
        return ["uncited_number"]  # the context has no statistics to cite
    return []


def check_output(check: OutputCheck, output: RuntimeOutput) -> list[str]:
    from sqlalchemy import select

    from app.db.models import DatasetColumn

    out = output.output
    if check.subject_kind != "dataset_version" or check.subject_id is None or not isinstance(
            out, DatasetInvestigation):
        return ["subject_not_a_dataset"]
    names = column_names(check.db, check.workspace_id, check.subject_id)
    if any(column not in names for column in _cited(out)):
        return ["unknown_column"]
    missing = set(check.db.scalars(select(DatasetColumn.name).where(
        DatasetColumn.workspace_id == check.workspace_id, DatasetColumn.dataset_id == check.subject_id,
        DatasetColumn.missing_count > 0)))
    if any(item.column not in missing for item in out.missing_values):
        return ["no_missing_values"]
    return []


CLASS = register_agent_class(AgentClass(
    agent_key=AGENT_KEY, output_schema=DatasetInvestigation, task=TASK, method="investigate",
    max_output_tokens=3000, to_output=to_output, validate_output=validate_output, check_output=check_output,
    context=context, version="1", prompt_version=1,
))
