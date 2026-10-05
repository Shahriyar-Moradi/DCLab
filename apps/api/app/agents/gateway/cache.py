"""Response cache over the ledger (ADR 0009 §4 step 7).

The key is stored in ``llm_invocations.input_evidence_digest``. A hit is the first
completed, non-cache row of the same workspace with that key and a non-null
``safe_output``; it is served as a new ledger row with ``cache_hit = true`` and
zero cost, but only if the sanitizer redacted or truncated nothing in it.
Lookups always filter by ``workspace_id``. Parts are joined with
``\\x1f``, which cannot occur in ids, model names, enums or hex digests and is
escaped inside canonical JSON. Jev's per-question cache entries live in
``semantic_decision_answers`` (P6.7-A); here a multi-question batch is keyed by
the digest of its per-question keys.
"""

from __future__ import annotations

import hashlib
import json
from typing import Any
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.db.models import LlmInvocation

_SEP = "\x1f"


def canonical_json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False)


def _digest(*parts: object) -> str:
    return hashlib.sha256(_SEP.join(str(part) for part in parts).encode("utf-8")).hexdigest()


def completion_key(
    *, workspace_id: UUID, prompt_release_id: UUID, model: str, data_class: str, outcome_scope: str,
    payload: dict[str, Any], output_schema_digest: str,
) -> str:
    return _digest(workspace_id, prompt_release_id, model, data_class, outcome_scope,
                   canonical_json(payload), output_schema_digest)


def jev_key(
    *, workspace_id: UUID, purpose: str, release_version: str, model: str, data_class: str,
    state: dict[str, Any], question_key: str,
) -> str:
    return _digest(workspace_id, purpose, release_version, model, data_class, canonical_json(state),
                   question_key)


def jev_question_keys(
    *, workspace_id: UUID, purpose: str, release_version: str, model: str, data_class: str,
    state: dict[str, Any], column_keys: dict[str, str], user_text: list[Any],
    questions: list[tuple[str, str, list[str], str | None]],
) -> list[str]:
    """Per-question Jev keys (ADR 0008 §5) over the wire form of ``state``. A question
    about a column (``column_id`` set) sees the shared state plus its own column's entries,
    not the other questions' columns, so its key and cache entry survive a different
    batch. ``questions``: ``(question_key, primitive, choices, column_id)``. The gateway
    and the semantic port compute the same keys for the same input."""

    asked = {column for *_rest, column in questions if column}
    subject_keys = {key for key, column in column_keys.items() if column in asked}
    keys = []
    for question_key, primitive, choices, column in questions:
        scoped = {k: v for k, v in state.items() if k not in subject_keys or column_keys[k] == column}
        keyed_state = scoped if not user_text else {"state": scoped, "user_text": user_text}
        keys.append(jev_key(workspace_id=workspace_id, purpose=purpose, release_version=release_version,
                            model=model, data_class=data_class, state=keyed_state,
                            question_key=canonical_json([question_key, primitive, list(choices)])))
    return keys


def jev_batch_key(question_keys: list[str]) -> str:
    return question_keys[0] if len(question_keys) == 1 else _digest("jev-batch", *sorted(question_keys))


# Counters of the observability sanitizer's summary; a hit must have none of them
# (an output stored with redactions or truncations is never served back).
SANITIZER_COUNTERS = ("redacted_fields", "redacted_strings", "truncated_strings", "truncated_lists",
                      "truncated_objects")


def lookup(db: Session, *, workspace_id: UUID, key: str) -> LlmInvocation | None:
    sanitized = LlmInvocation.redaction_summary["safe_output"]
    return db.scalar(
        select(LlmInvocation)
        .where(
            LlmInvocation.workspace_id == workspace_id,
            LlmInvocation.input_evidence_digest == key,
            LlmInvocation.cache_hit.is_(False),
            LlmInvocation.status == "completed",
            LlmInvocation.completed_at.is_not(None),
            LlmInvocation.safe_output.is_not(None),
            *(func.coalesce(sanitized[name].as_integer(), 1) == 0 for name in SANITIZER_COUNTERS),
        )
        .order_by(LlmInvocation.completed_at.asc(), LlmInvocation.id.asc())
        .limit(1)
    )
