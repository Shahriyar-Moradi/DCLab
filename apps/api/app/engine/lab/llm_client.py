"""Lab decision agents: result models, evidence envelopes and gateway wrappers.

The provider call lives in ``app/agents/gateway/providers/openai.py`` (ADR 0009 §1);
every request here goes through ``GatewayService.complete`` (policy, kill switches,
ADR 0005 labels, budget, ledger) with the legacy purposes unchanged until P6.9-A
(ADR 0009 §4). Evidence travels as a tagged ``ContextEnvelope``: names and dtypes as
untrusted metadata of their columns, counts and ratios as metadata, train-partition
statistics as aggregates, and raw values (``sample_rows``, ``sample_values``,
co-occurring values) as ``sample_values``, which the policy caps (no sample values,
founder Q3) always drop. ``WITHHELD`` names the evidence fields the model therefore
never sees; validators reject a decision that cites one.

``consult`` returns the gateway response; the ``request_*`` wrappers return the
decision or raise ``DecisionAgentUnavailable``. With ``ai_enabled`` off nothing is
called (kill switches stop it per call). The gateway cache replaces the old per-process
caches.
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from app.agents.contracts import ContextField
from app.agents.gateway.contract import Annotate, CompletionResponse, Refusal
from app.agents.governance.decision_points import LEGACY_PURPOSE_KEYS
from app.agents.legacy import LegacyCall, LegacyContext, complete, ctx_field, number, refusal_text, scalar, text
from app.config import get_settings
from app.engine.lab.evidence import (
    ColumnEvidence,
    ColumnTypeEvidence,
    LeakageReviewEvidence,
    TargetSelectionEvidence,
)

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


class DecisionAgentUnavailable(Exception):
    """The decision agent is off, or the gateway refused / failed closed."""


class MissingValueDecision(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    action: Action
    evidence_field: EvidenceField
    fill_value: str | int | float | bool | None
    rationale: str
    confidence: float = Field(ge=0.0, le=1.0)


class MissingValueDecisionV3(BaseModel):
    """missing_value v3: only actions the pipeline executes; no fill value (P6.9-A)."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    action: Literal["impute_median", "impute_most_frequent", "drop_column"]
    evidence_field: Literal["column", "dtype", "missing_count", "missing_fraction", "correlation_with_target",
                            "missingness_cooccurrence"]
    rationale: str
    confidence: float = Field(ge=0.0, le=1.0)

    @property
    def fill_value(self) -> None:  # the v2 validator and ledger read it; v3 never fills
        return None


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


def _call(key: str, version: int, purpose: str, point: str, schema: type[BaseModel], max_class: str) -> LegacyCall:
    # ADR 0008 §1: the legacy purpose maps onto its registry key (the ledger keeps the purpose).
    if LEGACY_PURPOSE_KEYS.get(purpose) != point:
        raise ValueError(f"legacy purpose {purpose!r} does not map to {point!r}")
    return LegacyCall(agent_role="legacy_decision", agent_key=key, prompt_version=version, purpose=purpose,
                      decision_point_key=point, output_schema=schema, max_output_tokens=1000, timeout_s=20.0,
                      max_data_class=max_class)


# Purpose and decision point per ADR 0008 §1; Lab-time points (target, role) are metadata only (§2c).
CALLS: dict[str, LegacyCall] = {
    "missing_value": _call("missing_value", 3, "semantic_missing_value", "column.missing_value_action",
                           MissingValueDecisionV3, "aggregates"),
    "column_type": _call("column_type", 2, "semantic_column_type", "column.semantic_role", ColumnTypeDecision,
                         "metadata"),
    "target_selection": _call("target_selection", 1, "semantic_target", "target.column", TargetSelectionDecision,
                              "metadata"),
    "leakage_review": _call("leakage_review", 1, "semantic_leakage", "feature.leakage_suspect",
                            LeakageReviewDecision, "aggregates"),
}
# Evidence the model never sees under the policy caps (sample values are always dropped).
WITHHELD: dict[str, frozenset[str]] = {
    "missing_value": frozenset({"sample_rows", "missingness_cooccurrence.other_value"}),
    "column_type": frozenset({"sample_values"}),
    "target_selection": frozenset({"columns.sample_values"}),
    "leakage_review": frozenset(),
}


# --- evidence → envelope ---------------------------------------------------------------


def _missing_value_fields(evidence: ColumnEvidence, ctx: LegacyContext, target: str | None) -> list[ContextField]:
    own = ctx.columns([evidence.column])
    flags = list(evidence.missingness_cooccurrence)
    others = [flag.other_column for flag in flags]
    row_columns = [name for row in evidence.sample_rows for name in row]
    return [
        ctx_field("column", text(evidence.column), "metadata", own),
        ctx_field("dtype", text(evidence.dtype), "metadata", own),
        ctx_field("missing_count", number(evidence.missing_count), "metadata", own),
        ctx_field("missing_fraction", number(evidence.missing_fraction), "metadata", own),
        ctx_field("correlation_with_target", number(evidence.correlation_with_target), "aggregates",
                  ctx.columns([evidence.column, *([target] if target else [])])),
        ctx_field("missingness_cooccurrence", [
            {"other_column": text(flag.other_column), "missing_and_value_count": number(flag.missing_and_value_count),
             "rows_with_value": number(flag.rows_with_value), "fraction_of_missing": number(flag.fraction_of_missing),
             "fraction_of_value": number(flag.fraction_of_value), "exact_match": bool(flag.exact_match)}
            for flag in flags
        ], "aggregates", ctx.columns([evidence.column, *others])),
        ctx_field("cooccurring_values", [
            {"other_column": text(flag.other_column), "other_value": scalar(flag.other_value)} for flag in flags
        ], "sample_values", ctx.columns(others), required=False),
        ctx_field("sample_rows", [
            [{"column": text(name), "value": scalar(value)} for name, value in row.items()]
            for row in evidence.sample_rows
        ], "sample_values", ctx.columns(row_columns), required=False),
    ]


def _column_type_fields(evidence: ColumnTypeEvidence, ctx: LegacyContext) -> list[ContextField]:
    own = ctx.columns([evidence.column])
    return [
        ctx_field("column", text(evidence.column), "metadata", own),
        ctx_field("dtype", text(evidence.dtype), "metadata", own),
        ctx_field("cardinality", number(evidence.cardinality), "metadata", own),
        ctx_field("cardinality_ratio", number(evidence.cardinality_ratio), "metadata", own),
        ctx_field("sample_values", [scalar(value) for value in evidence.sample_values], "sample_values", own,
                  required=False),
    ]


def _target_selection_fields(evidence: TargetSelectionEvidence, ctx: LegacyContext) -> list[ContextField]:
    names = [item.name for item in evidence.columns]
    candidates = ctx.columns(names)
    return [
        ctx_field("row_count", number(evidence.row_count), "metadata", candidates),
        ctx_field("column_count", number(evidence.column_count), "metadata", candidates),
        ctx_field("columns", [
            {"name": text(item.name), "dtype": text(item.dtype), "unique_count": number(item.unique_count),
             "unique_ratio": number(item.unique_ratio), "missing_ratio": number(item.missing_ratio),
             "identifier_likelihood": number(item.identifier_likelihood),
             "probable_task_type": text(item.probable_task_type),
             "deterministic_confidence": number(item.deterministic_confidence)}
            for item in evidence.columns
        ], "metadata", candidates),
        ctx_field("candidate_sample_values", [
            {"name": text(item.name), "sample_values": [scalar(value) for value in item.sample_values]}
            for item in evidence.columns
        ], "sample_values", candidates, required=False),
    ]


def _leakage_review_fields(evidence: LeakageReviewEvidence, ctx: LegacyContext) -> list[ContextField]:
    own = ctx.columns([evidence.column])
    with_target = ctx.columns([evidence.column, evidence.target])
    metadata = {
        "column": text(evidence.column), "task": text(evidence.task), "dtype": text(evidence.dtype),
        "cardinality": number(evidence.cardinality), "single_feature_score_kind": (
            None if evidence.single_feature_score_kind is None else text(evidence.single_feature_score_kind)),
        "suspicious_name_tokens": [text(token) for token in evidence.suspicious_name_tokens],
        "identifier_likelihood": number(evidence.identifier_likelihood),
        "unique_ratio": number(evidence.unique_ratio), "missing_fraction": number(evidence.missing_fraction),
        "availability_status": text(evidence.availability_status),
        "availability_reason": text(evidence.availability_reason),
    }
    statistics = {  # train-partition statistics against the target
        "exact_target_match_fraction": number(evidence.exact_target_match_fraction),
        "single_feature_score": number(evidence.single_feature_score),
        "datetime_after_fraction": number(evidence.datetime_after_fraction),
    }
    return [
        *(ctx_field(key, value, "metadata", own) for key, value in metadata.items()),
        ctx_field("target", text(evidence.target), "metadata", ctx.columns([evidence.target])),
        ctx_field("target_name_similarity", number(evidence.target_name_similarity), "metadata", with_target),
        # context only: one denied related column drops this field, never the whole review
        ctx_field("related_column_names", [text(name) for name in evidence.related_column_names], "metadata",
                  ctx.columns(evidence.related_column_names), required=False),
        *(ctx_field(key, value, "aggregates", with_target) for key, value in statistics.items()),
    ]


def envelope_fields(kind: str, evidence: Any, ctx: LegacyContext, *, target: str | None = None) -> list[ContextField]:
    if kind == "missing_value":
        return _missing_value_fields(evidence, ctx, target)
    if kind == "column_type":
        return _column_type_fields(evidence, ctx)
    if kind == "target_selection":
        return _target_selection_fields(evidence, ctx)
    if kind == "leakage_review":
        return _leakage_review_fields(evidence, ctx)
    raise ValueError(f"unknown legacy decision kind {kind!r}")


# --- calls ------------------------------------------------------------------------------


def agent_enabled() -> bool:
    """The Lab decision agent's gate: ``AI_ENABLED`` (P6.9-A retired its own flag). The
    gateway enforces the kill switches (``agent:<key>``, ``purpose:<purpose>``) per call,
    and the writers apply answers only through the decision-point levels (ADR 0008)."""

    return bool(getattr(get_settings(), "ai_enabled", False))


def consult(
    kind: str,
    evidence: Any,
    *,
    context: LegacyContext | None,
    annotate: Annotate | None = None,
    target: str | None = None,
) -> CompletionResponse:
    """One gateway call for ``kind``; a missing context (no attributable run) or a
    disabled agent is a refusal without a ledger row."""

    if not agent_enabled():
        return CompletionResponse(ok=False, refusal=Refusal(code="kill_switch", scope="decision_agent",
                                                            message="decision agent is disabled"))
    if context is None:
        return CompletionResponse(ok=False, refusal=Refusal(code="policy_denied", scope="attribution",
                                                            message="no attributable run for an AI call"))
    try:
        fields = envelope_fields(kind, evidence, context, target=target)
    except Exception as exc:  # an evidence value the envelope cannot carry is a refusal
        return CompletionResponse(ok=False, refusal=Refusal(code="policy_denied", message=type(exc).__name__))
    return complete(context, CALLS[kind], fields, annotate=annotate)


def _request(kind: str, evidence: Any, prompt_version: str, *, context: LegacyContext | None,
             annotate: Annotate | None = None, target: str | None = None) -> Any:
    if prompt_version != f"{kind}_v{CALLS[kind].prompt_version}":
        raise DecisionAgentUnavailable(f"unknown prompt version {prompt_version!r}")
    response = consult(kind, evidence, context=context, annotate=annotate, target=target)
    if not response.ok or response.output is None:
        raise DecisionAgentUnavailable(refusal_text(response.refusal))
    return response.output


def request_decision(evidence: ColumnEvidence, prompt_version: str, *, context: LegacyContext | None = None,
                     annotate: Annotate | None = None, target: str | None = None) -> MissingValueDecision:
    return _request("missing_value", evidence, prompt_version, context=context, annotate=annotate, target=target)


def request_column_type_decision(evidence: ColumnTypeEvidence, prompt_version: str, *,
                                 context: LegacyContext | None = None,
                                 annotate: Annotate | None = None) -> ColumnTypeDecision:
    return _request("column_type", evidence, prompt_version, context=context, annotate=annotate)


def request_target_selection_decision(evidence: TargetSelectionEvidence, prompt_version: str, *,
                                      context: LegacyContext | None = None,
                                      annotate: Annotate | None = None) -> TargetSelectionDecision:
    return _request("target_selection", evidence, prompt_version, context=context, annotate=annotate)


def request_leakage_review(evidence: LeakageReviewEvidence, prompt_version: str, *,
                           context: LegacyContext | None = None,
                           annotate: Annotate | None = None) -> LeakageReviewDecision:
    """The model cannot keep or exclude a feature; a deterministic validator owns the action."""

    return _request("leakage_review", evidence, prompt_version, context=context, annotate=annotate)
