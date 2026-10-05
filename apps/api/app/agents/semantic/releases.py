"""Pinned Jev purpose releases (ADR 0008 §5; ADR 0009 §2.8, §9). Code-owned data.

Each purpose has its question text, primitive, criteria, the state fields it may
send (closed band vocabularies, a column name only as untrusted text), its acting
band and its max level. ``state_violations`` is the gateway's check of a Jev
``state`` against the release (never numeric: counts, ratios and dates are turned
into bands by ``ratio_band`` / ``cardinality_band`` before they enter state).
A release is stored as a ``prompt_releases`` row ``jev:<purpose>`` whose
``prompt_digest`` is the digest of this definition; a text or threshold change is
a new ``version`` (and resets the purpose to L0: levels are keyed by release id).
This module imports no SDK and no gateway code.
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass, field
from datetime import UTC, datetime
from types import MappingProxyType
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.agents.contracts import UNTRUSTED_TEXT_MAX_CHARS, Untrusted
from app.db.models import PromptRelease

JEV_MODEL_ID = "jev-1.13.0"  # never an alias (jev-latest / jev-preview)
TYPESAFE_SDK_PIN = "0.7.2"
JEV_PREFIX = "jev:"
MAX_QUESTIONS = 50
MAX_STATE_BYTES = 8 * 1024
TIMEOUT_MS = 1000
SUBJECT_KEY = re.compile(r"^c[0-9]{1,2}$")  # per-column state entries: c0 … c49

UNTRUSTED, UNTRUSTED_LIST, BOOL = "untrusted", "untrusted_list", "bool"
DTYPE_KINDS = ("integer", "float", "boolean", "datetime", "string", "categorical", "other")
RATIO_BANDS = ("none", "under_1pct", "from_1_to_10pct", "from_10_to_50pct", "from_50_to_90pct", "from_90_to_99pct", "over_99pct", "all")
CARDINALITY_BANDS = ("constant", "binary", "low", "medium", "high", "very_high")
VALUE_PATTERNS = ("digits", "mixed", "date_like", "long_text", "short_text", "not_text")
TASKS = ("binary_classification", "multiclass_classification", "regression")
ROLES = ("numeric", "categorical_code", "identifier", "datetime", "free_text", "other")
PROPOSAL_KINDS = ("problem_spec", "experiment_change", "ref_move", "model_release", "review_flag",
                  "decision_record", "other")
COUNT_BANDS = ("none", "one", "several")
AVAILABILITY = ("before_prediction", "after_outcome", "unknown")  # from the spec, as a code label


def name_tokens(name: str) -> list[str]:
    """Lower-case tokens of a column name (``customerID_v2`` → customer, id, v2); the only
    tokens the gateway lets travel as ``name_tokens``."""

    spaced = re.sub(r"([a-z0-9])([A-Z])", r"\1 \2", name)
    return [token.lower() for token in re.split(r"[^0-9A-Za-z]+", spaced) if token][:16]


def ratio_band(value: float | None) -> str:
    """A ratio in [0, 1] as a band (``None`` / NaN → ``none``)."""

    if value is None or value != value or value <= 0:
        return "none"
    if value >= 1:
        return "all"
    for bound, band in ((0.01, "under_1pct"), (0.10, "from_1_to_10pct"), (0.50, "from_10_to_50pct"),
                        (0.90, "from_50_to_90pct"), (0.99, "from_90_to_99pct")):
        if value < bound:
            return band
    return "over_99pct"


def cardinality_band(distinct: int) -> str:
    if distinct <= 1:
        return "constant"
    if distinct == 2:
        return "binary"
    return "low" if distinct <= 10 else "medium" if distinct <= 100 else "high" if distinct <= 1000 else "very_high"


def count_band(count: int) -> str:
    return "none" if count <= 0 else "one" if count == 1 else "several"


@dataclass(frozen=True)
class JevRelease:
    purpose: str
    version: int
    primitive: str  # noul | choice | score
    question: str
    question_key: str | None  # the code key of a non-column question; None = one question per column
    criteria: Any = None  # noul {"true", "false"} / choice {label: description} / score [level descriptions]
    choices: tuple[str, ...] = ()  # fixed choices; empty for a choice over caller-supplied keys
    subject_fields: MappingProxyType = field(default_factory=lambda: MappingProxyType({}))
    context_fields: MappingProxyType = field(default_factory=lambda: MappingProxyType({}))
    yes_at: float | None = None  # noul: p >= yes_at → yes
    no_at: float | None = None  # noul: p <= no_at → no (None: no "no" band)
    confidence_at: float | None = None  # choice: confidence >= confidence_at
    max_level: int = 0
    user_text: bool = False  # the user's message travels (only with data.user_text_to_jev)
    model_id: str = JEV_MODEL_ID

    @property
    def agent_key(self) -> str:
        return f"{JEV_PREFIX}{self.purpose}"

    @property
    def sourceless(self) -> bool:
        """No dataset content at all: the state is code labels and flags only."""
        return not self.subject_fields and UNTRUSTED not in self.context_fields.values()

    def digest(self) -> str:
        body = {name: getattr(self, name) for name in self.__dataclass_fields__}
        body |= {"subject_fields": dict(self.subject_fields), "context_fields": dict(self.context_fields)}
        return hashlib.sha256(json.dumps(body, sort_keys=True, separators=(",", ":"),
                                         ensure_ascii=False).encode()).hexdigest()


def _fields(**spec: Any) -> MappingProxyType:
    return MappingProxyType(spec)


_COLUMN = {"column": UNTRUSTED, "dtype": DTYPE_KINDS}
RELEASES: MappingProxyType[str, JevRelease] = MappingProxyType({r.purpose: r for r in (
    JevRelease(
        "column.is_identifier", 1, "noul",
        "Is the column in this state entry an identifier: a key, code or id that names a row or an entity, "
        "rather than a measurement or a category?", None,
        criteria={"true": "The column identifies rows or entities.", "false": "The column describes them."},
        subject_fields=_fields(**_COLUMN, name_tokens=UNTRUSTED_LIST, uniqueness=RATIO_BANDS, nulls=RATIO_BANDS),
        yes_at=0.90, no_at=0.10, max_level=1),
    JevRelease(
        "column.semantic_role", 1, "choice",
        "Which role does the column in this state entry play in a tabular model?", None,
        criteria={"numeric": "a measured quantity", "categorical_code": "a label from a small set of codes",
                  "identifier": "a key or id", "datetime": "a date or time", "free_text": "free-form text",
                  "other": "none of these"},
        choices=ROLES,
        subject_fields=_fields(**_COLUMN, cardinality=CARDINALITY_BANDS, value_pattern=VALUE_PATTERNS,
                               nulls=RATIO_BANDS),
        confidence_at=0.80, max_level=2),
    JevRelease(
        "feature.leakage_suspect", 1, "noul",
        "Would the column in this state entry be unavailable at prediction time or encode the target "
        "(target leakage)?", None,
        criteria={"true": "The column likely leaks the target.", "false": "No sign of leakage."},
        subject_fields=_fields(**_COLUMN, availability=AVAILABILITY),
        context_fields=_fields(target=UNTRUSTED, task=TASKS),
        yes_at=0.90, max_level=1),
    JevRelease(
        "command.intent_route", 1, "choice",
        "Which quick action does the user's message ask for? Answer other when none fits.", "message",
        confidence_at=0.80, max_level=1, user_text=True),
    JevRelease(
        "proposal.completeness", 1, "score",
        "How complete is this proposal for a reviewer?", "proposal",
        criteria=["missing most parts", "has a kind and little else", "has a rationale or evidence",
                  "has rationale and evidence", "complete with citations and a revert path"],
        context_fields=_fields(proposal_kind=PROPOSAL_KINDS, validator_verdict=("accepted", "rejected"),
                               has_rationale=BOOL, has_evidence=BOOL, has_revert=BOOL, citations=COUNT_BANDS),
        max_level=0),
)})


def release_for(purpose: str, version: int | str | None = None) -> JevRelease | None:
    release = RELEASES.get(purpose)
    if release is None or (version is not None and str(version) != str(release.version)):
        return None
    return release


def _untrusted(value: Any) -> bool:
    if isinstance(value, Untrusted):
        return True
    return (isinstance(value, dict) and set(value) == {"untrusted_text"}
            and isinstance(value["untrusted_text"], str) and len(value["untrusted_text"]) <= UNTRUSTED_TEXT_MAX_CHARS)


def _value_ok(spec: Any, value: Any) -> bool:
    if spec == UNTRUSTED:
        return _untrusted(value)
    if spec == UNTRUSTED_LIST:
        return isinstance(value, (list, tuple)) and len(value) <= 16 and all(_untrusted(item) for item in value)
    if spec == BOOL:
        return isinstance(value, bool)
    return isinstance(value, str) and value in spec  # a band / code label from the closed vocabulary


def state_violations(release: JevRelease, state: Any, column_keys: dict[str, Any]) -> list[str]:
    """Why ``state`` is not a legal Jev state for ``release`` (empty = legal): only the
    release's fields, values from their vocabularies, per-column entries under
    ``c<N>`` keys mapped to a source column, untrusted context fields mapped to one too."""

    if not isinstance(state, dict):
        return ["state must be an object"]
    problems = []
    for key, value in state.items():
        if isinstance(key, str) and SUBJECT_KEY.fullmatch(key) and release.subject_fields:
            if key not in column_keys:
                problems.append(f"{key}: a per-column entry needs its column")
            elif not isinstance(value, dict) or not value:
                problems.append(f"{key}: must be an object")
            else:
                problems += [f"{key}.{name}: not allowed" for name, item in value.items()
                             if name not in release.subject_fields or not _value_ok(release.subject_fields[name], item)]
        elif key in release.context_fields:
            spec = release.context_fields[key]
            if not _value_ok(spec, value) or ((spec == UNTRUSTED) != (key in column_keys)):
                problems.append(f"{key}: not allowed")
        else:
            problems.append(f"{key!s:.64}: not a field of {release.purpose}")
    return problems


def sync_jev_releases(db: Session) -> dict[str, list[str]]:
    """Idempotent: one released ``prompt_releases`` row per purpose release; an older
    released version is retired; a row whose digest differs is reported, never rewritten."""

    result: dict[str, list[str]] = {"created": [], "retired": [], "unchanged": [], "mismatched": []}
    for release in RELEASES.values():
        label = f"{release.agent_key}@v{release.version}"
        rows = list(db.scalars(select(PromptRelease).where(PromptRelease.agent_key == release.agent_key)))
        for row in rows:
            if row.version != release.version and row.status == "released":
                row.status = "retired"
                result["retired"].append(f"{row.agent_key}@v{row.version}")
        current = next((row for row in rows if row.version == release.version), None)
        if current is None:
            db.add(PromptRelease(
                agent_key=release.agent_key, version=release.version, prompt_digest=release.digest(),
                output_schema_digest=_answers_schema_digest(), model_hint=release.model_id, status="released",
                released_at=datetime.now(UTC), notes="Jev purpose release (app/agents/semantic/releases.py)",
            ))
            result["created"].append(label)
        elif current.prompt_digest.strip() != release.digest():
            result["mismatched"].append(label)
        else:
            result["unchanged"].append(label)
    db.flush()
    return result


def _answers_schema_digest() -> str:
    from app.agents.gateway.contract import SemanticAnswers
    from app.agents.prompt_releases import output_schema_digest

    return output_schema_digest(SemanticAnswers)


__all__ = ["JEV_MODEL_ID", "RELEASES", "JevRelease", "cardinality_band", "count_band", "ratio_band",
           "release_for", "state_violations", "sync_jev_releases"]
