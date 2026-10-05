"""Decision points: precedence, the ``decision_point_resolved`` record and event (ADR 0008 §2, §7, §8).

The owning service of ``decision_point_resolved`` records. ``resolve()`` turns the
rule answers and the AI answers of one point (today Jev resolutions from the semantic
port; agent answers in P6.9-A step A2) into the values a stage uses, by precedence
**explicit branch/human override > inherited value > AI (>= L2, validator-accepted,
§1b-eligible) > rule**. The semantic port already applied the level and the agreement
table (an AI value arrives only as ``policy_outcome == "ai"`` at L2); this module adds
the override precedence and the audit:

* one pipeline event per point and run (``decision_point_resolved``; ``ai: "off"`` when AI
  is off — the only change AI-off runs see);
* one accepted record per point and run where AI participated, actor ``rule``
  (``decision_point.<key>.v1``) — also when a Jev value is applied at L2 (no agent run
  exists, ``ck_pdr_actor``) — with both answers, the value used, the level, the evidence
  partition, the policy digest and a revert that is an existing ``ExperimentChange``.

Per-column answers are stored as a list (``details.columns``, capped at 64): column names
are values, never JSON keys, so a column named like a secret cannot break the record.
Nothing here reads holdout data, computes a metric, selects a model or builds a split.
"""

from __future__ import annotations

import json
from collections import Counter
from collections.abc import Mapping
from dataclasses import dataclass, field, replace
from typing import Any
from uuid import UUID

from sqlalchemy.orm import Session

from app.agents.governance.decision_points import REGISTRY, answer_ceiling
from app.agents.semantic import policy
from app.agents.semantic.port import SemanticOutcome
from app.agents.semantic.releases import release_for
from app.db.models import ProjectDecisionRecord
from app.domain.decision_records import decision_point_rule
from app.domain.errors import InvalidDecisionRecordError
from app.domain.experiment_changes import ExperimentChangeSet
from app.domain.state_graph import STATE_GRAPH_JSON_MAX_BYTES
from app.services.decision_record_service import clean_json_object, evidence_ref, existing_record, rule_record

EVENT_TYPE = "decision_point_resolved"
EVENT_STAGE = "decision_points"
DECISION_TYPE = "decision_point_resolved"
SCHEMA_VERSION = 1
COLUMNS_DETAILED_MAX = 64
IDS_MAX = 64
_DETAILS_BUDGET = STATE_GRAPH_JSON_MAX_BYTES - 1024
# ``legacy``: the pre-Phase-6 decision agent already applied a value (retired in step A2).
_SOURCES = ("rule", "ai", "human", "ai_inherited", "human_pending", "legacy")

# Code-owned change tables (ADR 0008 §1b; ``experiment_branch_service._ROLE_TREATMENT``).
# revert: the change that restores the rule value after an applied AI value (L2 kinds only;
# always ``feature_transform_add`` of the rule's treatment, never a ``_remove`` of an imputation).
_ROLE_TRANSFORM = {"numeric": "impute_median", "categorical_code": "impute_most_frequent"}
# accept: what accepting an L1 review item would change (P6.6-A executes it through a branch).
_ACCEPT_ROLE = {**_ROLE_TRANSFORM, "identifier": "drop_column", "free_text": "drop_column",
                "other": "drop_column", "datetime": "datetime_extract"}


def revert_change(key: str, column: str, rule_value: Any) -> dict[str, Any] | None:
    if key == "column.semantic_role" and rule_value in _ROLE_TRANSFORM:
        return {"kind": "feature_transform_add", "column": column, "transform": _ROLE_TRANSFORM[rule_value]}
    return None


def accept_change(key: str, column: str, rule_value: Any, ai_value: Any) -> dict[str, Any] | None:
    if key == "column.semantic_role" and ai_value in _ACCEPT_ROLE:
        return {"kind": "feature_transform_add", "column": column, "transform": _ACCEPT_ROLE[ai_value]}
    if key == "column.is_identifier" and ai_value is True:
        return {"kind": "feature_transform_add", "column": column, "transform": "drop_column"}
    if key == "column.is_identifier" and ai_value is False and rule_value is True:
        return {"kind": "feature_transform_add", "column": column, "transform": "keep"}  # a re-inclusion (L1)
    return None  # feature.leakage_suspect: the AI may only add a review flag


@dataclass(frozen=True)
class Answer:
    """One question of one point after precedence."""

    question_key: str
    rule: Any
    used: Any
    source: str  # rule | ai | human | ai_inherited | human_pending (an L1 review item is open) | legacy
    agreement: str  # off | agree | disagree | abstain | unavailable
    ai: Any = None
    level: int = 0
    confidence: float | None = None
    answer_id: UUID | None = None
    invocation_id: UUID | None = None
    refusal: str | None = None
    validator_reasons: tuple[str, ...] = ()


@dataclass(frozen=True)
class PointResolution:
    key: str
    ai: str  # off | on
    answers: tuple[Answer, ...]
    evidence_partition: str
    reason: str | None = None  # why AI is off for this point
    policy_digest: str | None = None
    release: Mapping[str, Any] = field(default_factory=dict)  # prompt_release_id, release, model_id
    facts: Mapping[str, Any] = field(default_factory=dict)  # bands/counts the rule relied on (numbers only)

    @property
    def level(self) -> int:
        return max((a.level for a in self.answers), default=0)

    def counts(self) -> Counter:
        return Counter(a.agreement for a in self.answers)

    def agreement(self) -> str:
        kinds = set(self.counts())
        if not kinds:
            return self.ai if self.ai == "off" else "unavailable"
        return kinds.pop() if len(kinds) == 1 else "partial"

    def applied(self) -> list[Answer]:
        """AI values that changed what the stage uses (an L2 agreement applies the same value)."""
        return [a for a in self.answers if a.source == "ai" and a.used != a.rule]

    def reviews(self) -> list[Answer]:
        return [a for a in self.answers if a.source == "human_pending"]

    def used(self) -> dict[str, Any]:
        return {a.question_key: a.used for a in self.answers}

    def rule_fallback(self, reason: str) -> "PointResolution":
        """Applied AI values back to the rule value (their record could not be written)."""

        answers = tuple(replace(a, used=a.rule, source="rule", refusal=reason) if a in self.applied() else a
                        for a in self.answers)
        return replace(self, answers=answers, reason=reason)

    def event_payload(self) -> dict[str, Any]:
        """Same keys whether AI is on or off (the golden event snapshot pins them)."""

        return {
            "decision_point": self.key,
            "ai": self.ai,
            "reason": self.reason,
            "evidence_partition": self.evidence_partition,
            "level": self.level,
            "columns_total": len(self.answers),
            "agreement": self.agreement(),
            "ai_applied": len(self.applied()),
            "review_items": len(self.reviews()),
            "policy_digest": self.policy_digest,
        }


def resolve(
    key: str,
    outcome: SemanticOutcome,
    *,
    evidence_partition: str,
    overrides: Mapping[str, Any] | None = None,
    inherited: Mapping[str, Any] | None = None,
    legacy: Mapping[str, Any] | None = None,
    validator_reasons: Mapping[str, list[str]] | None = None,
    policy_digest: str | None = None,
    release: Mapping[str, Any] | None = None,
    reason: str | None = None,
    facts: Mapping[str, Any] | None = None,
) -> PointResolution:
    """Precedence per question: override > legacy (a value the pre-Phase-6 agent already
    applied; never asked) > inherited (A2) > AI (>= L2 and applied by the policy, i.e.
    validator- and §1b-accepted) > rule. Evidence must be ``train``/``metadata``."""

    if key not in REGISTRY:
        raise ValueError(f"unknown decision point {key}")
    if evidence_partition not in ("train", "metadata"):
        raise ValueError("evidence_partition must be train or metadata")
    overrides, inherited, reasons = dict(overrides or {}), dict(inherited or {}), dict(validator_reasons or {})
    legacy = dict(legacy or {})
    cap = REGISTRY[key].cap
    answers = []
    for item in outcome.resolutions:
        name = item.question_key
        if name in overrides:
            used, source = overrides[name], "human"
        elif name in legacy:
            used, source = legacy[name], "legacy"
        elif name in inherited:
            used, source = inherited[name], "ai_inherited"
        elif (item.policy_outcome == "ai" and 2 <= item.level <= cap
              and _ceiling(key, item.rule_answer, item.ai_answer) >= 2):  # never an exclusion, never L3
            used, source = item.value_used, "ai"
        else:
            used, source = item.rule_answer, "human_pending" if item.policy_outcome == "review" else "rule"
        answers.append(Answer(
            question_key=name, rule=item.rule_answer, used=used, source=source, agreement=item.agreement,
            ai=item.ai_answer, level=item.level, confidence=item.confidence, answer_id=item.answer_id,
            invocation_id=item.invocation_id, refusal=item.refusal,
            validator_reasons=tuple(reasons.get(name) or ()),
        ))
    return PointResolution(key=key, ai=outcome.ai, answers=tuple(answers), evidence_partition=evidence_partition,
                           reason=reason, policy_digest=policy_digest, release=dict(release or {}),
                           facts=dict(facts or {}))


# --- the record ------------------------------------------------------------------------


def idempotency_key(experiment_id: UUID, key: str) -> str:
    return f"{DECISION_TYPE}:{experiment_id}:{key}"


def _value(value: Any) -> Any:
    return value if value is None or isinstance(value, (bool, int, float, str)) else str(value)


def _revert(resolution: PointResolution) -> dict[str, Any]:
    changes = [c for a in resolution.applied() if (c := revert_change(resolution.key, a.question_key, a.rule))]
    if not changes:
        return {"kind": "none"}
    ExperimentChangeSet.model_validate({"changes": changes[:32]})  # closure: existing change kinds only
    return {"kind": "branch_change", "changes": changes[:32]}


def _ceiling(key: str, rule: Any, ai: Any) -> int:
    """§1b ceiling of one AI answer (Jev points: by ``policy.answer_kind``)."""

    release = release_for(key)
    return answer_ceiling(key, policy.answer_kind(release, rule, ai) if release is not None else None)


def _answer_ceiling(resolution: PointResolution) -> int:
    """Highest §1b ceiling among the AI answers (a point without AI answers: its no-kind ceiling)."""

    ceilings = [_ceiling(resolution.key, a.rule, a.ai) for a in resolution.answers if a.ai is not None]
    return max(ceilings, default=answer_ceiling(resolution.key, None))


def _column(resolution: PointResolution, answer: Answer) -> dict[str, Any]:
    row = {"column": answer.question_key[:256], "rule": _value(answer.rule), "ai": _value(answer.ai),
           "used": _value(answer.used), "source": answer.source, "agreement": answer.agreement,
           "level": answer.level, "confidence": answer.confidence}
    if answer.validator_reasons:
        row["validator_reasons"] = list(answer.validator_reasons)[:8]
    if answer.source == "human_pending":
        change = accept_change(resolution.key, answer.question_key, answer.rule, answer.ai)
        row["accept_change"] = change
    return row


def record_details(resolution: PointResolution) -> dict[str, Any]:
    point = REGISTRY[resolution.key]
    answers = list(resolution.answers)
    rejected = [a for a in answers if a.validator_reasons]
    details: dict[str, Any] = {
        "decision_point": resolution.key,
        "pattern": point.pattern,
        "level": resolution.level,
        "cap": point.cap,
        "answer_ceiling": _answer_ceiling(resolution),
        "outcome_scope": point.outcome_scope,
        "evidence_partition": resolution.evidence_partition,
        "policy_digest": resolution.policy_digest,
        # ADR 0008 §7 names it ``prompt_release_id``; "prompt" is a blocked key part of record JSON.
        "release_id": resolution.release.get("prompt_release_id"),
        "model_id": resolution.release.get("model_id"),
        "rule": {"rule_id": point.rule_id},
        "ai": {
            "kind": point.ai_kind.split(":", 1)[0],
            "purpose": point.ai_kind.split(":", 1)[-1],
            "release": resolution.release.get("release"),
            "agent_run_id": None,
            "invocation_ids": sorted({str(a.invocation_id) for a in answers if a.invocation_id})[:IDS_MAX],
            "semantic_answer_ids": [str(a.answer_id) for a in answers if a.answer_id][:IDS_MAX],
            "refusals": sorted({a.refusal for a in answers if a.refusal}),
        },
        "agreement": resolution.agreement(),
        "agreement_counts": dict(resolution.counts()),
        "used": {"source": "ai" if resolution.applied() else "rule", "ai_applied": len(resolution.applied()),
                 "legacy_overrides": sum(a.source == "legacy" for a in answers)},
        "validator": {"verdict": "rejected" if rejected else ("accepted" if resolution.applied() else "not_run"),
                      "rejected": len(rejected)},
        # Jev L1 review items are the semantic answers with policy_outcome ``review``; an
        # ``agent_proposals`` row needs an agent run (run_id NOT NULL), which Jev points lack.
        "proposal_id": None,
        "review_answer_ids": [str(a.answer_id) for a in resolution.reviews() if a.answer_id][:IDS_MAX],
        "revert": _revert(resolution),
        "columns_total": len(answers),
    }
    columns = [_column(resolution, a) for a in answers]
    limit = min(COLUMNS_DETAILED_MAX, len(columns))
    while True:  # bounded: shrink the per-column detail until the record fits (counts stay)
        details["columns"] = columns[:limit]
        details["columns_detailed"] = limit
        if limit == 0 or len(json.dumps(details, default=str).encode()) <= _DETAILS_BUDGET:
            return details
        limit //= 2


def _rationale(resolution: PointResolution) -> str:
    applied, reviews = len(resolution.applied()), len(resolution.reviews())
    return (f"{resolution.key}: rule value used for {len(resolution.answers) - applied} of "
            f"{len(resolution.answers)} questions; AI value applied for {applied} (level {resolution.level}); "
            f"{reviews} open for review; agreement {resolution.agreement()}.")


def write_record(
    db: Session, resolution: PointResolution, *, workspace_id: UUID, project_id: UUID, experiment_id: UUID,
) -> ProjectDecisionRecord | None:
    """The point's record for one run (idempotent per experiment and point); None when AI
    did not participate (AI off) or no question was asked. Not committed."""

    if resolution.ai != "on" or not resolution.answers:
        return None
    key = idempotency_key(experiment_id, resolution.key)
    found = existing_record(db, workspace_id=workspace_id, idempotency_key=key)
    if found is not None:
        return found
    details = record_details(resolution)
    try:
        details = clean_json_object(details, label="details")
    except InvalidDecisionRecordError:  # e.g. a control character in a column name: no names, counts only
        revert = details["revert"]
        details = clean_json_object({
            **details, "columns": [], "columns_detailed": 0,
            "revert": {"kind": revert["kind"], "changes_count": len(revert.get("changes") or []), "names_omitted": True},
        }, label="details")
    counts = resolution.counts()
    facts = {"columns_total": len(resolution.answers), "ai_applied": len(resolution.applied()),
             "review_items": len(resolution.reviews()),
             **{f"agreement_{name}": int(counts.get(name, 0))
                for name in ("agree", "disagree", "abstain", "unavailable")},
             **{k: v for k, v in resolution.facts.items() if isinstance(v, (int, float)) and not isinstance(v, bool)}}
    row = rule_record(
        workspace_id=workspace_id, project_id=project_id, decision_type=DECISION_TYPE, subject_kind="experiment",
        subject_id=experiment_id, actor_rule=decision_point_rule(resolution.key), rationale=_rationale(resolution),
        schema_version=SCHEMA_VERSION, idempotency_key=key, facts=facts,
        evidence_refs=[evidence_ref("experiment", experiment_id)], details=details,
    )
    db.add(row)
    return row


__all__ = ["DECISION_TYPE", "EVENT_STAGE", "EVENT_TYPE", "Answer", "PointResolution", "accept_change",
           "idempotency_key", "record_details", "resolve", "revert_change", "write_record"]
