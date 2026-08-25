"""Expected-value action selection for simulation policies.

Uplifts, costs, and risk penalties come from YAML and are planted / simulated.
This is not a causal estimator. `do_nothing` is a first-class action.
"""

from __future__ import annotations

from typing import Any


def _clip01(value: float) -> float:
    return max(0.0, min(1.0, float(value)))


def _get(entity: Any, name: str, default: Any = None) -> Any:
    if isinstance(entity, dict):
        return entity.get(name, default)
    return getattr(entity, name, default)


def member_agreement(member_probabilities: dict[str, float]) -> float:
    values = [float(v) for v in member_probabilities.values()]
    if len(values) < 2:
        return 1.0
    mean = sum(values) / len(values)
    var = sum((v - mean) ** 2 for v in values) / len(values)
    std = var**0.5
    return round(_clip01(1.0 - 2.0 * std), 4)


def _direction(policy: dict[str, Any]) -> str:
    objective = policy.get("objective") or {}
    return str(objective.get("direction") or "good")


def _uplift_for(entity: Any, action: str, policy: dict[str, Any]) -> float:
    by_entity = policy.get("action_uplift_by_entity") or {}
    external_id = str(_get(entity, "external_id") or "")
    if external_id in by_entity and action in by_entity[external_id]:
        return float(by_entity[external_id][action])
    return float((policy.get("action_uplift") or {}).get(action, 0.0))


def action_probability(
    base_p: float,
    action: str,
    policy: dict[str, Any],
    entity: Any,
    *,
    oracle: bool = False,
) -> float:
    if oracle:
        columns = policy.get("action_probability_column") or {}
        column = columns.get(action)
        planted = _get(entity, column) if column else None
        if planted is not None:
            return _clip01(float(planted))
        true_p0 = _get(entity, policy.get("true_p0_column"))
        if true_p0 is not None:
            return _clip01(float(true_p0) + _uplift_for(entity, action, policy))
    scales = policy.get("action_scale") or {}
    if action in scales:
        return _clip01(base_p * float(scales[action]))
    return _clip01(base_p + _uplift_for(entity, action, policy))


def action_economic_value(entity: Any, action: str, policy: dict[str, Any], default_value: float) -> float:
    named = (policy.get("action_value") or {}).get(action)
    if named is not None:
        return float(named)
    return default_value


def expected_value(
    *,
    probability: float,
    value: float,
    cost: float,
    risk: float,
    direction: str,
    risk_base: float | None = None,
) -> float:
    penalty = risk * (risk_base if risk_base is not None else value)
    if direction == "harm":
        return -value * probability - cost - penalty
    return value * probability - cost - penalty


def decide_simulated(
    entity: Any,
    probability: float,
    policy: dict[str, Any],
    *,
    oracle: bool = False,
) -> dict[str, Any]:
    """Pick the action with highest expected net value, including do_nothing."""
    direction = _direction(policy)
    value_col = str(policy.get("value_column") or "amount")
    default_value = float(_get(entity, value_col) or 0.0)
    risk_value_col = policy.get("risk_value_column")
    risk_base = float(_get(entity, risk_value_col) or default_value) if risk_value_col else default_value
    costs = dict(policy.get("action_cost") or {})
    risks = dict(policy.get("action_risk") or {})
    actions = list(policy.get("actions") or ["do_nothing"])

    base_p = float(probability)
    if oracle:
        planted = _get(entity, policy.get("true_p0_column"))
        if planted is not None:
            base_p = float(planted)

    table: list[dict[str, Any]] = []
    best_action = "do_nothing"
    best_ev = float("-inf")

    for action in actions:
        p_a = action_probability(base_p, action, policy, entity, oracle=oracle)
        value_a = action_economic_value(entity, action, policy, default_value)
        cost = float(costs.get(action, 0.0))
        risk = float(risks.get(action, 0.0))
        ev = expected_value(
            probability=p_a,
            value=value_a,
            cost=cost,
            risk=risk,
            direction=direction,
            risk_base=risk_base,
        )
        row = {
            "action": action,
            "probability": round(p_a, 4),
            "value": round(value_a, 2),
            "cost": round(cost, 2),
            "risk_penalty": round(risk * risk_base, 2),
            "expected_value": round(ev, 2),
        }
        table.append(row)
        if ev > best_ev:
            best_ev = ev
            best_action = action

    nothing = next((row for row in table if row["action"] == "do_nothing"), None)
    incremental = round(best_ev - (nothing["expected_value"] if nothing else 0.0), 2)
    return {
        "recommended_action": best_action.upper(),
        "action_key": best_action,
        "expected_value": round(best_ev, 2),
        "incremental_value": incremental,
        "action_table": table,
        "policy_version": str(policy["version"]),
        "uplift_is_simulated": True,
        "direction": direction,
    }
