"""``AgentService``: the only way to run an agent (ADR 0009 §5.1; P6.10-A).

Lifecycle of ``run(spec)``: (1) authorize the principal (a user: workspace read, ML
write checked by the ``capability`` pre-run hook for runs that may propose; a service
token: the ``authenticate_service_token`` checks re-done from the token row — tokens
enabled, active, same workspace, active creator with an explicit role, ``read`` scope;
write scopes need an ML-write role) and the project / subject nodes; (2) create the
run (``queued`` -> ``running``; limits = policy ⊓ spec; policy, levels and switches
digest; catalog digest); (3) build the context in agent consumer mode and write its
digest once; (4) redact it with the gateway's code to the run's class (dry run, the
summary is recorded); pre-run hooks; (5) reserve the hold (``AGENT_RUN_BUDGET_KIND``);
(6) run the runtime: model calls through pre-call hooks and the gateway, tool calls
through registry, capability, validator, consumer-mode service, shaping and post-tool
hooks (write tools create L1 ``ToolCallProposal`` rows and never act; an invalid call
is a ``rejected_by_validator`` proposal or a denial, never an exception); (7) validate
the output (post-run hooks: ``rejected_by_validator``); (8) persist the output's
proposal at its level; (9) release: usage and error code are written while the run is
live, then the gateway's ``release`` / ``release_run`` is the only live -> terminal
transition (and stamps ``budget_released_at``); (10) the eval sample is recorded with
``run_finished``. Every step is an ``agent_events`` row (``recorder``).

Caller contract: the harness commits its session before every gateway call and never
locks the run row. Assistant threads (``kind = 'assistant'``) are refused here; the lead
loop (``lead_loop``, P6.3-B) runs as a human's ``lead`` run whose spec carries the turn
(never queued: ``submit`` refuses it), checks each step through ``session.check`` (the
output validator, recorded as ``step_validated`` / ``step_rejected``) and ends with an
``assistant_message`` event (its answer, or the limit / unavailable template); a step,
token, wall or tool-call limit records a typed ``budget_exhausted`` event (the first three
end the run ``over_budget`` / ``timed_out``). Wall time is checked before every model call, and an async runtime
(``arun``: NOOA) runs under an asyncio timeout of the remaining wall time (P6.3-A); a
runtime that refuses (``RuntimeRefused``) ends the run ``failed`` with its code. The
built-in runtimes ``fake``, ``nooa_predict`` and ``lead_loop`` are registered here and
nowhere else (CI rule c). The ``agents.run`` job (payload ``{"agent_run_id"}``) calls ``run`` with
the spec rebuilt from the row (``spec_for_job``); ``submit`` queues it.
"""

from __future__ import annotations

import logging
import re
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from typing import Any
from uuid import UUID

from pydantic import BaseModel, ValidationError
from sqlalchemy import func, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.agents.contracts import AgentRunResult, AgentRunSpec, Citation, ContextEnvelope, RunLimits, Untrusted
from app.agents.gateway import redaction
from app.agents.gateway.budget import AGENT_RUN_BUDGET_KIND
from app.agents.gateway.contract import CompletionRequest, CompletionResponse, GatewayRefusal, Refusal
from app.agents.gateway.service import GatewayService
from app.agents.governance.decision_points import REGISTRY, answer_ceiling
from app.agents.governance.platform_default import AiPolicyV1
from app.agents.governance.policy import PolicyUnavailable, effective_level, effective_policy
from app.agents.governance.switches import effective_switches
from app.agents.harness import hooks as h
from app.agents.harness.context import build_context, within
from app.agents.harness.recorder import Recorder, canonical, digest, output_digest, preview
from app.agents.harness.validation import check_proposal_nodes, draft_reasons, output_reasons, proposal_payload
from app.agents import classes  # noqa: F401 - registers the specialist classes (P6.4-A)
from app.agents.lead import runtime as lead_runtime
from app.agents.runtime import fake_runtime, nooa_runtime
from app.agents.runtime.base import (
    AGENT_CLASSES,
    RuntimeFactory,
    RuntimeRefused,
    RuntimeWallTimeout,
    ToolOutcome,
    run_coroutine,
)
from app.agents.tools.catalog import ToolContext, ToolDefinition, ToolError, catalog_digest, get as get_tool, visible
from app.agents.tools.render import to_context_fields, to_mcp
from app.agents.tools.shaping import redact
from app.config import get_settings
from app.db.models import AgentProposal, AgentRun, ServiceToken, User
from app.domain.agent_records import (
    AGENT_RUN_LIVE_STATUSES,
    AGENT_RUN_TERMINAL_STATUSES,
    AGENT_SUBJECT_COLUMNS,
    CODE_PATTERN,
    KEY_PATTERN,
)
from app.domain.errors import RunCancelledError
from app.domain.ml_jobs import HANDLER_AGENTS_RUN, HANDLER_VERSION_AGENTS_RUN, JOB_TYPE_AGENT_RUN
from app.domain.service_tokens import SCOPE_READ, WRITE_SCOPES

logger = logging.getLogger("dclab.agents.harness")

RUNTIMES: dict[str, RuntimeFactory] = {}
RUN_KIND_ROLE = {"lead": "lead", "specialist": "specialist", "ops": "specialist"}
# Hook denials / gateway refusals that end the run with a specific status.
_STOP_STATUS = {"step_limit": "over_budget", "token_limit": "over_budget", "budget_exhausted": "over_budget",
                "wall_limit": "timed_out", "timeout": "timed_out", "kill_switch": "failed"}
_DENIAL_REFUSAL = {"step_limit": "budget_exhausted", "token_limit": "budget_exhausted",
                   "wall_limit": "timeout", "kill_switch": "kill_switch"}
_LIMITS = {"step_limit": "steps", "token_limit": "tokens", "wall_limit": "wall_time"}
_CODE = re.compile(CODE_PATTERN)
_KEY = re.compile(KEY_PATTERN)


def register_runtime(name: str, factory: RuntimeFactory) -> None:
    """P6.3-A registers ``fake`` / ``nooa_predict``; P6.3-B ``lead_loop``."""

    RUNTIMES[name] = factory


def unregister_runtime(name: str) -> None:
    RUNTIMES.pop(name, None)


register_runtime("fake", fake_runtime.factory)  # development / tests only (refused elsewhere)
register_runtime("nooa_predict", nooa_runtime.factory)  # fails closed without the agents extra
register_runtime("lead_loop", lead_runtime.factory)  # the assistant's bounded loop (needs the spec's turn)


class AgentRunRefused(Exception):
    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


def _code(value: str | None, fallback: str = "validator_rejected") -> str | None:
    if value is None:
        return None
    return value if _CODE.fullmatch(value) else fallback


@dataclass(frozen=True)
class Principal:
    user: User  # the acting human (a token's creator)
    token: ServiceToken | None
    token_scopes: frozenset[str] | None
    ml_write_role: bool
    can_read: bool
    can_propose: bool


def authorize(db: Session, spec: AgentRunSpec, *, settings: Any) -> Principal:
    from app.services import decision_record_service as drs
    from app.services.authorization_service import (
        can_execute_workspace_ml,
        can_read_workspace,
        explicit_workspace_role,
        is_ml_write_role,
    )
    from app.services.service_token_service import token_status

    if spec.kind == "assistant":
        raise AgentRunRefused("assistant_turns_unsupported")  # threads: P6.3-B
    if spec.runtime == "lead_loop" and spec.service_token_id is not None:
        raise AgentRunRefused("human_session_required")  # ADR 0009 §7.2: the assistant is human-only
    if spec.service_token_id is not None:
        if not settings.service_tokens_enabled:
            raise AgentRunRefused("service_tokens_disabled")
        token = db.scalar(select(ServiceToken).where(
            ServiceToken.id == spec.service_token_id, ServiceToken.workspace_id == spec.workspace_id))
        if token is None or token_status(token) != "active":
            raise AgentRunRefused("principal_inactive")
        user = db.get(User, token.created_by_user_id)
        role = explicit_workspace_role(db, user, spec.workspace_id) if user is not None and user.is_active else None
        if role is None:
            raise AgentRunRefused("principal_inactive")  # the creator lost the membership the token borrows
        scopes = frozenset(token.scopes)
        if SCOPE_READ not in scopes:
            raise AgentRunRefused("insufficient_scope")
        ml_write = is_ml_write_role(role)
        principal = Principal(user, token, scopes, ml_write, True, ml_write and bool(scopes & WRITE_SCOPES))
    else:
        user = db.get(User, spec.user_id)
        if user is None or not user.is_active or not can_read_workspace(db, user, spec.workspace_id):
            raise AgentRunRefused("forbidden")
        can = can_execute_workspace_ml(db, user, spec.workspace_id)
        principal = Principal(user, None, None, can, True, can)
    try:
        if spec.project_id is not None:
            drs.load_project(db, workspace_id=spec.workspace_id, project_id=spec.project_id)
            if spec.subject_kind in AGENT_SUBJECT_COLUMNS:
                drs.load_subject(db, workspace_id=spec.workspace_id, project_id=spec.project_id,
                                 subject_kind=spec.subject_kind, subject_id=spec.subject_id)
        elif spec.subject_kind in AGENT_SUBJECT_COLUMNS:
            raise AgentRunRefused("subject_not_found")
    except AgentRunRefused:
        raise
    except Exception:  # noqa: BLE001 - not found / foreign / invalid: one refusal, no oracle
        raise AgentRunRefused("subject_not_found") from None
    return principal


def policy_limits(policy: AiPolicyV1, kind: str) -> RunLimits:
    if kind == "lead":
        turn = policy.limits.assistant_turn
        return RunLimits(steps=turn.steps, tokens=turn.tokens, wall_s=turn.wall_s,
                         cost_micros=policy.budgets.assistant_turn_micros, tool_calls=turn.tool_calls)
    item = policy.limits.specialist
    return RunLimits(steps=item.calls, tokens=item.tokens, wall_s=item.wall_s,
                     cost_micros=policy.budgets.specialist_run_micros, tool_calls=0)


def spec_from_run(run: AgentRun) -> AgentRunSpec:
    """The spec of a stored run (``agents.run``, replay): the principal comes from the row."""

    column = AGENT_SUBJECT_COLUMNS.get(run.subject_kind or "")
    limits = dict(run.limits or {})
    return AgentRunSpec(
        workspace_id=run.workspace_id, project_id=run.project_id, kind=run.kind, agent_key=run.agent_key,
        agent_version=run.agent_version, runtime=run.runtime, runtime_version=run.runtime_version,
        purpose=run.purpose, decision_point_key=run.decision_point_key, subject_kind=run.subject_kind,
        subject_id=getattr(run, column) if column else None, prompt_release_id=run.prompt_release_id,
        user_id=None if run.created_by_service_token_id else run.created_by_user_id,
        service_token_id=run.created_by_service_token_id, outcome_scope=run.outcome_scope,
        limits=RunLimits(**{name: limits[name] for name in RunLimits.model_fields}),
        tool_surface=limits.get("tool_surface"), may_propose=bool(limits.get("may_propose", True)),
        parent_run_id=run.parent_run_id, run_id=run.id,
    )


class AgentService:
    def __init__(self, *, gateway: GatewayService | None = None, hooks: h.HookChain | None = None,
                 settings: Callable[[], Any] | None = None,
                 runtimes: Mapping[str, RuntimeFactory] | None = None) -> None:
        self.gateway = gateway or GatewayService()
        self.hooks = hooks or h.default_chain()
        if self.hooks.hooks[:len(h.BUILTIN_HOOKS)] != h.BUILTIN_HOOKS:
            raise h.HookViolation("a hook chain starts with the built-in hooks (use hooks.default_chain)")
        self._settings = settings or get_settings
        self._runtimes = runtimes

    def runtime_factory(self, name: str) -> RuntimeFactory | None:
        return (self._runtimes if self._runtimes is not None else RUNTIMES).get(name)

    def submit(self, db: Session, spec: AgentRunSpec) -> UUID:
        """Create a queued run and its ``agents.run`` job (payload: the run id only)."""

        from app.services.ml_job_service import create_ml_job

        if spec.runtime == "lead_loop":
            raise AgentRunRefused("runtime_not_queueable")  # the turn input is never persisted
        principal = authorize(db, spec, settings=self._settings())
        run = self._create(db, spec, principal)
        create_ml_job(db, workspace_id=spec.workspace_id, project_id=spec.project_id, job_type=JOB_TYPE_AGENT_RUN,
                      handler_key=HANDLER_AGENTS_RUN, handler_version=HANDLER_VERSION_AGENTS_RUN,
                      target_id=run.id, payload={"agent_run_id": str(run.id)}, max_attempts=1)
        db.commit()
        return run.id

    def run(self, db: Session, spec: AgentRunSpec, *, heartbeat: Callable[[], None] | None = None) -> AgentRunResult:
        settings = self._settings()
        try:
            principal = authorize(db, spec, settings=settings)
            run = self._claim(db, spec) if spec.run_id is not None else self._create(db, spec, principal)
        except AgentRunRefused as exc:
            db.rollback()
            # End the queued run only when it is the caller's own (the job path): a refused
            # caller never ends another principal's run.
            if spec.run_id is not None and exc.code not in ("run_not_queued", "principal_mismatch") \
                    and self._owns(db, spec):
                self.gateway.release_run(db, workspace_id=spec.workspace_id, agent_run_id=spec.run_id,
                                         final_status="failed", error_code=exc.code)
            return AgentRunResult(run_id=spec.run_id, status="failed" if spec.run_id else "refused",
                                  error_code=exc.code)
        return _Execution(self, db, spec, principal, run, settings, heartbeat).execute()

    @staticmethod
    def _principal_of(run: AgentRun) -> tuple[UUID | None, UUID | None]:
        token = run.created_by_service_token_id
        return token, None if token else run.created_by_user_id

    def _owns(self, db: Session, spec: AgentRunSpec) -> bool:
        run = db.scalar(select(AgentRun).where(AgentRun.workspace_id == spec.workspace_id, AgentRun.id == spec.run_id))
        result = run is not None and run.status == "queued" and self._principal_of(run) == (
            spec.service_token_id, spec.user_id)
        db.commit()
        return result

    def _claim(self, db: Session, spec: AgentRunSpec) -> AgentRun:
        run = db.scalar(select(AgentRun).where(AgentRun.workspace_id == spec.workspace_id, AgentRun.id == spec.run_id))
        if run is None or run.status != "queued":
            raise AgentRunRefused("run_not_queued")
        if self._principal_of(run) != (spec.service_token_id, spec.user_id):
            raise AgentRunRefused("principal_mismatch")  # the spec's principal is the row's, never another
        return run

    def _create(self, db: Session, spec: AgentRunSpec, principal: Principal) -> AgentRun:
        try:
            eff = effective_policy(db, spec.workspace_id)
        except PolicyUnavailable:
            raise AgentRunRefused("policy_unavailable") from None
        point = REGISTRY.get(spec.decision_point_key) if spec.decision_point_key else None
        if spec.decision_point_key and point is None:
            raise AgentRunRefused("decision_point_unknown")
        classes = [eff.policy.data.max_class, *([redaction.PARTITION_CLASS[point.evidence_partition]] if point else [])]
        if eff.policy.data.sample_values_per_column == 0:
            classes.append("aggregates")
        switches = effective_switches(db, spec.workspace_id)
        limits = policy_limits(eff.policy, spec.kind).narrow(spec.limits)
        run = AgentRun(
            workspace_id=spec.workspace_id, project_id=spec.project_id, kind=spec.kind, agent_key=spec.agent_key,
            agent_version=spec.agent_version, prompt_release_id=spec.prompt_release_id, runtime=spec.runtime,
            runtime_version=spec.runtime_version, purpose=spec.purpose, decision_point_key=spec.decision_point_key,
            subject_kind=spec.subject_kind, parent_run_id=spec.parent_run_id,
            policy_digest=digest({"policy": eff.digest, "switches": [switches.platform, switches.workspace],
                                  "level": effective_level(db, spec.workspace_id, point.key) if point else None}),
            tool_catalog_digest=catalog_digest(), data_class=redaction.min_class(*classes),
            outcome_scope=redaction.min_scope(spec.outcome_scope, *([point.outcome_scope] if point else [])),
            status="queued", usage={},
            limits={**limits.model_dump(), "tool_surface": spec.tool_surface, "may_propose": spec.may_propose},
            created_by_user_id=None if principal.token else principal.user.id,
            created_by_service_token_id=principal.token.id if principal.token else None,
        )
        column = AGENT_SUBJECT_COLUMNS.get(spec.subject_kind or "")
        if column:
            setattr(run, column, spec.subject_id)
        db.add(run)
        try:
            db.commit()
        except IntegrityError as exc:
            db.rollback()
            active = "uq_agent_runs_active_subject" in str(exc.orig)
            raise AgentRunRefused("run_already_active" if active else "run_invalid") from None
        return run


@dataclass
class _Execution:
    service: AgentService
    db: Session
    spec: AgentRunSpec
    principal: Principal
    run: AgentRun
    settings: Any
    heartbeat: Callable[[], None] | None
    envelope: ContextEnvelope = field(default_factory=ContextEnvelope)
    reservation: Any = None
    stop: tuple[str, str] | None = None
    finished: AgentRunResult | None = None
    output_digest: str | None = None
    citations: list[Citation] = field(default_factory=list)
    calls_seen: int = 0
    ttl_days: int = 7
    sample_values: int = 0
    provider: str | None = None
    model: str | None = None  # the routed model of the last answered call (levels, run row)
    runtime: Any = None
    checks: int = 0

    def __post_init__(self) -> None:
        run = self.run
        self.run_id, self.workspace_id, self.project_id = run.id, run.workspace_id, run.project_id
        self.data_class, self.outcome_scope = run.data_class, run.outcome_scope
        self.limits = RunLimits(**{name: run.limits[name] for name in RunLimits.model_fields})
        self.tools = tuple(item.name for item in visible("assistant")) if self.spec.tool_surface == "assistant" else ()
        self.recorder = Recorder(self.db.get_bind(), workspace_id=self.workspace_id, run_id=self.run_id)
        self.tool_ctx = ToolContext(db=self.db, actor=self.principal.user, workspace_id=self.workspace_id,
                                    service_token_id=self.principal.token.id if self.principal.token else None,
                                    agent_run_id=self.run_id)
        self.ctx = h.HookContext(
            db=self.db, run_id=self.run_id, workspace_id=self.workspace_id, project_id=self.project_id,
            agent_key=run.agent_key, purpose=run.purpose, principal=self.principal, may_propose=self.spec.may_propose,
            data_class=self.data_class, outcome_scope=self.outcome_scope, limits=self.limits, tools=self.tools,
            ai_enabled=bool(getattr(self.settings, "ai_enabled", False)),
            usage={"steps": 0, "calls": 0, "tokens_in": 0, "tokens_out": 0, "wall_ms": 0, "tool_calls": 0,
                   "cache_hits": 0},
            record=self.record, tool_ctx=self.tool_ctx,
        )

    # --- recording and bookkeeping ----------------------------------------------------------

    def record(self, event_type: str, payload: dict[str, Any], *, llm_invocation_id: UUID | None = None) -> None:
        self.recorder.record(event_type, payload, llm_invocation_id=llm_invocation_id)

    def _usage(self) -> dict[str, int]:
        self.ctx.usage["wall_ms"] = int((time.monotonic() - self.ctx.started) * 1000)
        return dict(self.ctx.usage)

    def _beat(self) -> None:
        """Usage + activity (the run stays live) and the job heartbeat after every step."""

        self.db.execute(update(AgentRun).where(
            AgentRun.workspace_id == self.workspace_id, AgentRun.id == self.run_id,
            AgentRun.status.in_(AGENT_RUN_LIVE_STATUSES)).values(usage=self._usage(), last_activity_at=func.now()))
        self.db.commit()
        if self.heartbeat is not None:
            self.heartbeat()  # raises RunCancelledError on a cancel request

    # --- lifecycle -------------------------------------------------------------------------

    def execute(self) -> AgentRunResult:
        try:
            return self._lifecycle()
        except RunCancelledError:
            self.db.rollback()
            self._finish("cancelled", "cancelled")
            raise
        except h.HookViolation:
            logger.exception("harness hook violation", extra={"agent_run_id": str(self.run_id)})
            self.db.rollback()
            return self._finish("failed", "hook_violation")
        except Exception:  # noqa: BLE001 - a run never raises to its caller
            logger.exception("agent run failed", extra={"agent_run_id": str(self.run_id)})
            self.db.rollback()
            return self._finish("failed", "internal_error")

    def _lifecycle(self) -> AgentRunResult:
        db = self.db
        started = db.execute(update(AgentRun).where(
            AgentRun.workspace_id == self.workspace_id, AgentRun.id == self.run_id, AgentRun.status == "queued",
        ).values(status="running", started_at=func.now(), last_activity_at=func.now())).rowcount
        db.commit()
        if started != 1:
            return AgentRunResult(run_id=self.run_id, status="failed", error_code="run_not_queued")
        try:
            policy = effective_policy(db, self.workspace_id).policy
        except PolicyUnavailable:
            return self._finish("failed", "policy_unavailable")
        self.ttl_days, self.sample_values = policy.proposals.ttl_days, policy.data.sample_values_per_column
        # The policy may have narrowed since the run was queued: limits = current policy ⊓ row.
        self.limits = self.ctx.limits = policy_limits(policy, self.spec.kind).narrow(self.limits)
        self.record("run_started", {
            "hooks": self.service.hooks.listing(), "limits": self.limits.model_dump(), "tools": list(self.tools),
            "tool_catalog_digest": self.run.tool_catalog_digest, "policy_digest": self.run.policy_digest,
            "runtime": self.spec.runtime, "data_class": self.data_class, "outcome_scope": self.outcome_scope,
        })
        item = AGENT_CLASSES.get(self.spec.agent_key) if self.spec.kind == "specialist" else None
        built = build_context(self.tool_ctx, project_id=self.project_id, subject_kind=self.spec.subject_kind,
                              subject_id=self.spec.subject_id, data_class=self.data_class,
                              outcome_scope=self.outcome_scope, extra=item.context if item is not None else None)
        self.envelope = built.envelope
        db.execute(update(AgentRun).where(AgentRun.workspace_id == self.workspace_id, AgentRun.id == self.run_id,
                                          AgentRun.context_digest.is_(None)).values(context_digest=built.digest))
        try:
            summary = redaction.redact(db, workspace_id=self.workspace_id, fields=built.envelope.fields, transcript=(),
                                       user_text=(), max_class=self.data_class, max_scope=self.outcome_scope).summary
        except GatewayRefusal:
            db.commit()
            return self._finish("failed", "context_refused")
        db.commit()
        self.record("context_built", {"context_digest": built.digest, "fields": len(built.envelope.fields),
                                      "reads": list(built.reads), "dropped": built.dropped,
                                      "redaction": {key: summary[key] for key in (
                                          "fields_kept", "dropped", "effective_data_class")}})
        effects = self.service.hooks.run("pre_run", self.ctx, None)
        for effect in effects:
            if isinstance(effect, h.NarrowLimits):  # cumulative: a later hook never undoes a narrowing
                self.limits = self.ctx.limits = self.limits.narrow(effect.limits)
        db.commit()
        denied = h.denial(effects)
        if denied:
            return self._finish(_STOP_STATUS.get(denied, "failed"), denied)
        held = self.service.gateway.reserve(
            db, workspace_id=self.workspace_id, project_id=self.project_id, agent_run_id=self.run_id,
            estimate_micros=min(self.spec.estimate_micros or self.limits.cost_micros, self.limits.cost_micros),
            run_kind=AGENT_RUN_BUDGET_KIND[self.spec.kind])
        if isinstance(held, Refusal):
            if held.code == "budget_exhausted":
                self.record("budget_exhausted", {"scope": held.scope})
                return self._finish("over_budget", "budget_exhausted")
            return self._finish("failed", held.code)
        self.reservation = held
        self.record("budget_reserved", {"held_micros": held.held_micros, "run_kind": held.run_kind,
                                        "expires_at": held.expires_at.isoformat()})
        factory = self.service.runtime_factory(self.spec.runtime)
        if factory is None:
            return self._finish("failed", "runtime_unavailable")
        try:
            runtime = factory(self.spec)
            output = self._run_runtime(runtime)
        except RuntimeRefused as exc:
            db.rollback()
            return self._finish("failed", exc.code)
        if self.stop is not None:
            if getattr(output, "meta", None) and output.meta.get("refusal"):
                self._say(output, self.stop[1])  # the lead's static limit / unavailable template
            return self._finish(*self.stop)
        effects = self.service.hooks.run("post_run", self.ctx, h.RunOutput(output, runtime))
        notes: dict[str, Any] = {}
        meta = getattr(output, "meta", None) or {}
        for key in ("refusal", "error"):  # why a runtime ended without output (codes only)
            if meta.get(key):
                notes[f"runtime_{key}"] = _code(str(meta[key]), "runtime_error")
        for effect in effects:
            if isinstance(effect, h.AttachCitation):
                self.citations.extend(effect.citations)
            elif isinstance(effect, h.Observe) and effect.note:
                notes.update(effect.note)
        self.output_digest = output_digest(output)
        denied = h.denial(effects)
        if denied:
            return self._finish("rejected_by_validator", _code(denied), notes)
        rejected = self._persist_draft(output)
        if rejected:
            return self._finish("rejected_by_validator", rejected, notes)
        self._say(output, notes.get("runtime_refusal"))
        return self._finish("completed", None, notes)

    def _say(self, output: Any, fallback: str | None) -> None:
        """The lead's conversation item (redacted, bounded; thread history)."""

        if self.spec.runtime == "lead_loop" and getattr(output, "message", None):
            self.record("assistant_message", {
                "kind": getattr(output.output, "kind", None), "message": preview(output.message, 4000),
                "citations": [item.model_dump(mode="json") for item in output.citations][:32],
                "llm_used": self.ctx.usage["calls"] > 0, "fallback": _code(fallback) if fallback else None})

    def _run_runtime(self, runtime: Any) -> Any:
        """Step 6. An async runtime (``arun``) runs under the remaining wall time and ends
        ``timed_out`` / ``wall_limit`` when it runs out; a sync runtime is bounded by the
        wall check before each model call (its calls are the only slow part)."""

        self.runtime = runtime
        session = SimpleNamespace(envelope=self.envelope, limits=self.limits, tools=self.tools,
                                  complete=self.complete, call_tool=self.call_tool, step=self.step,
                                  check=self.check)
        arun = getattr(runtime, "arun", None)
        if not callable(arun):
            return runtime.run(session)
        remaining = self.limits.wall_s - (time.monotonic() - self.ctx.started)
        try:
            if remaining <= 0:
                raise RuntimeWallTimeout
            return run_coroutine(lambda: arun(session), timeout_s=remaining)
        except RuntimeWallTimeout:
            self.db.rollback()
            self.stop = self.stop or ("timed_out", "wall_limit")
            return None

    def _finish(self, status: str, error_code: str | None, notes: dict[str, Any] | None = None) -> AgentRunResult:
        if self.finished is not None:
            return self.finished
        db, usage = self.db, self._usage()
        db.execute(update(AgentRun).where(
            AgentRun.workspace_id == self.workspace_id, AgentRun.id == self.run_id,
            AgentRun.status.in_(AGENT_RUN_LIVE_STATUSES)).values(
                usage=usage, error_code=error_code, last_activity_at=func.now(),
                **({"provider": self.provider[:32], "model": self.model[:128]} if self.model and self.provider else {})))
        db.commit()
        if self.reservation is not None:
            freed = self.service.gateway.release(db, self.reservation, final_status=status)
        else:
            freed = self.service.gateway.release_run(db, workspace_id=self.workspace_id, agent_run_id=self.run_id,
                                                     final_status=status, error_code=error_code)
        db.expire_all()
        row = db.execute(select(AgentRun.status, AgentRun.cost_micros).where(
            AgentRun.workspace_id == self.workspace_id, AgentRun.id == self.run_id)).one()
        db.commit()
        if row.status not in AGENT_RUN_TERMINAL_STATUSES:
            logger.error("agent run release failed; the run stays live for the janitor",
                         extra={"agent_run_id": str(self.run_id)})
        result = AgentRunResult(run_id=self.run_id, status=row.status, error_code=error_code,
                                output_digest=self.output_digest, proposal_ids=tuple(self.ctx.proposal_ids),
                                cost_micros=row.cost_micros, usage=usage)
        self.finished = result
        try:
            if self.reservation is not None:
                self.record("budget_settled", {"freed_micros": freed or 0, "cost_micros": row.cost_micros})
            self.record("run_finished" if status == "completed" else "run_failed", {
                "status": row.status, "error_code": error_code, "output_digest": self.output_digest,
                "proposal_ids": [str(item) for item in self.ctx.proposal_ids], "usage": usage,
                "citations": [item.model_dump(mode="json") for item in self.citations][:64], **(notes or {}),
            })
        except Exception:  # noqa: BLE001 - the run already ended; the events are best effort now
            logger.exception("could not record the run's last events", extra={"agent_run_id": str(self.run_id)})
        return result

    # --- the runtime's two calls ---------------------------------------------------------------

    def complete(self, *, output_schema: type[BaseModel], max_output_tokens: int, transcript: tuple = (),
                 user_text: tuple = ()) -> CompletionResponse:
        call = self.ctx.usage["calls"] + 1
        if self.stop is not None or self.reservation is None or self.spec.prompt_release_id is None:
            return self._step_rejected(call, "run_cannot_call")
        transcript, user_text = tuple(transcript), tuple(user_text)
        fields = self.envelope.fields + tuple(item for turn in transcript for item in turn.fields)
        effects = self.service.hooks.run("pre_call", self.ctx, h.CallInput(fields, user_text))
        denied = h.denial(effects)
        if denied:
            if denied in _STOP_STATUS:
                self.stop = (_STOP_STATUS[denied], denied)
            if denied in _LIMITS:  # the typed reason the turn ends (ADR 0008 §6 bounds)
                self.record("budget_exhausted", {"call": call, "limit": _LIMITS[denied]})
            return self._step_rejected(call, denied)
        notes = tuple(Untrusted(untrusted_text=f"[harness note] {item.text}")
                      for item in effects if isinstance(item, h.AddSystemNote))
        remaining = max(1.0, self.limits.wall_s - (time.monotonic() - self.ctx.started))
        try:
            request = CompletionRequest(
                agent_role=RUN_KIND_ROLE[self.spec.kind], agent_key=self.run.agent_key, purpose=self.run.purpose,
                decision_point_key=self.run.decision_point_key, workspace_id=self.workspace_id,
                project_id=self.project_id, agent_run_id=self.run_id, prompt_release_id=self.spec.prompt_release_id,
                envelope=self.envelope, transcript=transcript, user_text=user_text + notes,
                output_schema=output_schema, max_output_tokens=max_output_tokens, max_data_class=self.data_class,
                outcome_scope=self.outcome_scope, budget=self.reservation, timeout_s=min(60.0, remaining))
        except ValidationError:
            return self._step_rejected(call, "invalid_request")
        self.db.commit()  # gateway caller contract: nothing pending, no lock held
        self.record("llm_call_started", {
            "call": call, "fields": len(fields), "transcript_items": len(transcript), "notes": len(notes),
            "request_digest": digest({"fields": [item.model_dump(mode="json") for item in fields],
                                      "user_text": [str(item) for item in user_text + notes],
                                      "output_schema": output_schema.__name__}),
        })
        response = self.service.gateway.complete(self.db, request)
        usage = self.ctx.usage
        usage["steps"] += 1
        usage["calls"] += 1
        usage["tokens_in"] += response.usage.input_tokens
        usage["tokens_out"] += response.usage.output_tokens
        usage["cache_hits"] += int(response.cache_hit)
        refusal = response.refusal.code if response.refusal else None
        if response.ok and response.model:
            self.provider, self.model = response.provider, response.model
        self.record("llm_call_finished", {
            "call": call, "ok": response.ok, "refusal": refusal, "cache_hit": response.cache_hit,
            "invocation_id": str(response.invocation_id) if response.invocation_id else None,
            "usage": {"input": response.usage.input_tokens, "output": response.usage.output_tokens},
            "cost_micros": response.cost_micros,
            "output_digest": digest(response.output.model_dump(mode="json")) if response.output is not None else None,
        }, llm_invocation_id=response.invocation_id)
        if refusal in _STOP_STATUS:
            self.stop = (_STOP_STATUS[refusal], refusal)
            if refusal == "budget_exhausted":
                self.record("budget_exhausted", {"call": call, "scope": response.refusal.scope})
        self._beat()
        return response

    def _step_rejected(self, call: int, reason: str) -> CompletionResponse:
        """A model call refused before the gateway (recorded: replay feeds it back)."""

        refusal = _DENIAL_REFUSAL.get(reason, "policy_denied")
        self.record("step_rejected", {"call": call, "reason": reason, "refusal": refusal})
        return CompletionResponse(ok=False, refusal=Refusal(code=refusal, message=reason))

    def check(self, output: Any) -> list[str]:
        """One intermediate step through the run's output validator (ADR 0009 §7.1: tool
        names, citations in the run's project, cited CV numbers, Markdown subset, holdout);
        read-only, never raises (a failing validator rejects the step)."""

        self.checks += 1
        try:
            reasons = ["wall_limit"] if self._out_of_time(self.ctx.usage["calls"]) else output_reasons(
                self.db, output, self.runtime, workspace_id=self.workspace_id, project_id=self.project_id,
                run_id=self.run_id, tool_ctx=self.tool_ctx)
            self.db.commit()
        except Exception:  # noqa: BLE001 - fail closed
            logger.exception("step validation failed", extra={"agent_run_id": str(self.run_id)})
            self.db.rollback()
            reasons = ["validator_failed"]
        reasons = [_code(str(item), "validator_rejected") for item in reasons][:8]
        self.record("step_rejected" if reasons else "step_validated", {
            "check": self.checks, "call": self.ctx.usage["calls"], "reasons": reasons,
            "kind": getattr(getattr(output, "output", None), "kind", None)})
        return reasons

    def _out_of_time(self, call: int) -> bool:
        """Wall time is checked before every model call, tool call and step check: running out
        stops the run (``timed_out``) with the typed ``budget_exhausted`` event once."""

        if self.stop is None and time.monotonic() - self.ctx.started >= self.limits.wall_s:
            self.stop = ("timed_out", "wall_limit")
            self.record("budget_exhausted", {"call": call, "limit": "wall_time"})
        return self.stop is not None

    def step(self, *, method: str, strategy: str) -> None:
        """A runtime's validated strategy step (NOOA ``AfterTurn``; ADR 0009 §5.4)."""

        method, strategy = str(method)[:64], str(strategy)[:64]
        self.record("step_validated", {"call": self.ctx.usage["calls"],
                                       "method": method if _KEY.fullmatch(method) else "invalid_method",
                                       "strategy": strategy if _CODE.fullmatch(strategy) else "invalid_strategy"})

    def call_tool(self, name: str, arguments: Mapping[str, Any], *, reason: str = "") -> ToolOutcome:
        self.calls_seen += 1
        call, name = self.calls_seen, str(name)[:64]
        if not _KEY.fullmatch(name):
            name = "invalid_tool_name"
        argument_digest = digest(dict(arguments) if isinstance(arguments, Mapping) else repr(arguments))
        definition = get_tool(name)
        self.record("tool_call_requested", {"call": call, "tool": name, "argument_digest": argument_digest,
                                            "reason_digest": digest(reason) if reason else None})
        self._out_of_time(call)
        if self.stop is not None:
            return self._denied(call, name, "run_stopped", "harness")
        effects = self.service.hooks.run("pre_tool", self.ctx, h.ToolCallInput(call, name, definition, arguments))
        found = next((item for item in effects if isinstance(item, h.Deny)), None)
        if found is not None and found.reason == "tool_call_limit":
            self.record("budget_exhausted", {"call": call, "limit": "tool_calls"})
        if found is not None:
            if found.hook == h.VALIDATOR_HOOK and definition is not None and definition.effect == "proposal":
                return self._rejected(call, definition, found.reason, argument_digest, reason)
            return self._denied(call, name, found.reason, found.hook)
        if definition.effect == "read" and any(isinstance(item, h.DowngradeToProposal) for item in effects):
            return self._denied(call, name, "downgraded_to_proposal", "pre_tool")
        args = next(item.arguments for item in effects if isinstance(item, h.ModifyArguments))
        self.ctx.usage["tool_calls"] += 1
        if definition.effect == "proposal":
            return self._propose(call, definition, args, argument_digest, reason)
        try:
            shaped = definition.read(self.tool_ctx, args)
        except ToolError as exc:
            self.db.rollback()
            self.record("tool_call_finished", {"call": call, "tool": name, "argument_digest": argument_digest,
                                               "ok": False, "code": exc.code})
            self._beat()
            return ToolOutcome(tool=name, ok=False, status="error", code=exc.code)
        cited: list[Citation] = []
        for effect in self.service.hooks.run("post_tool", self.ctx,
                                             h.ToolResult(call, name, args, argument_digest, shaped)):
            if isinstance(effect, h.ModifyResult):
                shaped = effect.shaped
            elif isinstance(effect, h.AttachCitation):
                cited.extend(effect.citations)
        fields = within(to_context_fields(name, shaped), data_class=self.data_class, outcome_scope=self.outcome_scope)
        _payload, rendered = to_mcp(shaped)
        self.db.commit()
        result_digest = digest(rendered)
        self.citations.extend(cited)
        self.record("tool_call_finished", {"call": call, "tool": name, "argument_digest": argument_digest, "ok": True,
                                           "result_digest": result_digest, "fields": len(fields),
                                           **self._preview(fields)})
        self._beat()
        return ToolOutcome(tool=name, ok=True, status="result", fields=fields, result_digest=result_digest,
                           citations=tuple(cited))

    def _preview(self, fields: tuple) -> dict[str, Any]:
        """The stored preview is what the gateway would let through (ADR 0005 dataset /
        column labels, the run's class and scope); anything dropped is only counted."""

        try:
            redacted = redaction.redact(self.db, workspace_id=self.workspace_id, fields=fields, transcript=(),
                                        user_text=(), max_class=self.data_class, max_scope=self.outcome_scope,
                                        sample_values_per_column=self.sample_values)
        except GatewayRefusal:
            return {"preview": None, "preview_dropped": len(fields)}
        finally:
            self.db.commit()
        dropped = len(fields) - redacted.summary["fields_kept"]
        return {"preview": preview(canonical(redacted.payload["context"])), "preview_dropped": dropped}

    def _denied(self, call: int, name: str, code: str, hook: str) -> ToolOutcome:
        self.record("tool_call_denied", {"call": call, "tool": name, "code": code, "hook": hook})
        return ToolOutcome(tool=name, ok=False, status="denied", code=code)

    def _propose(self, call: int, definition: ToolDefinition, args: dict[str, Any], argument_digest: str,
                 reason: str) -> ToolOutcome:
        try:
            if self.project_id is None:
                raise ToolError("project_required", "a proposal needs the run's project")
            check_proposal_nodes(self.db, workspace_id=self.workspace_id, project_id=self.project_id,
                                 tool=definition.name, arguments=args)
        except ToolError as exc:
            self.db.rollback()
            return self._rejected(call, definition, exc.code, argument_digest, reason)
        payload, payload_digest = proposal_payload(definition, args)
        row = self._insert_proposal(definition, status="proposed", payload=payload, payload_digest=payload_digest,
                                    arguments=args, code=None, reason=reason)
        self.record("tool_call_finished", {"call": call, "tool": definition.name, "argument_digest": argument_digest,
                                           "ok": True, "proposal_id": str(row.id), "result_digest": payload_digest})
        self._beat()
        return ToolOutcome(tool=definition.name, ok=True, status="proposed", proposal_id=row.id,
                           result_digest=payload_digest)

    def _rejected(self, call: int, definition: ToolDefinition, code: str, argument_digest: str,
                  reason: str) -> ToolOutcome:
        """An invalid write-tool call: a ``rejected_by_validator`` proposal (the invalid
        arguments are kept only as their digest), never an exception."""

        code = _code(code, "invalid_arguments")
        if self.project_id is None:
            return self._denied(call, definition.name, code, h.VALIDATOR_HOOK)
        payload = {"tool": definition.name, "argument_digest": argument_digest, "rejected": True,
                   "decision_point_key": definition.decision_point_key, "schema_version": 1}
        row = self._insert_proposal(definition, status="rejected_by_validator", payload=payload,
                                    payload_digest=digest(payload), arguments={"argument_digest": argument_digest},
                                    code=code, reason=reason)
        self.record("tool_call_denied", {"call": call, "tool": definition.name, "code": code,
                                         "hook": h.VALIDATOR_HOOK, "proposal_id": str(row.id)})
        return ToolOutcome(tool=definition.name, ok=False, status="rejected_by_validator", code=code,
                           proposal_id=row.id)

    def _subject(self) -> dict[str, Any]:
        column = AGENT_SUBJECT_COLUMNS.get(self.spec.subject_kind or "")
        if column is None:
            return {"subject_kind": "project"}
        return {"subject_kind": self.spec.subject_kind, column: self.spec.subject_id}

    def _insert_proposal(self, definition: ToolDefinition, *, status: str, payload: dict[str, Any],
                         payload_digest: str, arguments: dict[str, Any], code: str | None,
                         reason: str) -> AgentProposal:
        key = definition.decision_point_key
        row = AgentProposal(
            workspace_id=self.workspace_id, project_id=self.project_id, run_id=self.run_id, decision_point_key=key,
            level_at_proposal=1, answer_ceiling=max(1, answer_ceiling(key)), proposal_type="ToolCallProposal",
            schema_version=1, payload=payload, payload_digest=payload_digest, citations=[],
            validator_verdict="rejected" if status == "rejected_by_validator" else "accepted",
            validator_reasons=[{"code": code}] if code else [], status=status, tool_name=definition.name,
            tool_arguments=arguments, proposed_rationale=(redact(reason)[:4000] or None) if reason else None,
            expires_at=datetime.now(UTC) + timedelta(days=self.ttl_days) if status == "proposed" else None,
            **self._subject(),
        )
        self.db.add(row)
        self.db.commit()
        self.ctx.proposal_ids.append(row.id)
        self.record("proposal_created", {"proposal_id": str(row.id), "tool": definition.name, "status": status,
                                         "level": 1, "payload_digest": payload_digest, "code": code})
        return row

    def _persist_draft(self, output: Any) -> str | None:
        """Step 8: the output's typed proposal at its level (L0 shadow, else L1 proposed;
        L2/L3 application belongs to the decision-point hook, P6.9-A). Returns a
        rejection code for an unrepresentable or invalid draft."""

        draft = getattr(output, "proposal", None)
        if draft is None:
            return None
        reasons = draft_reasons(draft) or ([] if self.project_id is not None else ["project_required"])
        if not reasons and not self.spec.may_propose:
            reasons = ["proposal_not_allowed"]  # §5.1 step 1: only a run that may propose stores one
        if reasons:
            return reasons[0]
        ceiling = answer_ceiling(draft.decision_point_key, draft.answer_kind)
        # The level evidence covers a (prompt release, model) pair: the run's own (ADR 0008 §3).
        level = min(1, ceiling, effective_level(self.db, self.workspace_id, draft.decision_point_key,
                                                draft.answer_kind, self.spec.prompt_release_id, self.model))
        payload = dict(draft.payload)
        row = AgentProposal(
            workspace_id=self.workspace_id, project_id=self.project_id, run_id=self.run_id,
            decision_point_key=draft.decision_point_key, level_at_proposal=level, answer_ceiling=ceiling,
            proposal_type=draft.proposal_type, schema_version=1, payload=payload, payload_digest=digest(payload),
            citations=[item.model_dump(mode="json") for item in output.citations][:64], validator_verdict="accepted",
            validator_reasons=[], status="shadow" if level == 0 else "proposed",
            proposed_rationale=redact(draft.rationale)[:4000] if draft.rationale else None,
            expires_at=datetime.now(UTC) + timedelta(days=self.ttl_days) if level else None, **self._subject(),
        )
        self.db.add(row)
        self.db.commit()
        self.ctx.proposal_ids.append(row.id)
        self.record("proposal_created", {"proposal_id": str(row.id), "type": draft.proposal_type,
                                         "status": row.status, "level": level, "payload_digest": row.payload_digest})
        return None


# --- the agents.run job ----------------------------------------------------------------------


def spec_for_job(db: Session, job: Any) -> AgentRunSpec | None:
    """``agents.run``: the payload is ``{"agent_run_id"}`` only and names the job's target;
    the spec comes from the run row (``AgentService.run`` re-authorizes it). A run a
    previous attempt left live is never resumed: it ends ``failed`` here (hold freed)."""

    if dict(job.payload or {}) != {"agent_run_id": str(job.target_id)}:
        raise ValueError("agents.run payload is {agent_run_id} only")
    run = db.scalar(select(AgentRun).where(AgentRun.workspace_id == job.workspace_id, AgentRun.id == job.target_id))
    if run is None:
        return None
    if run.status == "queued":
        return spec_from_run(run)
    if run.status in AGENT_RUN_LIVE_STATUSES:
        GatewayService().release_run(db, workspace_id=run.workspace_id, agent_run_id=run.id, final_status="failed",
                                     error_code="worker_abandoned")
    return None


def end_run_for_job(db: Session, job: Any) -> None:
    """Terminal sync of a failed or cancelled ``agents.run`` job: its run ends too."""

    GatewayService().release_run(db, workspace_id=job.workspace_id, agent_run_id=job.target_id,
                                 final_status="cancelled" if job.status == "cancelled" else "failed",
                                 error_code="job_cancelled" if job.status == "cancelled" else "job_failed")


# --- specialist runs (P6.4-A) ------------------------------------------------------------------

REVIEW_AGENT_KEY = "experiment_critic"
REVIEW_POINT = "experiment.review"


def specialist_runtime(settings: Any) -> tuple[str, str] | None:
    """The runtime a specialist run uses: NOOA Predict when installed, else the fake runtime in
    an explicit development environment (AGENTS_NOOA_JEV §3); ``None`` = no runtime can run."""

    from app.agents.governance.platform_default import fake_provider_allowed_by

    if nooa_runtime.available():
        return "nooa_predict", nooa_runtime.VERSION
    if fake_provider_allowed_by(settings):
        return "fake", fake_runtime.VERSION
    return None


def released_prompt_id(db: Session, agent_key: str, version: int) -> UUID | None:
    from app.db.models import PromptRelease

    return db.scalar(select(PromptRelease.id).where(
        PromptRelease.agent_key == agent_key, PromptRelease.version == version, PromptRelease.status == "released"))


def specialist_spec(db: Session, *, agent_key: str, workspace_id: UUID, project_id: UUID, user_id: UUID,
                    subject_kind: str, subject_id: UUID, decision_point_key: str, outcome_scope: str = "none",
                    settings: Any = None) -> AgentRunSpec | None:
    """The spec of one specialist run (class version, pinned prompt release, runtime);
    ``None`` when the class is unknown, its prompt is not released or no runtime can run."""

    item = AGENT_CLASSES.get(agent_key)
    runtime = specialist_runtime(settings or get_settings())
    release = released_prompt_id(db, agent_key, item.prompt_version) if item is not None else None
    if item is None or runtime is None or release is None:
        return None
    return AgentRunSpec(
        workspace_id=workspace_id, project_id=project_id, kind="specialist", agent_key=agent_key,
        agent_version=item.version, runtime=runtime[0], runtime_version=runtime[1], purpose=decision_point_key,
        decision_point_key=decision_point_key, subject_kind=subject_kind, subject_id=subject_id,
        prompt_release_id=release, user_id=user_id, outcome_scope=outcome_scope)


def enqueue_experiment_review(db: Session, *, experiment_id: UUID, user_id: UUID | None,
                              settings: Any = None) -> UUID | None:
    """``experiment.review`` (AI-after, ADR 0008 §1): queue the Critic for a COMPLETED run as an
    ``agents.run`` job. Nothing is created (and no model is called) when AI is off, a switch
    blocks the Critic, the run has no project, its requester cannot propose (ML write), the
    prompt is not released or no runtime can run. Never changes the run; refusals return None."""

    from app.db.models import Experiment
    from app.services.authorization_service import can_execute_workspace_ml

    settings = settings or get_settings()
    if not getattr(settings, "ai_enabled", False) or user_id is None:
        return None
    experiment = db.get(Experiment, experiment_id)
    if experiment is None or experiment.status != "COMPLETED" or experiment.project_id is None:
        return None
    ws = experiment.workspace_id
    if effective_switches(db, ws).blocking(ai_enabled=True, agent_key=REVIEW_AGENT_KEY, purpose=REVIEW_POINT):
        return None
    user = db.get(User, user_id)
    if user is None or not user.is_active or not can_execute_workspace_ml(db, user, ws):
        logger.info("experiment review skipped: the requester cannot propose",
                    extra={"experiment_id": str(experiment_id)})
        return None
    spec = specialist_spec(db, agent_key=REVIEW_AGENT_KEY, workspace_id=ws, project_id=experiment.project_id,
                           user_id=user_id, subject_kind="experiment", subject_id=experiment.id,
                           decision_point_key=REVIEW_POINT, outcome_scope="cv", settings=settings)
    if spec is None:
        logger.info("experiment review skipped: no released prompt or runtime",
                    extra={"experiment_id": str(experiment_id)})
        return None
    try:
        return AgentService(settings=lambda: settings).submit(db, spec)
    except AgentRunRefused as exc:  # e.g. run_already_active: one review per run
        db.rollback()
        logger.info("experiment review not queued", extra={"experiment_id": str(experiment_id), "code": exc.code})
        return None
