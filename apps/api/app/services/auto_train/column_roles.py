"""Stage: train-only column roles, feature engineering and the modeled set."""

from __future__ import annotations

from typing import Any

import pandas as pd
from pydantic import BaseModel, ConfigDict

from app.db.models import ClientLabUpload, LabDecisionRecord
from app.domain.lab_run_stages import FEATURE_ENGINEERING
from app.engine.features.combinations import features_for_groups, generate_group_combinations
from app.engine.lab.auto_prepare import (
    ColumnRoles,
    MissingValuePlan,
    apply_feature_engineering_actions,
    engineer_features,
    infer_column_roles,
    split_column_roles,
)
from app.engine.lab.schema_inference import TargetChoice, infer_entity_column
from app.engine.modeling.holdout_planner import HoldoutPlan
from app.engine.modeling.leakage_auditor import ModelDevelopmentPlan
from app.engine.modeling.objective import Objective
from app.engine.types import SearchConfig
from app.services.auto_train.branch import (
    apply_role_overrides,
    datetime_overrides,
    forced_datetime_action,
)
from app.services.auto_train.context import RunContext, StageHalt, service_module
from app.services.auto_train.decision_points import decision_points, resolve_column_points
from app.services.auto_train.plan_points import finish_missing_point, missing_treatments, resolve_families_point
from app.services.lab_decision_ledger import record_column_type_decisions


class ColumnRolesInput(BaseModel):
    model_config = ConfigDict(arbitrary_types_allowed=True)

    upload: ClientLabUpload
    frame: pd.DataFrame
    locked_train: pd.DataFrame
    locked_split: dict[str, Any]
    feature_columns: list[str]
    kept_columns: list[str]
    modeled_kept_columns: list[str]
    allowed_predictors: set[str]
    target: TargetChoice
    target_evidence: dict[str, Any]
    profile: dict[str, Any]
    cleaning_log: dict[str, Any]
    missing_plan: MissingValuePlan
    holdout_plan: HoldoutPlan
    development_plan: ModelDevelopmentPlan
    run_objective: Objective | None


class ColumnRolesOutput(BaseModel):
    model_config = ConfigDict(arbitrary_types_allowed=True)

    frame: pd.DataFrame
    initial_roles: ColumnRoles
    final_roles: ColumnRoles
    fe_transformations: list[dict[str, Any]]
    num_cols: list[str]
    cat_cols: list[str]
    identifier_cols: list[str]
    entity_column: str | None
    transformed_datetime: set[str]
    column_role_evidence: dict[str, Any]
    search: SearchConfig
    combos: list[tuple[str, ...]]
    modeled_cols: list[str]


def run_column_roles(ctx: RunContext, inp: ColumnRolesInput) -> ColumnRolesOutput:
    svc = service_module()
    db = ctx.db
    upload = inp.upload
    target = inp.target
    frame = inp.frame
    locked_train = inp.locked_train
    kept_columns = inp.kept_columns
    missing_plan = inp.missing_plan
    development_plan = inp.development_plan
    profile = inp.profile

    # Record raw/train-only roles before feature transformations, then
    # derive final modeled roles from the transformed training partition.
    evidence_timer = ctx.evidence_start("column_roles")
    initial_roles = infer_column_roles(locked_train, kept_columns)
    ctx.evidence_finish(evidence_timer)

    ctx.stage(FEATURE_ENGINEERING)
    evidence_timer = ctx.evidence_start("feature_engineering")
    branch = ctx.branch
    no_datetime, force_datetime = datetime_overrides(branch) if branch is not None else (set(), [])
    engineered_train, fe_transformations = engineer_features(
        locked_train, [name for name in inp.modeled_kept_columns if name not in no_datetime]
    )
    if force_datetime:
        converted = {str(name) for action in fe_transformations for name in action.get("columns") or []}
        engineered_train, forced = forced_datetime_action(engineered_train, force_datetime, converted)
        if forced is not None:
            fe_transformations.append(forced)
    frame = apply_feature_engineering_actions(frame, fe_transformations)
    ctx.evidence_finish(evidence_timer)
    ctx.trace(
        "feature_engineering_transforms",
        "app.engine.lab.auto_prepare.engineer_features",
        transformations=fe_transformations,
        evidence_scope="train_only",
        column_count=int(engineered_train.shape[1]),
    )
    evidence_timer = ctx.evidence_start("column_roles_finalization")
    final_roles = infer_column_roles(engineered_train, kept_columns)
    num_cols, cat_cols = split_column_roles(engineered_train, kept_columns)
    rule_numeric, rule_categorical = list(num_cols), list(cat_cols)  # before the legacy column-type writer
    num_cols, cat_cols = record_column_type_decisions(
        db,
        upload.id,
        engineered_train,
        num_cols,
        cat_cols,
    )
    db.commit()
    llm_identifier_cols = [
        name
        for name in final_roles.numerical
        if name not in num_cols and name not in cat_cols
    ]
    identifier_cols = list(dict.fromkeys(final_roles.identifier + llm_identifier_cols))
    entity_column = infer_entity_column(engineered_train, kept_columns)
    legacy_numeric, legacy_categorical = list(num_cols), list(cat_cols)
    num_cols = [name for name in num_cols if name in inp.allowed_predictors]
    cat_cols = [name for name in cat_cols if name in inp.allowed_predictors]
    transformed_datetime = {
        str(name)
        for action in fe_transformations
        for name in (action.get("output_columns") or action.get("columns") or [])
    }
    # P6.9-A: an applied missing-value action fixes the column's treatment (§1b,
    # ``_ROLE_TREATMENT``); those columns are not asked again at the role point.
    treated = {column: value for column, value in missing_treatments(ctx).items()
               if column in num_cols or column in cat_cols}
    for column, (role, _human) in treated.items():
        num_cols = [name for name in num_cols if name != column]
        cat_cols = [name for name in cat_cols if name != column]
        (num_cols if role == "numeric" else cat_cols).append(column)
    # P6.9-A decision points (precedence: branch override > AI at L2 > rule). Root runs
    # only ask; the identifier point is L1 (never changes a value); the role point may
    # move a modeled column between numeric and categorical at L2, validator-accepted.
    num_cols, cat_cols, ai_roles = resolve_column_points(
        ctx,
        engineered_train=engineered_train,
        kept_columns=kept_columns,
        rule_numeric=rule_numeric,
        rule_categorical=rule_categorical,
        rule_identifiers=final_roles.identifier,
        legacy_numeric=legacy_numeric,
        legacy_categorical=legacy_categorical,
        num_cols=num_cols,
        cat_cols=cat_cols,
        identifier_cols=identifier_cols,
        ignored=final_roles.ignored_free_text,
        transformed_datetime=transformed_datetime,
        protected={entity_column, development_plan.group_column, development_plan.time_column},
        missing_actions={item.column: item.action for item in missing_plan.column_decisions},
        leakage_excluded={item["column"] for item in development_plan.excluded_features},
        skip={column for column, (_role, human) in treated.items() if human},  # a person decided: final
        plan_roles={column: role for column, (role, human) in treated.items() if not human},  # §1b vs Jev
        train_rows=inp.locked_split.get("train_source_rows"),
    )
    num_cols, cat_cols = finish_missing_point(ctx, missing_plan, num_cols, cat_cols, rule_numeric)
    if branch is not None:
        num_cols, cat_cols = apply_role_overrides(
            branch,
            engineered_train,
            num_cols,
            cat_cols,
            allowed_predictors=inp.allowed_predictors,
            identifiers=set(identifier_cols),
        )
    ctx.evidence_finish(evidence_timer)
    role_decisions = {
        row.column: row
        for row in db.query(LabDecisionRecord)
        .filter(LabDecisionRecord.upload_id == upload.id)
        .all()
        if str(row.prompt_version).startswith("column_type_")
    }
    column_role_records: list[dict[str, Any]] = []
    raw_dtypes = {
        str(item.get("name")): str(item.get("dtype"))
        for item in profile.get("columns") or []
        if isinstance(item, dict)
    }
    for column in [*inp.feature_columns, target.column]:
        if column == target.column:
            final_role = "target"
            source = target.source
            reason = target.reason
            confidence = target.confidence
            verdict = target.validator_verdict
            llm_used = target.raw_llm_output is not None
        elif column in missing_plan.dropped_columns:
            final_role = "ignored/free_text"
            branch_drop = branch is not None and branch.columns.get(column, {}).get("treatment") == "drop"
            source = "branch_change_set" if branch_drop else "rule"
            reason = (
                "Excluded by the branch change set (drop_column)."
                if branch_drop
                else "Excluded by the train-only missing-value policy before modeling."
            )
            confidence = 1.0
            verdict = "not_run"
            llm_used = False
        elif column in {item["column"] for item in development_plan.excluded_features}:
            exclusion = next(item for item in development_plan.excluded_features if item["column"] == column)
            identifier_excluded = "identifier_not_a_predictor" in (exclusion.get("reasons") or [])
            final_role = "identifier" if identifier_excluded else "ignored/free_text"
            source = "rule"
            reason = f"Excluded from estimators by the train-only leakage plan: {exclusion.get('reason')}."
            confidence = 1.0
            verdict = "not_run"
            llm_used = False
        else:
            role_decision = role_decisions.get(column)
            if column in num_cols:
                final_role = "datetime" if column in transformed_datetime else "numerical"
            elif column in cat_cols:
                final_role = "boolean" if column in initial_roles.boolean else "categorical"
            elif column in identifier_cols:
                final_role = "identifier"
            else:
                final_role = "ignored/free_text"
            source = role_decision.source if role_decision is not None else "rule"
            reason = (
                "Validated semantic role decision."
                if role_decision is not None
                else f"Deterministic train-only role inference classified the column as {final_role}."
            )
            if column in ai_roles and (column in num_cols or column in cat_cols):
                source = "decision_point:column.semantic_role"
                reason = "Applied at L2 by the column.semantic_role decision point (validator-accepted; revertible)."
            confidence = (
                (role_decision.raw_llm_output or {}).get("confidence")
                if role_decision is not None
                else 1.0
            )
            verdict = role_decision.validator_verdict if role_decision is not None else "not_run"
            llm_used = bool(role_decision is not None and role_decision.raw_llm_output)
        column_role_records.append(
            {
                "column": column,
                "original_dtype": raw_dtypes.get(column, "unknown"),
                "final_role": final_role,
                "source": source,
                "reason": reason,
                "confidence": confidence,
                "validator_verdict": verdict,
                "llm_used": llm_used,
            }
        )
    column_role_evidence = {
        "decision_partition": "train",
        "evidence_source_rows": list(inp.locked_split.get("train_source_rows") or []),
        "columns": column_role_records,
    }
    if not num_cols and not cat_cols:
        ctx.fail(
            "no usable feature columns after removing identifiers, constants, and mostly-empty columns",
            extra={
                "target": inp.target_evidence,
                "dropped_columns": missing_plan.dropped_columns,
                "analysis": profile,
                "cleaning": inp.cleaning_log,
            },
        )
        raise StageHalt

    families, budget = resolve_families_point(ctx, target.task_type)  # training.families_budget
    search = svc._search_config(
        holdout_plan=inp.holdout_plan,
        development_plan=development_plan,
        objective=inp.run_objective,
        branch_overrides=None if branch is None else dict(branch.overrides),
        ai_policy_digest=decision_points(ctx).fingerprint_digest,
        families=families,
        max_training_seconds=budget,
    )
    groups_map = {"features": num_cols + cat_cols}
    combos = generate_group_combinations(
        list(groups_map.keys()),
        strategy="limited",
        max_combinations=search.max_feature_group_combinations,
        seed=search.seed,
    )
    combo = combos[0] if combos else tuple(groups_map.keys())
    modeled_cols = features_for_groups(groups_map, combo)
    ctx.trace(
        "feature_engineering",
        "app.engine.features.combinations.generate_group_combinations",
        groups=list(groups_map.keys()),
        combinations=[list(item) for item in combos],
        selected_group=list(combo),
        selected_columns=list(modeled_cols),
    )
    ctx.trace(
        "column_roles",
        "app.engine.lab.auto_prepare.split_column_roles",
        numerical_cols=list(num_cols),
        categorical_cols=list(cat_cols),
    )
    return ColumnRolesOutput(
        frame=frame,
        initial_roles=initial_roles,
        final_roles=final_roles,
        fe_transformations=fe_transformations,
        num_cols=num_cols,
        cat_cols=cat_cols,
        identifier_cols=identifier_cols,
        entity_column=entity_column,
        transformed_datetime=transformed_datetime,
        column_role_evidence=column_role_evidence,
        search=search,
        combos=combos,
        modeled_cols=modeled_cols,
    )
