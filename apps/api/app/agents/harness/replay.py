"""Replay a recorded agent run (ADR 0009 §5.5).

The runtime is rebuilt from the run row and driven by a fake provider fed from the
record: each model call returns the ledger's ``safe_output`` of the recorded call (or
its recorded refusal), in order. Live tools never run: a read tool returns a stub
carrying the recorded outcome and result digest, a write tool is stubbed (no
proposal). Equality = the same ordered ``(tool, argument digest)`` sequence, the same
final output digest and the same proposal payload digests; timestamps, latency, cost
and provider ids are ignored. Replay writes only a ``replay_checked`` event, and a
mismatch opens an ``ai_incidents`` row (``replay_mismatch``; one open incident per run). The runtime gets an
empty envelope (only its digest was recorded): a runtime decides from model outputs,
which are replayed. Viewing a replay needs workspace read plus platform read or ML
write (the development roles).
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass
from types import SimpleNamespace
from typing import Any
from uuid import UUID

from pydantic import BaseModel, ValidationError
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.agents.contracts import ContextEnvelope
from app.agents.gateway.contract import CompletionResponse, Refusal
from app.agents.harness.recorder import Recorder, digest, load_events, output_digest
from app.agents.harness.service import AgentService, spec_from_run
from app.agents.harness.validation import parse_tool_arguments, proposal_payload
from app.agents.runtime.base import ToolOutcome
from app.agents.tools.catalog import ToolError, get as get_tool
from app.db.models import AgentRun, AiIncident, LlmInvocation, User
from app.domain.agent_records import AGENT_RUN_TERMINAL_STATUSES, KEY_PATTERN

_KEY = re.compile(KEY_PATTERN)


class ReplayRefused(Exception):
    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


@dataclass(frozen=True)
class ReplayResult:
    run_id: UUID
    equal: bool
    mismatches: tuple[str, ...]
    tool_sequence: tuple[tuple[str, str], ...]
    output_digest: str | None
    incident_id: UUID | None = None


class _Record:
    def __init__(self, db: Session, run: AgentRun) -> None:
        events = load_events(db, workspace_id=run.workspace_id, run_id=run.id)
        self.tools = [(e.payload["tool"], e.payload["argument_digest"]) for e in events
                      if e.type == "tool_call_requested"]
        self.outcomes: dict[int, dict[str, Any]] = {}
        for e in events:
            if e.type in ("tool_call_finished", "tool_call_denied"):
                self.outcomes.setdefault(int(e.payload["call"]), {**e.payload, "event": e.type})
        calls = [e.payload if e.type == "llm_call_finished" else {"ok": False, "refusal": e.payload.get("refusal")}
                 for e in events if e.type in ("llm_call_finished", "step_rejected")]
        ids = [UUID(c["invocation_id"]) for c in calls if c.get("invocation_id")]
        outputs = dict(db.execute(select(LlmInvocation.id, LlmInvocation.safe_output).where(
            LlmInvocation.workspace_id == run.workspace_id, LlmInvocation.id.in_(ids))).all()) if ids else {}
        self.llm = [(c, outputs.get(UUID(c["invocation_id"])) if c.get("invocation_id") else None) for c in calls]
        self.proposals = [e.payload["payload_digest"] for e in events
                          if e.type == "proposal_created" and e.payload.get("status") == "proposed"]
        final = next((e.payload for e in reversed(events) if e.type in ("run_finished", "run_failed")), {})
        self.output_digest = final.get("output_digest")


class _ReplaySession:
    def __init__(self, record: _Record, tools: tuple[str, ...]) -> None:
        self.record, self.tools = record, tools
        self.llm_index, self.calls = 0, 0
        self.sequence: list[tuple[str, str]] = []
        self.proposals: list[str] = []
        self.mismatches: list[str] = []

    def complete(self, *, output_schema: type[BaseModel], max_output_tokens: int, transcript: tuple = (),
                 user_text: tuple = ()) -> CompletionResponse:
        if self.llm_index >= len(self.record.llm):
            self.mismatches.append("extra_llm_call")
            return CompletionResponse(ok=False, refusal=Refusal(code="provider_error", message="not recorded"))
        call, safe_output = self.record.llm[self.llm_index]
        self.llm_index += 1
        if not call.get("ok"):
            return CompletionResponse(ok=False, refusal=Refusal(code=call.get("refusal") or "provider_error"))
        try:
            output = output_schema.model_validate(safe_output)
        except ValidationError:
            self.mismatches.append("recorded_output_invalid")
            return CompletionResponse(ok=False, refusal=Refusal(code="invalid_output"))
        return CompletionResponse(ok=True, output=output, invocation_id=UUID(call["invocation_id"]))

    def call_tool(self, name: str, arguments: Mapping[str, Any], *, reason: str = "") -> ToolOutcome:
        self.calls += 1
        name = str(name)[:64] if _KEY.fullmatch(str(name)[:64]) else "invalid_tool_name"
        self.sequence.append((name, digest(dict(arguments) if isinstance(arguments, Mapping) else repr(arguments))))
        recorded = self.record.outcomes.get(self.calls, {})
        definition = get_tool(name)
        if definition is not None and definition.effect == "proposal" and recorded.get("proposal_id") \
                and recorded.get("event") == "tool_call_finished":
            try:
                _payload, payload_digest = proposal_payload(definition, parse_tool_arguments(definition, arguments))
                self.proposals.append(payload_digest)
            except ToolError:
                self.mismatches.append(f"tool_call_{self.calls}_arguments")
            return ToolOutcome(tool=name, ok=True, status="stub", result_digest=recorded.get("result_digest"))
        return ToolOutcome(tool=name, ok=bool(recorded.get("ok")), status="stub", code=recorded.get("code"),
                           result_digest=recorded.get("result_digest"))


def _authorize(db: Session, actor: User, workspace_id: UUID) -> None:
    from app.services.authorization_service import can_execute_workspace_ml, can_read_platform, can_read_workspace

    if not (can_read_workspace(db, actor, workspace_id)
            and (can_read_platform(db, actor) or can_execute_workspace_ml(db, actor, workspace_id))):
        raise ReplayRefused("forbidden")


def replay(db: Session, *, workspace_id: UUID, run_id: UUID, actor: User,
           service: AgentService | None = None) -> ReplayResult:
    _authorize(db, actor, workspace_id)
    run = db.scalar(select(AgentRun).where(AgentRun.workspace_id == workspace_id, AgentRun.id == run_id))
    if run is None:
        raise ReplayRefused("not_found")
    if run.status not in AGENT_RUN_TERMINAL_STATUSES:
        raise ReplayRefused("run_not_finished")
    service = service or AgentService()
    spec = spec_from_run(run)
    record = _Record(db, run)
    factory = service.runtime_factory(run.runtime)
    if factory is None:
        raise ReplayRefused("runtime_unavailable")
    tools = tuple(record_tool for record_tool, _ in record.tools)
    session = _ReplaySession(record, tools)
    replayed_digest = None
    try:
        output = factory(spec).run(SimpleNamespace(envelope=ContextEnvelope(), limits=spec.limits, tools=tools,
                                                   complete=session.complete, call_tool=session.call_tool))
        replayed_digest = output_digest(output)
    except Exception:  # noqa: BLE001 - a crashing replay is a mismatch, never an error to the viewer
        session.mismatches.append("runtime_error")
    mismatches = list(session.mismatches)
    if session.sequence != record.tools:
        mismatches.append("tool_sequence")
    if session.llm_index != len(record.llm):
        mismatches.append("llm_call_count")
    if replayed_digest != record.output_digest and run.status in ("completed", "rejected_by_validator"):
        mismatches.append("output_digest")
    if session.proposals != record.proposals:
        mismatches.append("proposal_payloads")
    incident_id = None
    if mismatches:  # one open replay_mismatch incident per run (later replays reuse it)
        incident_id = db.scalar(select(AiIncident.id).where(
            AiIncident.workspace_id == workspace_id, AiIncident.kind == "replay_mismatch",
            AiIncident.status == "open", AiIncident.evidence["agent_run_id"].astext == str(run.id)).limit(1))
        if incident_id is None:
            incident = AiIncident(workspace_id=workspace_id, kind="replay_mismatch", subject_kind="agent",
                                  subject_key=run.agent_key, action="none", status="open",
                                  evidence={"agent_run_id": str(run.id), "mismatches": mismatches[:20]})
            db.add(incident)
            db.commit()
            incident_id = incident.id
    db.commit()
    Recorder(db.get_bind(), workspace_id=workspace_id, run_id=run.id).record("replay_checked", {
        "equal": not mismatches, "mismatches": mismatches[:20], "tool_calls": len(session.sequence),
        "output_digest": replayed_digest, "incident_id": str(incident_id) if incident_id else None,
        "replayed_by": str(actor.id),
    })
    return ReplayResult(run_id=run.id, equal=not mismatches, mismatches=tuple(mismatches),
                        tool_sequence=tuple(session.sequence), output_digest=replayed_digest, incident_id=incident_id)
