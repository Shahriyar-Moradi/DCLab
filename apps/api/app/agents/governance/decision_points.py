"""Decision-point registry (ADR 0008 §1) and answer ceilings (§1b). Frozen code data.

Every point defaults to L0; ``cap`` is the highest level code ever allows; an
answer's ceiling (§1b) can only lower it. ``registry_violations()`` returns the
§1 invariants that fail (tests assert it is empty).
"""

from __future__ import annotations

from dataclasses import dataclass
from types import MappingProxyType

AUTO_TRAIN_STAGES = frozenset(
    {
        "run_target_resolution",
        "run_column_roles",
        "run_train_only_decisions",
        "run_holdout_lock",
        "run_preprocessing_setup",
    }
)
# Effects that exclude a column, lock a spec, create a split plan, move a ref,
# release a model or execute a write tool: cap <= L1 (ADR 0008 §1 invariants).
HIGH_IMPACT_EFFECTS = frozenset(
    {
        "exclusion",
        "proposal:problem_spec",
        "split_plan",
        "ref_move",
        "proposal:ref_move",
        "proposal:model_release",
        "write_tool",
    }
)

# §1b ceilings per answer value / change kind.
ANSWER_CEILINGS = MappingProxyType(
    {
        "role_numeric_categorical": 2,
        "impute_median_most_frequent": 2,
        "family_subset": 2,
        "time_budget": 2,
        "datetime_extract_modeled": 2,
        "fixed_hyperparameter": 1,
        "fixed_class_weighting": 1,
        "exclusion": 1,
        "reinclusion": 1,
        "spec_change": 1,
        "domain_fill": 1,
        "datetime_extract_unmodeled": 1,
    }
)


@dataclass(frozen=True)
class DecisionPoint:
    key: str
    stage: str
    pattern: str  # ai_before | ai_after | cross_check
    ai_kind: str  # jev:<purpose> | agent:<key> | none
    rule_id: str | None
    effects: tuple[str, ...]
    outcome_scope: str  # none | cv (never holdout)
    evidence_partition: str  # metadata | train
    cap: int
    phase: str
    # §1b answer kinds that apply; an answer of another kind gets ceiling 1.
    answer_kinds: frozenset[str] = frozenset()
    default_level: int = 0


def _p(key, stage, pattern, ai, rule, effects, scope, partition, cap, phase, kinds=()) -> DecisionPoint:
    return DecisionPoint(key, stage, pattern, ai, rule, tuple(effects), scope, partition, cap, phase,
                         frozenset(kinds))


_LEAD_TOOLS = (
    "propose_problem_spec",
    "record_decision",
    "move_ref",
    "run_experiment",
    "branch_experiment",
    "predict",
)

_POINTS = (
    _p("target.column", "run_target_resolution", "ai_before", "agent:dataset_investigator",
       "lab_decision_ledger.record_target_selection", ["proposal:problem_spec"], "none", "metadata", 1, "P6.9"),
    _p("spec.objective", "propose_problem_spec", "ai_before", "agent:experiment_planner",
       "metric_planner.default_metric", ["proposal:problem_spec"], "none", "metadata", 1, "P6.4"),
    _p("column.is_identifier", "run_column_roles", "cross_check", "jev:column.is_identifier",
       "schema_inference.identifier.v1", ["exclusion"], "none", "metadata", 1, "P6.7"),
    _p("column.semantic_role", "run_column_roles", "cross_check", "jev:column.semantic_role",
       "split_column_roles.v3", ["branch_change:feature_transform_add"], "none", "metadata", 2, "P6.7",
       ["role_numeric_categorical", "exclusion"]),
    _p("column.missing_value_action", "run_train_only_decisions", "ai_before",
       "agent:dataset_investigator", "missing_value_rules.v1", ["branch_change:feature_transform_add"],
       "none", "train", 2, "P6.9", ["impute_median_most_frequent", "exclusion", "domain_fill"]),
    _p("feature.leakage_suspect", "run_train_only_decisions", "cross_check", "jev:feature.leakage_suspect",
       "leakage_auditor.v1", ["flag"], "none", "train", 1, "P6.7"),
    _p("split.strategy", "run_holdout_lock", "ai_before", "agent:experiment_planner",
       "holdout_planner.v1", ["split_plan"], "none", "metadata", 1, "P6.9"),
    _p("training.families_budget", "run_preprocessing_setup", "ai_before", "agent:experiment_planner",
       "portfolio.v1", ["search_config"], "none", "train", 2, "P6.9",
       ["family_subset", "time_budget", "fixed_hyperparameter", "fixed_class_weighting"]),
    _p("experiment.review", "agents.run", "ai_after", "agent:experiment_critic", "pipeline_verifier.v1",
       ["none"], "cv", "train", 1, "P6.4"),
    _p("improve.next_action", "labs.improve", "cross_check", "agent:improvement_hypothesis",
       "rule_proposer.v1", ["proposal:experiment_change"], "cv", "train", 1, "P6.5"),
    _p("ops.diagnose", "monitoring.drift_window", "ai_after", "agent:dataset_investigator",
       "drift_finding.v1", ["notify"], "cv", "train", 3, "P7.8"),
    _p("ops.retrain", "ops.agent", "ai_before", "agent:ops", None, ["job:experiments.run"], "none",
       "metadata", 2, "P7.8"),
    _p("ops.release", "ops.agent", "ai_before", "agent:ops", "release_gate.v1",
       ["proposal:model_release"], "cv", "train", 1, "P7.8"),
    _p("ops.rollback", "ops.agent", "ai_before", "agent:ops", "ops.rollback_threshold.v1",
       ["proposal:ref_move"], "cv", "train", 1, "P7.8"),
    _p("command.intent_route", "assistant.turn", "ai_before", "jev:command.intent_route", None,
       ["none"], "none", "metadata", 1, "P6.7"),
    _p("proposal.completeness", "proposal.validated", "ai_after", "jev:proposal.completeness",
       "validator_verdict", ["none"], "none", "metadata", 0, "P6.7"),
    *(
        _p(f"lead.{tool}", "assistant.turn", "ai_before", "agent:lead", None, ["write_tool"], "cv",
           "metadata", 1, "P6.3-B")
        for tool in _LEAD_TOOLS
    ),
)

REGISTRY: MappingProxyType[str, DecisionPoint] = MappingProxyType({p.key: p for p in _POINTS})


def answer_ceiling(key: str, answer_kind: str | None = None) -> int:
    """§1b: the level an answer may reach at this point (never above the cap; unknown point → 0)."""

    point = REGISTRY.get(key)
    if point is None:
        return 0
    if not point.answer_kinds:
        return point.cap
    # Mixed points: an unknown or missing answer kind takes the strictest ceiling.
    ceiling = ANSWER_CEILINGS.get(answer_kind, 1) if answer_kind in point.answer_kinds else 1
    return min(point.cap, ceiling)


def registry_violations() -> list[str]:
    problems: list[str] = []
    for point in REGISTRY.values():
        if point.outcome_scope not in ("none", "cv"):
            problems.append(f"{point.key}: outcome_scope {point.outcome_scope}")
        if point.default_level != 0:
            problems.append(f"{point.key}: default level must be L0")
        if not 0 <= point.cap <= 3:
            problems.append(f"{point.key}: cap out of range")
        if HIGH_IMPACT_EFFECTS & set(point.effects) and point.cap > 1:
            problems.append(f"{point.key}: high-impact effect with cap > L1")
        if point.stage in AUTO_TRAIN_STAGES and point.cap > 2:
            problems.append(f"{point.key}: auto-train stage with cap > L2")
        if point.evidence_partition not in ("metadata", "train"):
            problems.append(f"{point.key}: evidence_partition {point.evidence_partition}")
        for kind in point.answer_kinds:
            if kind not in ANSWER_CEILINGS:
                problems.append(f"{point.key}: unknown answer kind {kind}")
        if "exclusion" in point.answer_kinds and ANSWER_CEILINGS["exclusion"] > 1:
            problems.append(f"{point.key}: exclusions above L1")
    return problems
