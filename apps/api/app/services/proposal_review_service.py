"""Proposal review flow: read, accept, reject, revert (P6.6-A; ADR 0009 §2.3, §6; ADR 0008 §7).

``agent_proposals`` is the one workflow item for specialist agents, Jev L1 review items and the
assistant's ``ToolCallProposal``s; this module is its only decider. A proposal is never the
authority: a signed-in person accepts it, and acceptance runs the NORMAL command service of that
proposal type as that person (the function the ``/v1`` route calls, which re-authorizes), in the same
transaction as the conditional ``proposed -> accepted`` UPDATE and the ``proposal_accepted``
decision record (actor = the person; evidence = the proposal and the AI answer). The status
compare-and-set makes a double accept impossible (0 rows -> 409); a command or validator failure
rolls everything back. Service tokens never reach the decide functions (HUMAN_ONLY_ROUTES).

Acceptance by type: ``ExperimentPlanProposal`` -> ``accepted`` (a run's ``plan`` input consumes it
later); advice (``ExperimentReviewProposal``, ``DatasetInvestigationProposal``,
``ImprovementActionProposal``) -> ``accepted`` (an acknowledgement; nothing is applied);
``SemanticReviewProposal`` -> a branch of the experiment with the answer's change (``applied``);
``ToolCallProposal`` -> its catalog tool through the service its route calls (``applied``; ref moves
need the versions the person saw). Revert: an applied L2 plan or an applied Jev item restores the RULE
value through a branch with the recorded revert changes and a superseding ``proposal_reverted``
record; anything else is not revertible in place (409).

Authority: every decision needs ML-write (``can_execute_workspace_ml``); ref moves (incl. a champion)
need ``promote_authority`` — the same human ML-write authority ``POST /v1/projects/{id}/refs/{kind}``
needs — and the ``If-Match`` versions of the refs they move.
"""

from __future__ import annotations

import json
import logging
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

from sqlalchemy import and_, case, func, or_, select, tuple_, update
from sqlalchemy.orm import Session, aliased

from app.agents.tools.catalog import ToolError
from app.agents.tools.catalog import get as get_tool
from app.agents.tools.shaping import HOLDOUT_KEY, strip_holdout
from app.db.models import AgentProposal, AgentRun, Experiment, ProjectDecisionRecord, User
from app.domain.agent_records import AGENT_SUBJECT_COLUMNS
from app.domain.decision_records import (
    EVIDENCE_REF_KINDS,
    STATE_ACCEPTED,
    STATE_REJECTED,
    SUBJECT_COLUMNS,
    DecisionActor,
)
from app.domain.errors import InvalidDecisionRecordError
from app.domain.proposal_reviews import (
    PAGE_DEFAULT,
    PAGE_MAX,
    UNVERIFIED_LABEL,
    AgentRunRead,
    ProposalRead,
    SubjectRead,
)
from app.domain.run_plans import PLAN_PROPOSAL_TYPE, ExperimentPlan
from app.services import decision_record_service as drs
from app.services import project_ref_service as prs
from app.services.authorization_service import can_approve_ai_policy, can_execute_workspace_ml, can_read_workspace
from app.services.cursor_codec import open_cursor, scope_digest, sign_cursor

logger = logging.getLogger("dclab.proposals")

ADVICE_TYPES = frozenset({"ExperimentReviewProposal", "DatasetInvestigationProposal", "ImprovementActionProposal"})
DECIDABLE_TYPES = ADVICE_TYPES | {PLAN_PROPOSAL_TYPE, "SemanticReviewProposal", "ToolCallProposal"}
RUN_KINDS_LISTED = ("specialist", "ops")  # assistant threads and their turns are read through /v1/assistant
DETAILS_ARGUMENTS_MAX = 6000


class ProposalError(Exception):
    def __init__(self, http_status: int, code: str, message: str, **details: Any) -> None:
        super().__init__(message)
        self.status, self.code, self.message, self.details = http_status, code, message, details


def promote_authority(db: Session, user: User, workspace_id: UUID) -> bool:
    """Who may accept a ref move or a champion: the human ML-write authority of the refs route."""

    return can_execute_workspace_ml(db, user, workspace_id)


# --- reads ---------------------------------------------------------------------------------------


def _aware(value: datetime | None) -> datetime | None:
    return value if value is None or value.tzinfo is not None else value.replace(tzinfo=UTC)


def proposed_by_kind(row: AgentProposal) -> str:
    return "jev" if row.semantic_answer_id is not None else ("assistant" if row.proposal_type == "ToolCallProposal"
                                                              else "agent")


def is_open(row: AgentProposal) -> bool:
    expires = _aware(row.expires_at)
    return row.status == "proposed" and (expires is None or expires > datetime.now(UTC))


def _subject(kind: str | None, row: Any) -> SubjectRead:
    column = AGENT_SUBJECT_COLUMNS.get(kind or "")
    return SubjectRead(kind=kind or "project", id=getattr(row, column) if column else None)


def proposal_read(row: AgentProposal, *, agent: bool) -> ProposalRead:
    """The bounded /v1 projection. ``agent`` (service tokens, MCP): no holdout key or scope anywhere."""

    shape = strip_holdout if agent else (lambda value: value)
    rationale = row.proposed_rationale
    reasons = list(row.validator_reasons or [])
    if agent:  # free text for an agent reader: nothing that names the holdout
        rationale = None if rationale and HOLDOUT_KEY.search(rationale) else rationale
        reasons = [r for r in strip_holdout(reasons) if not (isinstance(r, str) and HOLDOUT_KEY.search(r))]
    return ProposalRead(
        id=row.id, project_id=row.project_id, source="jev" if row.semantic_answer_id else "agent_run",
        run_id=row.run_id, semantic_answer_id=row.semantic_answer_id, decision_point_key=row.decision_point_key,
        level_at_proposal=row.level_at_proposal, answer_ceiling=row.answer_ceiling, proposal_type=row.proposal_type,
        proposed_by=proposed_by_kind(row), schema_version=row.schema_version, status=row.status,
        supersede_reason=row.supersede_reason, open=is_open(row), subject=_subject(row.subject_kind, row),
        payload=shape(row.payload), rule_answer=shape(row.rule_answer), citations=shape(list(row.citations or [])),
        validator_verdict=row.validator_verdict, validator_reasons=reasons,
        tool_name=row.tool_name, tool_arguments=shape(row.tool_arguments), proposed_rationale=rationale,
        proposed_rationale_label=UNVERIFIED_LABEL if rationale else None,
        estimated_cost_micros=row.estimated_cost_micros, estimated_duration_s=row.estimated_duration_s,
        expires_at=row.expires_at, decided_by_user_id=row.decided_by_user_id, decided_at=row.decided_at,
        decision_record_id=row.decision_record_id, applied_decision_record_id=row.applied_decision_record_id,
        created_at=row.created_at)


def _page(rows: list[Any], limit: int, scope: str, stamp: Callable[[Any], tuple[datetime, UUID]]) -> tuple[list, str | None]:
    page = rows[:limit]
    cursor = None
    if len(rows) > limit:
        at, ident = stamp(page[-1])
        cursor = sign_cursor(scope, [_aware(at).isoformat(), str(ident)])
    return page, cursor


def _after(cursor: str | None, scope: str) -> tuple[datetime, UUID] | None:
    if not cursor or not cursor.strip():
        return None
    try:
        stamp, ident = open_cursor(cursor, scope, message="cursor is not for this list and filter set")
        return datetime.fromisoformat(stamp), UUID(ident)
    except (ValueError, TypeError) as exc:
        from app.domain.errors import InvalidCursorError

        raise InvalidCursorError("cursor is not for this list and filter set") from exc


def owner_user_id_sql() -> Any:
    """Whose conversation a proposal came from, as a scalar subquery correlated to ``agent_proposals``:
    the creator of its run's parent (the assistant thread), else of the run itself (P4.16-A reads
    it for a whole page in one statement)."""

    run, parent = aliased(AgentRun), aliased(AgentRun)
    return (
        select(case((parent.id.is_not(None), parent.created_by_user_id), else_=run.created_by_user_id))
        .select_from(run)
        .outerjoin(parent, and_(parent.id == run.parent_run_id, parent.workspace_id == run.workspace_id))
        .where(run.id == AgentProposal.run_id, run.workspace_id == AgentProposal.workspace_id)
        .correlate(AgentProposal)
        .scalar_subquery()
    )


def _owner_id(db: Session, row: AgentProposal) -> UUID | None:
    """Whose conversation an assistant proposal came from: the creator of the lead run's thread."""

    if row.run_id is None:
        return None
    return db.scalar(select(owner_user_id_sql()).select_from(AgentProposal).where(
        AgentProposal.id == row.id, AgentProposal.workspace_id == row.workspace_id))


def can_see(db: Session, user: User, row: AgentProposal) -> bool:
    """Assistant tool calls are thread content (ADR 0009 §7.2): the thread's owner and the workspace's
    approvers; every other proposal is visible to workspace readers."""

    if row.proposal_type != "ToolCallProposal":
        return True
    return user.id == _owner_id(db, row) or can_approve_ai_policy(db, user, row.workspace_id)


def visibility_clause(db: Session, *, workspace_id: UUID, viewer: User | None, agent: bool) -> Any | None:
    """The SQL form of ``can_see`` for lists (``None``: no restriction). Assistant tool calls: never for
    an agent reader; for a person only their own threads' (approvers: all)."""

    if not (agent or (viewer is not None and not can_approve_ai_policy(db, viewer, workspace_id))):
        return None
    if agent:
        return AgentProposal.proposal_type != "ToolCallProposal"
    # The same owner as ``_owner_id``: a list never shows a call that ``get_proposal`` would 404.
    return or_(AgentProposal.proposal_type != "ToolCallProposal", owner_user_id_sql() == viewer.id)


def list_proposals(db: Session, *, workspace_id: UUID, project_id: UUID | None = None, run_id: UUID | None = None,
                   level: int | None = None, decision_point_key: str | None = None, status: str | None = None,
                   proposal_type: str | None = None, cursor: str | None = None,
                   limit: int = PAGE_DEFAULT, viewer: User | None = None,
                   agent: bool = False) -> tuple[list[AgentProposal], str | None, int]:
    """The workspace's proposals, newest first (workspace-scoped; the caller holds workspace read).
    Assistant tool calls: never for an agent reader; for a person only their own threads' (approvers: all)."""

    limit = max(1, min(int(limit), PAGE_MAX))
    filters = {"project": project_id, "run": run_id, "level": level, "point": decision_point_key,
               "status": status, "type": proposal_type, "viewer": viewer.id if viewer else None, "agent": agent}
    scope = f"proposals:{workspace_id}:{scope_digest(filters)}"
    stmt = select(AgentProposal).where(AgentProposal.workspace_id == workspace_id)
    for column, value in ((AgentProposal.project_id, project_id), (AgentProposal.run_id, run_id),
                          (AgentProposal.level_at_proposal, level),
                          (AgentProposal.decision_point_key, decision_point_key), (AgentProposal.status, status),
                          (AgentProposal.proposal_type, proposal_type)):
        if value is not None:
            stmt = stmt.where(column == value)
    visible = visibility_clause(db, workspace_id=workspace_id, viewer=viewer, agent=agent)
    if visible is not None:
        stmt = stmt.where(visible)
    after = _after(cursor, scope)
    if after is not None:
        stmt = stmt.where(tuple_(AgentProposal.created_at, AgentProposal.id) < tuple_(*after))
    rows = list(db.scalars(stmt.order_by(AgentProposal.created_at.desc(), AgentProposal.id.desc()).limit(limit + 1)))
    page, next_cursor = _page(rows, limit, scope, lambda r: (r.created_at, r.id))
    return page, next_cursor, limit


def get_proposal(db: Session, *, workspace_id: UUID, proposal_id: UUID, viewer: User | None = None,
                 agent: bool = False) -> AgentProposal:
    row = db.scalar(select(AgentProposal).where(AgentProposal.id == proposal_id,
                                                AgentProposal.workspace_id == workspace_id))
    if row is not None and ((agent and row.proposal_type == "ToolCallProposal")
                            or (viewer is not None and not can_see(db, viewer, row))):
        row = None  # an assistant thread's content: the same 404 as an unknown id
    if row is None:
        raise ProposalError(404, "not_found", "proposal not found")  # another workspace's looks the same
    return row


def agent_run_read(db: Session, run: AgentRun) -> AgentRunRead:
    ids = list(db.scalars(select(AgentProposal.id).where(
        AgentProposal.workspace_id == run.workspace_id, AgentProposal.run_id == run.id).limit(100)))
    return AgentRunRead(
        id=run.id, project_id=run.project_id, kind=run.kind, agent_key=run.agent_key, agent_version=run.agent_version,
        runtime=run.runtime, purpose=run.purpose, decision_point_key=run.decision_point_key,
        subject=_subject(run.subject_kind, run), status=run.status, error_code=run.error_code,
        cost_micros=run.cost_micros, currency=run.currency, usage=strip_holdout(dict(run.usage or {})),
        parent_run_id=run.parent_run_id, proposal_ids=ids,
        requested_by="service_token" if run.created_by_service_token_id else "user",
        created_at=run.created_at, started_at=run.started_at, finished_at=run.finished_at)


def list_agent_runs(db: Session, *, workspace_id: UUID, project_id: UUID | None = None, agent_key: str | None = None,
                    status: str | None = None, cursor: str | None = None,
                    limit: int = PAGE_DEFAULT) -> tuple[list[AgentRun], str | None, int]:
    limit = max(1, min(int(limit), PAGE_MAX))
    scope = f"agent-runs:{workspace_id}:{scope_digest({'project': project_id, 'agent': agent_key, 'status': status})}"
    stmt = select(AgentRun).where(AgentRun.workspace_id == workspace_id, AgentRun.kind.in_(RUN_KINDS_LISTED))
    for column, value in ((AgentRun.project_id, project_id), (AgentRun.agent_key, agent_key),
                          (AgentRun.status, status)):
        if value is not None:
            stmt = stmt.where(column == value)
    after = _after(cursor, scope)
    if after is not None:
        stmt = stmt.where(tuple_(AgentRun.created_at, AgentRun.id) < tuple_(*after))
    rows = list(db.scalars(stmt.order_by(AgentRun.created_at.desc(), AgentRun.id.desc()).limit(limit + 1)))
    page, next_cursor = _page(rows, limit, scope, lambda r: (r.created_at, r.id))
    return page, next_cursor, limit


def get_agent_run(db: Session, *, workspace_id: UUID, run_id: UUID) -> AgentRun:
    run = db.scalar(select(AgentRun).where(AgentRun.id == run_id, AgentRun.workspace_id == workspace_id,
                                           AgentRun.kind.in_(RUN_KINDS_LISTED)))
    if run is None:
        raise ProposalError(404, "not_found", "agent run not found")
    return run


# --- deciding ------------------------------------------------------------------------------------


@dataclass
class _Ctx:
    db: Session
    user: User
    workspace_id: UUID
    row: AgentProposal
    rationale: str | None
    ref_versions: dict[str, int | None] | None
    bind: Callable[[Any], None]
    extra: dict[str, Any] = field(default_factory=dict)  # facts the decision record carries

    @property
    def actor(self) -> DecisionActor:
        return DecisionActor.human(self.user)


_NOT_OPEN = {"superseded": "proposal_superseded", "expired": "proposal_expired",
             "shadow": "proposal_not_actionable", "rejected_by_validator": "proposal_not_actionable"}


def _load(db: Session, user: User, workspace_id: UUID, proposal_id: UUID, action: str = "accept") -> AgentProposal:
    """Workspace-scoped (404 across tenants and for another person's assistant thread), then the capability
    every decision needs (403). An assistant tool call is accepted or reverted by its thread's owner only;
    the owner or a workspace approver may reject it."""

    row = get_proposal(db, workspace_id=workspace_id, proposal_id=proposal_id, viewer=user)
    if not can_read_workspace(db, user, workspace_id):
        raise ProposalError(404, "not_found", "proposal not found")
    if not can_execute_workspace_ml(db, user, workspace_id):
        raise ProposalError(403, "capability_denied", "deciding a proposal needs workspace ML-write access")
    if row.proposal_type not in DECIDABLE_TYPES:
        raise ProposalError(409, "proposal_not_actionable", f"{row.proposal_type} is not decided here")
    if (row.proposal_type == "ToolCallProposal" and action != "reject" and user.id != _owner_id(db, row)):
        raise ProposalError(403, "owner_only", "only the person whose conversation proposed this can confirm it")
    return row


def _not_open(row: AgentProposal, wanted: str) -> ProposalError:
    expired = row.status == "proposed" and not is_open(row)
    code = "proposal_expired" if expired else _NOT_OPEN.get(row.status, "proposal_not_open" if wanted == "open"
                                                          else "proposal_not_applied")
    return ProposalError(409, code, f"the proposal is {'expired' if expired else row.status}",
                         proposal_status=row.status, supersede_reason=row.supersede_reason)


def _cas(db: Session, user: User, row: AgentProposal, new: str, *, wanted: str, **extra: Any) -> None:
    """The status compare-and-set: 0 rows means someone else decided, or the state is wrong."""

    old = "proposed" if wanted == "open" else "applied"
    where = [AgentProposal.id == row.id, AgentProposal.workspace_id == row.workspace_id, AgentProposal.status == old]
    values: dict[str, Any] = {"status": new, **extra}  # a final status takes its links in this UPDATE
    if wanted == "open":
        where.append(or_(AgentProposal.expires_at.is_(None), AgentProposal.expires_at > func.now()))
        values |= {"decided_by_user_id": user.id, "decided_at": func.now()}
    if db.execute(update(AgentProposal).where(*where).values(**values)
                  .execution_options(synchronize_session=False)).rowcount != 1:
        db.refresh(row)
        raise _not_open(row, wanted)
    db.refresh(row)


def _text(ctx: _Ctx, default: str) -> str:
    return drs.clean_rationale(ctx.rationale if ctx.rationale and ctx.rationale.strip() else default)


def _details(row: AgentProposal, extra: dict[str, Any]) -> dict[str, Any]:
    details: dict[str, Any] = {
        "proposal_id": str(row.id), "proposal_type": row.proposal_type, "decision_point": row.decision_point_key,
        "level": row.level_at_proposal, "proposed_by": proposed_by_kind(row),
        "run_id": str(row.run_id) if row.run_id else None,
        "semantic_answer_id": str(row.semantic_answer_id) if row.semantic_answer_id else None,
        "validator_verdict": row.validator_verdict, "payload_digest": row.payload_digest, **extra,
    }
    if row.semantic_answer_id is not None:  # the AI answer the person judged
        payload = row.payload or {}
        details["ai_answer"] = {key: payload.get(key) for key in ("column", "rule", "ai", "confidence")}
    if row.tool_name:
        details["tool"] = row.tool_name
        encoded = json.dumps(row.tool_arguments or {}, sort_keys=True, default=str)
        details["tool_arguments" if len(encoded) <= DETAILS_ARGUMENTS_MAX else "tool_arguments_digest"] = (
            row.tool_arguments if len(encoded) <= DETAILS_ARGUMENTS_MAX else row.payload_digest)
    return details


def _linked_record(db: Session, row: AgentProposal) -> ProjectDecisionRecord | None:
    """The ``decision_point_resolved`` record that lists this proposal (a Jev review item's). One record
    covers many items, so a decision LINKS to it (evidence ref) and never supersedes it."""

    pdr = ProjectDecisionRecord
    return db.scalars(select(pdr).where(
        pdr.workspace_id == row.workspace_id, pdr.project_id == row.project_id,
        pdr.decision_type == "decision_point_resolved",
        pdr.details["proposal_ids"].contains([str(row.id)])).order_by(pdr.recorded_at.desc()).limit(1)).first()


def _write_record(ctx: _Ctx, decision_type: str, state: str, rationale: str, extra: dict[str, Any], *,
                  supersedes: ProjectDecisionRecord | None = None, link: ProjectDecisionRecord | None = None,
                  key_suffix: str = "") -> ProjectDecisionRecord:
    row, db = ctx.row, ctx.db
    kind = row.subject_kind if row.subject_kind in SUBJECT_COLUMNS else "project"
    subject_id = getattr(row, AGENT_SUBJECT_COLUMNS[kind]) if kind in AGENT_SUBJECT_COLUMNS else None
    subject = drs.load_subject(db, workspace_id=ctx.workspace_id, project_id=row.project_id, subject_kind=kind,
                               subject_id=subject_id)
    refs = [drs.evidence_ref(kind, subject_id)] if subject_id and kind in EVIDENCE_REF_KINDS else []
    extra = {**ctx.extra, **extra}
    if link is not None:
        refs.append(drs.evidence_ref("decision_record", link.id))
        extra["resolved_record_id"] = str(link.id)
    if decision_type == "proposal_reverted":
        extra["reverted_by"] = str(ctx.user.id)
    details = _details(row, extra)
    try:
        details = drs.clean_json_object(details, label="details")
    except InvalidDecisionRecordError:  # free text (a tool argument) the record refuses: keep the digest only
        details.pop("tool_arguments", None)
        details = drs.clean_json_object({**details, "arguments_omitted": True}, label="details")
    proposed_text = (row.proposed_rationale or "").strip()
    if proposed_text:  # the agent's text, always untrusted (never the record's own rationale)
        try:
            details = drs.clean_json_object({**details, "proposed_rationale": proposed_text[:1000]}, label="details")
        except InvalidDecisionRecordError:
            details["proposed_rationale_omitted"] = True
    record = drs.build_record(
        workspace_id=ctx.workspace_id, project_id=row.project_id, actor=ctx.actor, decision_type=decision_type,
        state=state, subject_kind=kind, subject_id=subject.id if subject is not None else None,
        subject_digest=drs.node_digest(subject), rationale=rationale, facts={"proposal_id": str(row.id)},
        evidence_refs=refs, details=details, supersedes_id=supersedes.id if supersedes is not None else None,
        idempotency_key=drs.scoped_idempotency_key(ctx.actor, f"{decision_type}:{row.id}{key_suffix}"))
    return drs.insert_record(db, record)


def _default_rationale(ctx: _Ctx, verb: str) -> str:
    return f"{verb} {proposed_by_kind(ctx.row)} proposal {ctx.row.id}"


def _finish(ctx: _Ctx, executed: dict[str, Any] | None, *, applied: bool) -> ProjectDecisionRecord:
    """Inside the command's transaction (before its commit): the ``proposal_accepted`` record, the
    proposal's links and, for an executed command, ``accepted -> applied``; then bind the key."""

    row = ctx.row
    record = _write_record(ctx, "proposal_accepted", STATE_ACCEPTED, _text(ctx, _default_rationale(ctx, "accepted")),
                           {"executed": executed} if executed else {}, link=_linked_record(ctx.db, row))
    row.decision_record_id = record.id
    if applied:
        row.status = "applied"
    ctx.db.flush()
    ctx.bind(row.id)
    return record


def _run(ctx: _Ctx, command: Callable[[Callable[[Any], None]], Any] | None, kind: str = "experiment") -> None:
    """Execute ``command(hook)``: commands with a ``before_commit`` commit themselves after the
    hook; the rest return and are committed here. ``None``: an acknowledgement (no command)."""

    if command is None:
        _finish(ctx, None, applied=False)
        ctx.db.commit()
        return
    done: list[bool] = []

    def hook(obj: Any) -> None:
        done.append(True)
        _finish(ctx, {"kind": kind, "id": str(getattr(obj, "id", obj))}, applied=True)

    result = command(hook)
    if not done:  # a service that returned without calling the hook (no before_commit)
        hook(result)
        ctx.db.commit()


def accept(db: Session, *, user: User, workspace_id: UUID, proposal_id: UUID, rationale: str | None,
           ref_versions: dict[str, int | None] | None, bind: Callable[[Any], None]) -> AgentProposal:
    """``proposed -> accepted`` (and ``applied`` once its command ran). Commits."""

    row = _load(db, user, workspace_id, proposal_id)
    if row.status != "proposed" or not is_open(row):
        raise _not_open(row, "open")
    ctx = _Ctx(db, user, workspace_id, row, rationale, ref_versions, bind)
    command, kind = _command_for(ctx)  # validates and authorizes before anything changes
    _cas(db, user, row, "accepted", wanted="open")
    _run(ctx, command, kind)
    db.refresh(row)
    return row


def reject(db: Session, *, user: User, workspace_id: UUID, proposal_id: UUID, rationale: str | None,
           bind: Callable[[Any], None]) -> AgentProposal:
    """``proposed -> rejected`` (terminal) with a ``proposal_rejected`` record. Commits."""

    row = _load(db, user, workspace_id, proposal_id, "reject")
    if row.status != "proposed" or not is_open(row):
        raise _not_open(row, "open")
    ctx = _Ctx(db, user, workspace_id, row, rationale, None, bind)
    record = _write_record(ctx, "proposal_rejected", STATE_REJECTED, _text(ctx, _default_rationale(ctx, "rejected")),
                           {}, link=_linked_record(db, row))
    _cas(db, user, row, "rejected", wanted="open", decision_record_id=record.id)  # 0 rows rolls the record back
    bind(row.id)
    db.commit()
    db.refresh(row)
    return row


# --- commands by proposal type --------------------------------------------------------------------


def _command_for(ctx: _Ctx) -> tuple[Callable[[Callable[[Any], None]], Any] | None, str]:
    row = ctx.row
    if row.proposal_type == PLAN_PROPOSAL_TYPE:
        try:
            ExperimentPlan.model_validate(row.payload)
        except Exception:  # noqa: BLE001
            raise ProposalError(409, "proposal_invalid", "the stored plan no longer validates") from None
        return None, "plan"
    if row.proposal_type in ADVICE_TYPES:
        return None, "advice"
    if row.proposal_type == "SemanticReviewProposal":
        return _semantic_command(ctx)
    return _tool_command(ctx)


def _branch(ctx: _Ctx, parent_id: UUID, changes: list[dict[str, Any]], intent: str):
    from app.services.experiment_branch_service import branch_experiment

    def command(hook: Callable[[Any], None]) -> Any:
        return branch_experiment(ctx.db, actor=ctx.user, workspace_id=ctx.workspace_id, parent_id=parent_id,
                                 changes={"changes": changes}, intent=intent, before_commit=hook).experiment

    return command


def payload_ai(row: AgentProposal) -> Any:
    return (row.payload or {}).get("ai")


def _semantic_command(ctx: _Ctx):
    """Apply the Jev answer through the decision-point change table (ADR 0008 §1b; an L1 item is a
    person's to accept, so exclusions and drops are allowed here)."""

    row = ctx.row
    change = (row.payload or {}).get("accept_change")
    if not change:  # e.g. a leakage flag: acknowledged only, nothing is applied
        ctx.extra["acknowledgement_only"] = True
        return None, "advice"
    if row.experiment_id is None:
        raise ProposalError(409, "proposal_invalid", "the review item has no experiment to branch")
    from app.domain.experiment_changes import ExperimentChangeSet

    try:
        ExperimentChangeSet.model_validate({"changes": [change]})
    except Exception:  # noqa: BLE001
        raise ProposalError(409, "proposal_invalid", "the stored change no longer validates") from None
    evidence = (row.payload or {}).get("evidence") or {}
    band = evidence.get("cardinality")
    band = band.get("untrusted_text") if isinstance(band, dict) else band
    if payload_ai(row) == "categorical_code" and band in ("high", "very_high"):  # the engine's one-hot cap
        raise ProposalError(409, "proposal_invalid", "the column has too many distinct values to treat as categorical",
                            reason="above_one_hot_cardinality_cap")
    return _branch(ctx, row.experiment_id, [change], f"Accept Jev review item {row.id}"[:500]), "experiment"


def _tool_command(ctx: _Ctx):
    from app.agents.harness.validation import check_proposal_nodes, parse_tool_arguments

    row = ctx.row
    definition = get_tool(row.tool_name or "")
    if definition is None or definition.effect != "proposal":
        raise ProposalError(409, "proposal_invalid", "the tool is not in the catalog")
    try:  # the validator again, against the current graph
        args = parse_tool_arguments(definition, row.tool_arguments or {})
        check_proposal_nodes(ctx.db, workspace_id=ctx.workspace_id, project_id=row.project_id, tool=definition.name,
                             arguments=args)
    except ToolError as exc:
        raise ProposalError(409, "proposal_stale", exc.message[:300], reason=exc.code) from None
    moves = args.get("ref_moves") if row.tool_name == "record_decision" else None
    if (moves or row.tool_name == "propose_problem_spec") and not promote_authority(ctx.db, ctx.user, ctx.workspace_id):
        raise ProposalError(403, "capability_denied", "moving a ref needs promote authority")
    handler = _TOOLS.get(row.tool_name or "")
    if handler is None:
        raise ProposalError(409, "proposal_not_actionable", f"{row.tool_name} is not decided here")
    return handler(ctx, args)


def _uid(value: Any) -> UUID | None:
    return UUID(str(value)) if value else None


def _note_plan_refusals(ctx: _Ctx, args: dict[str, Any]) -> None:
    """A spec that names a plan takes its answers under the run rules: an objective chosen after results
    exist is refused (``results_exist``); the refused fields are a fact on the record."""

    from app.services.run_plan_service import plan_spec_refusals

    if args.get("plan"):
        refused = plan_spec_refusals(ctx.db, workspace_id=ctx.workspace_id, project_id=ctx.row.project_id,
                                     plan_id=UUID(str(args["plan"])))
        if refused:
            ctx.extra["plan_fields_refused"] = refused


def _t_propose_spec(ctx: _Ctx, args: dict[str, Any]):
    from app.services.problem_spec_service import create_problem_spec

    _note_plan_refusals(ctx, args)

    def command(hook: Callable[[Any], None]) -> Any:
        fields = {k: v for k, v in args.items() if k not in ("project_id", "rationale")}
        spec = create_problem_spec(ctx.db, actor=ctx.user, workspace_id=ctx.workspace_id,
                                   project_id=ctx.row.project_id, status="locked", **fields)
        ctx.db.flush()
        result = _move_refs(ctx, {"problem_spec": spec.id}, [drs.evidence_ref("problem_spec", spec.id)],
                            args["rationale"], None)
        hook(spec)
        ctx.db.commit()
        return result

    return command, "problem_spec"


def _t_run(ctx: _Ctx, args: dict[str, Any]):
    from app.services.experiment_service import start_root_experiment

    def command(hook: Callable[[Any], None]) -> Any:
        return start_root_experiment(
            ctx.db, actor=ctx.user, workspace_id=ctx.workspace_id,
            **{k: v for k, v in args.items() if v is not None}, before_commit=hook).experiment

    return command, "experiment"


def _t_branch(ctx: _Ctx, args: dict[str, Any]):
    return _branch(ctx, UUID(str(args["experiment_id"])), list(args["changes"]), args["intent"]), "experiment"


def _t_predict(ctx: _Ctx, args: dict[str, Any]):
    from app.services.batch_prediction_service import create_batch_prediction

    def command(hook: Callable[[Any], None]) -> Any:
        return create_batch_prediction(
            ctx.db, actor=ctx.user, workspace_id=ctx.workspace_id, model_version_id=UUID(str(args["model_version_id"])),
            dataset_id=UUID(str(args["dataset_id"])), output_format=args.get("output_format", "csv"),
            before_commit=hook)

    return command, "batch_prediction"


def _check_champion(ctx: _Ctx, model: Any) -> None:
    """An agent-proposed champion needs a run whose deterministic verification did not FAIL; the SplitPlan's
    holdout-look count (locked evaluations on that plan) is recorded with the decision."""

    from app.db.models import MlRunVerification

    status = ctx.db.scalar(select(MlRunVerification.deterministic_status).where(
        MlRunVerification.workspace_id == ctx.workspace_id, MlRunVerification.experiment_id == model.pipeline_run_id
    ).order_by(MlRunVerification.started_at.desc()).limit(1))
    if status == "FAILED":
        raise ProposalError(409, "verification_failed",
                            "this model's run failed its deterministic verification; it cannot be promoted from a proposal")
    experiment = ctx.db.scalar(select(Experiment).where(
        Experiment.id == model.pipeline_run_id, Experiment.workspace_id == ctx.workspace_id))
    if experiment is not None and experiment.split_plan_id is not None:
        ctx.extra["holdout_looks"] = int(ctx.db.scalar(select(func.count()).select_from(Experiment).where(
            Experiment.workspace_id == ctx.workspace_id, Experiment.split_plan_id == experiment.split_plan_id,
            Experiment.scientific_evidence_locked_at.is_not(None))) or 0)


def _move_refs(ctx: _Ctx, targets: dict[str, UUID], evidence: list[dict[str, str]], rationale: str,
               facts: dict[str, Any] | None):
    """A ref move as the accepting person: they send the versions they saw; a champion also moves the
    model's recipe and carries the model's own final evaluation (a human may see it)."""

    from app.db.models import FeatureSetVersion, ModelVersion

    kinds = dict(targets)
    evidence = list(evidence)
    if "champion_model" in kinds:
        model = ctx.db.scalar(select(ModelVersion).where(
            ModelVersion.id == kinds["champion_model"], ModelVersion.workspace_id == ctx.workspace_id,
            ModelVersion.project_id == ctx.row.project_id))
        recipe = ctx.db.scalar(select(FeatureSetVersion).where(
            FeatureSetVersion.id == model.feature_set_version_id, FeatureSetVersion.workspace_id == ctx.workspace_id)
        ) if model and model.feature_set_version_id else None
        if model is not None:
            _check_champion(ctx, model)
        if "feature_recipe" not in kinds and recipe is not None and recipe.locked_at is not None:
            kinds["feature_recipe"] = recipe.id
        evaluation = prs.champion_final_evaluation(ctx.db, workspace_id=ctx.workspace_id,
                                                   project_id=ctx.row.project_id, model_version_id=kinds["champion_model"])
        evidence.append(drs.evidence_ref("candidate", evaluation.candidate_id, scope=prs.FINAL_HOLDOUT_SCOPE))
    given = ctx.ref_versions
    missing = sorted(kind for kind in kinds if given is None or kind not in given)
    if missing:
        raise ProposalError(428, "ref_versions_required",
                            "send ref_versions: the version you saw of each ref this moves (null = no such ref yet)",
                            ref_kinds=missing)
    moves = [prs.RefMove(kind, target, given[kind]) for kind, target in kinds.items()]
    return prs.move_ref(ctx.db, workspace_id=ctx.workspace_id, project_id=ctx.row.project_id, actor=ctx.actor,
                        moves=moves, rationale=rationale, evidence_refs=evidence, facts=facts,
                        idempotency_key=f"accept:{ctx.row.id}")


def _t_record(ctx: _Ctx, args: dict[str, Any]):
    row = ctx.row

    def command(hook: Callable[[Any], None]) -> Any:
        evidence = list(args.get("evidence_refs") or [])
        if args.get("ref_moves"):
            result = _move_refs(ctx, {k: UUID(str(v)) for k, v in args["ref_moves"].items()}, evidence,
                                args["rationale"], args.get("facts"))
            record = result.record
        else:
            record = drs.record(
                ctx.db, workspace_id=ctx.workspace_id, project_id=row.project_id, actor=ctx.actor,
                decision_type=args["decision_type"], subject_kind=args["subject_kind"], subject_id=_uid(args.get("subject_id")),
                rationale=args["rationale"], state=STATE_ACCEPTED, facts=args.get("facts"), evidence_refs=evidence,
                idempotency_key=f"accept:{row.id}")
        hook(record)
        ctx.db.commit()
        return record

    return command, "decision_record"


_TOOLS: dict[str, Callable[[_Ctx, dict[str, Any]], Any]] = {
    "create_problem_spec": lambda ctx, args: (lambda hook: _create_draft(ctx, args, hook), "problem_spec"),
    "propose_problem_spec": _t_propose_spec,
    "run_experiment": _t_run,
    "branch_experiment": _t_branch,
    "predict": _t_predict,
    "record_decision": _t_record,
}


def _create_draft(ctx: _Ctx, args: dict[str, Any], hook: Callable[[Any], None]) -> Any:
    from app.services.problem_spec_service import create_problem_spec

    _note_plan_refusals(ctx, args)

    fields = {k: v for k, v in args.items() if k not in ("project_id", "rationale")}
    spec = create_problem_spec(ctx.db, actor=ctx.user, workspace_id=ctx.workspace_id, project_id=ctx.row.project_id,
                               status="draft", **fields)
    ctx.db.flush()
    hook(spec)
    ctx.db.commit()
    return spec


# --- revert ----------------------------------------------------------------------------------------


def _applied_plan_records(db: Session, row: AgentProposal) -> list[ProjectDecisionRecord]:
    """The ``decision_point_resolved`` records of the latest run that applied this L2 plan (not
    superseded), each with its recorded revert."""

    pdr = ProjectDecisionRecord
    rows = [r for r in db.scalars(select(pdr).where(
        pdr.workspace_id == row.workspace_id, pdr.project_id == row.project_id,
        pdr.decision_type == "decision_point_resolved", pdr.state == STATE_ACCEPTED,
        pdr.details["ai"]["plan_proposal_id"].astext == str(row.id)).order_by(pdr.recorded_at.desc()))
        if drs.successor_id(db, r) is None]
    if not rows:
        return []
    latest = rows[0].experiment_id
    return [r for r in rows if r.experiment_id == latest]


def _revertible_applied(record: ProjectDecisionRecord) -> int:
    """Applied AI answers a revert must undo. The time budget has no revert change (a branch already
    runs on the rule's budget), so it is not counted; per-answer detail is used when it is complete."""

    details = record.details or {}
    columns = details.get("columns") or []
    if columns and int(details.get("columns_detailed") or 0) >= int(details.get("columns_total") or 0):
        return sum(1 for c in columns if c.get("source") in ("ai", "ai_inherited")
                   and c.get("column") != "max_training_seconds")
    return int((details.get("used") or {}).get("ai_applied") or 0)


def _budget_only(records: list[ProjectDecisionRecord]) -> bool:
    """Every applied answer of these records is the time budget (complete per-answer detail only)."""

    applied = []
    for record in records:
        details = record.details or {}
        if int(details.get("columns_detailed") or 0) < int(details.get("columns_total") or 0):
            return False
        applied += [c.get("column") for c in details.get("columns") or [] if c.get("source") in ("ai", "ai_inherited")]
    return bool(applied) and set(applied) == {"max_training_seconds"}


def _revert_plan(ctx: _Ctx) -> tuple[UUID, list[dict[str, Any]], list[ProjectDecisionRecord], dict[str, Any]]:
    records = _applied_plan_records(ctx.db, ctx.row)
    kinds = [(r.details or {}).get("revert", {}).get("kind") for r in records]
    if not records or "new_root" in kinds:
        raise ProposalError(409, "not_revertible_in_place",
                            "this applied value changed a new-root answer; start a new root instead" if records
                            else "no decision record applied this plan", kinds=sorted({str(k) for k in kinds}))
    revertible = [r for r in records if ((r.details or {}).get("revert") or {}).get("kind") == "branch_change"]
    changes: list[dict[str, Any]] = []
    for record in revertible:
        revert_ = record.details["revert"]
        mine = list(revert_.get("changes") or [])
        applied = _revertible_applied(record)
        if revert_.get("names_omitted") or len(mine) < applied:  # never report a partial revert as the rule value
            raise ProposalError(409, "not_revertible_in_place", "the record does not list every applied value",
                                applied=applied, recorded=len(mine))
        changes += mine
    if not changes:
        if _budget_only(records):  # a branch already runs on the rule's budget: closing is the whole revert
            return records[0].experiment_id, [], records, {"budget_only": True}
        raise ProposalError(409, "nothing_to_revert", "no recorded value differs from the rule's")
    return (revertible[0].experiment_id, changes, revertible,
            {"decision_record_ids": [str(r.id) for r in revertible[:20]]})


def _revert_semantic(ctx: _Ctx) -> tuple[UUID, list[dict[str, Any]], list[ProjectDecisionRecord], dict[str, Any]]:
    """Branch from the experiment accept created (it used the AI value); the rule value comes back
    through ``decision_point_service.revert_changes`` or, for a dropped identifier, a re-inclusion."""

    from app.services.decision_point_service import revert_changes

    row, payload = ctx.row, ctx.row.payload or {}
    change = payload.get("accept_change") or {}
    key, column = row.decision_point_key, str(payload.get("column"))
    changes = revert_changes(key, column, payload.get("rule"), payload.get("ai"))
    if (not changes and key == "column.is_identifier" and payload.get("rule") is False
            and change.get("transform") == "drop_column"):
        changes = [{"kind": "feature_transform_add", "column": column, "transform": "keep"}]
    record = ctx.db.scalar(select(ProjectDecisionRecord).where(
        ProjectDecisionRecord.id == row.decision_record_id, ProjectDecisionRecord.workspace_id == row.workspace_id)
    ) if row.decision_record_id else None
    executed = ((record.details or {}).get("executed") or {}) if record is not None else {}
    parent = ctx.db.scalar(select(Experiment.id).where(
        Experiment.id == _uid(executed.get("id")), Experiment.workspace_id == row.workspace_id,
        Experiment.project_id == row.project_id)) if executed.get("kind") == "experiment" else None
    if not changes or record is None or parent is None:
        raise ProposalError(409, "not_revertible_in_place", "this accepted item has no in-place revert")
    return parent, changes, [record], {}


def has_in_place_revert(row: AgentProposal) -> bool:
    """The proposal types ``revert`` can undo (once ``applied``): an L2 plan and a Jev review item."""

    return ((row.proposal_type == PLAN_PROPOSAL_TYPE and row.level_at_proposal >= 2)
            or row.proposal_type == "SemanticReviewProposal")


def revert(db: Session, *, user: User, workspace_id: UUID, proposal_id: UUID, rationale: str | None,
           bind: Callable[[Any], None]) -> AgentProposal:
    """``applied -> reverted``: the RULE value is restored by a branch of the experiment that used the
    AI value (the recorded revert changes), under a ``proposal_reverted`` record that supersedes the
    record of the applied value. Commits."""

    row = _load(db, user, workspace_id, proposal_id, "revert")
    if row.status != "applied":
        raise _not_open(row, "applied")
    ctx = _Ctx(db, user, workspace_id, row, rationale, None, bind)
    if not has_in_place_revert(row):
        raise ProposalError(409, "not_revertible_in_place", f"a {row.proposal_type} has no in-place revert")
    if row.proposal_type == PLAN_PROPOSAL_TYPE:
        parent, changes, records, extra = _revert_plan(ctx)
    else:
        parent, changes, records, extra = _revert_semantic(ctx)
    from app.domain.experiment_changes import ExperimentChangeSet

    from app.db.models import MlJob

    parent_status = db.scalar(select(Experiment.status).where(
        Experiment.id == parent, Experiment.workspace_id == workspace_id))
    budget_only = bool(extra.get("budget_only"))
    if budget_only or str(parent_status).upper() in ("FAILED", "CANCELLED"):
        # budget_only: nothing to undo but the proposal itself (branches use the rule's budget).
        # Failed child: the run never produced a model, so there is nothing to branch from. Either way the
        # revert only closes the proposal and records why, superseding its record.
        if not budget_only and db.scalar(select(func.count()).select_from(MlJob).where(
                MlJob.workspace_id == workspace_id, MlJob.pipeline_run_id == parent,
                MlJob.status.in_(("queued", "running")))):
            # a FAILED status does not end the run: a crashed worker's job is requeued and may still complete
            raise ProposalError(409, "parent_not_completed", "the run that used the AI value is still queued or running")
        kind = "budget_only" if budget_only else "child_failed"
        text = _text(ctx, _default_rationale(ctx, "reverted"))
        first = None
        for index, record in enumerate(records):
            made = _write_record(ctx, "proposal_reverted", STATE_ACCEPTED, text,
                                 {"revert": {"kind": kind, "experiment_id": str(parent)}, **extra},
                                 supersedes=record if drs.successor_id(db, record) is None else None,
                                 key_suffix=f":{index}" if index else "")
            first = first or made
        _cas(db, user, row, "reverted", wanted="applied",
             **({"decision_record_id": first.id} if row.decision_record_id is None else {}))
        bind(row.id)
        db.commit()
        db.refresh(row)
        return row
    try:  # closure: existing change kinds only; a set too large for one branch is never cut short
        ExperimentChangeSet.model_validate({"changes": changes})
    except Exception:  # noqa: BLE001
        raise ProposalError(409, "not_revertible_in_place", "the recorded revert is not one valid branch") from None

    def hook(shell: Any) -> None:
        text = _text(ctx, _default_rationale(ctx, "reverted"))
        first = None
        for index, record in enumerate(records):  # one superseding record per reverted record (supersedes_id is 1:1)
            reverted = _write_record(
                ctx, "proposal_reverted", STATE_ACCEPTED, text,
                {"revert": {"kind": "branch_change", "changes": changes, "branch_experiment_id": str(shell.id),
                            "restores": "rule_value"}, **extra},
                supersedes=record if drs.successor_id(db, record) is None else None,
                key_suffix=f":{index}" if index else "")
            first = first or reverted
        # compare-and-set last: a concurrent revert waits here and finds 0 rows (its branch rolls back);
        # an L2 value's first record on the proposal is this one (links are write-once, set with the status)
        _cas(db, user, row, "reverted", wanted="applied",
             **({"decision_record_id": first.id} if row.decision_record_id is None else {}))
        bind(row.id)

    _branch(ctx, parent, changes, f"Revert proposal {row.id} to the rule value"[:500])(hook)
    db.refresh(row)
    return row


# --- request_agent_review ------------------------------------------------------------------------

REVIEW_SPECS = {
    "experiment_critic": ("experiment.review", "experiment", "cv"),
    "dataset_investigator": ("target.column", "dataset_version", "none"),
    "experiment_planner": ("spec.objective", "dataset_version", "none"),
}
_REFUSED = {"forbidden": 403, "insufficient_scope": 403, "principal_inactive": 403, "service_tokens_disabled": 403,
            "subject_not_found": 404}


def request_review(db: Session, *, user: User, workspace_id: UUID, project_id: UUID, agent: str,
                   experiment_id: UUID | None, dataset_id: UUID | None, service_token_id: UUID | None = None,
                   bind: Callable[[Any], None] | None = None) -> AgentRun:
    """Queue a Critic / Investigator / Planner run through the harness for a run or dataset the caller
    can read (ML-write; the harness authorizes again). Never decides anything: the run only proposes."""

    from app.agents.governance.switches import effective_switches
    from app.agents.harness.service import AgentRunRefused, specialist_spec, submit_review
    from app.config import get_settings
    from app.db.models import Dataset, Experiment

    point, subject_kind, scope = REVIEW_SPECS[agent]
    subject_id = experiment_id if subject_kind == "experiment" else dataset_id
    if subject_id is None or (experiment_id is not None and dataset_id is not None):
        raise ProposalError(422, "invalid_subject", f"{agent} reviews exactly one "
                            f"{'experiment_id' if subject_kind == 'experiment' else 'dataset_id'}")
    if not can_read_workspace(db, user, workspace_id):
        raise ProposalError(404, "not_found", "subject not found")
    model = Experiment if subject_kind == "experiment" else Dataset
    subject = db.scalar(select(model).where(model.id == subject_id, model.workspace_id == workspace_id,
                                            model.project_id == project_id))
    if subject is None:
        raise ProposalError(404, "not_found", "subject not found")
    if subject_kind == "experiment" and subject.status != "COMPLETED":
        raise ProposalError(409, "experiment_not_completed", "a Critic reviews a completed experiment")
    if not can_execute_workspace_ml(db, user, workspace_id):
        raise ProposalError(403, "capability_denied", "requesting an agent review needs ML-write access")
    settings = get_settings()
    if not getattr(settings, "ai_enabled", False) or effective_switches(db, workspace_id).blocking(
            ai_enabled=True, agent_key=agent, purpose=point):
        raise ProposalError(409, "agent_unavailable", "AI is off for this workspace or agent")
    spec = specialist_spec(db, agent_key=agent, workspace_id=workspace_id, project_id=project_id,
                           user_id=user.id, subject_kind=subject_kind, subject_id=subject_id,
                           decision_point_key=point, outcome_scope=scope, settings=settings)
    if spec is None:
        raise ProposalError(409, "agent_unavailable", "the agent has no released prompt or runtime")
    if service_token_id is not None:  # a token acts as its creator; the run records the token
        spec = spec.model_copy(update={"user_id": None, "service_token_id": service_token_id})
    try:
        run_id = submit_review(db, spec, settings=settings, bind=bind)  # the key binds before the commit
    except AgentRunRefused as exc:
        db.rollback()
        raise ProposalError(_REFUSED.get(exc.code, 409), exc.code, f"the review was refused: {exc.code}") from None
    return db.get(AgentRun, run_id)


__all__ = ["ProposalError", "accept", "agent_run_read", "get_agent_run", "get_proposal", "has_in_place_revert",
           "is_open", "list_agent_runs", "list_proposals", "owner_user_id_sql", "promote_authority", "proposal_read",
           "proposed_by_kind", "reject", "request_review", "revert", "visibility_clause"]
