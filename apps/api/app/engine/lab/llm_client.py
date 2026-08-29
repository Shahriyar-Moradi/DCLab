"""Call a model for a Lab missing-value decision, or fail closed.

Cache: in-process dict keyed by sha256(canonical evidence JSON + prompt_version).
It lives only in this process — not in the database, not shared across workers,
and cleared on restart. Re-running the same evidence with the same prompt
version does not hit the provider again.

The public function is `request_decision`. It never retries, uses a hard
HTTP timeout, and converts every provider/parse failure into
`DecisionAgentUnavailable` so callers never see an unhandled exception.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict
from typing import Any, Literal

import httpx
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from app.config import get_settings
from app.engine.lab.evidence import ColumnEvidence
from app.engine.lab.prompts.missing_value_v1 import PROMPT_VERSION as MISSING_VALUE_V1
from app.engine.lab.prompts.missing_value_v1 import SYSTEM_PROMPT as MISSING_VALUE_V1_PROMPT

_PROVIDER_URL = "https://api.openai.com/v1/chat/completions"
_REQUEST_TIMEOUT_SECONDS = 20.0

_PROMPTS: dict[str, str] = {
    MISSING_VALUE_V1: MISSING_VALUE_V1_PROMPT,
}

Action = Literal[
    "drop_rows",
    "impute_mean",
    "impute_median",
    "impute_most_frequent",
    "domain_fill",
]
EvidenceField = Literal[
    "column",
    "dtype",
    "missing_count",
    "missing_fraction",
    "correlation_with_target",
    "missingness_cooccurrence",
    "sample_rows",
]

_ACTIONS: tuple[str, ...] = (
    "drop_rows",
    "impute_mean",
    "impute_median",
    "impute_most_frequent",
    "domain_fill",
)
_EVIDENCE_FIELDS: tuple[str, ...] = (
    "column",
    "dtype",
    "missing_count",
    "missing_fraction",
    "correlation_with_target",
    "missingness_cooccurrence",
    "sample_rows",
)

_DECISION_JSON_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "action": {"type": "string", "enum": list(_ACTIONS)},
        "evidence_field": {"type": "string", "enum": list(_EVIDENCE_FIELDS)},
        "fill_value": {
            "anyOf": [
                {"type": "string"},
                {"type": "number"},
                {"type": "boolean"},
                {"type": "null"},
            ]
        },
        "rationale": {"type": "string"},
        "confidence": {"type": "number", "minimum": 0, "maximum": 1},
    },
    "required": ["action", "evidence_field", "fill_value", "rationale", "confidence"],
}


class DecisionAgentUnavailable(Exception):
    """The decision agent is off, unconfigured, or the provider failed closed."""


class MissingValueDecision(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    action: Action
    evidence_field: EvidenceField
    fill_value: str | int | float | bool | None
    rationale: str
    confidence: float = Field(ge=0.0, le=1.0)


# In-process cache. See module docstring.
_CACHE: dict[str, MissingValueDecision] = {}


def request_decision(evidence: ColumnEvidence, prompt_version: str) -> MissingValueDecision:
    """Return a schema-validated missing-value decision, or raise unavailable.

    Temperature is 0. Output is constrained by the provider's JSON-schema
    structured-output mode, then validated again with pydantic — never scraped
    from free text.
    """
    try:
        settings = get_settings()
        if not settings.decision_agent_enabled:
            raise DecisionAgentUnavailable("decision agent is disabled")
        api_key = (settings.decision_agent_api_key or "").strip()
        if not api_key:
            raise DecisionAgentUnavailable("decision agent API key is not configured")
        system_prompt = _PROMPTS.get(prompt_version)
        if not system_prompt:
            raise DecisionAgentUnavailable(f"unknown prompt version {prompt_version!r}")
        cache_key = _cache_key(evidence, prompt_version)
        cached = _CACHE.get(cache_key)
        if cached is not None:
            return cached
        decision = _complete_structured(
            system_prompt=system_prompt,
            evidence=evidence,
            api_key=api_key,
            model=settings.decision_agent_model,
        )
        _CACHE[cache_key] = decision
        return decision
    except DecisionAgentUnavailable:
        raise
    except Exception as exc:
        raise DecisionAgentUnavailable("decision agent failed") from exc


def _cache_key(evidence: ColumnEvidence, prompt_version: str) -> str:
    blob = _evidence_json(evidence) + "\0" + prompt_version
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


def _evidence_json(evidence: ColumnEvidence) -> str:
    return json.dumps(asdict(evidence), sort_keys=True, default=str)


def _complete_structured(
    *,
    system_prompt: str,
    evidence: ColumnEvidence,
    api_key: str,
    model: str,
) -> MissingValueDecision:
    payload = {
        "model": model,
        "temperature": 0,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": _evidence_json(evidence)},
        ],
        "response_format": {
            "type": "json_schema",
            "json_schema": {
                "name": "missing_value_decision",
                "strict": True,
                "schema": _DECISION_JSON_SCHEMA,
            },
        },
    }
    response = httpx.post(
        _PROVIDER_URL,
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        },
        json=payload,
        timeout=_REQUEST_TIMEOUT_SECONDS,
    )
    try:
        response.raise_for_status()
    except httpx.HTTPStatusError as exc:
        raise DecisionAgentUnavailable("decision agent provider rejected the request") from exc

    try:
        body = response.json()
        content = body["choices"][0]["message"]["content"]
    except (KeyError, IndexError, TypeError, ValueError) as exc:
        raise DecisionAgentUnavailable("decision agent returned an empty or malformed payload") from exc

    if not isinstance(content, str) or not content.strip():
        raise DecisionAgentUnavailable("decision agent returned an empty or malformed payload")

    try:
        parsed = json.loads(content)
        return MissingValueDecision.model_validate(parsed)
    except (json.JSONDecodeError, ValidationError) as exc:
        raise DecisionAgentUnavailable("decision agent returned JSON that failed the schema") from exc
