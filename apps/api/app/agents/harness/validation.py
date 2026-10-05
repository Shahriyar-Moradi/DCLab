"""Deterministic validation of tool calls, outputs and proposals (ADR 0009 §5.1 step 7).

Nothing here raises to a user: a tool-call failure is a typed ``ToolError`` the harness
turns into a denial or a ``rejected_by_validator`` proposal; output and proposal
checks return reasons (codes) the harness turns into ``rejected_by_validator``.
Write-tool arguments may name only nodes of the run's project (re-run at accept,
P6.6-A); a champion move needs the final evaluation DCLab attaches itself
(``project_ref_service.check_proposed_ref_moves``); no argument, output or proposal
names the holdout.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.agents.contracts import Citation
from app.agents.harness.recorder import canonical, digest
from app.agents.tools.catalog import ToolDefinition, ToolError
from app.agents.tools.shaping import HOLDOUT_KEY
from app.db.models import (
    AgentProposal,
    BatchPrediction,
    Dataset,
    Experiment,
    ExperimentCandidate,
    ModelVersion,
    ProblemSpec,
    ProjectDecisionRecord,
    SplitPlan,
)
from app.domain.agent_records import (
    PROPOSAL_PAYLOAD_MAX_BYTES,
    PROPOSAL_TYPES,
    SERVICE_ONLY_PROPOSAL_TYPES,
    TOOL_ARGUMENTS_MAX_BYTES,
)
from app.domain.errors import DecisionRecordError

PAYLOAD_SCHEMA_VERSION = 1
_NODE_TABLES: dict[str, Any] = {
    "experiment": Experiment, "dataset_version": Dataset, "problem_spec": ProblemSpec, "split_plan": SplitPlan,
    "model_version": ModelVersion, "candidate": ExperimentCandidate, "decision": ProjectDecisionRecord,
    "prediction": BatchPrediction, "proposal": AgentProposal,
}
# Write-tool argument -> node kind it must name in the run's project.
_ARGUMENT_NODES = {"experiment_id": "experiment", "dataset_id": "dataset_version",
                   "model_version_id": "model_version", "problem_spec_id": "problem_spec",
                   "plan": "proposal"}  # P6.9-A: a run plan is a proposal of the run's project


def names_holdout(value: Any) -> bool:
    if isinstance(value, Mapping):
        return any(HOLDOUT_KEY.search(str(key)) or names_holdout(item) for key, item in value.items())
    if isinstance(value, (list, tuple)):
        return any(names_holdout(item) for item in value)
    return isinstance(value, str) and bool(HOLDOUT_KEY.search(value))


def parse_tool_arguments(definition: ToolDefinition, arguments: Any) -> dict[str, Any]:
    """Schema + the tool's deterministic validator, bounded to 8 KB (``tool_arguments``)."""

    if not isinstance(arguments, Mapping):
        raise ToolError("invalid_arguments", "arguments must be an object")
    if len(canonical(dict(arguments)).encode("utf-8")) > TOOL_ARGUMENTS_MAX_BYTES:
        raise ToolError("arguments_too_large", f"tool arguments are limited to {TOOL_ARGUMENTS_MAX_BYTES} bytes")
    parsed = definition.parse(arguments).model_dump(mode="json")
    if len(canonical(parsed).encode("utf-8")) > TOOL_ARGUMENTS_MAX_BYTES:
        raise ToolError("arguments_too_large", f"tool arguments are limited to {TOOL_ARGUMENTS_MAX_BYTES} bytes")
    return parsed


def proposal_payload(definition: ToolDefinition, arguments: dict[str, Any]) -> tuple[dict[str, Any], str]:
    """The ``ToolCallProposal`` payload and its digest: a pure function of the validated
    arguments (replay recomputes it without the database)."""

    payload: dict[str, Any] = {"tool": definition.name, "arguments": arguments,
                               "decision_point_key": definition.decision_point_key,
                               "schema_version": PAYLOAD_SCHEMA_VERSION}
    if "champion_model" in (arguments.get("ref_moves") or {}):
        payload["server_attaches"] = ["champion_final_evaluation"]  # never an id or a value
    return payload, digest(payload)


def _node_exists(db: Session, kind: str, node_id: Any, *, workspace_id: UUID, project_id: UUID) -> bool:
    model = _NODE_TABLES[kind]
    try:
        node = UUID(str(node_id))
    except ValueError:
        return False
    return db.scalar(select(model.id).where(model.id == node, model.workspace_id == workspace_id,
                                            model.project_id == project_id)) is not None


def check_proposal_nodes(db: Session, *, workspace_id: UUID, project_id: UUID, tool: str,
                         arguments: dict[str, Any]) -> None:
    """Every node a write tool names is in the run's project (404-safe: a foreign id
    and an unknown id are the same refusal)."""

    from app.services import decision_record_service as drs
    from app.services import project_ref_service as prs

    if "project_id" in arguments and str(arguments["project_id"]) != str(project_id):
        raise ToolError("node_not_in_project", "project_id must be the run's project")
    for key, kind in _ARGUMENT_NODES.items():
        if arguments.get(key) is not None and not _node_exists(
                db, kind, arguments[key], workspace_id=workspace_id, project_id=project_id):
            raise ToolError("node_not_in_project", f"{key} is not a node of this project")
    if tool != "record_decision":
        return
    try:
        if arguments.get("subject_kind"):
            drs.load_subject(db, workspace_id=workspace_id, project_id=project_id,
                             subject_kind=arguments["subject_kind"],
                             subject_id=UUID(arguments["subject_id"]) if arguments.get("subject_id") else None)
        if arguments.get("evidence_refs"):
            drs.validate_evidence_refs(db, workspace_id=workspace_id, project_id=project_id,
                                       refs=arguments["evidence_refs"])
        if arguments.get("ref_moves"):
            prs.check_proposed_ref_moves(db, workspace_id=workspace_id, project_id=project_id,
                                         moves={k: UUID(str(v)) for k, v in arguments["ref_moves"].items()})
    except DecisionRecordError as exc:
        raise ToolError(exc.reason if exc.reason else "invalid_arguments", exc.detail_message[:300]) from None


def citation_reasons(db: Session, citations: Sequence[Citation], *, workspace_id: UUID,
                     project_id: UUID | None) -> list[str]:
    if not citations:
        return []
    if project_id is None:
        return ["citation_without_project"]
    for item in citations:
        if item.kind not in _NODE_TABLES or not _node_exists(
                db, item.kind, item.id, workspace_id=workspace_id, project_id=project_id):
            return ["citation_not_found"]
    return []


def output_reasons(db: Session, output: Any, runtime: Any, *, workspace_id: UUID, project_id: UUID | None,
                   run_id: UUID | None = None) -> list[str]:
    """Pydantic (the runtime's declared output schema) + holdout + citations + the
    runtime's own deterministic validator (``validate_output(output) -> [codes]``) + its
    agent class's database-backed validator (``check_output``, P6.4-A: cited columns, CV
    metrics, findings) against the run's subject."""

    if output is None:
        return ["no_output"]
    schema = getattr(runtime, "output_schema", None)
    if output.output is not None:
        if schema is not None:
            try:
                schema.model_validate(output.output.model_dump(mode="json"))
            except Exception:  # noqa: BLE001 - any validation failure is a typed reason
                return ["output_schema_invalid"]
        if names_holdout(output.output.model_dump(mode="json")):
            return ["holdout_in_output"]
    elif schema is not None:
        return ["no_output"]
    if output.message is not None and HOLDOUT_KEY.search(output.message):
        return ["holdout_in_output"]
    reasons = citation_reasons(db, output.citations, workspace_id=workspace_id, project_id=project_id)
    custom = getattr(runtime, "validate_output", None)
    if not reasons and callable(custom):
        reasons = [str(code)[:64] for code in custom(output) or ()]
    check = getattr(getattr(runtime, "agent_class", None), "check_output", None)
    if not reasons and callable(check):
        reasons = [str(code)[:64] for code in check(_output_check(db, workspace_id, project_id, run_id), output) or ()]
    return reasons


def _output_check(db: Session, workspace_id: UUID, project_id: UUID | None, run_id: UUID | None) -> Any:
    from app.agents.runtime.base import OutputCheck
    from app.db.models import AgentRun
    from app.domain.agent_records import AGENT_SUBJECT_COLUMNS

    run = db.scalar(select(AgentRun).where(AgentRun.workspace_id == workspace_id, AgentRun.id == run_id)) \
        if run_id is not None else None
    kind = run.subject_kind if run is not None else None
    column = AGENT_SUBJECT_COLUMNS.get(kind or "")
    return OutputCheck(db=db, workspace_id=workspace_id, project_id=project_id, subject_kind=kind,
                       subject_id=getattr(run, column) if column else None)


def draft_reasons(draft: Any) -> list[str]:
    from app.agents.governance.decision_points import REGISTRY

    if draft.proposal_type not in PROPOSAL_TYPES or draft.proposal_type in SERVICE_ONLY_PROPOSAL_TYPES:
        return ["proposal_type_invalid"]
    if draft.decision_point_key not in REGISTRY:
        return ["decision_point_unknown"]
    payload = dict(draft.payload)
    if len(canonical(payload).encode("utf-8")) > PROPOSAL_PAYLOAD_MAX_BYTES:
        return ["proposal_too_large"]
    if names_holdout(payload):
        return ["holdout_in_proposal"]
    return []
