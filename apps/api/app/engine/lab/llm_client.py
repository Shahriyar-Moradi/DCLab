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
from typing import Any, Literal, TypeVar

import httpx
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from app.config import get_settings
from app.engine.lab.evidence import (
    ColumnEvidence,
    ColumnTypeEvidence,
    LeakageReviewEvidence,
    TargetSelectionEvidence,
)
from app.engine.lab.prompts.column_type_v1 import PROMPT_VERSION as COLUMN_TYPE_V1
from app.engine.lab.prompts.column_type_v1 import SYSTEM_PROMPT as COLUMN_TYPE_V1_PROMPT
from app.engine.lab.prompts.leakage_review_v1 import PROMPT_VERSION as LEAKAGE_REVIEW_V1
from app.engine.lab.prompts.leakage_review_v1 import SYSTEM_PROMPT as LEAKAGE_REVIEW_V1_PROMPT
from app.engine.lab.prompts.missing_value_v1 import PROMPT_VERSION as MISSING_VALUE_V1
from app.engine.lab.prompts.missing_value_v1 import SYSTEM_PROMPT as MISSING_VALUE_V1_PROMPT
from app.engine.lab.prompts.target_selection_v1 import PROMPT_VERSION as TARGET_SELECTION_V1
from app.engine.lab.prompts.target_selection_v1 import SYSTEM_PROMPT as TARGET_SELECTION_V1_PROMPT

_PROVIDER_URL = "https://api.openai.com/v1/chat/completions"
_REQUEST_TIMEOUT_SECONDS = 20.0

_PROMPTS: dict[str, str] = {
    MISSING_VALUE_V1: MISSING_VALUE_V1_PROMPT,
    COLUMN_TYPE_V1: COLUMN_TYPE_V1_PROMPT,
    TARGET_SELECTION_V1: TARGET_SELECTION_V1_PROMPT,
    LEAKAGE_REVIEW_V1: LEAKAGE_REVIEW_V1_PROMPT,
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
ColumnTypeAction = Literal["numerical", "categorical", "identifier"]
ColumnTypeEvidenceField = Literal["column", "dtype", "cardinality", "cardinality_ratio", "sample_values"]
TargetTaskType = Literal["binary", "multiclass", "regression"]
LeakageAvailability = Literal[
    "known_before_prediction",
    "known_at_prediction",
    "known_after_prediction",
    "unknown",
]
LeakageRiskLevel = Literal["NONE", "LOW", "MEDIUM", "HIGH", "CRITICAL"]
LeakageEvidenceField = Literal[
    "column",
    "target",
    "task",
    "dtype",
    "cardinality",
    "related_column_names",
    "exact_target_match_fraction",
    "single_feature_score",
    "single_feature_score_kind",
    "suspicious_name_tokens",
    "target_name_similarity",
    "datetime_after_fraction",
    "identifier_likelihood",
    "unique_ratio",
    "missing_fraction",
    "availability_status",
    "availability_reason",
]
_LEAKAGE_AVAILABILITY: tuple[str, ...] = (
    "known_before_prediction",
    "known_at_prediction",
    "known_after_prediction",
    "unknown",
)
_LEAKAGE_RISK: tuple[str, ...] = ("NONE", "LOW", "MEDIUM", "HIGH", "CRITICAL")
_LEAKAGE_EVIDENCE_FIELDS: tuple[str, ...] = (
    "column",
    "target",
    "task",
    "dtype",
    "cardinality",
    "related_column_names",
    "exact_target_match_fraction",
    "single_feature_score",
    "single_feature_score_kind",
    "suspicious_name_tokens",
    "target_name_similarity",
    "datetime_after_fraction",
    "identifier_likelihood",
    "unique_ratio",
    "missing_fraction",
    "availability_status",
    "availability_reason",
)

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
_COLUMN_TYPE_ACTIONS: tuple[str, ...] = ("numerical", "categorical", "identifier")
_COLUMN_TYPE_EVIDENCE_FIELDS: tuple[str, ...] = (
    "column",
    "dtype",
    "cardinality",
    "cardinality_ratio",
    "sample_values",
)

_TARGET_SELECTION_JSON_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "target": {"type": "string"},
        "task_type": {"type": "string", "enum": ["binary", "multiclass", "regression"]},
        "evidence_field": {"type": "string", "enum": ["columns"]},
        "rationale": {"type": "string"},
        "confidence": {"type": "number", "minimum": 0, "maximum": 1},
    },
    "required": ["target", "task_type", "evidence_field", "rationale", "confidence"],
}

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

_COLUMN_TYPE_JSON_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "action": {"type": "string", "enum": list(_COLUMN_TYPE_ACTIONS)},
        "evidence_field": {"type": "string", "enum": list(_COLUMN_TYPE_EVIDENCE_FIELDS)},
        "rationale": {"type": "string"},
        "confidence": {"type": "number", "minimum": 0, "maximum": 1},
    },
    "required": ["action", "evidence_field", "rationale", "confidence"],
}

_LEAKAGE_REVIEW_JSON_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "availability_status": {"type": "string", "enum": list(_LEAKAGE_AVAILABILITY)},
        "risk_level": {"type": "string", "enum": list(_LEAKAGE_RISK)},
        "evidence_field": {"type": "string", "enum": list(_LEAKAGE_EVIDENCE_FIELDS)},
        "rationale": {"type": "string"},
        "confidence": {"type": "number", "minimum": 0, "maximum": 1},
    },
    "required": [
        "availability_status",
        "risk_level",
        "evidence_field",
        "rationale",
        "confidence",
    ],
}

T = TypeVar("T", bound=BaseModel)


class DecisionAgentUnavailable(Exception):
    """The decision agent is off, unconfigured, or the provider failed closed."""


class MissingValueDecision(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    action: Action
    evidence_field: EvidenceField
    fill_value: str | int | float | bool | None
    rationale: str
    confidence: float = Field(ge=0.0, le=1.0)


class ColumnTypeDecision(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    action: ColumnTypeAction
    evidence_field: ColumnTypeEvidenceField
    rationale: str
    confidence: float = Field(ge=0.0, le=1.0)


class TargetSelectionDecision(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    target: str
    task_type: TargetTaskType
    evidence_field: Literal["columns"]
    rationale: str
    confidence: float = Field(ge=0.0, le=1.0)


class LeakageReviewDecision(BaseModel):
    """Recommendation only. Extra=forbid so keep/exclude cannot be supplied."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    availability_status: LeakageAvailability
    risk_level: LeakageRiskLevel
    evidence_field: LeakageEvidenceField
    rationale: str
    confidence: float = Field(ge=0.0, le=1.0)


# In-process caches. See module docstring.
_CACHE: dict[str, MissingValueDecision] = {}
_COLUMN_TYPE_CACHE: dict[str, ColumnTypeDecision] = {}
_TARGET_SELECTION_CACHE: dict[str, TargetSelectionDecision] = {}
_LEAKAGE_REVIEW_CACHE: dict[str, LeakageReviewDecision] = {}


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
            schema=_DECISION_JSON_SCHEMA,
            schema_name="missing_value_decision",
            result_model=MissingValueDecision,
        )
        _CACHE[cache_key] = decision
        return decision
    except DecisionAgentUnavailable:
        raise
    except Exception as exc:
        raise DecisionAgentUnavailable("decision agent failed") from exc


def request_column_type_decision(evidence: ColumnTypeEvidence, prompt_version: str) -> ColumnTypeDecision:
    """Return a schema-validated column-type decision, or raise unavailable."""
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
        cached = _COLUMN_TYPE_CACHE.get(cache_key)
        if cached is not None:
            return cached
        decision = _complete_structured(
            system_prompt=system_prompt,
            evidence=evidence,
            api_key=api_key,
            model=settings.decision_agent_model,
            schema=_COLUMN_TYPE_JSON_SCHEMA,
            schema_name="column_type_decision",
            result_model=ColumnTypeDecision,
        )
        _COLUMN_TYPE_CACHE[cache_key] = decision
        return decision
    except DecisionAgentUnavailable:
        raise
    except Exception as exc:
        raise DecisionAgentUnavailable("decision agent failed") from exc


def request_target_selection_decision(
    evidence: TargetSelectionEvidence,
    prompt_version: str,
) -> TargetSelectionDecision:
    """Return a schema-validated semantic target decision, or fail closed."""
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
        cached = _TARGET_SELECTION_CACHE.get(cache_key)
        if cached is not None:
            return cached
        decision = _complete_structured(
            system_prompt=system_prompt,
            evidence=evidence,
            api_key=api_key,
            model=settings.decision_agent_model,
            schema=_TARGET_SELECTION_JSON_SCHEMA,
            schema_name="target_selection_decision",
            result_model=TargetSelectionDecision,
        )
        _TARGET_SELECTION_CACHE[cache_key] = decision
        return decision
    except DecisionAgentUnavailable:
        raise
    except Exception as exc:
        raise DecisionAgentUnavailable("decision agent failed") from exc


def request_leakage_review(
    evidence: LeakageReviewEvidence,
    prompt_version: str,
) -> LeakageReviewDecision:
    """Return a schema-validated leakage recommendation, or fail closed.

    The model cannot keep or exclude a feature. It only recommends availability
    and risk; a deterministic validator still owns the action.
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
        cached = _LEAKAGE_REVIEW_CACHE.get(cache_key)
        if cached is not None:
            return cached
        decision = _complete_structured(
            system_prompt=system_prompt,
            evidence=evidence,
            api_key=api_key,
            model=settings.decision_agent_model,
            schema=_LEAKAGE_REVIEW_JSON_SCHEMA,
            schema_name="leakage_review_decision",
            result_model=LeakageReviewDecision,
        )
        _LEAKAGE_REVIEW_CACHE[cache_key] = decision
        return decision
    except DecisionAgentUnavailable:
        raise
    except Exception as exc:
        raise DecisionAgentUnavailable("decision agent failed") from exc


def _cache_key(
    evidence: ColumnEvidence | ColumnTypeEvidence | TargetSelectionEvidence | LeakageReviewEvidence,
    prompt_version: str,
) -> str:
    blob = _evidence_json(evidence) + "\0" + prompt_version
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


def _evidence_json(
    evidence: ColumnEvidence | ColumnTypeEvidence | TargetSelectionEvidence | LeakageReviewEvidence,
) -> str:
    return json.dumps(asdict(evidence), sort_keys=True, default=str)


def _complete_structured(
    *,
    system_prompt: str,
    evidence: ColumnEvidence | ColumnTypeEvidence | TargetSelectionEvidence | LeakageReviewEvidence,
    api_key: str,
    model: str,
    schema: dict[str, Any],
    schema_name: str,
    result_model: type[T],
) -> T:
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
                "name": schema_name,
                "strict": True,
                "schema": schema,
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
        return result_model.model_validate(parsed)
    except (json.JSONDecodeError, ValidationError) as exc:
        raise DecisionAgentUnavailable("decision agent returned JSON that failed the schema") from exc
