"""The ``plan`` input of a root run: accept at request time, re-validate at job run,
supersede pending plans (ADR 0008 § Consequences, §2; P6.9-A step A2).

Acceptance never fails the request: a plan that is not of this workspace and project,
not ``accepted`` (human) / ``applied`` (L2 kinds only), already consumed, or invalid
makes the run rule-only and the refusal is recorded on the execution request
(``request_spec.plan_refusal``). A consumable plan is bound to the request
(``execution_requests.plan_proposal_id``; unique — single use). At job claim the plan is
loaded again and re-validated (status, payload, §1b); the stages re-validate each answer
against the current graph (columns, families, split structure).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any
from uuid import UUID

from pydantic import ValidationError
from sqlalchemy import func, or_, select, update
from sqlalchemy.orm import Session

from app.db.models import AgentProposal, AgentRun, ClientLabUpload, ExecutionRequest, Experiment, WorkflowRun
from app.domain.run_plans import PLAN_PROPOSAL_TYPE, PLAN_STATUSES, ExperimentPlan


@dataclass(frozen=True)
class RunPlan:
    proposal_id: UUID | None
    status: str | None = None  # accepted (human) | applied (L2)
    plan: ExperimentPlan | None = None
    agent_run_id: UUID | None = None
    prompt_release_id: UUID | None = None
    model_id: str | None = None
    refusal: str | None = None
    # Answers refused at job claim although the plan is usable, e.g. a metric or a portfolio
    # choice made after results exist (ADR 0008 §2): field -> refusal code.
    refused_fields: dict[str, str] = field(default_factory=dict)

    @property
    def usable(self) -> bool:
        return self.plan is not None and self.refusal is None

    @property
    def human(self) -> bool:
        return self.status == "accepted"

    def evidence(self) -> dict[str, Any]:
        return {"proposal_id": str(self.proposal_id) if self.proposal_id else None, "status": self.status,
                "refusal": self.refusal, **({"refused_fields": dict(self.refused_fields)} if self.refused_fields else {})}


def _check(db: Session, row: AgentProposal | None, *, project_id: UUID | None) -> tuple[ExperimentPlan | None, str | None]:
    if row is None or row.project_id != project_id:
        return None, "plan_not_found"  # another workspace's or project's row looks the same
    if row.proposal_type != PLAN_PROPOSAL_TYPE:
        return None, "not_a_plan"
    if row.status not in PLAN_STATUSES:
        return None, "plan_not_accepted"
    try:
        plan = ExperimentPlan.model_validate(row.payload)
    except ValidationError:
        return None, "plan_invalid"
    if row.status == "applied" and plan.l1_fields():
        return None, "applied_plan_has_l1_answers"  # §1b: an applied plan carries L2 kinds only
    return plan, None


def plan_for_request(db: Session, *, workspace_id: UUID, project_id: UUID, plan_id: UUID) -> tuple[UUID | None, dict | None]:
    """(the proposal to bind, None) or (None, the refusal to record on the request)."""

    row = db.scalar(select(AgentProposal).where(AgentProposal.id == plan_id, AgentProposal.workspace_id == workspace_id))
    _plan, refusal = _check(db, row, project_id=project_id)
    if refusal is None and db.scalar(select(ExecutionRequest.id).where(
            ExecutionRequest.workspace_id == workspace_id, ExecutionRequest.plan_proposal_id == plan_id).limit(1)):
        refusal = "plan_already_used"
    if refusal is not None:
        return None, {"code": refusal, "proposal_id": str(plan_id)}
    return row.id, None


SPEC_PLAN_FIELDS = ("target_column", "primary_metric")  # target.column, spec.objective (both L1)


def plan_spec_refusals(db: Session, *, workspace_id: UUID, project_id: UUID, plan_id: UUID) -> dict[str, str]:
    """Plan answers a ProblemSpec may not take: an objective answer made after results exist
    (``results_exist``; the same rule ``load_run_plan`` applies to a run)."""

    row = db.scalar(select(AgentProposal).where(AgentProposal.id == plan_id, AgentProposal.workspace_id == workspace_id))
    if row is None or row.project_id != project_id or not (row.payload or {}).get("primary_metric"):
        return {}
    # a spec governs the whole project: any completed experiment of it is a result
    query = select(func.min(Experiment.ended_at)).where(
        Experiment.workspace_id == workspace_id, Experiment.project_id == project_id, Experiment.status == "COMPLETED")
    results_at = db.scalar(query)
    if results_at is not None and row.created_at is not None and row.created_at > results_at:
        return {"primary_metric": RESULTS_EXIST}
    return {}


def plan_spec_answers(db: Session, *, workspace_id: UUID, project_id: UUID, plan_id: UUID) -> dict[str, str]:
    """A ProblemSpec's answers from a run plan (P6.9-A): the same checks as a run's ``plan``
    (``plan_for_request``), then its target and metric. Both points are L1: the spec is a
    draft, or a proposal a person accepts. A metric chosen after results exist is refused
    (``plan_spec_refusals``). A spec does not consume the plan (single use binds runs; a
    spec-side binding would need a migration). Raises ``PlanRefusedError``."""

    from app.domain.errors import PlanRefusedError

    _bound, refusal = plan_for_request(db, workspace_id=workspace_id, project_id=project_id, plan_id=plan_id)
    if refusal is not None:
        raise PlanRefusedError(str(refusal["code"]))
    row = db.get(AgentProposal, plan_id)
    plan = ExperimentPlan.model_validate(row.payload)
    refused = plan_spec_refusals(db, workspace_id=workspace_id, project_id=project_id, plan_id=plan_id)
    answers = {name: getattr(plan, name) for name in SPEC_PLAN_FIELDS
               if getattr(plan, name) is not None and name not in refused}
    if not answers:
        raise PlanRefusedError(RESULTS_EXIST if refused else "plan_has_no_spec_answers")
    return answers


def load_run_plan(db: Session, upload: ClientLabUpload) -> RunPlan | None:
    """The run's plan, re-validated at job claim; None when the request named none."""

    from app.services.execution_request_service import execution_request_for_upload

    request = execution_request_for_upload(db, upload)
    if request is None:
        return None
    refused = (request.request_spec or {}).get("plan_refusal")
    if request.plan_proposal_id is None:
        if not isinstance(refused, dict):
            return None
        try:
            proposal_id = UUID(str(refused.get("proposal_id")))
        except ValueError:
            proposal_id = None
        return RunPlan(proposal_id=proposal_id, refusal=str(refused.get("code") or "plan_refused")[:64])
    row = db.scalar(select(AgentProposal).where(
        AgentProposal.id == request.plan_proposal_id, AgentProposal.workspace_id == request.workspace_id))
    plan, refusal = _check(db, row, project_id=request.project_id)
    if refusal is not None:
        return RunPlan(proposal_id=request.plan_proposal_id, refusal=refusal)
    run = db.get(AgentRun, row.run_id) if row.run_id is not None else None
    results_at = first_results_at(db, request, upload, plan)
    refused = {}
    if plan.split is not None and (request.request_spec or {}).get("split_resolution") == "keep_rule_split":
        refused["split"] = "kept_rule_split"  # a person answered the split confirmation
    if results_at is not None and row.created_at is not None and row.created_at > results_at:
        # "Decisions that cannot follow results": an objective or portfolio answer made after
        # the first completed experiment could have adapted to its results.
        refused |= {name: RESULTS_EXIST for name in RESULTS_BOUND_FIELDS if getattr(plan, name) is not None}
    return RunPlan(proposal_id=row.id, status=row.status, plan=plan, agent_run_id=row.run_id,
                   prompt_release_id=run.prompt_release_id if run is not None else None,
                   model_id=run.model if run is not None else None, refused_fields=refused)


RESULTS_EXIST = "results_exist"
RESULTS_BOUND_FIELDS = ("primary_metric", "families", "max_training_seconds")


def first_results_at(db: Session, request: ExecutionRequest, upload: ClientLabUpload,
                     plan: ExperimentPlan) -> datetime | None:
    """When the first experiment of the run's problem spec completed (without a spec: of
    the same source dataset and target; any target when none is known yet)."""

    workflow_run = db.get(WorkflowRun, request.workflow_run_id) if request.workflow_run_id else None
    query = (select(func.min(Experiment.ended_at)).join(WorkflowRun, WorkflowRun.id == Experiment.workflow_run_id)
             .where(Experiment.workspace_id == request.workspace_id, Experiment.status == "COMPLETED"))
    if request.pipeline_run_id is not None:
        query = query.where(Experiment.id != request.pipeline_run_id)
    if workflow_run is not None and workflow_run.problem_spec_id is not None:
        query = query.where(WorkflowRun.problem_spec_id == workflow_run.problem_spec_id)
    else:
        query = query.where(Experiment.source_dataset_id == upload.dataset_id)
        target = upload.explicit_target_column or plan.target_column
        if target:
            query = query.where(WorkflowRun.resolved_target == target)
    return db.scalar(query)


def supersede_plans(db: Session, *, workspace_id: UUID, project_id: UUID | None, reason: str,
                    dataset_id: UUID | None = None, target_column: str | None = None,
                    problem_spec_id: UUID | None = None) -> int:
    """ADR 0008 §2 "decisions that cannot follow results": pending plans carrying a split
    answer are superseded (``plan_exists``) once a SplitPlan exists for (source dataset,
    target, task); pending plans carrying a primary metric or a portfolio answer (families,
    time budget; ``results_exist``) once the spec's first experiment completes (a plan made
    later is refused those answers at job claim, ``load_run_plan``). The Planner's plans are
    about the dataset (their one subject column; the subject CHECK leaves ``problem_spec_id``
    NULL), so ``results_exist`` also matches spec-less plans on the run's source dataset
    (``dataset_id``) whose target is unset or the run's (``target_column``). Answers are
    tested as JSON text (a JSON ``null`` is not an answer). Not committed."""

    if project_id is None:
        return 0
    query = update(AgentProposal).where(
        AgentProposal.workspace_id == workspace_id, AgentProposal.project_id == project_id,
        AgentProposal.proposal_type == PLAN_PROPOSAL_TYPE, AgentProposal.status == "proposed",
    )
    if reason == "plan_exists":
        query = query.where(AgentProposal.payload["split"].isnot(None),
                            AgentProposal.payload["split"].astext != "null")
        if dataset_id is not None:
            query = query.where((AgentProposal.dataset_id == dataset_id) | AgentProposal.dataset_id.is_(None))
        if target_column is not None:
            query = query.where(AgentProposal.payload["target_column"].astext.is_(None)
                                | (AgentProposal.payload["target_column"].astext == target_column))
    elif reason == "results_exist":  # objective and portfolio answers (ADR 0008 §2)
        subjects = [AgentProposal.problem_spec_id == problem_spec_id] if problem_spec_id is not None else []
        if dataset_id is not None:
            target = AgentProposal.payload["target_column"].astext
            on_dataset = AgentProposal.problem_spec_id.is_(None) & (AgentProposal.dataset_id == dataset_id)
            subjects.append(on_dataset & (target.is_(None) | (target == target_column)) if target_column
                            else on_dataset)
        if not subjects:
            return 0
        query = query.where(or_(*subjects), or_(*(AgentProposal.payload[name].astext.isnot(None)
                                                  for name in RESULTS_BOUND_FIELDS)))
    else:
        raise ValueError(reason)
    result = db.execute(query.values(status="superseded", supersede_reason=reason)
                        .execution_options(synchronize_session=False))
    return int(result.rowcount or 0)


def supersede_for_results(db: Session, experiment: Experiment, workflow_run: WorkflowRun | None) -> int:
    """``run_finalize``: a completed experiment supersedes the pending objective / portfolio
    plans of its spec and the Planner's plans on its source dataset (same or no target)."""

    spec_id = workflow_run.problem_spec_id if workflow_run is not None else None
    if spec_id is None and experiment.source_dataset_id is None:
        return 0
    return supersede_plans(db, workspace_id=experiment.workspace_id, project_id=experiment.project_id,
                           reason=RESULTS_EXIST, problem_spec_id=spec_id, dataset_id=experiment.source_dataset_id,
                           target_column=workflow_run.resolved_target if workflow_run is not None else None)


__all__ = ["RunPlan", "load_run_plan", "plan_for_request", "plan_spec_refusals", "supersede_for_results", "supersede_plans"]
