"""AI-before decision points inside auto-train (P6.9-A step A2; ADR 0008 §1, §1b, §2, §2c).

``target.column``, ``spec.objective``, ``split.strategy``, ``training.families_budget`` and
``column.missing_value_action``: the agent's answers travel into the run in its ``plan``
(``run_plan_service``); the worker never waits on an LLM. Each point computes the rule
answer first, validates the plan's answer deterministically against the current graph,
and resolves through ``decision_points.resolve_plan_point`` (human-accepted plan → human
input; applied plan → L2 kinds at L2 only). No plan, AI off or a branch: nothing here runs
(no event, no record), except that a branch re-applies its parent's applied
``column.missing_value_action`` / ``training.families_budget`` values (``ai_inherited``).

``split.strategy`` never changes the holdout in a run: a plan that agrees is recorded; a
valid change (a tighter structure, or a larger holdout fraction ≥ the code-owned floor)
stops the run at ``needs_input`` before ``run_holdout_lock`` (a person answers; executing
a forced group/time structure needs the holdout and validation planners to accept it, an
engine change); a loosening or a fraction below the floor is refused. Once a SplitPlan
exists for (source dataset, target, task), a plan's split answer is refused and pending
plans with a split answer are superseded.
"""

from __future__ import annotations

from dataclasses import replace
from typing import Any

import pandas as pd
from sqlalchemy import select

from app.db.models import ClientLabUpload, SplitPlan
from app.domain.execution_requests import SPLIT_CONFIRMATION_REQUIRED
from app.domain.lab_run_stages import NEEDS_INPUT
from app.domain.run_plans import HOLDOUT_FRACTION_FLOOR, IMPUTE_ACTIONS, RULE_TRAINING_SECONDS
from app.engine.lab.auto_prepare import MAX_CATEGORICAL_CARDINALITY
from app.engine.lab.schema_inference import IDENTIFIER_UNIQUE_RATIO, choose_target_deterministically
from app.engine.modeling.holdout_planner import RANDOM, STRATIFIED_RANDOM, HoldoutPlan
from app.engine.modeling.objective import ObjectiveError, parse_objective
from app.services.auto_train.context import StageHalt, service_module
from app.services.auto_train.decision_points import (
    FAMILIES,
    MISSING,
    OBJECTIVE,
    SPLIT,
    TARGET,
    decision_points,
    plan_answers,
    plan_refusal,
    resolve_plan_point,
)
from app.services.decision_point_service import resolve
from app.services.run_plan_service import supersede_plans

_LOOSE = (RANDOM, STRATIFIED_RANDOM)


def resolve_target_point(ctx: Any, *, frame: pd.DataFrame, columns: list[str], requested: str | None,
                         spec_target: str | None) -> str | None:
    """The target to request: the human-accepted plan's target when the request and spec
    name none (L1: an applied plan never carries it); else ``requested``."""

    answer = plan_answers(ctx, TARGET)
    if answer is None:
        return requested
    rule = requested or spec_target or choose_target_deterministically(frame, list(columns)).column
    reasons = [] if answer in columns else ["unknown_column"]
    if (requested or spec_target) and answer != (requested or spec_target):
        reasons.append("conflicts_with_request_or_spec")
    resolution = resolve_plan_point(ctx, TARGET, {"target_column": rule}, {"target_column": answer},
                                    reasons={"target_column": reasons})
    used = resolution.used()["target_column"] if resolution is not None else rule
    return used if used != rule else requested


def resolve_objective_point(ctx: Any, task_type: str, objective: Any) -> Any:
    """The run objective with the human-accepted plan's primary metric (validated)."""

    answer = plan_answers(ctx, OBJECTIVE)
    if answer is None:
        return objective
    rule = objective.primary_metric if objective is not None else None
    try:
        metric = parse_objective(task_type, primary_metric=answer).primary_metric
        reasons = []
    except ObjectiveError:
        metric, reasons = answer, ["invalid_metric_for_task"]
    reasons += plan_refusal(ctx, "primary_metric")
    resolution = resolve_plan_point(ctx, OBJECTIVE, {"primary_metric": rule}, {"primary_metric": metric},
                                    reasons={"primary_metric": reasons})
    used = resolution.used()["primary_metric"] if resolution is not None else rule
    if used == rule:
        return objective
    if objective is None:
        return parse_objective(task_type, primary_metric=used)
    return replace(objective, primary_metric=used, primary_metric_reason="accepted run plan")


def _split_value(plan: Any) -> dict[str, Any]:
    if isinstance(plan, HoldoutPlan):
        return {"strategy": plan.strategy, "group_column": plan.group_column, "time_column": plan.time_column,
                "test_size": round(float(plan.test_size), 4)}
    return {**plan.model_dump(mode="json"), "test_size": plan.test_size}


def _normalized(value: dict[str, Any], rule: HoldoutPlan) -> dict[str, Any]:
    """A plan split answer in the rule's shape: an omitted group/time column on the rule's
    strategy is the rule's; the fraction is rounded like the rule's."""

    same = value.get("strategy") == rule.strategy
    return {"strategy": value.get("strategy"),
            "group_column": value.get("group_column") or (rule.group_column if same else None),
            "time_column": value.get("time_column") or (rule.time_column if same else None),
            "test_size": round(float(value.get("test_size") or rule.test_size), 4)}


def split_reasons(rule: HoldoutPlan, answer: dict[str, Any], frame: pd.DataFrame, *, target: str | None = None,
                  task_type: str | None = None) -> tuple[list[str], bool]:
    """Tighten-only validator (ADR 0008 §2): (refusal reasons, structural tightening?)."""

    size = answer.get("test_size") or rule.test_size
    if size < HOLDOUT_FRACTION_FLOOR:
        return ["below_holdout_fraction_floor"], False
    strategy = answer["strategy"]
    if strategy == rule.strategy:
        same = (answer.get("group_column") in (None, rule.group_column)
                and answer.get("time_column") in (None, rule.time_column))
        return ([] if same else ["structure_columns_differ"]), False
    if rule.strategy not in _LOOSE:
        return ["loosens_split"], False  # a temporal/group rule answer never becomes looser
    if strategy in _LOOSE:
        return ["loosens_split"], False
    if strategy == "temporal_future":
        ok = answer.get("time_column") in ((rule.evidence or {}).get("time_candidates") or [])
        return ([] if ok else ["time_column_not_found_by_rule"]), ok
    column = answer.get("group_column")
    if column is None or column == target or target not in frame.columns:
        return ["group_column_invalid"], False
    from app.engine.modeling.problem_profile import build_problem_profile

    # The rule's own repeated-entity candidates (the profile plan_holdout builds), as for time.
    profile = build_problem_profile(frame, target=target, task_type=str(task_type or "binary"))
    ok = column in {item["column"] for item in profile.repeated_entity_candidates}
    return ([] if ok else ["group_column_not_found_by_rule"]), ok


def resolve_split_point(ctx: Any, *, frame: pd.DataFrame, rule: HoldoutPlan, source: Any, target: Any,
                        failure_extra: dict[str, Any]) -> HoldoutPlan:
    """The holdout plan to lock (root runs; before any stored-plan lookup). Always the
    rule's plan: a plan answer that agrees is recorded; any valid change (a structural
    tightening or a larger holdout fraction) stops the run at ``needs_input`` before the
    lock — re-choosing the comparison basis of the dataset is a person's call, and an
    in-run change would also leave later rule-only runs on a second, overlapping holdout;
    an invalid answer is refused and the run goes on."""

    answer = plan_answers(ctx, SPLIT)
    if answer is None or ctx.branch is not None:
        return rule
    db = ctx.db
    value = {**answer.model_dump(mode="json"), "test_size": answer.test_size or rule.test_size}
    exists = source is not None and db.scalar(select(SplitPlan.id).where(
        SplitPlan.workspace_id == source.workspace_id, SplitPlan.dataset_id == source.id,
        SplitPlan.target_column == target.column, SplitPlan.task_type == target.task_type).limit(1))
    if exists:
        reasons, confirm = ["plan_exists"], False
        supersede_plans(db, workspace_id=source.workspace_id, project_id=source.project_id, reason="plan_exists",
                        dataset_id=source.id, target_column=target.column)
        db.commit()
    else:
        value = _normalized(value, rule)
        reasons, _structural = split_reasons(rule, value, frame, target=target.column, task_type=target.task_type)
        confirm = not reasons and value != _split_value(rule)
    if confirm:  # a person confirms a valid change before anything is split
        reasons = ["needs_confirmation"]
    resolution = resolve_plan_point(ctx, SPLIT, {"split": _split_value(rule)}, {"split": value},
                                    reasons={"split": reasons})
    if confirm and resolution is not None:
        _needs_input(ctx, rule, value, failure_extra)
    return rule


def _needs_input(ctx: Any, rule: HoldoutPlan, value: dict[str, Any], extra: dict[str, Any]) -> None:
    from app.services.execution_request_service import mark_execution_needs_input

    db = ctx.db
    row = db.get(ClientLabUpload, ctx.upload_id)
    if row is None:
        raise StageHalt
    waiting = {"kind": "split_strategy_confirmation", "decision_point": SPLIT, "rule": _split_value(rule),
               "plan": value, "reason": "the run plan changes the split (tighter structure or a larger holdout); "
                                         "nothing is split until a person confirms (ADR 0008 §2)"}
    ctx.finish_stage(status="completed")
    service_module()._mark(db, row, status=NEEDS_INPUT, log={**extra, "split_confirmation": waiting,
                                                            "reason": waiting["reason"]})
    mark_execution_needs_input(db, upload=row, waiting=waiting, source="run_plan",
                               event_type=SPLIT_CONFIRMATION_REQUIRED)
    db.commit()
    raise StageHalt


def _missing_kind(rule: str, ai: str) -> str | None:
    if ai == "drop_column":
        return "exclusion"
    if rule == "drop_column" and ai in IMPUTE_ACTIONS:
        return "reinclusion"
    if rule in IMPUTE_ACTIONS and ai in IMPUTE_ACTIONS and rule != ai:
        return "impute_median_most_frequent"
    return None


def _missing_reasons(column: str, rule: str, ai: str, train: pd.DataFrame, *, leakage_excluded: set[str],
                     protected: set[str]) -> list[str]:
    if column not in train.columns:
        return ["unknown_column"]
    if column in protected:
        return ["protected_column"]
    if rule == "keep" and ai in IMPUTE_ACTIONS:
        return ["no_missing_values"]
    if rule not in ("drop_column", *IMPUTE_ACTIONS):
        return ["rule_action_not_switchable"]  # domain_fill and friends stay the rule's
    if rule == "drop_column" and column in leakage_excluded:
        return ["leakage_excluded_column"]
    series = train[column]
    if ai == "impute_median" and not (pd.api.types.is_numeric_dtype(series) and not pd.api.types.is_bool_dtype(series)):
        return ["not_numeric"]
    if ai == "impute_most_frequent" and (int(series.nunique(dropna=True)) > MAX_CATEGORICAL_CARDINALITY
                                         or series.nunique(dropna=True) / max(len(series), 1) > IDENTIFIER_UNIQUE_RATIO):
        return ["above_one_hot_cardinality_cap"]
    return []


def resolve_missing_point(ctx: Any, *, locked_train: pd.DataFrame, missing_plan: Any, leakage_excluded: set[str],
                          protected: set[str]) -> None:
    """Apply the plan's (root) or the parent's inherited (branch) missing-value actions to
    ``missing_plan`` (before the branch's own drop/keep overrides, which win)."""

    decisions = {item.column: item for item in missing_plan.column_decisions}
    rules = {column: item.action for column, item in decisions.items()}
    points = decision_points(ctx)
    inherited = {c: v for c, v in (points.inherited.get(MISSING) or {}).items() if c in rules}
    if ctx.branch is not None and inherited:
        reasons = {c: _missing_reasons(c, rules[c], a, locked_train, leakage_excluded=leakage_excluded,
                                       protected=protected) for c, a in inherited.items()}
        # The branch's own treatment of a column (incl. a revert) takes precedence (ADR 0008 §2).
        treatment = {"numeric": "impute_median", "categorical": "impute_most_frequent", "drop": "drop_column"}
        overrides = {c: treatment[spec["treatment"]] for c, spec in ctx.branch.columns.items()
                     if c in inherited and spec.get("treatment") in treatment}
        resolution = points.keep(ctx, resolve(
            MISSING, _off_outcome(rules), evidence_partition="metadata", overrides=overrides,
            inherited={c: v for c, v in inherited.items() if not reasons[c]}, validator_reasons=reasons))
    else:
        answers = plan_answers(ctx, MISSING) or {}
        answers = {c: a for c, a in answers.items() if c in rules}
        plan = points.plan
        resolution = resolve_plan_point(
            ctx, MISSING, rules, answers, kinds={c: _missing_kind(rules[c], a) for c, a in answers.items()},
            reasons={c: _missing_reasons(c, rules[c], a, locked_train, leakage_excluded=leakage_excluded,
                                         protected=protected) for c, a in answers.items()},
            # An applied (L2) treatment is settled against the role point first (§1b): recorded
            # in run_column_roles. A human-accepted one is final and recorded now.
            defer=plan is not None and not plan.human)
    for answer in resolution.changed() if resolution is not None else []:
        decision = decisions[answer.question_key]
        decision.action = answer.used
        if answer.used == "drop_column" and answer.question_key not in missing_plan.dropped_columns:
            missing_plan.dropped_columns.append(answer.question_key)
        elif answer.used != "drop_column" and answer.question_key in missing_plan.dropped_columns:
            missing_plan.dropped_columns.remove(answer.question_key)


_TREATMENT_ROLE = {"impute_median": "numeric", "impute_most_frequent": "categorical_code"}


def missing_treatments(ctx: Any) -> dict[str, tuple[str, bool]]:
    """Role treatments the applied missing-value actions imply (``_ROLE_TREATMENT``):
    impute_median → numeric, impute_most_frequent → categorical (ADR 0008 §1b), with
    whether a person decided them (a human-accepted plan or a branch's own change)."""

    points = decision_points(ctx)
    resolution = points.pending.get(MISSING) or points.resolved.get(MISSING)
    if resolution is None:
        return {}
    return {a.question_key: (_TREATMENT_ROLE[a.used], MISSING not in points.pending)
            for a in resolution.changed() if a.used in _TREATMENT_ROLE}


def finish_missing_point(ctx: Any, missing_plan: Any, num: list[str], cat: list[str],
                         rule_numeric: list[str]) -> tuple[list[str], list[str]]:
    """Record a deferred (applied-plan) missing-value point after the role point: a column
    in §1b conflict goes back to the rule's action; a failed record rolls the rest back."""

    points = decision_points(ctx)
    resolution = points.pending.pop(MISSING, None)
    if resolution is None:
        return num, cat
    conflicts = points.missing_conflicts
    resolution = replace(resolution, answers=tuple(
        replace(a, used=a.rule, source="rule", refusal="conflicts_with_semantic_role")
        if a.question_key in conflicts and a in resolution.changed() else a for a in resolution.answers))
    before = {a.question_key for a in resolution.changed()}
    resolution = points.keep(ctx, resolution)
    undone = (before - {a.question_key for a in resolution.changed()}) | (conflicts & before)
    decisions = {item.column: item for item in missing_plan.column_decisions}
    for answer in resolution.answers:
        if answer.question_key in undone | conflicts and answer.question_key in decisions:
            decisions[answer.question_key].action = answer.rule
    for column in undone - conflicts:  # the record failed: the treatment goes too
        num = [c for c in num if c != column]
        cat = [c for c in cat if c != column]
        (num if column in rule_numeric else cat).append(column)
    return num, cat


def resolve_families_point(ctx: Any, task_type: str) -> tuple[list[str] | None, float | None]:
    """(``SearchConfig.families``, ``max_training_seconds``): a family subset (the dummy
    baseline is always added by the generator) and a time budget ≤ the rule's (L2 kinds)."""

    from app.engine.search.generator import open_ingest_families

    rule_families = open_ingest_families(task_type)
    rules = {"families": rule_families, "max_training_seconds": RULE_TRAINING_SECONDS}
    points = decision_points(ctx)
    inherited = points.inherited.get(FAMILIES) or {}
    if ctx.branch is not None and inherited:
        # A time budget has no revert change kind: a branch runs at the rule's budget and
        # records that the parent's value was not inherited (modeled columns are unaffected).
        budget = inherited.get("max_training_seconds")
        marker = {"max_training_seconds": RULE_TRAINING_SECONDS} if budget is not None else {}
        resolution = resolve(FAMILIES, _off_outcome(rules), evidence_partition="metadata",
                             inherited={**{k: v for k, v in inherited.items() if k != "max_training_seconds"},
                                        **marker})
        resolution = points.keep(ctx, replace(resolution, answers=tuple(
            replace(a, source="rule", used=a.rule, ai=budget, refusal="budget_not_inherited")
            if a.question_key == "max_training_seconds" and budget is not None else a
            for a in resolution.answers)))
    else:
        answers = plan_answers(ctx, FAMILIES) or {}
        reasons = {name: plan_refusal(ctx, name) for name in answers}
        if "families" in answers:
            unknown = [f for f in answers["families"] if f not in rule_families]
            reasons["families"] += ["family_not_in_portfolio"] if unknown else []
            answers["families"] = [f for f in rule_families if f in answers["families"]]  # canonical order
        resolution = resolve_plan_point(ctx, FAMILIES, rules, answers, reasons=reasons,
                                        kinds={"families": "family_subset", "max_training_seconds": "time_budget"})
    used = resolution.used() if resolution is not None else {}
    families = used.get("families") if used.get("families") not in (None, rule_families) else None
    budget = used.get("max_training_seconds")
    return families, (float(budget) if budget not in (None, RULE_TRAINING_SECONDS) else None)


def _off_outcome(rules: dict[str, Any]) -> Any:
    from app.agents.semantic.port import Resolution, SemanticOutcome

    return SemanticOutcome(purpose="inherited", ai="off", resolutions=tuple(
        Resolution(question_key=k, column_id=None, rule_answer=v, value_used=v, agreement="off")
        for k, v in rules.items()))


__all__ = ["finish_missing_point", "missing_treatments", "resolve_families_point", "resolve_missing_point", "resolve_objective_point",
           "resolve_split_point", "resolve_target_point", "split_reasons"]
