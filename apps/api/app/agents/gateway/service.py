"""The AI gateway pipeline: ``complete()`` for LLMs, ``decide()`` for Jev (ADR 0009 §4).

Steps, in order: (1) policy — fail closed; (2) switches — ``AI_ENABLED``, platform
``global_ai``, workspace ``all_ai``, agent / provider / purpose; (3) router and
prompt release; (4) redaction; (5) limits; (6) budget worst case; (7) cache;
(8) provider; (9) validate; (10) ledger; (11) settle. Callers never see an
exception: every failure is a ``Refusal`` and every call writes exactly one
``llm_invocations`` row (unless its attribution lies outside the workspace, in
which case nothing is written and nothing is sent).

Caller contract: COMMIT before calling the gateway, and never hold ``FOR UPDATE``
(or ``FOR NO KEY UPDATE``) locks on rows the call references (workspace, project,
experiment, workflow run, agent run). The gateway writes on its own short sessions
bound to the caller's engine (``lock_timeout`` 5 s; a second pooled connection
for the duration of each write), so a ledger row survives the caller's rollback
and budget locks are never held across a provider call; a locked reference makes
the call refuse after 5 s. A caller session with unflushed new / changed /
deleted objects is refused outright (no row, no provider call). A separate pool
for the gateway is a follow-up. The
pending row is committed before the provider is called; outcome and cost are
written by one UPDATE; settle runs last in its own transaction (a failed settle
leaves ``budget_settled = false`` for reconciliation, the row's cost is truth).
"""

from __future__ import annotations

import hashlib
import logging
import threading
import time
from collections import Counter
from collections.abc import Callable, Iterator, Mapping
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import UUID, uuid4

from pydantic import BaseModel, ValidationError
from sqlalchemy import select, text
from sqlalchemy.orm import Session

from app.agents.gateway import budget, cache, ledger, redaction
from app.agents.gateway.contract import (
    Annotate,
    BudgetReservation,
    CompletionRequest,
    CompletionResponse,
    GatewayRefusal,
    LedgerNote,
    Refusal,
    SemanticAnswers,
    SemanticDecisionRequest,
    SemanticDecisionResponse,
    Usage,
)
from app.agents.gateway.limits import LIMITS, GatewayLimits
from app.agents.gateway.providers import Provider, ProviderCall, ProviderError, default_providers
from app.agents.gateway.router import Route, route
from app.agents.governance.decision_points import REGISTRY
from app.agents.governance.policy import PolicyUnavailable, effective_policy
from app.agents.governance.switches import effective_switches
from app.agents.prompt_releases import (
    PROMPTS_ROOT,
    PromptReleaseMismatch,
    load_release_text,
    output_schema_digest,
)
from app.config import get_settings
from app.db.models import PromptRelease

logger = logging.getLogger(__name__)

_PROVIDER_REFUSALS = {"timeout": "timeout", "rate_limited": "rate_limited", "invalid_output": "invalid_output"}
_BREAKER_FAILURES = ("timeout", "server_error")
JEV_INSTRUCTIONS = "Answer each question about the supplied state with the release's primitive."
# Calls refused because the caller had not committed (no ledger row exists for them), by
# purpose; in-process like the limits. Logged too, so a call site that forgets the caller
# contract shows up in logs and diagnostics instead of silently taking its rule path.
_CALLER_SESSION_REFUSALS: Counter[str] = Counter()
_CALLER_SESSION_LOCK = threading.Lock()


def caller_session_refusals() -> dict[str, int]:
    with _CALLER_SESSION_LOCK:
        return dict(_CALLER_SESSION_REFUSALS)


@dataclass
class _Call:
    kind: str
    request: Any
    role: str
    agent_key: str | None
    agent_role: str | None
    provider_kind: str
    mode: str
    started_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    t0: float = field(default_factory=time.monotonic)
    attribution_ok: bool = False
    reservation_ok: bool = False
    sample_values_per_column: int = 0
    user_text_to_jev: bool = False
    prompt_release_id: UUID | None = None
    prompt_version: str = "unreleased"
    schema_version: str = "-"
    route: Route | None = None
    adapter: Provider | None = None
    data_class: str = "metadata"
    outcome_scope: str = "none"
    digest: str | None = None
    summary: dict[str, Any] = field(default_factory=dict)
    annotate: Annotate | None = None

    def latency_ms(self) -> int:
        return int((time.monotonic() - self.t0) * 1000)

    def note(self, output: BaseModel | None, refusal: Refusal | None, *, llm_used: bool) -> LedgerNote:
        """The caller's wording for this row (``complete(..., annotate=)``); never raises."""

        if self.annotate is None:
            return LedgerNote()
        try:
            note = self.annotate(output, refusal, llm_used)
        except Exception:
            logger.exception("ledger annotation failed; the gateway wording is kept")
            return LedgerNote()
        return note if isinstance(note, LedgerNote) else LedgerNote()

    def identity_digest(self) -> str:
        # Refused before the cache key existed: a digest of identity only, never content.
        r = self.request
        parts = (self.kind, r.workspace_id, r.purpose, self.agent_key, self.role)
        return hashlib.sha256("\x1f".join(str(p) for p in parts).encode()).hexdigest()

    def entry(self, *, llm_used: bool, provider_kind: str | None = None,
              provider: str | None = None) -> ledger.LedgerEntry:
        r = self.request
        return ledger.LedgerEntry(
            invocation_id=uuid4(),
            workspace_id=r.workspace_id, project_id=r.project_id, experiment_id=r.experiment_id,
            workflow_run_id=r.workflow_run_id, agent_run_id=r.agent_run_id, purpose=r.purpose,
            provider_kind=provider_kind or self.provider_kind, mode=self.mode,
            prompt_version=self.prompt_version, schema_version=self.schema_version,
            input_evidence_digest=self.digest or self.identity_digest(),
            data_class=self.data_class, outcome_scope=self.outcome_scope, started_at=self.started_at,
            llm_used=llm_used, prompt_release_id=self.prompt_release_id, agent_role=self.agent_role,
            decision_point_key=r.decision_point_key,
            budget_reservation_id=r.budget.id if self.reservation_ok else None,
            provider=provider, model=self.route.model if self.route and llm_used else None,
            redaction_summary=self.summary,
        )


@dataclass
class _Prepared:
    response: Any = None
    pending_id: UUID | None = None
    provider_call: ProviderCall | None = None
    worst_micros: int = 0


@dataclass
class _Outcome:
    output: BaseModel | None = None
    refusal: Refusal | None = None
    usage: Usage = field(default_factory=Usage)
    cost_micros: int = 0
    request_id: str | None = None
    resolved_model: str | None = None
    provider_called: bool = False


class _CompletionSteps:
    def __init__(self, service: "GatewayService", request: CompletionRequest) -> None:
        self.service, self.request = service, request
        self.requested_model = request.model
        self.attempts = ledger.MAX_ATTEMPTS if request.agent_role == "specialist" else 1
        self.use_cache = request.cache
        self.max_output_tokens = request.max_output_tokens

    def load_release(self, db: Session, call: _Call) -> str:
        release = db.get(PromptRelease, self.request.prompt_release_id)
        if release is None or release.status != "released" or release.agent_key != self.request.agent_key:
            raise GatewayRefusal("policy_denied", "prompt release is not released for this agent")
        try:
            prompt = load_release_text(release, self.service.prompts_root)
        except PromptReleaseMismatch as exc:
            raise GatewayRefusal("policy_denied", str(exc)) from exc
        if output_schema_digest(self.request.output_schema) != release.output_schema_digest.strip():
            raise GatewayRefusal("policy_denied", "output schema differs from the prompt release")
        call.prompt_release_id = release.id
        call.prompt_version = f"{release.agent_key}:v{release.version}"
        call.schema_version = release.output_schema_digest.strip()
        return f"{prompt.rstrip()}\n\n{redaction.UNTRUSTED_NOTICE}"

    def redact(self, db: Session, call: _Call, max_class: str, max_scope: str) -> dict[str, Any]:
        r = self.request
        result = redaction.redact(
            db, workspace_id=r.workspace_id, fields=r.envelope.fields, transcript=r.transcript,
            user_text=r.user_text, max_class=max_class, max_scope=max_scope,
            sample_values_per_column=call.sample_values_per_column,
        )
        call.data_class, call.outcome_scope, call.summary = result.data_class, result.outcome_scope, result.summary
        return result.payload

    def cache_key(self, call: _Call, payload: dict[str, Any]) -> str:
        return cache.completion_key(
            workspace_id=self.request.workspace_id, prompt_release_id=call.prompt_release_id,
            model=call.route.model, data_class=call.data_class, outcome_scope=call.outcome_scope,
            payload=payload, output_schema_digest=call.schema_version,
        )

    def provider_call(self, call: _Call, instructions: str, input_json: str) -> ProviderCall:
        r = self.request
        return ProviderCall(model=call.route.model, instructions=instructions, input_json=input_json,
                            output_schema=r.output_schema, max_output_tokens=r.max_output_tokens,
                            temperature=r.temperature, timeout_s=r.timeout_s, purpose=r.purpose,
                            agent_key=r.agent_key)

    def validate(self, output: dict[str, Any]) -> BaseModel:
        return self.request.output_schema.model_validate(output)

    def respond(self, call: _Call, **values: Any) -> CompletionResponse:
        base = dict(
            ok=values.get("refusal") is None, provider=call.adapter.name if call.adapter else None,
            model=call.route.model if call.route else None, prompt_release_id=call.prompt_release_id,
            data_class=call.data_class, outcome_scope=call.outcome_scope, latency_ms=call.latency_ms(),
        )
        return CompletionResponse(**{**base, **values})


class _SemanticSteps:
    requested_model = None
    attempts = 1  # no retry for Jev
    use_cache = True
    max_output_tokens = 4096

    def __init__(self, service: "GatewayService", request: SemanticDecisionRequest) -> None:
        self.service, self.request = service, request

    def load_release(self, db: Session, call: _Call) -> str:
        r = self.request
        release = db.scalar(select(PromptRelease).where(
            PromptRelease.agent_key == f"jev:{r.purpose}", PromptRelease.version == int(r.release_version),
            PromptRelease.status == "released",
        ))
        if release is None:
            raise GatewayRefusal("policy_denied", "Jev release is not released")
        call.prompt_release_id = release.id
        call.prompt_version = f"jev:{r.purpose}:v{release.version}"[:64]
        call.schema_version = output_schema_digest(SemanticAnswers)
        return JEV_INSTRUCTIONS

    def redact(self, db: Session, call: _Call, max_class: str, max_scope: str) -> dict[str, Any]:
        r = self.request
        allowed = redaction.semantic_class(db, workspace_id=r.workspace_id, source_datasets=r.source_datasets,
                                           source_columns=r.source_columns, max_class=max_class)
        if redaction.class_rank(r.data_class) > redaction.class_rank(allowed):
            raise GatewayRefusal("data_class_exceeded", f"state is {r.data_class}, the call allows {allowed}")
        call.data_class, call.outcome_scope = r.data_class, max_scope
        if r.user_text and not call.user_text_to_jev:
            raise GatewayRefusal("policy_denied", "user text reaches Jev only with data.user_text_to_jev")
        user = [redaction.as_untrusted(item) for item in r.user_text]
        if any(text is None for text in user):
            raise GatewayRefusal("policy_denied", "user text must be wrapped as Untrusted")
        state = redaction.semantic_state(r.state, column_keys=r.column_keys, source_columns=r.source_columns)
        call.summary = {"effective_data_class": allowed, "questions": len(r.questions), "untrusted_marked": True,
                        "input_evidence_persisted": False, "raw_rows_stored": False, "secrets_stored": False}
        return {"purpose": r.purpose, "state": state, "user_text": [{"untrusted_text": t} for t in user],
                "questions": [
            {"question_key": {"untrusted_text": q.question_key}, "primitive": q.primitive, "choices": list(q.choices)}
            for q in r.questions
        ]}

    def cache_key(self, call: _Call, payload: dict[str, Any]) -> str:
        r = self.request
        state = payload["state"] if not payload["user_text"] else {
            "state": payload["state"], "user_text": payload["user_text"]}
        return cache.jev_batch_key([
            cache.jev_key(workspace_id=r.workspace_id, purpose=r.purpose, release_version=r.release_version,
                          model=call.route.model, data_class=call.data_class, state=state,
                          question_key=cache.canonical_json([q.question_key, q.primitive, list(q.choices)]))
            for q in r.questions
        ])

    def provider_call(self, call: _Call, instructions: str, input_json: str) -> ProviderCall:
        r = self.request
        return ProviderCall(model=call.route.model, instructions=instructions, input_json=input_json,
                            output_schema=SemanticAnswers, max_output_tokens=self.max_output_tokens,
                            temperature=None, timeout_s=r.timeout_ms / 1000, purpose=r.purpose, agent_key=None)

    def validate(self, output: dict[str, Any]) -> BaseModel:
        parsed = SemanticAnswers.model_validate(output)
        if [a.question_key for a in parsed.answers] != [q.question_key for q in self.request.questions]:
            raise ValueError("answers do not match the questions")
        for question, answer in zip(self.request.questions, parsed.answers):
            value = answer.answer.get("value")
            if question.primitive == "choice" and value not in question.choices:
                raise ValueError("a choice answer outside the listed choices")
            if question.primitive == "score" and (isinstance(value, bool) or not isinstance(value, (int, float))):
                raise ValueError("a score answer must be a number")
        return parsed

    def respond(self, call: _Call, **values: Any) -> SemanticDecisionResponse:
        output = values.pop("output", None)
        for unused in ("usage", "resolved_model"):
            values.pop(unused, None)
        return SemanticDecisionResponse(ok=values.get("refusal") is None, latency_ms=call.latency_ms(),
                                        answers=output.answers if output is not None else (), **values)


class GatewayService:
    def __init__(
        self,
        *,
        providers: Mapping[str, Provider] | None = None,
        limits: GatewayLimits | None = None,
        settings: Callable[[], Any] = get_settings,
        prompts_root: Path = PROMPTS_ROOT,
    ) -> None:
        self._providers = providers
        self._limits = limits or LIMITS
        self._settings = settings
        self.prompts_root = prompts_root

    # --- sessions and budget entry points ----------------------------------------

    @contextmanager
    def _session(self, db: Session) -> Iterator[Session]:
        session = Session(bind=db.get_bind(), expire_on_commit=False)
        try:
            session.execute(text("SET LOCAL lock_timeout = '5s'"))
            yield session
            session.commit()
        except BaseException:
            session.rollback()
            raise
        finally:
            session.close()

    def _provider(self, name: str) -> Provider | None:
        if self._providers is None:
            self._providers = default_providers()
        return self._providers.get(name)

    def reserve(self, db: Session, *, workspace_id: UUID, estimate_micros: int, project_id: UUID | None = None,
                run_kind: str | None = None, agent_run_id: UUID | None = None) -> BudgetReservation | Refusal:
        try:
            with self._session(db) as session:
                policy = effective_policy(session, workspace_id).policy
                return budget.reserve(session, policy=policy, workspace_id=workspace_id,
                                      estimate_micros=estimate_micros, project_id=project_id,
                                      run_kind=run_kind, agent_run_id=agent_run_id)
        except PolicyUnavailable:
            return Refusal(code="policy_unavailable", message="effective AI policy unavailable")
        except GatewayRefusal as exc:
            return exc.refusal
        except Exception:
            logger.exception("budget reservation failed", extra={"workspace_id": str(workspace_id)})
            return Refusal(code="provider_error", message="budget reservation failed")

    def release(self, db: Session, reservation: BudgetReservation, *, final_status: str = "completed") -> int:
        """Free the remaining hold (run end; with an agent run this also moves the run to
        ``final_status``, once). Never raises: a failure is logged and the hold stays
        reserved for reconciliation."""

        if not budget.seal_valid(reservation):
            logger.warning("refused to release an unsealed reservation")
            return 0
        try:
            with self._session(db) as session:
                freed = budget.release(session, reservation, final_status=final_status)
        except Exception:
            logger.exception("budget release failed", extra={"reservation_id": str(reservation.id)})
            return 0
        budget.mark_released(reservation)
        return freed

    # --- public calls ---------------------------------------------------------------

    def complete(self, db: Session, request: CompletionRequest, *,
                 annotate: Annotate | None = None) -> CompletionResponse:
        """``annotate`` (legacy writers only): see ``contract.LedgerNote``."""

        try:
            call = _Call(kind="completion", request=request, role=request.agent_role,
                         agent_key=request.agent_key, agent_role=request.agent_role,
                         provider_kind="llm_provider", mode="completion", annotate=annotate)
            return self._execute(db, call, _CompletionSteps(self, request), request.max_data_class,
                                 request.outcome_scope)
        except Exception:  # last line of defence: callers never see an exception
            logger.exception("gateway completion failed")
            return CompletionResponse(ok=False, refusal=Refusal(code="provider_error", message="gateway failure"))

    def decide(self, db: Session, request: SemanticDecisionRequest) -> SemanticDecisionResponse:
        try:
            call = _Call(kind="semantic", request=request, role="jev", agent_key=None, agent_role=None,
                         provider_kind="semantic_decision", mode="semantic_decision")
            return self._execute(db, call, _SemanticSteps(self, request), request.data_class, "none")
        except Exception:
            logger.exception("gateway decision failed")
            return SemanticDecisionResponse(ok=False, refusal=Refusal(code="provider_error",
                                                                      message="gateway failure"))

    # --- pipeline -------------------------------------------------------------------

    def _execute(self, db: Session, call: _Call, steps: Any, max_class: str, max_scope: str) -> Any:
        if db.new or db.dirty or db.deleted:  # the gateway cannot see uncommitted caller work
            purpose = str(call.request.purpose)
            with _CALLER_SESSION_LOCK:
                _CALLER_SESSION_REFUSALS[purpose] += 1
                count = _CALLER_SESSION_REFUSALS[purpose]
            logger.warning("gateway refused: caller session has unflushed changes",
                           extra={"purpose": purpose, "caller_session_refusals": count})
            return steps.respond(call, refusal=Refusal(
                code="provider_error", scope="caller_session",
                message="commit the caller's session before calling the gateway"))
        try:
            with self._session(db) as session:
                prepared = self._prepare(session, call, steps, max_class, max_scope)
        except GatewayRefusal as exc:
            return self._refuse(db, call, steps, exc.refusal)
        except Exception:
            logger.exception("gateway call failed before the provider", extra={"purpose": call.request.purpose})
            return self._refuse(db, call, steps, Refusal(code="provider_error", message="internal gateway error"))
        if prepared.response is not None:
            return prepared.response
        outcome = self._invoke(db, call, steps, prepared)
        return self._finish(db, call, steps, prepared, outcome)

    def _prepare(self, db: Session, call: _Call, steps: Any, max_class: str, max_scope: str) -> _Prepared:
        r = call.request
        reservation = r.budget
        if not ledger.attribution_in_workspace(db, call.entry(llm_used=False)):
            raise GatewayRefusal("policy_denied", "attribution outside the workspace", scope="workspace")
        call.attribution_ok = True  # from here on a refusal row can be written
        if not budget.seal_valid(reservation) or (reservation.workspace_id, reservation.project_id,
                                                  reservation.agent_run_id) != (r.workspace_id, r.project_id,
                                                                                r.agent_run_id):
            raise GatewayRefusal("policy_denied", "the budget reservation does not belong to this call", scope="budget")
        call.reservation_ok = True
        if budget.expired(reservation):
            raise GatewayRefusal("budget_exhausted", "the budget reservation expired", scope="reservation")
        # 1. policy (fail closed)
        try:
            policy = effective_policy(db, r.workspace_id).policy
        except PolicyUnavailable as exc:
            raise GatewayRefusal("policy_unavailable", "effective AI policy unavailable") from exc
        # 2. switches (the provider switch is checked again once the route is known)
        switches = effective_switches(db, r.workspace_id)
        ai_enabled = bool(self._settings().ai_enabled)
        blocking = switches.blocking(ai_enabled=ai_enabled, agent_key=call.agent_key, purpose=r.purpose)
        if blocking:
            raise GatewayRefusal("kill_switch", "AI is switched off", scope=blocking)
        # 3. router, provider adapter, prompt release
        call.route = route(policy, role=call.role, agent_key=call.agent_key, requested=steps.requested_model)
        blocking = switches.blocking(ai_enabled=ai_enabled, agent_key=call.agent_key,
                                     provider=call.route.provider, purpose=r.purpose)
        if blocking:
            raise GatewayRefusal("kill_switch", "AI is switched off", scope=blocking)
        call.adapter = self._provider(call.route.provider)
        if call.adapter is None:
            raise GatewayRefusal("provider_error", "provider not configured", scope=f"provider:{call.route.provider}")
        instructions = steps.load_release(db, call)
        call.user_text_to_jev = policy.data.user_text_to_jev
        point = REGISTRY.get(r.decision_point_key) if r.decision_point_key else None
        if r.decision_point_key and point is None:
            raise GatewayRefusal("policy_denied", "unknown decision point", scope=r.decision_point_key)
        point_class = [redaction.PARTITION_CLASS[point.evidence_partition]] if point else []
        if policy.data.sample_values_per_column == 0:
            point_class.append("aggregates")  # no sample values at all (founder Q3)
        call.sample_values_per_column = policy.data.sample_values_per_column
        max_class = redaction.min_class(policy.data.max_class, max_class, *point_class)
        max_scope = redaction.min_scope(max_scope, *([point.outcome_scope] if point else []))
        # 4. redaction
        payload = steps.redact(db, call, max_class, max_scope)
        input_json = cache.canonical_json(payload)
        # 5. limits
        self._limits.admit(provider=call.route.provider, workspace_id=r.workspace_id, purpose=r.purpose)
        # 6. budget: the worst case must fit what the hold still has, counting in-flight
        # calls; the advisory lock lasts until the pending row below is committed.
        worst = budget.worst_case_micros(call.route.model, input_tokens=budget.estimate_tokens(instructions + input_json),
                                         max_output_tokens=steps.max_output_tokens)
        budget.lock_hold(db, reservation)
        remaining = budget.available_hold(db, reservation)
        if worst > remaining:
            raise GatewayRefusal("budget_exhausted", "the call's worst case exceeds the remaining hold",
                                 scope="reservation")
        # 7. cache
        call.digest = steps.cache_key(call, payload)
        if steps.use_cache:
            hit = cache.lookup(db, workspace_id=r.workspace_id, key=call.digest)
            parsed = self._parse_cached(steps, hit)
            if parsed is not None:
                note = call.note(parsed, None, llm_used=True)
                row = ledger.insert_completed(
                    db, call.entry(llm_used=True, provider=hit.provider),
                    status="rejected" if note.rejected else "completed",
                    validator_verdict=note.validator_verdict or "accepted", reason=note.reason or "cache hit",
                    output=hit.safe_output if note.safe_output is None else note.safe_output,
                    final_decision=note.final_decision, cache_hit=True, budget_settled=True,
                    provider_resolved_model=hit.provider_resolved_model, latency_ms=call.latency_ms(),
                )
                served = {"provider": hit.provider} if call.kind == "completion" else {}
                return _Prepared(response=steps.respond(
                    call, output=parsed, cache_hit=True, invocation_id=row.id,
                    resolved_model=hit.provider_resolved_model, **served,
                ))
        # 8 (prelude). The pending row is durable before anything leaves the process.
        pending = ledger.insert_pending(db, call.entry(llm_used=True, provider=call.adapter.name),
                                        worst_case_micros=worst)
        return _Prepared(pending_id=pending.id, provider_call=steps.provider_call(call, instructions, input_json),
                         worst_micros=worst)

    @staticmethod
    def _parse_cached(steps: Any, hit: Any) -> BaseModel | None:
        if hit is None:
            return None
        try:
            return steps.validate(hit.safe_output)
        except (ValidationError, ValueError, TypeError):
            return None

    def _claim_retry(self, db: Session, call: _Call, prepared: _Prepared, spent_micros: int) -> bool:
        """A retry must fit the hold too: re-check under the hold lock and widen the
        pending row's stored worst case before the second attempt."""

        needed = spent_micros + prepared.worst_micros
        try:
            with self._session(db) as session:
                budget.lock_hold(session, call.request.budget)
                if needed > budget.available_hold(session, call.request.budget, exclude=prepared.pending_id):
                    return False
                ledger.raise_pending_estimate(session, invocation_id=prepared.pending_id,
                                              workspace_id=call.request.workspace_id, worst_case_micros=needed)
            return True
        except Exception:
            logger.exception("could not claim a retry", extra={"invocation_id": str(prepared.pending_id)})
            return False

    def _invoke(self, db: Session, call: _Call, steps: Any, prepared: _Prepared) -> _Outcome:
        """Steps 8-9: never raises."""

        r, outcome = call.request, _Outcome()
        limits = {"provider": call.route.provider, "workspace_id": r.workspace_id, "purpose": r.purpose}
        tokens_in = tokens_out = 0
        for attempt in range(steps.attempts):
            last = attempt + 1 == steps.attempts
            if attempt and not self._claim_retry(db, call, prepared, outcome.cost_micros):
                break
            try:
                self._limits.begin_call(**limits)
            except GatewayRefusal as exc:
                outcome.refusal = exc.refusal
                return outcome
            outcome.provider_called = True
            try:
                result = call.adapter.complete(prepared.provider_call)
            except ProviderError as exc:
                self._limits.record(**limits, outcome="failure" if exc.kind in _BREAKER_FAILURES else "neutral")
                if exc.kind == "invalid_output" and not last:
                    continue
                outcome.refusal = Refusal(code=_PROVIDER_REFUSALS.get(exc.kind, "provider_error"),
                                          scope=f"provider:{call.route.provider}", message=exc.kind)
                return outcome
            except Exception:
                logger.exception("provider adapter raised", extra={"provider": call.route.provider})
                self._limits.record(**limits, outcome="neutral")
                outcome.refusal = Refusal(code="provider_error", message="provider adapter failed")
                return outcome
            self._limits.record(**limits, outcome="success")
            tokens_in, tokens_out = tokens_in + result.input_tokens, tokens_out + result.output_tokens
            outcome.usage = Usage(input_tokens=tokens_in, output_tokens=tokens_out)
            outcome.cost_micros = budget.cost_micros(call.route.model, tokens_in, tokens_out)
            outcome.request_id = result.request_id or outcome.request_id
            outcome.resolved_model = result.resolved_model or outcome.resolved_model
            try:
                outcome.output = steps.validate(result.output)
                return outcome
            except (ValidationError, ValueError, TypeError):
                continue
        outcome.refusal = Refusal(code="invalid_output", message="the output did not validate")
        return outcome

    def _finish(self, db: Session, call: _Call, steps: Any, prepared: _Prepared, outcome: _Outcome) -> Any:
        refusal = outcome.refusal
        note = call.note(outcome.output if refusal is None else None, refusal, llm_used=True)
        output = outcome.output.model_dump(mode="json") if refusal is None else None
        try:
            with self._session(db) as session:
                ledger.finalize(
                    session, invocation_id=prepared.pending_id, workspace_id=call.request.workspace_id,
                    status="failed" if refusal is not None else ("rejected" if note.rejected else "completed"),
                    validator_verdict=note.validator_verdict or ("accepted" if refusal is None else (
                        "rejected" if refusal.code == "invalid_output" else "not_run")),
                    reason=note.reason or ("completed" if refusal is None else (refusal.message or refusal.code)),
                    refusal_code=refusal.code if refusal else None,
                    output=output if note.safe_output is None else note.safe_output,
                    final_decision=note.final_decision,
                    cost_micros=outcome.cost_micros, usage=outcome.usage, latency_ms=call.latency_ms(),
                    provider_request_id=outcome.request_id, provider_resolved_model=outcome.resolved_model,
                )
        except Exception:
            logger.exception("could not finalize the ledger row", extra={"invocation_id": str(prepared.pending_id)})
            return steps.respond(call, refusal=Refusal(code="provider_error", message="ledger finalize failed"),
                                 invocation_id=prepared.pending_id, cost_micros=outcome.cost_micros)
        try:
            with self._session(db) as session:
                budget.settle(session, call.request.budget, prepared.pending_id,
                              provider_called=outcome.provider_called)
        except Exception:
            logger.exception("budget settle failed; the row stays unsettled", extra={
                "invocation_id": str(prepared.pending_id)})
        return steps.respond(
            call, output=outcome.output if refusal is None else None, refusal=refusal, usage=outcome.usage,
            cost_micros=outcome.cost_micros, invocation_id=prepared.pending_id,
            resolved_model=outcome.resolved_model,
        )

    def _refuse(self, db: Session, call: _Call, steps: Any, refusal: Refusal) -> Any:
        if not call.attribution_ok:
            logger.warning("gateway refusal without ledger row (attribution outside the workspace)")
            return steps.respond(call, refusal=refusal)
        note = call.note(None, refusal, llm_used=False)
        try:
            with self._session(db) as session:
                row = ledger.insert_completed(
                    session, call.entry(llm_used=False, provider_kind="deterministic_fallback"),
                    status="refused", validator_verdict=note.validator_verdict or "not_run",
                    reason=note.reason or refusal.message or refusal.code, refusal_code=refusal.code,
                    final_decision=note.final_decision,  # a refused row never stores an output
                    budget_settled=call.reservation_ok, latency_ms=call.latency_ms(),
                )
            return steps.respond(call, refusal=refusal, invocation_id=row.id)
        except Exception:
            logger.exception("could not write the refusal row", extra={"refusal": refusal.code})
            return steps.respond(call, refusal=refusal)
