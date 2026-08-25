"""Compare naive ranking vs best-single vs fusion vs planted oracle.

Profit and regret are evaluated under planted P(Y|do(a)), not observational P(Y).
"""

from __future__ import annotations

from typing import Any

import pandas as pd

from app.sim.decide import decide_simulated, expected_value, action_economic_value, action_probability


def _true_ev(entity: dict[str, Any], action: str, policy: dict[str, Any]) -> float:
    planted = entity.get(policy.get("true_p0_column"))
    base_p = float(planted if planted is not None else 0.0)
    p_a = action_probability(base_p, action, policy, entity, oracle=True)
    value = action_economic_value(
        entity, action, policy, float(entity.get(policy.get("value_column") or "amount") or 0)
    )
    cost = float((policy.get("action_cost") or {}).get(action, 0.0))
    risk = float((policy.get("action_risk") or {}).get(action, 0.0))
    risk_col = policy.get("risk_value_column")
    risk_base = float(entity.get(risk_col) or value) if risk_col else value
    direction = (policy.get("objective") or {}).get("direction") or "good"
    return expected_value(
        probability=p_a,
        value=value,
        cost=cost,
        risk=risk,
        direction=direction,
        risk_base=risk_base,
    )


def _aggressive_action(policy: dict[str, Any]) -> str:
    uplifts = dict(policy.get("action_uplift") or {})
    candidates = [a for a in (policy.get("actions") or []) if a != "do_nothing"]
    if not candidates:
        return "do_nothing"
    return max(candidates, key=lambda action: abs(float(uplifts.get(action, 0.0))))


def naive_action(probability: float, policy: dict[str, Any], threshold: float) -> str:
    if probability < threshold:
        return "do_nothing"
    return _aggressive_action(policy)


def summarize_policy(
    rows: list[dict[str, Any]],
    policy: dict[str, Any],
    chosen_key: str,
) -> dict[str, Any]:
    nothing = "do_nothing"
    profits = []
    regrets = []
    actions: dict[str, int] = {}
    for row in rows:
        entity = row["entity"]
        chosen = row[chosen_key]
        oracle = row["oracle_action"]
        profit = _true_ev(entity, chosen, policy) - _true_ev(entity, nothing, policy)
        regret = _true_ev(entity, oracle, policy) - _true_ev(entity, chosen, policy)
        profits.append(profit)
        regrets.append(regret)
        actions[chosen] = actions.get(chosen, 0) + 1
    return {
        "n": len(rows),
        "profit_vs_do_nothing": round(float(sum(profits)), 2),
        "regret_vs_oracle": round(float(sum(regrets)), 2),
        "mean_profit": round(float(sum(profits) / max(len(profits), 1)), 4),
        "actions": actions,
    }


def compare_holdout(
    test_df: pd.DataFrame,
    scored: list[dict[str, Any]],
    policy: dict[str, Any],
) -> dict[str, Any]:
    probs = [row["probability"] for row in scored]
    threshold = float(pd.Series(probs).quantile(0.7)) if probs else 1.0
    prepared: list[dict[str, Any]] = []
    for row in scored:
        entity = row["entity"]
        fusion_dec = decide_simulated(entity, row["probability"], policy)
        single_dec = decide_simulated(entity, row["best_single_probability"], policy)
        oracle_dec = decide_simulated(entity, row["probability"], policy, oracle=True)
        naive = naive_action(row["probability"], policy, threshold)
        prepared.append(
            {
                "entity": entity,
                "naive_action": naive,
                "fusion_action": fusion_dec["action_key"],
                "single_action": single_dec["action_key"],
                "oracle_action": oracle_dec["action_key"],
            }
        )
    return {
        "naive": summarize_policy(prepared, policy, "naive_action"),
        "best_single": summarize_policy(prepared, policy, "single_action"),
        "fusion": summarize_policy(prepared, policy, "fusion_action"),
        "oracle": summarize_policy(prepared, policy, "oracle_action"),
        "naive_threshold": round(threshold, 4),
        "fusion_beats_best_single_on_profit": (
            summarize_policy(prepared, policy, "fusion_action")["profit_vs_do_nothing"]
            > summarize_policy(prepared, policy, "single_action")["profit_vs_do_nothing"]
        ),
    }
