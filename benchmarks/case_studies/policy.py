"""Generic decision-policy evaluator for the benchmark harness.

Same YAML shape and spirit as ``app.services.decision_service`` (version /
objective / constraints / actions / action_uplift — uplift is a PLACEHOLDER
expected-incremental-value fraction until real treatment-effect data exists,
exactly the same caveat that already applies to the M1 opportunity policy).
This is a separate, more general evaluator rather than a reuse of
``decision_service.decide()`` because that function hardcodes an
"opportunity" shape (``amount``, ``sales_rep_available``,
``last_contact_days_ago``) that half of these six case studies don't have —
forcing every case study into that shape would be a worse distortion than
writing six lines of generic policy logic.

Decision rule, deliberately simple and auditable: each non-default action
has its own minimum-score threshold ("tiered eligibility"). An entity
qualifies for the highest-threshold action whose threshold its score meets
or exceeds; if it meets none, it gets ``default_action``. This is separate
from *valuation*: once an action is chosen, its expected/realized value is
``value * action_uplift[action]`` — the uplift fraction is not used to pick
the action, only to value the pick, exactly mirroring how ``action_uplift``
is used (and captioned as a placeholder) in the existing M1 policy.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import yaml


@dataclass
class PolicyConfig:
    version: str
    objective: str
    score_basis: str  # "probability" (binary tasks) or "predicted_value" (regression tasks)
    default_action: str
    actions: list[str]
    action_thresholds: dict[str, float]
    action_uplift: dict[str, float]
    value_column: str | None = None
    flat_value: float = 1.0
    notes: str = ""

    @classmethod
    def from_yaml(cls, path: Path) -> "PolicyConfig":
        raw = yaml.safe_load(Path(path).read_text()) or {}
        return cls(
            version=str(raw["version"]),
            objective=str(raw["objective"]),
            score_basis=str(raw["score_basis"]),
            default_action=str(raw["default_action"]),
            actions=list(raw["actions"]),
            action_thresholds={str(k): float(v) for k, v in (raw.get("action_thresholds") or {}).items()},
            action_uplift={str(k): float(v) for k, v in (raw.get("action_uplift") or {}).items()},
            value_column=raw.get("value_column"),
            flat_value=float(raw.get("flat_value", 1.0)),
            notes=str(raw.get("notes") or ""),
        )


def choose_action(score: float, policy: PolicyConfig) -> str:
    """Highest-threshold non-default action whose threshold `score` meets; else default."""
    qualifying = [
        (threshold, action)
        for action, threshold in policy.action_thresholds.items()
        if score >= threshold
    ]
    if not qualifying:
        return policy.default_action
    qualifying.sort(reverse=True)
    return qualifying[0][1]


def decide(*, score: float, entity_value: float, policy: PolicyConfig) -> dict:
    """One decision for one entity.

    ``score``: probability (binary) or predicted_value (regression) — used
    both for eligibility tiering and, for binary tasks, multiplied by
    ``entity_value`` to get the dollar baseline.
    ``entity_value``: for binary tasks, the value_column/flat_value for this
    entity (a dollar figure); for regression tasks, unused (predicted_value
    already IS the dollar baseline) — pass the same number as `score`.
    """
    action = choose_action(score, policy)
    baseline_value = entity_value * score if policy.score_basis == "probability" else entity_value
    uplift = policy.action_uplift.get(action, 0.0)
    expected_value = baseline_value * uplift
    return {"action": action, "expected_value": expected_value, "baseline_value": baseline_value}
