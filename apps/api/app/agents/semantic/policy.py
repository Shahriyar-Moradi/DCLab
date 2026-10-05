"""The ADR 0008 §5 agreement table, applied deterministically (pure functions).

| Jev vs rule | In band | L0 | L1 | L2 |
| agree | yes | rule (agree) | rule | same value applied (``ai``) |
| disagree | yes | rule (disagree) | rule + review item (``review``) | Jev value if the validator accepts, else rule |
| any | no (abstain) | rule | rule | rule |
| unavailable (timeout / breaker / budget / switch / policy) | — | rule | rule | rule |

The level is ``governance.policy.effective_level`` keyed by (prompt release, model id),
lowered to the release's max level and the §1b ceiling of the answer
(``answer_kind``); every purpose starts at L0. Nothing here reads holdout data,
computes a metric or excludes a column.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from app.agents.semantic.releases import JevRelease

_MODELED_ROLES = ("numeric", "categorical_code")
_EXCLUDING_ROLES = ("identifier", "free_text", "other")
_LEAKAGE_SUSPECT = ("exclude", "review_flag")


@dataclass(frozen=True)
class Banded:
    in_acting_band: bool
    value: Any  # the answer in the rule's vocabulary (None when abstaining)


def band(release: JevRelease, answer: dict[str, Any] | None, confidence: float | None) -> Banded:
    """The release's acting band: noul p >= yes_at → yes, p <= no_at → no; choice
    confidence >= confidence_at; score is display only (never in band)."""

    if answer is None:
        return Banded(False, None)
    value = answer.get("value")
    if release.primitive == "noul":
        if release.yes_at is not None and value >= release.yes_at:
            return Banded(True, True)
        if release.no_at is not None and value <= release.no_at:
            return Banded(True, False)
        return Banded(False, None)
    if release.primitive == "choice":
        in_band = confidence is not None and release.confidence_at is not None and confidence >= release.confidence_at
        return Banded(in_band, value if in_band else None)
    return Banded(False, value)


def _comparable(release: JevRelease, rule: Any) -> Any:
    if release.purpose == "feature.leakage_suspect":
        return rule in _LEAKAGE_SUSPECT or rule is True
    return rule


def agreement(release: JevRelease, rule: Any, banded: Banded) -> str:
    if not banded.in_acting_band:
        return "abstain"
    return "agree" if _comparable(release, rule) == banded.value else "disagree"


def answer_kind(release: JevRelease, rule: Any, ai_value: Any) -> str | None:
    """ADR 0008 §1b kind of the AI answer (sets its ceiling at mixed points)."""

    if release.purpose == "column.semantic_role":
        if ai_value in _EXCLUDING_ROLES:
            return "exclusion"
        if ai_value in _MODELED_ROLES and rule in _MODELED_ROLES:
            return "role_numeric_categorical"
    return None


@dataclass(frozen=True)
class Applied:
    policy_outcome: str  # rule | ai | review
    value_used: Any


def apply(level: int, agreement_value: str, rule: Any, ai_value: Any, *, validator_ok: bool) -> Applied:
    """One row of the table. ``validator_ok``: the caller's deterministic validator
    accepted the Jev value (consulted only for an L2 disagreement)."""

    if agreement_value in ("unavailable", "abstain", "off") or level <= 0:
        return Applied("rule", rule)
    if level == 1:
        return Applied("review" if agreement_value == "disagree" else "rule", rule)
    if agreement_value == "agree":
        return Applied("ai", ai_value)
    return Applied("ai", ai_value) if validator_ok else Applied("rule", rule)


__all__ = ["Applied", "Banded", "agreement", "answer_kind", "apply", "band"]
