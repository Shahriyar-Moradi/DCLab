"""Assistant threads and turns (P6.3-B2; ADR 0009 §7.2, §7.3, §7.6). Transport-free.

A thread is an ``agent_runs`` row of kind ``assistant`` (owner = its creator, status
``waiting_user``, the thread limits frozen at creation); each turn is a child ``lead`` run
(runtime ``lead_loop``, purpose ``assistant.turn``) that the harness creates queued
(``prepare_turn``: one live turn per thread, the Idempotency-Key bound in the same
transaction) and runs on a worker thread of the API process with its own session while the
caller streams the run's ``agent_events`` as typed public events. A client disconnect never
stops a turn: it runs to its end or limit and releases its budget hold.

Per turn: the owner is re-checked in the request's workspace (another user's thread or
another workspace's is ``not found``); the turn's limits are the policy's per-turn limits
narrowed to what the thread has left (steps, tokens, wall time, cost over all its turns),
and an exhausted thread is ``thread_limit_reached`` (429) with a thread ``budget_exhausted``
event; per-user turns per hour and concurrent turns come from the policy too. The
transcript is rebuilt from events (user messages, earlier answers, proposals with their
current status), never sent by the client; the user's text is the run's first event
(``user_message``). With AI off (setting, platform or workspace switch, or no released lead
prompt) a turn is a deterministic template from the read tools (``templates``), recorded on
the thread as ``user_message`` + ``assistant_message`` with ``templated: true`` and
``llm_used: false``: no run, no gateway call, no budget.

Replies are plain text with ``dclab://<kind>/<id>`` references only (the lead validator
allows no other link); clients render them with linkify off, and any model free text
(proposal ``reason``, ``rationale``, ``business_objective``, ``intent``) as plain text.
"""

from __future__ import annotations

import asyncio
import logging
import threading
import time
from collections.abc import AsyncIterator, Callable
from dataclasses import dataclass
from datetime import timedelta
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import func, select, update
from sqlalchemy.orm import Session

from app.agents.assistant import templates
from app.agents.contracts import (
    SYSTEM_SOURCE,
    WORKSPACE_TEXT_SOURCE,
    ContextField,
    FieldSource,
    LeadTurn,
    RunLimits,
    TranscriptItem,
    Untrusted,
)
from app.agents.governance.platform_default import PLATFORM_DEFAULT
from app.agents.governance.policy import PolicyUnavailable, advisory_lock, effective_policy
from app.agents.governance.switches import effective_switches
from app.agents.harness.recorder import Recorder, preview
from app.agents.harness.service import (
    AgentRunRefused,
    AgentService,
    end_abandoned_turns,
    lead_spec,
    policy_limits,
)
from app.agents.tools.catalog import ToolContext
from app.config import get_settings
from app.db.models import AgentEvent, AgentProposal, AgentRun, Dataset, User
from app.domain.agent_records import AGENT_RUN_TERMINAL_STATUSES
from app.domain.errors import IdempotencyKeyReusedError
from app.services import idempotency_service
from app.services.authorization_service import can_execute_workspace_ml

logger = logging.getLogger("dclab.agents.assistant")

MESSAGE_MAX_CHARS = 4000
TRANSCRIPT_ITEMS = 20
HISTORY_MAX = 400
POLL_S = 0.1
RESOURCE_TURN = "assistant_turn"
LOCK_NS_ASSISTANT_USER = 72065  # next to the recorder's 72064
QUICK_ACTIONS = templates.QUICK_ACTIONS
_LIVE = ("queued", "running")
# Stored event -> public event (SSE and history); the other event types stay internal.
_PUBLIC = {"run_started": "turn_started", "tool_call_requested": "tool_call", "tool_call_finished": "tool_result",
           "tool_call_denied": "call_refused", "step_rejected": "step_rejected", "proposal_created": "proposal_created",
           "assistant_message": "assistant_message", "budget_exhausted": "budget_exhausted",
           "run_finished": "turn_done", "run_failed": "turn_done", "user_message": "user_message"}
_FIELDS = {"tool_call": ("call", "tool"), "tool_result": ("call", "tool", "ok", "code", "proposal_id", "preview"),
           "proposal_created": ("proposal_id", "tool", "type", "status", "code"),
           "assistant_message": ("kind", "message", "citations", "llm_used", "fallback", "templated", "actions"),
           "budget_exhausted": ("scope", "limit", "call"), "turn_done": ("status", "error_code", "proposal_ids"),
           "user_message": ("message",)}


class AssistantError(Exception):
    def __init__(self, status: int, code: str, message: str, **details: Any) -> None:
        super().__init__(message)
        self.status, self.code, self.message, self.details = status, code, message, details


def agent_service() -> AgentService:
    return AgentService()


def ai_settings() -> Any:
    return get_settings()


def _not_found() -> AssistantError:
    return AssistantError(404, "not_found", "assistant thread not found")


def _refused(exc: AgentRunRefused) -> AssistantError:
    status = {"turn_in_progress": 409, "run_already_active": 409, "human_session_required": 403,
              "policy_unavailable": 503}.get(exc.code, 404 if exc.code in ("forbidden", "subject_not_found",
                                                                          "thread_not_found") else 409)
    return _not_found() if status == 404 else AssistantError(status, exc.code, f"turn refused: {exc.code}")


# --- threads -------------------------------------------------------------------------------


def thread_spec(db: Session, *, user: User, workspace_id: UUID, project_id: UUID) -> Any:
    from app.services import decision_record_service as drs

    try:
        drs.load_project(db, workspace_id=workspace_id, project_id=project_id)
    except Exception:  # noqa: BLE001 - unknown or foreign project: one answer
        raise _not_found() from None
    spec = lead_spec(db, workspace_id=workspace_id, project_id=project_id, user_id=user.id)
    if spec is None:
        raise AssistantError(503, "assistant_unavailable", "the assistant prompt is not released")
    return spec


def open_thread(db: Session, spec: Any, *, title: str | None, bind: Callable[[UUID], None]) -> AgentRun:
    try:
        return agent_service().open_thread(db, spec, title=title, bind=bind)
    except AgentRunRefused as exc:
        raise _refused(exc) from None


def load_thread(db: Session, *, user: User, workspace_id: UUID, thread_id: UUID) -> AgentRun:
    """The caller's own thread in the request's workspace; anything else is not found."""

    thread = db.scalar(select(AgentRun).where(AgentRun.workspace_id == workspace_id, AgentRun.id == thread_id,
                                              AgentRun.kind == "assistant", AgentRun.created_by_user_id == user.id))
    if thread is None:
        raise _not_found()
    return thread


def list_threads(db: Session, *, user: User, workspace_id: UUID, project_id: UUID | None, limit: int) -> list[AgentRun]:
    query = select(AgentRun).where(AgentRun.workspace_id == workspace_id, AgentRun.kind == "assistant",
                                   AgentRun.created_by_user_id == user.id)
    if project_id is not None:
        query = query.where(AgentRun.project_id == project_id)
    return list(db.scalars(query.order_by(AgentRun.last_activity_at.desc(), AgentRun.id).limit(limit)))


def ai_blocked(db: Session, thread: AgentRun) -> str | None:
    settings = ai_settings()
    return effective_switches(db, thread.workspace_id).blocking(
        ai_enabled=bool(getattr(settings, "ai_enabled", False)), agent_key=thread.agent_key, purpose=thread.purpose)


@dataclass(frozen=True)
class Allowance:
    limits: RunLimits  # the thread's limits (current policy ⊓ row)
    used: dict[str, int]

    def remaining(self) -> dict[str, int]:
        return {"steps": self.limits.steps - self.used["steps"], "tokens": self.limits.tokens - self.used["tokens"],
                "wall_s": (self.limits.wall_s * 1000 - self.used["wall_ms"]) // 1000,
                "cost_micros": self.limits.cost_micros - self.used["cost_micros"]}


def allowance(db: Session, thread: AgentRun, policy: Any) -> Allowance:
    usage = AgentRun.usage

    def total(*keys: str) -> Any:
        return func.coalesce(func.sum(sum(func.coalesce(usage[key].as_integer(), 0) for key in keys)), 0)

    row = db.execute(select(func.count(), total("steps"), total("tokens_in", "tokens_out"), total("wall_ms"),
                            func.coalesce(func.sum(AgentRun.cost_micros), 0)).where(
        AgentRun.workspace_id == thread.workspace_id, AgentRun.parent_run_id == thread.id)).one()
    stored = RunLimits(**{name: thread.limits[name] for name in RunLimits.model_fields})
    return Allowance(policy_limits(policy, "assistant").narrow(stored), {
        "turns": row[0], "steps": row[1], "tokens": row[2], "wall_ms": row[3], "cost_micros": row[4]})


# --- events ----------------------------------------------------------------------------------


def public_event(event: AgentEvent, turn_id: UUID) -> dict[str, Any] | None:
    kind = _PUBLIC.get(event.type)
    if kind is None:
        return None
    payload = dict(event.payload or {})
    if event.type == "step_rejected" and "check" not in payload:  # a model call refused before the gateway
        kind, data = "call_refused", {"target": "model", "call": payload.get("call"), "code": payload.get("reason")}
    elif kind == "call_refused":
        data = {"target": "tool", **{key: payload.get(key) for key in ("call", "tool", "code", "proposal_id")}}
    elif kind == "step_rejected":
        data = {"check": payload.get("check"), "reasons": payload.get("reasons") or []}
    else:
        data = {key: payload[key] for key in _FIELDS.get(kind, ()) if key in payload}
    return {"type": kind, "turn_id": str(turn_id), "seq": event.seq, "created_at": event.created_at, "data": data}


def _thread_events(db: Session, thread: AgentRun, limit: int = HISTORY_MAX) -> list[AgentEvent]:
    turns = select(AgentRun.id).where(AgentRun.workspace_id == thread.workspace_id,
                                      AgentRun.parent_run_id == thread.id)
    rows = db.scalars(select(AgentEvent).where(
        AgentEvent.workspace_id == thread.workspace_id, AgentEvent.type.in_(tuple(_PUBLIC)),
        (AgentEvent.run_id == thread.id) | AgentEvent.run_id.in_(turns),
    ).order_by(AgentEvent.created_at.desc(), AgentEvent.run_id.desc(), AgentEvent.seq.desc()).limit(limit))
    return list(reversed(list(rows)))


def _turn_of(event: AgentEvent, thread: AgentRun) -> UUID:
    if event.run_id != thread.id:
        return event.run_id
    try:
        return UUID(str((event.payload or {}).get("turn_id")))
    except ValueError:
        return thread.id  # a thread-level event (thread limit reached)


def history(db: Session, thread: AgentRun) -> list[dict[str, Any]]:
    return [item for event in _thread_events(db, thread)
            if (item := public_event(event, _turn_of(event, thread))) is not None]


def transcript(db: Session, thread: AgentRun, policy: Any) -> tuple[TranscriptItem, ...]:
    """The last items of the conversation, tagged for the gateway: user text is workspace
    text (metadata); an earlier answer may state CV numbers, so its text travels as
    aggregates of the project's datasets (dropped by their ADR 0005 labels, omitted when the
    policy allows only metadata); proposals as system fields with their current status."""

    events = [e for e in _thread_events(db, thread, 200)
              if e.type in ("user_message", "assistant_message", "proposal_created")][-TRANSCRIPT_ITEMS:]
    datasets = tuple(FieldSource(kind="dataset", dataset_id=item) for item in db.scalars(select(Dataset.id).where(
        Dataset.workspace_id == thread.workspace_id, Dataset.project_id == thread.project_id).limit(65)))
    # Every project dataset must be a source (its label can deny); beyond the 64-source cap, no text.
    numbers_allowed = policy.data.max_class in ("aggregates", "sample_values") and len(datasets) <= 64
    ids = [UUID(e.payload["proposal_id"]) for e in events if e.type == "proposal_created"]
    status = dict(db.execute(select(AgentProposal.id, AgentProposal.status).where(
        AgentProposal.workspace_id == thread.workspace_id, AgentProposal.id.in_(ids))).all()) if ids else {}

    def tagged(key: str, value: Any, *, sources: tuple = (SYSTEM_SOURCE,), data_class: str = "metadata",
               scope: str = "none") -> ContextField:
        return ContextField(key=key, value=value, data_class=data_class, outcome_scope=scope, sources=sources)

    items = []
    for event in events:
        payload = event.payload or {}
        text = str(payload.get("message") or "")[:MESSAGE_MAX_CHARS]
        if event.type == "user_message" and text:
            items.append(TranscriptItem(kind="user_message", fields=(
                tagged("message", Untrusted(untrusted_text=text), sources=(WORKSPACE_TEXT_SOURCE,)),)))
        elif event.type == "assistant_message":
            cited: dict[str, list[UUID]] = {}
            for item in payload.get("citations") or []:
                cited.setdefault(str(item.get("kind")), []).append(UUID(str(item.get("id"))))
            fields = [tagged("citations", cited)] if cited else []
            if text and numbers_allowed and datasets:
                fields.append(tagged("message", Untrusted(untrusted_text=text), sources=datasets,
                                     data_class="aggregates", scope="cv"))
            items.append(TranscriptItem(kind="assistant_message", fields=tuple(fields)))
        elif event.type == "proposal_created" and payload.get("status") == "proposed":
            now = status.get(UUID(payload["proposal_id"]), "proposed")
            items.append(TranscriptItem(kind="tool_result", tool_name=payload.get("tool"), fields=(
                tagged("tool_outcome", {"status": {now: True}, "proposal_id": UUID(payload["proposal_id"]),
                                        "pending_confirmation": now == "proposed"}),)))
    return tuple(items[-TRANSCRIPT_ITEMS:])


# --- turns -----------------------------------------------------------------------------------

MAX_TURN_THREADS = 16  # turns this process runs at once (each holds a DB connection)
MAX_STREAMS_PER_TURN = 2  # concurrent SSE streams (first + replays) polling one turn
_TURN_SLOTS = threading.BoundedSemaphore(MAX_TURN_THREADS)
_RUNNING: set[UUID] = set()  # turns this process is running: never swept as abandoned
_STREAMS: dict[UUID, int] = {}
_GUARD = threading.Lock()


@dataclass
class Turn:
    turn_id: UUID
    replayed: bool
    events: AsyncIterator[dict[str, Any]]
    templated: bool = False


def _limit_reached(db: Session, thread: AgentRun, limit: str, *, scope: str, code: str) -> AssistantError:
    """429 with a thread ``budget_exhausted`` event, recorded once per repeated refusal."""

    body = {"scope": scope, "limit": limit}
    last = db.execute(select(AgentEvent.type, AgentEvent.payload).where(
        AgentEvent.workspace_id == thread.workspace_id, AgentEvent.run_id == thread.id,
    ).order_by(AgentEvent.seq.desc()).limit(1)).first()
    if last is None or (last.type, last.payload) != ("budget_exhausted", body):
        Recorder(_bind(thread), workspace_id=thread.workspace_id, run_id=thread.id).record("budget_exhausted", body)
    return AssistantError(429, code, f"the {scope} limit ({limit}) is reached", limit=limit)


def _bind(row: Any) -> Any:
    from sqlalchemy.orm import object_session

    return object_session(row).get_bind()


def _own_threads(thread: AgentRun) -> Any:
    return select(AgentRun.id).where(AgentRun.workspace_id == thread.workspace_id, AgentRun.kind == "assistant",
                                     AgentRun.created_by_user_id == thread.created_by_user_id)


def _lock_owner(db: Session, thread: AgentRun) -> None:
    """The owner's limit checks and the turn's writes are one critical section (until commit)."""

    advisory_lock(db, LOCK_NS_ASSISTANT_USER, f"{thread.workspace_id}:{thread.created_by_user_id}")


def _user_limits(db: Session, thread: AgentRun, policy: Any, *, templated: bool = False) -> str | None:
    """The owner's ``assistant_user`` limits in this workspace (the platform default when no
    policy resolves): live turns, and turns of the last hour (model turns are child runs,
    templated turns ``user_message`` events on the threads)."""

    limits = (policy or PLATFORM_DEFAULT).limits.assistant_user
    threads, hour = _own_threads(thread), func.now() - timedelta(hours=1)
    turns = select(func.count()).select_from(AgentRun).where(
        AgentRun.workspace_id == thread.workspace_id, AgentRun.kind == "lead", AgentRun.parent_run_id.in_(threads))
    if not templated and db.scalar(turns.where(AgentRun.status.in_(_LIVE))) >= limits.concurrent_turns:
        return "concurrent_turns"
    said = select(func.count()).select_from(AgentEvent).where(
        AgentEvent.workspace_id == thread.workspace_id, AgentEvent.run_id.in_(threads),
        AgentEvent.type == "user_message", AgentEvent.created_at >= hour)
    if db.scalar(turns.where(AgentRun.created_at >= hour)) + db.scalar(said) >= limits.turns_per_hour:
        return "turns_per_hour"
    return None


def post_message(db: Session, *, user: User, thread: AgentRun, text: str, scope: Any, binding: Any) -> Turn:
    """Replay the turn bound to the Idempotency-Key, or start one (LLM or template)."""

    try:
        found = idempotency_service.find_bound(db, scope, binding)
    except IdempotencyKeyReusedError:
        db.rollback()
        raise AssistantError(409, "idempotency_key_conflict", "the Idempotency-Key was used for another message")
    if found is not None:
        return _replay(db, thread, found.resource_id)
    db.execute(update(AgentRun).where(AgentRun.workspace_id == thread.workspace_id, AgentRun.id == thread.id)
               .values(last_activity_at=func.now()))
    db.commit()
    try:
        policy = effective_policy(db, thread.workspace_id).policy
    except PolicyUnavailable:
        policy = None
    if ai_blocked(db, thread) or lead_spec(db, workspace_id=thread.workspace_id, project_id=thread.project_id,
                                           user_id=user.id) is None:
        return _templated(db, user=user, thread=thread, text=text, scope=scope, binding=binding, policy=policy)
    if policy is None:
        raise AssistantError(503, "policy_unavailable", "the AI policy is unavailable")
    with _GUARD:
        running = frozenset(_RUNNING)
    end_abandoned_turns(db, workspace_id=thread.workspace_id, parent_ids=_own_threads(thread), skip=running)
    _lock_owner(db, thread)
    left = allowance(db, thread, policy).remaining()
    exhausted = next((name for name, value in left.items() if value < 1), None)
    if exhausted is not None:
        raise _limit_reached(db, thread, exhausted, scope="thread", code="thread_limit_reached")
    user_limit = _user_limits(db, thread, policy)
    if user_limit is not None:
        raise _limit_reached(db, thread, user_limit, scope="user", code="user_turn_limit")
    turn = LeadTurn(user_text=Untrusted(untrusted_text=text), transcript=transcript(db, thread, policy))
    spec = lead_spec(db, workspace_id=thread.workspace_id, project_id=thread.project_id, user_id=user.id, turn=turn,
                     parent_run_id=thread.id, may_propose=can_execute_workspace_ml(db, user, thread.workspace_id),
                     limits=RunLimits(steps=left["steps"], tokens=left["tokens"], wall_s=min(3600, left["wall_s"]),
                                      cost_micros=left["cost_micros"], tool_calls=1000))

    def bind(run_id: UUID) -> None:
        idempotency_service.bind(db, scope, binding, resource_kind=RESOURCE_TURN, resource_id=run_id,
                                 response_status=200)

    if not _TURN_SLOTS.acquire(blocking=False):
        db.rollback()
        raise AssistantError(503, "assistant_busy", "too many assistant turns are running; retry shortly")
    started = False
    service = agent_service()
    try:
        try:
            run_id = service.prepare_turn(db, spec, bind=bind)
        except (AgentRunRefused, idempotency_service.IdempotencyKeyRaceError) as exc:
            db.rollback()
            again = idempotency_service.find_bound(db, scope, binding)  # a concurrent duplicate won the key
            if again is not None:
                return _replay(db, thread, again.resource_id)
            if isinstance(exc, AgentRunRefused):
                raise _refused(exc) from None
            raise AssistantError(409, "concurrent_conflict", "a concurrent request conflicted; retry") from None
        bind_ = _bind(thread)

        def execute() -> None:
            try:
                with Session(bind=bind_) as session:
                    service.run(session, spec.model_copy(update={"run_id": run_id}))
            except Exception:  # noqa: BLE001 - the harness ends the run; this only logs
                logger.exception("assistant turn failed", extra={"agent_run_id": str(run_id)})
            finally:
                with _GUARD:
                    _RUNNING.discard(run_id)
                _TURN_SLOTS.release()

        try:  # started here, not by the stream: a client gone before the first byte never strands it
            Recorder(bind_, workspace_id=thread.workspace_id, run_id=run_id).record("user_message", {
                "message": preview(text, MESSAGE_MAX_CHARS), "chars": len(text),
                "transcript_items": len(turn.transcript)})
            with _GUARD:
                _RUNNING.add(run_id)
            worker = threading.Thread(target=execute, name=f"assistant-turn-{run_id}")
            worker.start()
            started = True
        except Exception:
            with _GUARD:
                _RUNNING.discard(run_id)
            service.gateway.release_run(db, workspace_id=thread.workspace_id, agent_run_id=run_id,
                                        final_status="failed", error_code="turn_not_started")
            raise
    finally:
        if not started:
            _TURN_SLOTS.release()
    return Turn(run_id, False, _run_events(bind_, thread.workspace_id, run_id, worker))


def _templated(db: Session, *, user: User, thread: AgentRun, text: str, scope: Any, binding: Any,
               policy: Any) -> Turn:
    """A deterministic reply; the key and both events commit together (never a bound, empty turn)."""

    turn_id = uuid4()
    reply = templates.reply(ToolContext(db=db, actor=user, workspace_id=thread.workspace_id), thread.project_id, text)
    db.rollback()  # the reads above
    _lock_owner(db, thread)
    limit = _user_limits(db, thread, policy, templated=True)
    if limit is not None:
        raise _limit_reached(db, thread, limit, scope="user", code="user_turn_limit")
    recorder = Recorder(_bind(thread), workspace_id=thread.workspace_id, run_id=thread.id)
    try:
        idempotency_service.bind(db, scope, binding, resource_kind=RESOURCE_TURN, resource_id=turn_id,
                                 response_status=200)
        recorder.record("user_message", {"turn_id": str(turn_id), "message": preview(text, MESSAGE_MAX_CHARS),
                                         "chars": len(text)}, session=db)
        recorder.record("assistant_message", {**reply, "turn_id": str(turn_id), "templated": True,
                                              "llm_used": False, "fallback": None, "actions": list(QUICK_ACTIONS)},
                        session=db)
        db.commit()
    except idempotency_service.IdempotencyKeyRaceError:
        db.rollback()
        again = idempotency_service.find_bound(db, scope, binding)
        if again is None:
            raise AssistantError(409, "concurrent_conflict", "a concurrent request conflicted; retry") from None
        return _replay(db, thread, again.resource_id)
    return Turn(turn_id, False, _iterate(_templated_items(db, thread, turn_id)), templated=True)


def _replay(db: Session, thread: AgentRun, turn_id: UUID) -> Turn:
    run = db.scalar(select(AgentRun.id).where(AgentRun.workspace_id == thread.workspace_id, AgentRun.id == turn_id,
                                              AgentRun.parent_run_id == thread.id))
    if run is None:
        return Turn(turn_id, True, _iterate(_templated_items(db, thread, turn_id)), templated=True)
    with _GUARD:
        if _STREAMS.get(turn_id, 0) >= MAX_STREAMS_PER_TURN:
            raise AssistantError(429, "too_many_streams", "this turn is already being streamed; retry when it ends")
    return Turn(turn_id, True, _run_events(_bind(thread), thread.workspace_id, turn_id, None))


def _templated_items(db: Session, thread: AgentRun, turn_id: UUID) -> list[dict[str, Any]]:
    """A templated turn as its stream (read now: the request's session never reaches the stream)."""

    events = db.scalars(select(AgentEvent).where(
        AgentEvent.workspace_id == thread.workspace_id, AgentEvent.run_id == thread.id,
        AgentEvent.type == "assistant_message", AgentEvent.payload["turn_id"].astext == str(turn_id),
    ).order_by(AgentEvent.seq))
    said = [item for event in events if (item := public_event(event, turn_id)) is not None]
    done = {"status": "completed", "error_code": None, "proposal_ids": [], "templated": True}
    return [{"type": "turn_started", "turn_id": str(turn_id), "seq": 0, "data": {"templated": True}}, *said,
            {"type": "turn_done", "turn_id": str(turn_id), "seq": 0, "data": done}]


async def _iterate(items: list[dict[str, Any]]) -> AsyncIterator[dict[str, Any]]:
    for item in items:
        yield item


def _poll(bind: Any, workspace_id: UUID, run_id: UUID, after: int) -> tuple[list[AgentEvent], str | None, int]:
    with Session(bind=bind) as session:
        rows = list(session.scalars(select(AgentEvent).where(
            AgentEvent.workspace_id == workspace_id, AgentEvent.run_id == run_id, AgentEvent.seq > after,
        ).order_by(AgentEvent.seq)))
        run = session.execute(select(AgentRun.status, AgentRun.limits).where(
            AgentRun.workspace_id == workspace_id, AgentRun.id == run_id)).one_or_none()
        session.expunge_all()
    return rows, run.status if run else None, int((run.limits or {}).get("wall_s", 120)) if run else 0


async def _run_events(bind: Any, workspace_id: UUID, run_id: UUID,
                      worker: threading.Thread | None) -> AsyncIterator[dict[str, Any]]:
    """The turn run's public events as they are recorded, until its ``run_finished`` /
    ``run_failed``; ``worker`` (a new turn's thread) is never cancelled by the stream."""

    with _GUARD:
        _STREAMS[run_id] = _STREAMS.get(run_id, 0) + 1
    try:
        after, grace, deadline = 0, False, None
        while True:
            rows, status, wall_s = await asyncio.to_thread(_poll, bind, workspace_id, run_id, after)
            deadline = deadline or time.monotonic() + wall_s + 120
            for event in rows:
                after = event.seq
                if (item := public_event(event, run_id)) is not None and item["type"] != "user_message":
                    yield item
                if event.type in ("run_finished", "run_failed"):
                    return
            ended = (worker is not None and not worker.is_alive()) or (worker is None and status in
                                                                       AGENT_RUN_TERMINAL_STATUSES)
            if (ended and not rows and grace) or time.monotonic() > deadline:  # ended without its last event
                yield {"type": "turn_done", "turn_id": str(run_id), "seq": after,
                       "data": {"status": status, "error_code": "turn_stream_ended", "proposal_ids": []}}
                return
            grace = ended and not rows
            await asyncio.sleep(POLL_S)
    finally:
        with _GUARD:
            left = _STREAMS.pop(run_id, 1) - 1
            if left > 0:
                _STREAMS[run_id] = left
