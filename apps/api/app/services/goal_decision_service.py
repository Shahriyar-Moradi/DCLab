"""D3a item 14: who suggested and who confirmed the goal (target, test design, metric, rule).

The owning writer of the reserved ``target_confirmed`` / ``test_design_confirmed`` /
``goal_metric_confirmed`` / ``goal_rule_confirmed`` decision entries (never recorded or
corrected through the generic decision path). Written inside the transaction of the act they
describe, after that act was authorized by its own route:

- the execution-request target and split confirmations (``execution_request_service``);
- a LOCKED goal version a PERSON saved in the product that sets a target, a ranking metric or a
  business rule (``create_problem_spec(record_goal=...)`` from ``POST /v1/projects/{id}/problem-specs``
  and from an accepted AI proposal). Drafts, service-token specs and internal callers write no entry:
  a tool can never make a goal look "confirmed".

The only agent-actor entry is a service token's target confirmation (its route allows tokens):
ACCEPTED because the act already happened, so it is provenance, not an agent decision; its
rationale is always DCLab's default and its value the confirmed column (validated against the
dataset). The rationale is otherwise the person's own text, or a default marked ``rationale_default``.
The read ties target / metric / rule to the project's current ``problem_spec`` ref.
"""

from __future__ import annotations

from typing import Any
from uuid import UUID

from sqlalchemy import String, cast, select
from sqlalchemy.orm import Session

from app.db.models import ExecutionRequest, Experiment, ProblemSpec, ProjectDecisionRecord, User
from app.domain.data_goal import GOAL_VALUE_MAX_CHARS, GOAL_WHY_MAX_CHARS, GoalDecisionRowRead, GoalDecisionsRead
from app.domain.decision_records import (
    DECISION_SPLIT_PLAN_CREATED,
    GOAL_CONFIRMATION_DECISION_TYPES,
    STATE_ACCEPTED,
    DecisionActor,
)
from app.domain.errors import InvalidDecisionRecordError
from app.services import decision_record_service as drs
from app.services.audience_projection import public_diagnostic
from app.services.project_ref_service import current_refs
from app.services.project_service import get_project

_SUGGESTERS = frozenset({"rules", "assistant", "person", "connected_tool"})


def clean_person_rationale(text: str | None) -> str | None:
    """A person's own reason, validated before anything changes (``InvalidDecisionRecordError``: 422)."""

    return drs.clean_rationale(text) if (text or "").strip() else None


def _actor(user: User, service_token_id: UUID | None) -> DecisionActor:
    return DecisionActor.agent(service_token_id=service_token_id) if service_token_id else DecisionActor.human(user)


def _write(db: Session, *, workspace_id: UUID, project_id: UUID, what: str, actor: DecisionActor,
           suggested_by: str, value: str | None, rationale: str | None, default: str,
           subject: ProblemSpec | None = None, details: dict[str, Any] | None = None) -> ProjectDecisionRecord:
    own = rationale if actor.kind == "human" else None  # agent text is never stored here
    facts: dict[str, Any] = {"what": what, "suggested_by": suggested_by, "rationale_default": own is None}
    try:  # user data (a column name) that fails the record's text gate is left out, never the act
        facts = drs.clean_json_object({**facts, "value": value[:GOAL_VALUE_MAX_CHARS] if value else None},
                                      label="facts")
    except InvalidDecisionRecordError:
        facts = drs.clean_json_object(facts, label="facts")
    row = drs.build_record(
        workspace_id=workspace_id, project_id=project_id, actor=actor,
        decision_type=GOAL_CONFIRMATION_DECISION_TYPES[what], state=STATE_ACCEPTED,
        subject_kind="problem_spec" if subject is not None else "project",
        subject_id=subject.id if subject is not None else None,
        subject_digest=drs.node_digest(subject) if subject is not None else None,
        rationale=own or default, facts=facts, evidence_refs=[],
        details=drs.clean_json_object(details or {}, label="details"),
    )
    return drs.insert_record(db, row)


def _name(value: Any) -> str | None:
    text = str(value or "").strip()
    return text or None


def record_target_confirmed(db: Session, *, request: Any, waiting: dict[str, Any], user: User,
                            service_token_id: UUID | None, target: str, rationale: str | None) -> None:
    """The target confirmation of an execution request (skipped without a project)."""

    if request.project_id is None:
        return
    suggestion = waiting.get("ai_suggestion") if isinstance(waiting.get("ai_suggestion"), dict) else {}
    if target == _name(waiting.get("recommended_column")):
        suggested = "rules"
    elif target == _name(suggestion.get("target_column")):
        suggested = "assistant"
    else:
        suggested = "connected_tool" if service_token_id else "person"
    _write(db, workspace_id=request.workspace_id, project_id=request.project_id, what="target",
           actor=_actor(user, service_token_id), suggested_by=suggested, value=target, rationale=rationale,
           default="Target confirmed when the run asked which column to predict.",
           details={"execution_request_id": str(request.id)})


def record_test_design_confirmed(db: Session, *, request: Any, user: User, answer: str,
                                 rationale: str | None) -> None:
    """A person kept the rules' split when a run plan proposed another one (human-only route)."""

    if request.project_id is None:
        return
    _write(db, workspace_id=request.workspace_id, project_id=request.project_id, what="test_design",
           actor=DecisionActor.human(user), suggested_by="rules", value=answer, rationale=rationale,
           default="Kept the rules' test design when a run plan proposed a different split.",
           details={"execution_request_id": str(request.id)})


def record_goal_version(db: Session, *, spec: ProblemSpec, user: User, service_token_id: UUID | None,
                        from_plan: bool, from_proposal: bool = False) -> None:
    """A saved goal version: one entry per part it sets (target, ranking metric, business rule).
    ``from_plan``: a run plan filled target / metric; ``from_proposal``: a person accepted an AI
    proposal of the whole goal version."""

    if service_token_id is not None or spec.status != "locked" or spec.locked_at is None:
        return  # only a person's locked goal confirms anything
    author = "assistant" if from_proposal else "person"
    planned = "assistant" if from_plan else author
    common = {"db": db, "workspace_id": spec.workspace_id, "project_id": spec.project_id,
              "actor": DecisionActor.human(user),
              "rationale": None, "subject": spec, "details": {"problem_spec_version": spec.version}}
    if _name(spec.target_column):
        _write(**common, what="target", suggested_by=planned, value=spec.target_column,
               default=f"Target set in goal version {spec.version}.")
    if _name(spec.primary_metric):
        _write(**common, what="metric", suggested_by=planned, value=spec.primary_metric,
               default=f"Ranking metric set in goal version {spec.version}.")
    if spec.constraints or spec.success_criteria:
        _write(**common, what="rule", suggested_by=author, value=None,
               default=f"Business rule set in goal version {spec.version}.")


def _confirmed_by(row: ProjectDecisionRecord, viewer_id: UUID | None) -> str | None:
    if row.actor_kind == "human":
        return "you" if viewer_id is not None and row.actor_user_id == viewer_id else "person"
    return "connected_tool" if row.actor_service_token_id is not None else None


def _plan_confirmation(db: Session, workspace_id: UUID, project_id: UUID,
                       plan_id: UUID | None) -> ProjectDecisionRecord | None:
    """The newest ``test_design_confirmed`` entry whose execution request's run uses ``plan_id``
    (entry -> ``details.execution_request_id`` -> request -> run -> split plan). An entry for an
    older plan, or for a run that has no plan yet, never describes this one."""

    if plan_id is None:
        return None
    pdr = ProjectDecisionRecord
    return db.scalar(
        select(pdr)
        .join(ExecutionRequest, (ExecutionRequest.workspace_id == pdr.workspace_id)
              & (cast(ExecutionRequest.id, String) == pdr.details["execution_request_id"].astext))
        .join(Experiment, (Experiment.workspace_id == ExecutionRequest.workspace_id)
              & (Experiment.id == ExecutionRequest.pipeline_run_id))
        .where(pdr.workspace_id == workspace_id, pdr.project_id == project_id,
               pdr.decision_type == GOAL_CONFIRMATION_DECISION_TYPES["test_design"], pdr.state == STATE_ACCEPTED,
               Experiment.project_id == project_id, Experiment.split_plan_id == plan_id)
        .order_by(pdr.recorded_at.desc(), pdr.id.desc()).limit(1))


def goal_decisions(db: Session, *, actor: User, workspace_id: UUID, project_id: UUID,
                   viewer_id: UUID | None) -> GoalDecisionsRead:
    """The newest entry per part; the test design is the newest split plan's own confirmation, else
    that plan's entry (suggested by the rules, confirmed by nobody). ``viewer_id`` is None for
    service tokens."""

    project = get_project(db, actor=actor, workspace_id=workspace_id, project_id=project_id)
    ref = current_refs(db, workspace_id=workspace_id, project_id=project.id).get("problem_spec")
    goal_id = ref.problem_spec_id if ref is not None else None
    pdr = ProjectDecisionRecord

    def newest(decision_type: str, *, scoped: bool) -> ProjectDecisionRecord | None:
        query = select(pdr).where(pdr.workspace_id == workspace_id, pdr.project_id == project.id,
                                  pdr.decision_type == decision_type, pdr.state == STATE_ACCEPTED)
        if scoped and goal_id is not None:  # the current goal's own entry, never an older or unadopted one
            query = query.where(pdr.problem_spec_id == goal_id)
        return db.scalar(query.order_by(pdr.recorded_at.desc(), pdr.id.desc()).limit(1))

    found = {what: newest(decision_type, scoped=what != "test_design")
             for what, decision_type in GOAL_CONFIRMATION_DECISION_TYPES.items()}
    found["split_plan"] = newest(DECISION_SPLIT_PLAN_CREATED, scoped=False)
    if found["split_plan"] is not None:  # D8a: only the newest plan's own confirmation describes it
        found["test_design"] = _plan_confirmation(db, workspace_id, project.id, found["split_plan"].split_plan_id)
    items = []
    for what in GOAL_CONFIRMATION_DECISION_TYPES:
        row = found[what]
        if row is None and what == "test_design" and (plan := found["split_plan"]):
            items.append(GoalDecisionRowRead(what=what, suggested_by="rules", confirmed_by=None, when=plan.recorded_at,
                                             why=None, why_is_default=False, value=None, decision_id=plan.id))
            continue
        if row is None:
            items.append(GoalDecisionRowRead(what=what, suggested_by=None, confirmed_by=None, when=None, why=None,
                                             why_is_default=False, value=None, decision_id=None))
            continue
        facts = row.facts if isinstance(row.facts, dict) else {}
        why = public_diagnostic(row.rationale or "")
        value = public_diagnostic(facts.get("value")) if isinstance(facts.get("value"), str) else None
        items.append(GoalDecisionRowRead(
            what=what, suggested_by=facts.get("suggested_by") if facts.get("suggested_by") in _SUGGESTERS else None,
            confirmed_by=_confirmed_by(row, viewer_id), when=row.recorded_at,
            why=why[:GOAL_WHY_MAX_CHARS] if isinstance(why, str) and why else None,
            why_is_default=facts.get("rationale_default") is True,
            value=value[:GOAL_VALUE_MAX_CHARS] if isinstance(value, str) else None, decision_id=row.id,
        ))
    return GoalDecisionsRead(project_id=project.id, items=items)
