"""Inbox read model (P4.16-A): a read-only projection, no tables, no writes.

Each tab merges keyset-paged sources newest first (the ``/v1/activity`` merge, ``keyset_before``):

* ``needs_decision``: proposed decision records without a successor; open agent proposals
  (``proposed``, not expired); execution requests waiting in ``needs_input`` (questions);
* ``applied_automatically``: agent proposals ``applied`` without a person at level >= 2;
* ``done``: decision proposals with their accept/reject record; agent proposals a person decided,
  reverted, superseded or expired; finished runs.

Every source is one statement filtered by workspace (and project), cut at ``limit + 1``; assistant
``ToolCallProposal`` visibility is ``proposal_review_service.visibility_clause`` (owner + approvers,
never tokens) and the owner check behind ``allowed`` is its ``owner_user_id_sql`` (one statement per
page, no N+1). ``counts`` is one statement for all tabs. Actions only name existing routes; the
routes re-authorize every call.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from datetime import datetime
from typing import Any
from uuid import UUID

from sqlalchemy import and_, exists, func, not_, or_, select
from sqlalchemy.orm import Session, aliased

from app.agents.tools.shaping import holdout_scoped
from app.db.models import AgentProposal, ExecutionRequest, Experiment, ProjectDecisionRecord, User
from app.domain.agent_records import AGENT_SUBJECT_COLUMNS
from app.domain.decision_records import (
    ACTOR_AGENT,
    ACTOR_RULE,
    EVIDENCE_REF_KINDS,
    REF_HISTORY_DECISION_TYPES,
    REF_MOVE_DECISION_TYPES,
    SERVICE_ONLY_DECISION_TYPES,
    STATE_ACCEPTED,
    STATE_PROPOSED,
    SUBJECT_COLUMNS,
)
from app.domain.errors import IdentityError, InvalidCursorError
from app.domain.execution_requests import REQUEST_NEEDS_INPUT
from app.domain.inbox_reads import (
    INBOX_ANSWER_MAX_BYTES,
    INBOX_EVIDENCE_MAX,
    INBOX_KIND_RANK,
    INBOX_PAGE_DEFAULT,
    INBOX_PAGE_MAX,
    INBOX_TABS,
    InboxActionRead,
    InboxCounts,
    InboxItemRead,
    InboxPage,
    InboxRefRead,
    InboxSubjectRead,
    InboxViewerRead,
)
from app.domain.state_graph import REF_KINDS, node_key
from app.services import proposal_review_service as prs
from app.services.audience_projection import public_diagnostic
from app.services.authorization_service import can_approve_ai_policy, can_execute_workspace_ml, can_read_workspace
from app.services.cursor_codec import open_cursor, scope_digest, sign_cursor
from app.services.experiment_service import STATUS_SQL
from app.services.home_read_service import decision_summary, keyset_before, run_label
from app.services.project_service import get_project

# Existing routes the actions name (operation ids of the OpenAPI inventory).
OP_DECISION_ACCEPT = "POST /v1/decisions/{decision_id}/accept"
OP_DECISION_REJECT = "POST /v1/decisions/{decision_id}/reject"
OP_DECISION_SUPERSEDE = "POST /v1/decisions/{decision_id}/supersede"
OP_MOVE_REF = "POST /v1/projects/{project_id}/refs/{ref_kind}"
OP_PROPOSAL_ACCEPT = "POST /v1/proposals/{proposal_id}/accept"
OP_PROPOSAL_REJECT = "POST /v1/proposals/{proposal_id}/reject"
OP_PROPOSAL_REVERT = "POST /v1/proposals/{proposal_id}/revert"
OP_TARGET_ANSWER = "POST /v1/execution-requests/{request_id}/target-confirmation"
OP_SPLIT_ANSWER = "POST /v1/execution-requests/{request_id}/split-confirmation"

_PROPOSAL_LABELS = {
    "ExperimentPlanProposal": "Experiment plan",
    "ExperimentReviewProposal": "Experiment review",
    "DatasetInvestigationProposal": "Dataset investigation",
    "ImprovementActionProposal": "Improvement action",
    "ToolCallProposal": "Assistant tool call",
    "ReleaseProposal": "Release",
    "SemanticReviewProposal": "Column review",
}
_PROPOSAL_DONE = ("accepted", "rejected", "reverted", "superseded", "expired")
_SPLIT_QUESTION = "split_strategy_confirmation"
_RUN_FINISHED = {"completed": "completed", "failed": "failed", "cancelled": "cancelled", "skipped": "skipped"}
_CODE_KEY = re.compile(r"[a-z][a-z0-9_.]{0,63}")

_Key = tuple[datetime, int, UUID]


@dataclass
class _Ctx:
    db: Session
    ws: UUID
    project_id: UUID | None
    viewer: User
    agent: bool
    can_decide: bool
    approver: bool
    visible: Any  # ToolCallProposal visibility (None: no restriction)


# --- sources: (statement, occurred_at column, id column) ------------------------------------------


def _scoped(stmt: Any, ws_column: Any, project_column: Any, ctx: _Ctx) -> Any:
    stmt = stmt.where(ws_column == ctx.ws)
    return stmt.where(project_column == ctx.project_id) if ctx.project_id is not None else stmt


def _decisions_open(ctx: _Ctx) -> tuple[Any, Any, Any]:
    pdr, successor = ProjectDecisionRecord, aliased(ProjectDecisionRecord)
    stmt = select(pdr).where(pdr.state == STATE_PROPOSED, ~exists().where(
        successor.supersedes_id == pdr.id, successor.workspace_id == pdr.workspace_id))
    return _scoped(stmt, pdr.workspace_id, pdr.project_id, ctx), pdr.recorded_at, pdr.id


def _decisions_done(ctx: _Ctx) -> tuple[Any, Any, Any]:
    pdr, resolution, correction = ProjectDecisionRecord, aliased(ProjectDecisionRecord), aliased(ProjectDecisionRecord)
    stmt = (
        select(pdr, resolution.id, resolution.state, resolution.recorded_at, correction.id)
        .join(resolution, and_(resolution.supersedes_id == pdr.id, resolution.workspace_id == pdr.workspace_id))
        .outerjoin(correction, and_(correction.supersedes_id == resolution.id,
                                    correction.workspace_id == resolution.workspace_id))
        .where(pdr.state == STATE_PROPOSED)
    )
    return _scoped(stmt, pdr.workspace_id, pdr.project_id, ctx), resolution.recorded_at, pdr.id


def _auto_applied() -> Any:
    ap = AgentProposal
    return and_(ap.status == "applied", ap.decided_by_user_id.is_(None), ap.level_at_proposal >= 2)


def _proposals(ctx: _Ctx, tab: str) -> tuple[Any, Any, Any]:
    ap = AgentProposal
    unexpired = or_(ap.expires_at.is_(None), ap.expires_at > func.now())
    at = ap.created_at
    if tab == "needs_decision":
        where = and_(ap.status == "proposed", unexpired)
    elif tab == "applied_automatically":
        where = _auto_applied()
    else:
        where = or_(ap.status.in_(_PROPOSAL_DONE), and_(ap.status == "applied", not_(_auto_applied())),
                    and_(ap.status == "proposed", not_(unexpired)))
        at = func.coalesce(ap.decided_at, ap.created_at)
    stmt = select(ap, prs.owner_user_id_sql().label("owner_id"), at.label("at")).where(where)
    if ctx.visible is not None:
        stmt = stmt.where(ctx.visible)
    return _scoped(stmt, ap.workspace_id, ap.project_id, ctx), at, ap.id


def _questions(ctx: _Ctx) -> tuple[Any, Any, Any]:
    er = ExecutionRequest
    stmt = select(er.id, er.project_id, er.pipeline_run_id, er.result_summary, er.created_at).where(
        er.status == REQUEST_NEEDS_INPUT)
    return _scoped(stmt, er.workspace_id, er.project_id, ctx), er.created_at, er.id


def _runs_finished(ctx: _Ctx) -> tuple[Any, Any, Any]:
    stmt = select(Experiment.id, Experiment.project_id, Experiment.ended_at, Experiment.run_number,
                  Experiment.parent_pipeline_run_id, STATUS_SQL).where(Experiment.ended_at.is_not(None))
    return _scoped(stmt, Experiment.workspace_id, Experiment.project_id, ctx), Experiment.ended_at, Experiment.id


_SOURCES: dict[str, tuple[tuple[str, Any], ...]] = {
    "needs_decision": (
        ("decision_proposal", _decisions_open),
        ("agent_proposal", lambda c: _proposals(c, "needs_decision")),
        ("question", _questions),
    ),
    "applied_automatically": (("agent_proposal", lambda c: _proposals(c, "applied_automatically")),),
    "done": (
        ("decision_proposal", _decisions_done),
        ("agent_proposal", lambda c: _proposals(c, "done")),
        ("run_finished", _runs_finished),
    ),
}


# --- shaping --------------------------------------------------------------------------------------


def _answer(value: Any, ctx: _Ctx) -> tuple[dict[str, Any] | None, bool]:
    """People only: redacted, bounded structured values (plain text inside, never instructions)."""

    if ctx.agent or value is None:
        return None, False
    shaped = public_diagnostic(value if isinstance(value, dict) else {"value": value})
    if not isinstance(shaped, dict):
        return None, False
    if len(json.dumps(shaped, default=str).encode("utf-8")) > INBOX_ANSWER_MAX_BYTES:
        return None, True
    return shaped, False


def _code_key(value: Any) -> str | None:
    return value if isinstance(value, str) and _CODE_KEY.fullmatch(value) else None


def _uuid(value: Any) -> UUID | None:
    try:
        return value if isinstance(value, UUID) else UUID(str(value))
    except (TypeError, ValueError):
        return None


def _ref(kind: str, value: Any) -> InboxRefRead | None:
    ident = _uuid(value) if value is not None else None
    return InboxRefRead(kind=kind, id=ident) if ident is not None else None


def _refs(*refs: InboxRefRead | None) -> list[InboxRefRead]:
    return [ref for ref in refs if ref is not None][:INBOX_EVIDENCE_MAX]


def _subject(kind: str, node_id: UUID | None) -> InboxSubjectRead:
    return InboxSubjectRead(kind=kind, id=node_id, key=node_key(kind, node_id) if node_id is not None else None)


def _action(name: str, operation: str, allowed: bool, body: dict[str, str] | None = None,
            **params: Any) -> InboxActionRead:
    return InboxActionRead(name=name, operation=operation, path_params={k: str(v) for k, v in params.items()},
                           body=body or {}, allowed=allowed)


def _item(**fields: Any) -> InboxItemRead:
    actions = fields.get("actions") or []
    return InboxItemRead(**fields, can_act=any(a.allowed for a in actions))


def _primary_ref_kind(details: Any) -> str | None:
    """The path ref of a ref-move proposal (the champion when it moves, else the first move)."""

    kinds = [m.get("ref_kind") for m in (details or {}).get("ref_moves") or [] if isinstance(m, dict)]
    kinds = [k for k in kinds if k in REF_KINDS]
    return "champion_model" if "champion_model" in kinds else (kinds[0] if kinds else None)


def _decision_item(ctx: _Ctx, tab: str, row: ProjectDecisionRecord, at: datetime,
                   resolution: tuple[UUID, str, UUID | None] | None) -> InboxItemRead:
    column = SUBJECT_COLUMNS.get(row.subject_kind)
    subject_id = getattr(row, column) if column else row.project_id
    # Agent readers never get a holdout-scoped ref (the `GET /v1/decisions/{id}` agent view drops them too).
    evidence = [_ref(str(r.get("kind")), r.get("id")) for r in list(row.evidence_refs or [])[:INBOX_EVIDENCE_MAX]
                if isinstance(r, dict) and r.get("kind") in EVIDENCE_REF_KINDS
                and not (ctx.agent and holdout_scoped(r))]
    actions: list[InboxActionRead] = []
    status = STATE_PROPOSED
    can = ctx.can_decide and row.decision_type not in SERVICE_ONLY_DECISION_TYPES
    if resolution is None:
        ref_kind = _primary_ref_kind(row.details) if row.decision_type in REF_MOVE_DECISION_TYPES else None
        if row.decision_type in REF_MOVE_DECISION_TYPES:
            if ref_kind is not None:
                actions.append(_action("accept", OP_MOVE_REF, can, {"proposal_id": str(row.id)},
                                       project_id=row.project_id, ref_kind=ref_kind))
        else:
            actions.append(_action("accept", OP_DECISION_ACCEPT, can, decision_id=row.id))
        actions.append(_action("reject", OP_DECISION_REJECT, can, decision_id=row.id))
    else:
        resolution_id, state, correction_id = resolution
        status = "superseded" if correction_id is not None else state
        if (state == STATE_ACCEPTED and correction_id is None
                and row.decision_type not in REF_HISTORY_DECISION_TYPES):
            actions.append(_action("supersede", OP_DECISION_SUPERSEDE, can, decision_id=resolution_id))
    proposed_by = {ACTOR_RULE: "rule", ACTOR_AGENT: "agent"}.get(row.actor_kind, "person")
    return _item(
        id=f"decision_proposal:{row.id}", kind="decision_proposal", tab=tab, occurred_at=at,
        project_id=row.project_id, summary=decision_summary(row.decision_type, status), status=status,
        source=InboxRefRead(kind="decision_record", id=row.id), subject=_subject(row.subject_kind, subject_id),
        proposed_by=proposed_by, decision_type=row.decision_type,
        resolution_record_id=resolution[0] if resolution else None, evidence_refs=_refs(*evidence), actions=actions,
    )


def _proposal_status(row: AgentProposal) -> str:
    return "expired" if row.status == "proposed" and not prs.is_open(row) else row.status


def _proposal_summary(row: AgentProposal, status: str) -> str:
    label = _PROPOSAL_LABELS.get(row.proposal_type, "Proposal")
    tool = _code_key(row.tool_name) if row.proposal_type == "ToolCallProposal" else None
    if tool:
        label = f"{label} {tool}"
    if status == "proposed":
        return f"{label} waiting for a decision"
    if status == "applied" and row.decided_by_user_id is None:
        return f"{label} applied automatically at L{row.level_at_proposal}"
    return f"{label} {status}"


def _proposal_item(ctx: _Ctx, tab: str, row: AgentProposal, owner_id: UUID | None, at: datetime) -> InboxItemRead:
    status = _proposal_status(row)
    actions: list[InboxActionRead] = []
    if status == "proposed" and row.proposal_type in prs.DECIDABLE_TYPES:
        owner_ok = row.proposal_type != "ToolCallProposal" or owner_id == ctx.viewer.id
        actions.append(_action("accept", OP_PROPOSAL_ACCEPT, ctx.can_decide and owner_ok, proposal_id=row.id))
        actions.append(_action("reject", OP_PROPOSAL_REJECT, ctx.can_decide, proposal_id=row.id))
    elif status == "applied" and prs.has_in_place_revert(row):
        actions.append(_action("revert", OP_PROPOSAL_REVERT, ctx.can_decide, proposal_id=row.id))
    column = AGENT_SUBJECT_COLUMNS.get(row.subject_kind or "")
    subject_id = getattr(row, column) if column else (row.project_id if row.subject_kind == "project" else None)
    rule, rule_cut = _answer(row.rule_answer, ctx)
    ai, ai_cut = _answer({"tool": row.tool_name, "arguments": row.tool_arguments}
                         if row.proposal_type == "ToolCallProposal" else row.payload, ctx)
    return _item(
        id=f"agent_proposal:{row.id}", kind="agent_proposal", tab=tab, occurred_at=at, project_id=row.project_id,
        summary=_proposal_summary(row, status), status=status, source=InboxRefRead(kind="agent_proposal", id=row.id),
        subject=_subject(row.subject_kind or "project", subject_id), proposed_by=prs.proposed_by_kind(row),
        proposal_type=row.proposal_type, decision_point_key=row.decision_point_key, level=row.level_at_proposal,
        expires_at=row.expires_at, rule_answer=rule, ai_answer=ai, answers_truncated=rule_cut or ai_cut,
        evidence_refs=_refs(_ref("agent_run", row.run_id), _ref("semantic_answer", row.semantic_answer_id),
                            _ref("decision_record", row.decision_record_id),
                            _ref("decision_record", row.applied_decision_record_id)),
        actions=actions,
    )


def _question_item(ctx: _Ctx, request_id: UUID, project_id: UUID | None, run_id: UUID | None,
                   waiting: Any, at: datetime) -> InboxItemRead:
    waiting = waiting if isinstance(waiting, dict) else {}
    split = waiting.get("kind") == _SPLIT_QUESTION
    rule, rule_cut = _answer(waiting.get("rule"), ctx) if split else (None, False)
    ai, ai_cut = _answer(waiting.get("plan") if split else waiting.get("ai_suggestion"), ctx)
    return _item(
        id=f"question:{request_id}", kind="question", tab="needs_decision", occurred_at=at, project_id=project_id,
        summary="Run needs a split confirmation" if split else "Run needs a target column", status=REQUEST_NEEDS_INPUT,
        source=InboxRefRead(kind="execution_request", id=request_id),
        subject=_subject("experiment", run_id) if run_id else _subject("project", project_id),
        proposed_by="run", decision_point_key=_code_key(waiting.get("decision_point")) if split else None,
        rule_answer=rule, ai_answer=ai, answers_truncated=rule_cut or ai_cut,
        evidence_refs=_refs(_ref("experiment", run_id)),
        actions=[_action("answer", OP_SPLIT_ANSWER if split else OP_TARGET_ANSWER, ctx.can_decide,
                         request_id=request_id)],
    )


def _run_item(exp_id: UUID, project_id: UUID | None, at: datetime, number: int | None, parent: UUID | None,
              status: Any) -> InboxItemRead:
    public = _RUN_FINISHED.get(str(status), "ended")
    return _item(
        id=f"run_finished:{exp_id}", kind="run_finished", tab="done", occurred_at=at, project_id=project_id,
        summary=f"{run_label(number, parent is not None)} {public}", status=str(status),
        source=InboxRefRead(kind="experiment", id=exp_id), subject=_subject("experiment", exp_id), proposed_by="run",
        evidence_refs=_refs(_ref("experiment", parent)), actions=[],
    )


def _items(ctx: _Ctx, tab: str, kind: str, source: Any, after: _Key | None, n: int) -> list[InboxItemRead]:
    stmt, at_column, id_column = source(ctx)
    cut = keyset_before(after, kind, at_column, id_column, INBOX_KIND_RANK)
    if cut is not None:
        stmt = stmt.where(cut)
    rows = ctx.db.execute(stmt.order_by(at_column.desc(), id_column.desc()).limit(n)).all()
    if kind == "decision_proposal" and tab == "done":
        return [_decision_item(ctx, tab, r[0], r[3], (r[1], r[2], r[4])) for r in rows]
    if kind == "decision_proposal":
        return [_decision_item(ctx, tab, r[0], r[0].recorded_at, None) for r in rows]
    if kind == "agent_proposal":
        return [_proposal_item(ctx, tab, r[0], r[1], r[2]) for r in rows]
    if kind == "question":
        return [_question_item(ctx, r[0], r[1], r[2], r[3], r[4]) for r in rows]
    return [_run_item(*r) for r in rows]


# --- entry points ---------------------------------------------------------------------------------


def _context(db: Session, *, actor: User, workspace_id: UUID, project_id: UUID | None, agent: bool) -> _Ctx:
    if not can_read_workspace(db, actor, workspace_id):
        raise IdentityError("not authorized for this workspace", status_code=403)
    if project_id is not None:
        get_project(db, actor=actor, workspace_id=workspace_id, project_id=project_id)
    approver = not agent and can_approve_ai_policy(db, actor, workspace_id)
    return _Ctx(
        db=db, ws=workspace_id, project_id=project_id, viewer=actor, agent=agent,
        can_decide=not agent and can_execute_workspace_ml(db, actor, workspace_id), approver=approver,
        visible=prs.visibility_clause(db, workspace_id=workspace_id, viewer=None if agent else actor, agent=agent),
    )


def _scope(ctx: _Ctx, tab: str) -> str:
    viewer = "agent" if ctx.agent else str(ctx.viewer.id)
    return f"inbox:{ctx.ws}:{scope_digest({'tab': tab, 'project_id': ctx.project_id, 'viewer': viewer})}"


def _open(cursor: str | None, scope: str) -> _Key | None:
    if cursor is None or not cursor.strip():
        return None
    message = "cursor is not an inbox cursor for this workspace, tab and filter"
    try:
        stamp, rank, ident = open_cursor(cursor, scope, message=message)
        at = datetime.fromisoformat(stamp)
        if at.tzinfo is None or type(rank) is not int or rank not in INBOX_KIND_RANK.values():
            raise ValueError(message)
        return at, rank, UUID(ident)
    except (InvalidCursorError, ValueError, TypeError) as exc:
        raise InvalidCursorError(message) from exc


def _key(item: InboxItemRead) -> _Key:
    return item.occurred_at, INBOX_KIND_RANK[item.kind], item.source.id


def list_inbox(
    db: Session,
    *,
    actor: User,
    workspace_id: UUID,
    tab: str = "needs_decision",
    project_id: UUID | None = None,
    cursor: str | None = None,
    limit: int = INBOX_PAGE_DEFAULT,
    viewer_is_agent: bool = False,
) -> InboxPage:
    """One tab of the workspace's (or one project's) inbox, newest first. ``ProjectNotFoundError`` for a
    project of another workspace; ``InvalidCursorError`` for a cursor of another tab, filter or viewer."""

    if tab not in INBOX_TABS:
        raise ValueError(f"unknown inbox tab {tab!r}")
    ctx = _context(db, actor=actor, workspace_id=workspace_id, project_id=project_id, agent=viewer_is_agent)
    limit = max(1, min(int(limit), INBOX_PAGE_MAX))
    scope = _scope(ctx, tab)
    after = _open(cursor, scope)
    candidates = [item for kind, source in _SOURCES[tab] for item in _items(ctx, tab, kind, source, after, limit + 1)]
    candidates.sort(key=_key, reverse=True)
    page = candidates[:limit]
    next_cursor = None
    if len(candidates) > limit:
        at, rank, ident = _key(page[-1])
        next_cursor = sign_cursor(scope, [at.isoformat(), rank, str(ident)])
    return InboxPage(
        tab=tab, items=page, next_cursor=next_cursor, limit=limit,
        viewer=InboxViewerRead(is_agent=ctx.agent, can_decide=ctx.can_decide, can_approve_ai_policy=ctx.approver),
    )


def inbox_counts(db: Session, *, actor: User, workspace_id: UUID, project_id: UUID | None = None,
                 viewer_is_agent: bool = False) -> InboxCounts:
    """Totals per tab, equal to what paging each tab returns, in one statement."""

    ctx = _context(db, actor=actor, workspace_id=workspace_id, project_id=project_id, agent=viewer_is_agent)
    columns = []
    for tab in INBOX_TABS:
        parts = []
        for _kind, source in _SOURCES[tab]:
            stmt, _at, id_column = source(ctx)
            ids = stmt.with_only_columns(id_column).subquery()
            parts.append(select(func.count()).select_from(ids).scalar_subquery())
        total = parts[0]
        for part in parts[1:]:
            total = total + part
        columns.append(total.label(tab))
    row = db.execute(select(*columns)).one()
    return InboxCounts(**{tab: int(getattr(row, tab)) for tab in INBOX_TABS})
