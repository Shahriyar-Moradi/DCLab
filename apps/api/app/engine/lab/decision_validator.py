"""Second-line check on a missing-value decision before it can override auto_prepare.

The LLM's structured decision is not trusted on its own. This module re-reads
the evidence object and accepts the decision only when:

- the action is one of the enum values listed in missing_value_v1.py
- every cited evidence field exists on the evidence object
- the *value* of that field actually supports the claimed action (existence
  is not enough — an empty co-occurrence list does not justify domain_fill)
- stated confidence is at least MIN_CONFIDENCE

Anything else is a reject with a reason. The caller then keeps the existing
rule-engine action from auto_prepare.py, unchanged.
"""

from __future__ import annotations

from dataclasses import dataclass, fields
from typing import Any, Literal

from app.engine.lab.auto_prepare import DROP_ROWS_MAX_FRACTION, DROP_ROWS_MIN_ABSOLUTE
from app.engine.lab.evidence import ColumnEvidence, MissingnessCooccurrence
from app.engine.lab.llm_client import MissingValueDecision
from app.engine.lab.prompts.missing_value_v1 import SYSTEM_PROMPT

# Reject below this. 0.7 means the model has to be at least reasonably sure;
# a coin-flip or hedged answer is not enough to override auto_prepare.
MIN_CONFIDENCE = 0.7

# Same bar the evidence builder uses before it records a co-occurrence flag.
_COOCCURRENCE_SUPPORT = 0.8

_EVIDENCE_FIELD_NAMES = {item.name for item in fields(ColumnEvidence)}


def _allowed_actions() -> frozenset[str]:
    for line in SYSTEM_PROMPT.splitlines():
        if "|" not in line:
            continue
        parts = tuple(part.strip() for part in line.split("|") if part.strip())
        if len(parts) >= 2 and all(all(char.islower() or char == "_" for char in part) for part in parts):
            return frozenset(parts)
    raise RuntimeError("missing_value_v1 prompt does not list an action enum")


ALLOWED_ACTIONS = _allowed_actions()


@dataclass(frozen=True)
class ValidationResult:
    verdict: Literal["accept", "reject"]
    reason: str


def validate_decision(evidence: ColumnEvidence, decision: MissingValueDecision) -> ValidationResult:
    """Accept a structured decision only if evidence actually backs the claim."""
    action = getattr(decision, "action", None)
    if action not in ALLOWED_ACTIONS:
        return _reject(f"action {action!r} is not in the missing_value_v1 enum")

    confidence = getattr(decision, "confidence", None)
    if isinstance(confidence, bool) or not isinstance(confidence, (int, float)):
        return _reject("decision did not state a numeric confidence")
    if float(confidence) < MIN_CONFIDENCE:
        return _reject(
            f"confidence {float(confidence)} is below MIN_CONFIDENCE={MIN_CONFIDENCE}"
        )

    cited = getattr(decision, "evidence_field", None)
    if not isinstance(cited, str) or cited not in _EVIDENCE_FIELD_NAMES:
        return _reject(f"cited field {cited!r} does not exist on the evidence object")

    support_reason = _claim_unsupported(evidence, decision, action, cited)
    if support_reason:
        return _reject(support_reason)

    return ValidationResult(verdict="accept", reason="")


def _reject(reason: str) -> ValidationResult:
    return ValidationResult(verdict="reject", reason=reason)


def _claim_unsupported(
    evidence: ColumnEvidence,
    decision: MissingValueDecision,
    action: str,
    cited: str,
) -> str | None:
    value = getattr(evidence, cited)
    fill_value = getattr(decision, "fill_value", None)

    if action == "domain_fill":
        return _domain_fill_unsupported(evidence, cited, value, fill_value)
    if action == "drop_rows":
        return _drop_rows_unsupported(evidence, cited, value)
    if action in {"impute_mean", "impute_median"}:
        return _numeric_impute_unsupported(evidence, cited, value)
    if action == "impute_most_frequent":
        return _most_frequent_unsupported(evidence, cited, value)
    return f"action {action!r} has no evidence check"


def _domain_fill_unsupported(
    evidence: ColumnEvidence,
    cited: str,
    value: Any,
    fill_value: Any,
) -> str | None:
    if cited == "missingness_cooccurrence":
        flags = value if isinstance(value, list) else []
        strong = [flag for flag in flags if _is_strong_cooccurrence(flag)]
        if not strong:
            return (
                "missingness_cooccurrence does not support domain_fill: "
                "no exact or high-fraction co-occurrence is recorded"
            )
        if fill_value is None:
            return "domain_fill requires a fill_value taken from the evidence"
        if not any(_values_match(fill_value, flag.other_value) for flag in strong):
            return (
                f"claimed fill_value {fill_value!r} does not match any "
                "missingness_cooccurrence.other_value"
            )
        return None

    if cited == "sample_rows":
        rows = value if isinstance(value, list) else []
        if not _sample_rows_show_stable_missing_pattern(evidence.column, rows):
            return "sample_rows do not show a stable missingness pattern that supports domain_fill"
        if fill_value is None:
            return "domain_fill requires a fill_value taken from the evidence"
        return None

    return f"cited field {cited!r} does not support domain_fill"


def _is_strong_cooccurrence(flag: Any) -> bool:
    if not isinstance(flag, MissingnessCooccurrence):
        return False
    if flag.exact_match:
        return True
    return (
        flag.fraction_of_missing >= _COOCCURRENCE_SUPPORT
        and flag.fraction_of_value >= _COOCCURRENCE_SUPPORT
    )


def _sample_rows_show_stable_missing_pattern(column: str, rows: list[Any]) -> bool:
    if not rows or not all(isinstance(row, dict) for row in rows):
        return False
    if not all(_is_missing_sample(row.get(column)) for row in rows):
        return False
    partners = [key for key in rows[0] if key != column]
    if not partners:
        return False
    for key in partners:
        observed = [row.get(key) for row in rows]
        if len(set(_hashable(item) for item in observed)) == 1:
            return True
    return False


def _drop_rows_unsupported(evidence: ColumnEvidence, cited: str, value: Any) -> str | None:
    if cited == "missing_fraction":
        if not isinstance(value, (int, float)) or isinstance(value, bool):
            return "missing_fraction is not numeric"
        if float(value) <= 0:
            return "missing_fraction is 0 and does not support drop_rows"
        small_enough = float(value) < DROP_ROWS_MAX_FRACTION or evidence.missing_count < DROP_ROWS_MIN_ABSOLUTE
        if not small_enough:
            return (
                f"missing_fraction {float(value)} is not small enough to support drop_rows "
                f"(need < {DROP_ROWS_MAX_FRACTION} unless missing_count < {DROP_ROWS_MIN_ABSOLUTE})"
            )
        return None
    if cited == "missing_count":
        if not isinstance(value, (int, float)) or isinstance(value, bool) or int(value) <= 0:
            return "missing_count does not support drop_rows"
        small_enough = (
            evidence.missing_fraction < DROP_ROWS_MAX_FRACTION or int(value) < DROP_ROWS_MIN_ABSOLUTE
        )
        if not small_enough:
            return f"missing_count {int(value)} is not small enough to support drop_rows"
        return None
    return f"cited field {cited!r} does not support drop_rows"


def _numeric_impute_unsupported(evidence: ColumnEvidence, cited: str, value: Any) -> str | None:
    if cited == "dtype":
        if not _is_numeric_dtype(str(value)):
            return f"dtype {value!r} is not numeric and does not support mean/median impute"
        if evidence.missing_count <= 0:
            return "no missing values to impute"
        return None
    if cited in {"missing_count", "missing_fraction"}:
        if evidence.missing_count <= 0:
            return "no missing values to impute"
        if not _is_numeric_dtype(evidence.dtype):
            return f"dtype {evidence.dtype!r} is not numeric and does not support mean/median impute"
        return None
    return f"cited field {cited!r} does not support mean/median impute"


def _most_frequent_unsupported(evidence: ColumnEvidence, cited: str, value: Any) -> str | None:
    if cited == "dtype":
        if _is_numeric_dtype(str(value)):
            return f"dtype {value!r} is numeric and does not support impute_most_frequent"
        if evidence.missing_count <= 0:
            return "no missing values to impute"
        return None
    if cited in {"missing_count", "missing_fraction"}:
        if evidence.missing_count <= 0:
            return "no missing values to impute"
        if _is_numeric_dtype(evidence.dtype):
            return f"dtype {evidence.dtype!r} is numeric and does not support impute_most_frequent"
        return None
    return f"cited field {cited!r} does not support impute_most_frequent"


def _is_numeric_dtype(dtype: str) -> bool:
    key = dtype.lower()
    if "object" in key or "str" in key or "bool" in key or "category" in key:
        return False
    return any(token in key for token in ("int", "float", "uint", "double", "complex"))


def _is_missing_sample(value: Any) -> bool:
    if value is None:
        return True
    if isinstance(value, str) and not value.strip():
        return True
    try:
        return value != value  # NaN
    except Exception:
        return False


def _values_match(left: Any, right: Any) -> bool:
    if left == right:
        return True
    try:
        return float(left) == float(right)
    except (TypeError, ValueError):
        return str(left).strip() == str(right).strip()


def _hashable(value: Any) -> Any:
    if isinstance(value, (list, dict)):
        return repr(value)
    try:
        hash(value)
        return value
    except TypeError:
        return repr(value)
