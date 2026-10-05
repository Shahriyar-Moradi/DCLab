"""The run recorder: ``agent_events`` rows (ADR 0009 §2.2, §5.4).

Every event is written in its own short, committed transaction on a session bound to
the caller's engine, with ``seq`` assigned under a per-run transaction-scoped
advisory lock (namespace ``LOCK_NS_AGENT_EVENTS``); the run row is never locked, so
the recorder never holds a lock across a gateway call (gateway caller contract).
Payloads are bounded (≤ 16 KB, else a digest-only stub), carry no forbidden top-level
key and are digested canonically (``payload_digest``, used by replay). Tool results
are stored as digest + bounded preview, never in full; model outputs as their digest
(the full redacted output is the ledger's ``safe_output``).
"""

from __future__ import annotations

import hashlib
import json
from typing import Any
from uuid import UUID

from sqlalchemy import func, select, text
from sqlalchemy.orm import Session

from app.agents.tools.shaping import redact
from app.db.models import AgentEvent
from app.domain.agent_records import EVENT_PAYLOAD_MAX_BYTES
from app.domain.execution_requests import FORBIDDEN_REQUEST_PAYLOAD_KEYS

LOCK_NS_AGENT_EVENTS = 72064  # next to governance 72061/72062 and the budget hold 72063
PREVIEW_CHARS = 1000


def canonical(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), default=str, ensure_ascii=False)


def digest(value: Any) -> str:
    return hashlib.sha256(canonical(value).encode("utf-8")).hexdigest()


def preview(textual: str, limit: int = PREVIEW_CHARS) -> str:
    return redact(textual)[:limit]


def output_digest(output: Any) -> str | None:
    """Digest of a runtime's final output (the replay equality key)."""

    if output is None:
        return None
    body = {
        "output": output.output.model_dump(mode="json") if output.output is not None else None,
        "citations": [item.model_dump(mode="json") for item in output.citations],
        "message": output.message,
        "proposal": None if output.proposal is None else {
            "type": output.proposal.proposal_type, "point": output.proposal.decision_point_key,
            "payload": dict(output.proposal.payload)},
    }
    return digest(body)


def _bounded(payload: dict[str, Any]) -> dict[str, Any]:
    safe = {(f"x_{key}" if str(key) in FORBIDDEN_REQUEST_PAYLOAD_KEYS else str(key)): value
            for key, value in payload.items()}
    if len(canonical(safe).encode("utf-8")) <= EVENT_PAYLOAD_MAX_BYTES - 512:
        return json.loads(canonical(safe))
    return {"oversized": True, "full_digest": digest(safe), "keys": sorted(safe)[:50]}


class Recorder:
    def __init__(self, bind: Any, *, workspace_id: UUID, run_id: UUID) -> None:
        self.bind, self.workspace_id, self.run_id = bind, workspace_id, run_id

    def record(self, event_type: str, payload: dict[str, Any], *, llm_invocation_id: UUID | None = None,
               session: Session | None = None) -> int:
        """Own committed transaction, or (``session``) the caller's: then the event commits or
        rolls back with the caller's other writes (an assistant template and its key)."""

        body = _bounded(payload)
        owned = session is None
        session = Session(bind=self.bind) if owned else session
        try:
            if owned:
                session.execute(text("SET LOCAL lock_timeout = '5s'"))
            session.execute(text("SELECT pg_advisory_xact_lock(:ns, hashtext(:k))"),
                            {"ns": LOCK_NS_AGENT_EVENTS, "k": str(self.run_id)})
            seq = 1 + int(session.scalar(select(func.coalesce(func.max(AgentEvent.seq), 0)).where(
                AgentEvent.workspace_id == self.workspace_id, AgentEvent.run_id == self.run_id)) or 0)
            session.add(AgentEvent(workspace_id=self.workspace_id, run_id=self.run_id, seq=seq, type=event_type,
                                   payload=body, payload_digest=digest(body), llm_invocation_id=llm_invocation_id))
            if owned:
                session.commit()
            else:
                session.flush()
            return seq
        except BaseException:
            if owned:
                session.rollback()
            raise
        finally:
            if owned:
                session.close()


def load_events(db: Session, *, workspace_id: UUID, run_id: UUID) -> list[AgentEvent]:
    return list(db.scalars(select(AgentEvent).where(
        AgentEvent.workspace_id == workspace_id, AgentEvent.run_id == run_id).order_by(AgentEvent.seq)))
