"""Branch experiments with typed change sets (ADR 0006 §4, §9 P2.4-A).

``branch_experiment`` validates an ``ExperimentChangeSet`` against the parent's
locked evidence (registry families, objective metric sets, observed classes,
dataset columns, leakage exclusions), materializes the effective run
configuration (parent's ``experiments.config`` ⊕ change set) and enqueues the
child on the existing auto-train path: a run envelope (upload row, WorkflowRun,
shell Experiment, ExecutionRequest, MlJob) that shares the parent's published
source dataset. The child is inserted with the parent's ``source_dataset_id`` and
``split_plan_id`` (the DB lineage guard enforces both); the worker partitions by
the stored map and never plans a new holdout or folds.

A branch can never change the dataset, target, task, ProblemSpec, holdout or
validation plan, nor re-include a leakage-excluded column: that is a new root.

``compare_experiments`` is the read model for "diff vs parent": metrics come from
``evaluation_metrics`` and only experiments on the same SplitPlan are compared.
"""

from __future__ import annotations

import copy
import hashlib
import json
import re
from dataclasses import dataclass, replace
from typing import Any
from uuid import UUID

from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models import (
    ClientLabUpload,
    Dataset,
    EvaluationMetric,
    ExecutionRequest,
    Experiment,
    ExperimentCandidate,
    MlJob,
    ModelEvaluation,
    ModelSelectionDecision,
    User,
    WorkflowRun,
)
from app.domain.errors import (
    ExperimentComparisonError,
    ExperimentNotBranchableError,
    ExperimentNotFoundError,
    InvalidChangeSetError,
)
from app.domain.execution_requests import OPERATION_MODEL_BUILD, SOURCE_API
from app.domain.experiment_changes import (
    CHANGE_KINDS,
    CHANGE_SET_SCHEMA_VERSION,
    INTENT_MAX_CHARS,
    NON_EXCLUDABLE_FAMILIES,
    ExperimentChangeSet,
)
from app.engine.modeling.objective import (
    CONSTRAINT_METRICS,
    PRIMARY_METRICS,
    ObjectiveError,
    normalize_metric,
    objective_from_dict,
)
from app.engine.models.registry import HYPERPARAMETER_OVERRIDES, available_families
from app.engine.search.generator import (
    CUSTOM_CLASS_WEIGHT_FAMILIES,
    DUMMY_FAMILIES,
    class_weight_variants,
    open_ingest_portfolio,
)
from app.engine.validation.splits import SOURCE_ROW_COLUMN
from app.services.authorization_service import can_execute_workspace_ml, can_read_workspace

BRANCH_OVERRIDES_VERSION = 1
COMPARISON_SCHEMA_VERSION = 1
# Change kinds a client might try that alter lineage: never a branch (new root).
NEW_ROOT_CHANGE_KINDS = frozenset(
    {
        "dataset_change",
        "dataset_version",
        "target_change",
        "task_change",
        "holdout_change",
        "split_plan_change",
        "validation_change",
        "problem_spec_change",
    }
)
NEW_ROOT_KEYS = frozenset(
    {
        "dataset_id",
        "source_dataset_id",
        "split_plan_id",
        "target_column",
        "task_type",
        "problem_spec_id",
        "holdout_plan",
        "validation_plan",
    }
)
_ROLE_TREATMENT = {
    "drop_column": "drop",
    "keep": "keep",
    "impute_median": "numeric",
    "impute_most_frequent": "categorical",
}
INCLUDING_TREATMENTS = frozenset({"keep", "numeric", "categorical"})
_CONTROL_CHARS = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")


# --- parsing ------------------------------------------------------------------


def _error_path(loc: tuple[Any, ...]) -> str:
    path = ""
    for part in loc:
        if isinstance(part, int):
            path += f"[{part}]"
        elif str(part) not in CHANGE_KINDS:  # the union tag adds no information
            path += f".{part}" if path else str(part)
    return path or "changes"


def parse_change_set(changes: Any) -> ExperimentChangeSet:
    """Structural gate. Lineage-changing requests are refused as ``new_root_required``."""

    raw = changes if isinstance(changes, dict) else {"changes": changes}
    if not isinstance(raw, dict):
        raise InvalidChangeSetError("schema", "change set must be an object")
    for key in sorted(set(raw) & NEW_ROOT_KEYS):
        raise InvalidChangeSetError(
            "new_root_required", f"{key} cannot change in a branch; start a new root", path=key
        )
    items = raw.get("changes")
    if isinstance(items, list):
        for index, item in enumerate(items):
            if not isinstance(item, dict):
                continue
            if str(item.get("kind")) in NEW_ROOT_CHANGE_KINDS:
                raise InvalidChangeSetError(
                    "new_root_required",
                    f"{item.get('kind')} cannot change in a branch; start a new root",
                    path=f"changes[{index}].kind",
                )
            for key in sorted(set(item) & NEW_ROOT_KEYS):
                raise InvalidChangeSetError(
                    "new_root_required",
                    f"{key} cannot change in a branch; start a new root",
                    path=f"changes[{index}].{key}",
                )
    payload = {"schema_version": CHANGE_SET_SCHEMA_VERSION, **raw}
    try:
        return ExperimentChangeSet.model_validate(payload)
    except ValidationError as exc:
        errors = exc.errors()
        # NaN/inf (allow_inf_nan=False) is the most specific failure of a scalar union.
        first = next((item for item in errors if item.get("type") == "finite_number"), errors[0])
        message = str(first.get("msg") or "invalid change set")
        loc = tuple(first.get("loc") or ())
        if first.get("type") == "finite_number":
            reason = "non_finite_value"
            loc = loc[:-1] if loc and loc[-1] in {"int", "float"} else loc
        else:
            reason = "baseline_not_excludable" if "cannot be excluded" in message else "schema"
        raise InvalidChangeSetError(reason, message, path=_error_path(loc)) from exc


def change_set_digest(change_set: ExperimentChangeSet) -> str:
    encoded = json.dumps(change_set.to_storage(), sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def sanitize_intent(intent: str | None) -> str:
    """Untrusted free text: control characters stripped, capped; never instructions."""

    text = _CONTROL_CHARS.sub("", str(intent or "")).strip()[:INTENT_MAX_CHARS].strip()
    if not text:
        raise InvalidChangeSetError("intent_required", "a branch needs a non-blank intent", path="intent")
    return text


# --- parent context -----------------------------------------------------------


@dataclass(frozen=True)
class ParentContext:
    """What the change set is validated against: the parent's locked evidence."""

    experiment: Experiment
    upload: ClientLabUpload
    workflow_run: WorkflowRun
    task_type: str
    target_column: str
    overrides: dict[str, Any]
    objective: dict[str, Any] | None
    portfolio: list[str]
    dataset_columns: set[str]
    reserved_columns: set[str]
    leakage_excluded: set[str]
    identifiers: set[str]
    dropped: set[str]
    numeric: set[str]
    categorical: set[str]
    datetime_converted: set[str]
    numeric_dtype: set[str]
    observed_classes: list[str]


def _parent_overrides(parent: Experiment) -> dict[str, Any]:
    raw = dict((parent.config or {}).get("branch_overrides") or {})
    return copy.deepcopy(raw)


def _numeric_dtype_columns(result: dict[str, Any]) -> set[str]:
    columns = (result.get("analysis") or {}).get("columns") or []
    numeric = set()
    for item in columns:
        if not isinstance(item, dict):
            continue
        dtype = str(item.get("dtype") or "").lower()
        if dtype.startswith(("int", "float", "uint")):
            numeric.add(str(item.get("name")))
    return numeric


def _observed_classes(task_type: str, result: dict[str, Any]) -> list[str]:
    if task_type == "multiclass":
        return [str(label) for label in result.get("class_labels") or []]
    if task_type == "binary":
        profile = result.get("problem_profile") or {}
        distribution = profile.get("class_distribution") or {}
        return sorted(str(key) for key in distribution) or ["0", "1"]
    return []


def load_parent_context(db: Session, *, actor: User, workspace_id: UUID, parent_id: UUID) -> ParentContext:
    if not can_read_workspace(db, actor, workspace_id) or not can_execute_workspace_ml(
        db, actor, workspace_id
    ):
        raise ExperimentNotFoundError("experiment not found")
    parent = db.scalar(
        select(Experiment).where(Experiment.id == parent_id, Experiment.workspace_id == workspace_id)
    )
    if parent is None:
        raise ExperimentNotFoundError("experiment not found")
    if str(parent.status).upper() != "COMPLETED" or parent.scientific_evidence_locked_at is None:
        raise ExperimentNotBranchableError(
            "parent_not_completed", "only a completed experiment with locked evidence can be branched"
        )
    if parent.split_plan_id is None:
        raise ExperimentNotBranchableError(
            "parent_without_split_plan",
            "the parent has no split plan (pre-Phase-2 run); re-run it as a new root",
        )
    if parent.source_dataset_id is None or parent.project_id is None:
        raise ExperimentNotBranchableError(
            "parent_without_source_dataset", "the parent has no project-scoped source dataset"
        )
    upload = db.scalar(
        select(ClientLabUpload).where(
            ClientLabUpload.workspace_id == workspace_id,
            ClientLabUpload.experiment_id == parent.id,
        )
    )
    workflow_run = db.get(WorkflowRun, parent.workflow_run_id) if parent.workflow_run_id else None
    if (
        upload is None
        or upload.dataset_id != parent.source_dataset_id
        or workflow_run is None
        or workflow_run.workspace_id != workspace_id
    ):
        raise ExperimentNotBranchableError(
            "parent_without_run_envelope", "the parent has no auto-train run on its source dataset"
        )
    result = dict(parent.result or {})
    task = dict(result.get("task") or {})
    task_type = str(task.get("task_type") or workflow_run.task_type or "")
    target_column = str(workflow_run.resolved_target or task.get("target") or "")
    if task_type not in PRIMARY_METRICS or not target_column:
        raise ExperimentNotBranchableError("parent_not_completed", "the parent has no resolved target/task")
    overrides = _parent_overrides(parent)
    development = dict(result.get("model_development_plan") or {})
    holdout = dict(result.get("holdout_plan") or {})
    reserved = {target_column, SOURCE_ROW_COLUMN}
    for plan in (development, holdout):
        for key in ("group_column", "time_column"):
            if plan.get(key):
                reserved.add(str(plan[key]))
    from app.services.target_intent_service import column_names_from_dataset_id

    preprocessing = dict(result.get("preprocessing") or {})
    return ParentContext(
        experiment=parent,
        upload=upload,
        workflow_run=workflow_run,
        task_type=task_type,
        target_column=target_column,
        overrides=overrides,
        objective=copy.deepcopy((parent.config or {}).get("objective")),
        portfolio=open_ingest_portfolio(task_type, overrides),
        dataset_columns=set(column_names_from_dataset_id(db, parent.source_dataset_id)),
        reserved_columns=reserved,
        leakage_excluded={
            str(item.get("column"))
            for item in development.get("excluded_features") or []
            if isinstance(item, dict) and item.get("column")
        },
        identifiers=set(
            ((upload.pipeline_log or {}).get("column_roles") or {}).get("identifier") or []
        ),
        dropped=set((result.get("cleaning") or {}).get("dropped_columns") or []),
        numeric=set(preprocessing.get("numeric_columns") or []),
        categorical=set(preprocessing.get("categorical_columns") or []),
        datetime_converted=set((result.get("feature_engineering") or {}).get("transformed_features") or []),
        numeric_dtype=_numeric_dtype_columns(result),
        observed_classes=_observed_classes(task_type, result),
    )


# --- validation + materialization ---------------------------------------------


def _bad(reason: str, message: str, index: int, field: str | None = None) -> InvalidChangeSetError:
    path = f"changes[{index}]" + (f".{field}" if field else "")
    return InvalidChangeSetError(reason, message, path=path)


def _hyperparameter(change, index: int, task_type: str, portfolio: list[str]) -> dict[str, Any]:
    family = change.family
    if family not in available_families(task_type):
        raise _bad("unknown_family", f"{family!r} is not a registry family for {task_type}", index, "family")
    if family not in portfolio:
        raise _bad("family_not_in_portfolio", f"{family!r} is not in the branch portfolio", index, "family")
    allowed = HYPERPARAMETER_OVERRIDES.get(family) or {}
    values: dict[str, Any] = {}
    for key, value in sorted(change.parameters.items()):
        spec = allowed.get(key)
        if spec is None:
            raise _bad(
                "unknown_hyperparameter",
                f"{key!r} is not an overridable hyperparameter of {family!r}",
                index,
                f"parameters.{key}",
            )
        kind, low, high = spec
        if isinstance(value, bool) or not isinstance(value, kind) or not low <= value <= high:
            raise _bad(
                "invalid_hyperparameter_value",
                f"{key!r} must be {kind.__name__} in [{low}, {high}]",
                index,
                f"parameters.{key}",
            )
        values[key] = value
    return values


def _feature_column(change, index: int, ctx: ParentContext) -> str:
    column = change.column
    if column not in ctx.dataset_columns:
        raise _bad("unknown_column", f"{column!r} is not a column of the dataset version", index, "column")
    if column in ctx.reserved_columns:
        raise _bad(
            "reserved_column", f"{column!r} is the target, group or time column", index, "column"
        )
    if column in ctx.leakage_excluded:
        # Founder Q4: a branch never touches a leakage-excluded column (new root + record).
        raise _bad(
            "leakage_excluded_column",
            f"{column!r} was excluded by the leakage plan; re-including it needs a new root",
            index,
            "column",
        )
    including = (change.kind == "feature_transform_add") != (change.transform == "drop_column")
    if including and column in ctx.identifiers:
        raise _bad(
            "identifier_column",
            f"{column!r} was classified as an identifier; it is never modeled",
            index,
            "column",
        )
    if change.parameters:
        raise _bad("transform_not_applicable", "allowlisted transforms take no parameters", index, "parameters")
    return column


def _parent_applied(ctx: ParentContext, column: str, transform: str) -> bool:
    if transform == "drop_column":
        return column in ctx.dropped
    if transform == "datetime_extract":
        return column in ctx.datetime_converted
    if transform == "impute_median":
        return column in ctx.numeric
    if transform == "impute_most_frequent":
        return column in ctx.categorical
    return column in ctx.numeric or column in ctx.categorical  # keep


def _column_treatments(change_set: ExperimentChangeSet, ctx: ParentContext) -> dict[str, dict[str, Any]]:
    """Per-column effect of the feature-transform changes (adds win over removes)."""

    adds: dict[str, dict[str, Any]] = {}
    removes: dict[str, dict[str, Any]] = {}
    for index, change in enumerate(change_set.changes):
        if change.kind not in {"feature_transform_add", "feature_transform_remove"}:
            continue
        column = _feature_column(change, index, ctx)
        target = adds if change.kind == "feature_transform_add" else removes
        spec = target.setdefault(column, {"index": index})
        if change.kind == "feature_transform_remove" and not _parent_applied(ctx, column, change.transform):
            raise _bad(
                "transform_not_applied",
                f"the parent did not apply {change.transform!r} to {column!r}",
                index,
                "transform",
            )
        if change.transform == "datetime_extract":
            other = removes if target is adds else adds
            if "datetime_extract" in other.get(column, {}):
                raise _bad("conflicting_transforms", f"datetime_extract added and removed on {column!r}", index)
            numeric_source = column in ctx.numeric_dtype or (
                column in ctx.numeric and column not in ctx.datetime_converted
            )
            if change.kind == "feature_transform_add" and numeric_source:
                # Numbers would be read as epoch nanoseconds: never a date.
                raise _bad("transform_not_applicable", f"{column!r} is numeric, not a date", index, "transform")
            spec["datetime_extract"] = change.kind == "feature_transform_add"
            continue
        treatment = _ROLE_TREATMENT[change.transform]
        if change.kind == "feature_transform_remove":
            # Undo the parent's step: an undropped column is kept; a removed
            # imputation leaves the column without a modeled treatment (dropped).
            treatment = "keep" if change.transform == "drop_column" else "drop"
        if "treatment" in spec and spec["treatment"] != treatment:
            raise _bad("conflicting_transforms", f"conflicting treatments for {column!r}", index)
        spec["treatment"] = treatment
    merged: dict[str, dict[str, Any]] = {}
    inherited = dict(ctx.overrides.get("columns") or {})
    for column in sorted(set(adds) | set(removes)):
        spec = {**removes.get(column, {}), **adds.get(column, {})}
        index = int(spec.pop("index"))
        # Checked on the effective spec: the parent's materialized treatment ⊕ this set.
        effective = {**dict(inherited.get(column) or {}), **spec}
        treatment = effective.get("treatment")
        converted = effective.get("datetime_extract")
        if treatment == "drop" and converted is True:
            raise _bad("conflicting_transforms", f"{column!r} cannot be dropped and converted", index)
        numeric_capable = (
            column in ctx.numeric_dtype
            or converted is True
            or (column in ctx.datetime_converted and converted is not False)
            or (column in ctx.numeric and column not in ctx.datetime_converted)
        )
        if treatment == "numeric" and not numeric_capable:
            raise _bad("transform_not_applicable", f"{column!r} is not numeric", index, "transform")
        if spec.get("datetime_extract") is True and treatment is None and column in ctx.dropped:
            raise _bad(
                "transform_not_applicable",
                f"{column!r} is not modeled by the parent; add 'keep' as well",
                index,
                "transform",
            )
        merged[column] = spec
    return merged


def _class_weighting(change, index: int, ctx: ParentContext) -> dict[str, Any]:
    if ctx.task_type not in {"binary", "multiclass"}:
        raise _bad("class_weighting_not_applicable", "class weighting needs a classification task", index)
    payload: dict[str, Any] = {"mode": change.mode}
    if change.mode != "custom":
        return payload
    unknown = sorted(set(change.weights or {}) - set(ctx.observed_classes))
    if unknown:
        raise _bad(
            "unknown_class_label",
            f"labels {unknown} are not observed classes {ctx.observed_classes}",
            index,
            "weights",
        )
    supported = [f for f in ctx.portfolio if f in CUSTOM_CLASS_WEIGHT_FAMILIES]
    if ctx.task_type == "binary" and "xgboost" in ctx.portfolio:
        supported.append("xgboost")
    if not supported:
        raise _bad("class_weighting_not_applicable", "no portfolio family accepts class weights", index)
    # Label -> estimator code: binary labels are already 0/1; multiclass codes are
    # positions in the parent's full sorted label set (same rows, same codes).
    codes = {label: str(position) for position, label in enumerate(ctx.observed_classes)}
    if ctx.task_type == "binary":
        codes = {label: label for label in ctx.observed_classes}
    # Every class gets a weight (unnamed ones 1.0): sklearn rejects a mapping
    # that names a class missing from a fold while leaving others unweighted.
    given = dict(change.weights or {})
    payload["weights"] = {codes[label]: float(given.get(label, 1.0)) for label in ctx.observed_classes}
    payload["labels"] = {codes[label]: label for label in ctx.observed_classes}
    return payload


def _objective(change_set: ExperimentChangeSet, ctx: ParentContext) -> dict[str, Any] | None:
    objective = dict(ctx.objective or {})
    changed = False
    for index, change in enumerate(change_set.changes):
        if change.kind == "metric_override":
            metric = normalize_metric(change.primary_metric)
            if metric not in PRIMARY_METRICS[ctx.task_type]:
                raise _bad(
                    "invalid_primary_metric",
                    f"{change.primary_metric!r} is not a primary metric for {ctx.task_type}",
                    index,
                    "primary_metric",
                )
            objective["primary_metric"] = metric
            objective["primary_metric_reason"] = _CONTROL_CHARS.sub("", change.reason).strip()[:512] or None
            changed = True
        elif change.kind == "threshold_objective":
            if ctx.task_type != "binary":
                raise _bad(
                    "threshold_objective_not_applicable", "threshold objectives are binary-only", index
                )
            constraints = []
            for position, item in enumerate(change.constraints):
                metric = normalize_metric(item.metric)
                if metric not in CONSTRAINT_METRICS["binary"]:
                    raise _bad(
                        "invalid_constraint_metric",
                        f"{item.metric!r} is not a constraint metric for binary",
                        index,
                        f"constraints[{position}].metric",
                    )
                constraints.append({"metric": metric, "op": item.op, "value": float(item.value)})
            costs = (change.cost_false_positive, change.cost_false_negative)
            if (costs[0] is None) != (costs[1] is None) or costs == (0.0, 0.0):
                raise _bad(
                    "invalid_cost_matrix",
                    "give both cost_false_positive and cost_false_negative, not both zero",
                    index,
                )
            # Replace semantics: the change is the whole threshold objective, so
            # parent constraints/costs it does not restate are cleared.
            objective["constraints"] = constraints
            objective["cost_false_positive"] = costs[0]
            objective["cost_false_negative"] = costs[1]
            changed = True
    if not changed:
        return ctx.objective
    try:
        parsed = objective_from_dict(objective, task_type=ctx.task_type)
    except ObjectiveError as exc:
        raise InvalidChangeSetError("invalid_objective", str(exc)) from exc
    return None if parsed is None or parsed.is_empty else parsed.to_dict()


def materialize_branch(
    change_set: ExperimentChangeSet, ctx: ParentContext
) -> tuple[dict[str, Any], dict[str, Any] | None]:
    """Validate every change; return (branch_overrides, objective) = parent ⊕ change set."""

    overrides = copy.deepcopy(ctx.overrides)
    include = list(overrides.get("families_include") or [])
    exclude = list(overrides.get("families_exclude") or [])
    portfolio = list(ctx.portfolio)
    installed = available_families(ctx.task_type)
    for index, change in enumerate(change_set.changes):
        if change.kind not in {"family_include", "family_exclude"}:
            continue
        family = change.family
        if family not in installed:
            raise _bad(
                "unknown_family", f"{family!r} is not an installed registry family for {ctx.task_type}", index, "family"
            )
        if change.kind == "family_include":
            if family in portfolio or family in DUMMY_FAMILIES:
                raise _bad("family_already_in_portfolio", f"{family!r} is already evaluated", index, "family")
            exclude = [name for name in exclude if name != family]
            include = [name for name in include if name != family] + [family]
            portfolio.append(family)
        else:
            if family in NON_EXCLUDABLE_FAMILIES:
                raise _bad("baseline_not_excludable", f"dummy baseline {family!r} is always evaluated", index, "family")
            if family not in portfolio:
                raise _bad("family_not_in_portfolio", f"{family!r} is not in the parent portfolio", index, "family")
            include = [name for name in include if name != family]
            exclude = [name for name in exclude if name != family] + [family]
            portfolio.remove(family)
    if not portfolio:
        raise InvalidChangeSetError("portfolio_empty", "a branch needs at least one learned family")
    overrides["families_include"] = include
    overrides["families_exclude"] = exclude
    hyperparameters = dict(overrides.get("hyperparameters") or {})
    for index, change in enumerate(change_set.changes):
        if change.kind == "hyperparameter_override":
            values = _hyperparameter(change, index, ctx.task_type, portfolio)
            hyperparameters[change.family] = {**dict(hyperparameters.get(change.family) or {}), **values}
        elif change.kind == "class_weighting":
            overrides["class_weighting"] = _class_weighting(change, index, replace(ctx, portfolio=portfolio))
    overrides["hyperparameters"] = {family: hyperparameters[family] for family in sorted(hyperparameters)}
    columns = dict(overrides.get("columns") or {})
    for column, spec in _column_treatments(change_set, ctx).items():
        columns[column] = {**dict(columns.get(column) or {}), **spec}
    overrides["columns"] = {column: columns[column] for column in sorted(columns)}
    overrides.update(
        {
            "version": BRANCH_OVERRIDES_VERSION,
            "change_set_digest": change_set_digest(change_set),
            # Lineage pins the worker re-checks: a branch never changes them.
            "target_column": ctx.target_column,
            "task_type": ctx.task_type,
        }
    )
    return overrides, _objective(change_set, ctx)


# --- branch creation ----------------------------------------------------------


@dataclass(frozen=True)
class BranchResult:
    experiment: Experiment
    execution_request: ExecutionRequest
    upload: ClientLabUpload
    ml_job: MlJob | None
    created: bool


def _existing_branch(
    db: Session, *, workspace_id: UUID, key: str, parent_id: UUID, digest: str
) -> BranchResult | None:
    request = db.scalar(
        select(ExecutionRequest).where(
            ExecutionRequest.workspace_id == workspace_id, ExecutionRequest.idempotency_key == key
        )
    )
    if request is None:
        return None
    spec = dict(request.request_spec or {})
    if spec.get("parent_experiment_id") != str(parent_id) or spec.get("change_set_digest") != digest:
        raise ExperimentNotBranchableError(
            "idempotency_key_conflict", "idempotency key was used for another branch"
        )
    experiment = db.get(Experiment, request.pipeline_run_id)
    upload = db.scalar(select(ClientLabUpload).where(ClientLabUpload.experiment_id == experiment.id))
    job = db.scalar(select(MlJob).where(MlJob.execution_request_id == request.id))
    return BranchResult(experiment, request, upload, job, created=False)


def branch_experiment(
    db: Session,
    *,
    actor: User,
    workspace_id: UUID,
    parent_id: UUID,
    changes: Any,
    intent: str | None,
    idempotency_key: str | None = None,
    source_surface: str = SOURCE_API,
) -> BranchResult:
    """Validate, materialize and enqueue one branch of ``parent_id``. Commits.

    Raises ``ExperimentNotFoundError`` (404, also cross-tenant / no ML write),
    ``ExperimentNotBranchableError`` (409) or ``InvalidChangeSetError`` (422).
    """

    ctx = load_parent_context(db, actor=actor, workspace_id=workspace_id, parent_id=parent_id)
    change_set = parse_change_set(changes)
    text = sanitize_intent(intent)
    overrides, objective = materialize_branch(change_set, ctx)
    digest = overrides["change_set_digest"]
    key = (idempotency_key or "").strip()[:256] or None
    if key is not None:
        existing = _existing_branch(db, workspace_id=workspace_id, key=key, parent_id=parent_id, digest=digest)
        if existing is not None:
            return existing

    from app.services.auto_train_service import enqueue_auto_train
    from app.services.execution_request_service import (
        _legacy_labs_request_spec,
        create_execution_request,
    )
    from app.services.lab_service import search_from_mapping
    from app.services.lineage_service import create_pipeline_run, create_workflow_run
    from app.services.ml_job_service import create_auto_train_job

    parent = ctx.experiment
    parent_upload = ctx.upload
    source = db.get(Dataset, parent.source_dataset_id)
    # Run envelope on the parent's published source dataset: no new bytes, no
    # new Dataset; the explicit target pins the parent's resolved target.
    upload = ClientLabUpload(
        workspace_id=workspace_id,
        requested_by=actor.id,
        category=parent_upload.category,
        original_filename=parent_upload.original_filename,
        stored_path=parent_upload.stored_path,
        kind=parent_upload.kind,
        record_count=parent_upload.record_count,
        fields_noticed=parent_upload.fields_noticed,
        has_named_fields=parent_upload.has_named_fields,
        explicit_target_column=ctx.target_column,
        pipeline_status="queued",
        client_status="queued",
        dataset_id=source.id,
        artifact_id=parent_upload.artifact_id,
        data_source_id=parent_upload.data_source_id,
        ingestion_run_id=parent_upload.ingestion_run_id,
    )
    db.add(upload)
    db.flush()
    workflow_run = create_workflow_run(
        db,
        workspace_id=workspace_id,
        workflow=ctx.workflow_run.workflow,
        requester=actor,
        trigger_type="manual",
        source_type=upload.kind,
        source_upload=upload,
        explicit_target=ctx.target_column,
        inputs=[(source, "reference")],
        problem_spec_id=ctx.workflow_run.problem_spec_id,
    )
    config = search_from_mapping(
        {**dict(parent.config or {}), "objective": objective, "branch_overrides": overrides}
    )
    shell = create_pipeline_run(
        db,
        workflow_run=workflow_run,
        environment=source.environment,
        dataset=source,
        task=None,
        pipeline_name="open_ingest_deterministic_ml",
        pipeline_purpose="training_and_scoring",
        input_role=None,
        commit=False,
        config=config,
        parent_pipeline_run_id=parent.id,
        source_dataset_id=parent.source_dataset_id,
        split_plan_id=parent.split_plan_id,
        intent=text,
        change_set=change_set.to_storage(),
    )
    upload.experiment_id = shell.id
    request = create_execution_request(
        db,
        workspace_id=workspace_id,
        project_id=parent.project_id,
        operation=OPERATION_MODEL_BUILD,
        source_surface=source_surface,
        requested_by_user_id=actor.id,
        idempotency_key=key,
        request_spec=_legacy_labs_request_spec(
            upload,
            problem_spec_id=ctx.workflow_run.problem_spec_id,
            origin={"parent_experiment_id": str(parent.id), "change_set_digest": digest},
        ),
        workflow_run_id=workflow_run.id,
        pipeline_run_id=shell.id,
    )
    job = create_auto_train_job(
        db,
        workspace_id=workspace_id,
        project_id=parent.project_id,
        upload_id=upload.id,
        execution_request_id=request.id,
        workflow_run_id=workflow_run.id,
        pipeline_run_id=shell.id,
    )
    db.commit()
    enqueue_auto_train(upload.id)
    return BranchResult(shell, request, upload, job, created=True)


# --- comparison (read model) --------------------------------------------------


def _winner_evidence(db: Session, experiment: Experiment) -> dict[str, Any]:
    decision = db.scalar(
        select(ModelSelectionDecision).where(ModelSelectionDecision.pipeline_run_id == experiment.id)
    )
    if decision is None:
        raise ExperimentComparisonError("winner_missing", f"experiment {experiment.id} has no locked winner")
    winner = db.get(ExperimentCandidate, decision.selected_candidate_id)
    rows = db.execute(
        select(ModelEvaluation.evaluation_scope, EvaluationMetric.metric_name, EvaluationMetric.metric_value)
        .join(EvaluationMetric, EvaluationMetric.model_evaluation_id == ModelEvaluation.id)
        .where(
            ModelEvaluation.workspace_id == experiment.workspace_id,
            ModelEvaluation.candidate_id == decision.selected_candidate_id,
            ModelEvaluation.evaluation_scope.in_(("cv_aggregate", "final_holdout")),
        )
    ).all()
    metrics: dict[str, dict[str, float]] = {"cv_aggregate": {}, "final_holdout": {}}
    for scope, name, value in rows:
        metrics[str(scope)][str(name)] = float(value)
    threshold = dict((experiment.result or {}).get("decision_threshold") or {})
    return {
        "candidate_id": winner.candidate_key if winner is not None else None,
        "family": winner.model_family if winner is not None else None,
        "selection_metric": decision.selection_metric,
        "selected_score": decision.selected_score,
        "cv": metrics["cv_aggregate"],
        "holdout": metrics["final_holdout"],
        "decision_threshold": threshold.get("value"),
        "constraint_status": threshold.get("status"),
    }


def _metric_diff(parent: dict[str, float], child: dict[str, float]) -> dict[str, dict[str, float]]:
    return {
        name: {"parent": parent[name], "child": child[name], "delta": child[name] - parent[name]}
        for name in sorted(set(parent) & set(child))
        if name != "decision_threshold"
    }


def compare_experiments(db: Session, parent: Experiment, child: Experiment) -> dict[str, Any]:
    """Diff of ``child`` vs ``parent`` from ``evaluation_metrics`` (never authoritative).

    Only experiments on the same SplitPlan are comparable (ADR 0006 Rev 2): same
    holdout rows and outer folds. CV metrics compare the locked winners' CV
    aggregates; holdout metrics are each run's single evaluation at its own
    locked threshold.
    """

    if parent.workspace_id != child.workspace_id:
        raise ExperimentComparisonError("not_comparable", "experiments belong to different workspaces")
    if parent.split_plan_id is None or parent.split_plan_id != child.split_plan_id:
        raise ExperimentComparisonError(
            "split_plan_mismatch", "experiments do not share a split plan; their holdouts differ"
        )
    before, after = _winner_evidence(db, parent), _winner_evidence(db, child)
    for side in (before, after):
        if not side["cv"] or not side["holdout"]:
            raise ExperimentComparisonError(
                "evidence_missing", "a locked winner has no CV aggregate or final-holdout metrics"
            )
    return {
        "schema_version": COMPARISON_SCHEMA_VERSION,
        "status": "compared",
        "source": "evaluation_metrics",
        "authoritative": False,
        "parent_experiment_id": str(parent.id),
        "split_plan_id": str(child.split_plan_id),
        "selection_metric": {"parent": before["selection_metric"], "child": after["selection_metric"]},
        "winner": {
            key: {"parent": before[key], "child": after[key]}
            for key in ("candidate_id", "family", "decision_threshold", "constraint_status")
        },
        "cv": _metric_diff(before["cv"], after["cv"]),
        "holdout": _metric_diff(before["holdout"], after["holdout"]),
        # Metrics recorded on one side only (e.g. a threshold-objective constraint).
        "unmatched": {
            section: {
                "parent_only": sorted(set(before[section]) - set(after[section])),
                "child_only": sorted(set(after[section]) - set(before[section])),
            }
            for section in ("cv", "holdout")
        },
    }


def branch_comparison(db: Session, child: Experiment) -> dict[str, Any]:
    """Cached on the child's result; a refusal is recorded with its typed reason."""

    parent = db.get(Experiment, child.parent_pipeline_run_id)
    if parent is None:
        return {"status": "not_comparable", "reason": "parent_missing"}
    try:
        return compare_experiments(db, parent, child)
    except ExperimentComparisonError as exc:
        return {
            "schema_version": COMPARISON_SCHEMA_VERSION,
            "status": "not_comparable",
            "reason": exc.code,
            "parent_experiment_id": str(parent.id),
        }


# --- applied-change evidence --------------------------------------------------


def _candidates(result: dict[str, Any]) -> list[dict[str, Any]]:
    return [row for row in result.get("candidates") or [] if isinstance(row, dict)]


def applied_change_evidence(change_set: dict[str, Any], result: dict[str, Any]) -> list[dict[str, Any]]:
    """What each change did in this run, read back from the run's own evidence."""

    candidates = _candidates(result)
    ids = {row.get("candidate_id") for row in candidates}
    families = {row.get("model_family") for row in candidates}
    task_type = str((result.get("task") or {}).get("task_type") or "")
    overrides = dict((result.get("config") or {}).get("branch_overrides") or {})
    preprocessing = dict(result.get("preprocessing") or {})
    numeric = set(preprocessing.get("numeric_columns") or [])
    categorical = set(preprocessing.get("categorical_columns") or [])
    converted = set((result.get("feature_engineering") or {}).get("transformed_features") or [])
    metric_plan = dict(result.get("metric_plan") or {})
    threshold = dict(result.get("decision_threshold") or {})
    evidence: list[dict[str, Any]] = []
    for index, change in enumerate(change_set.get("changes") or []):
        kind = change.get("kind")
        row: dict[str, Any] = {"index": index, "kind": kind}
        if kind == "hyperparameter_override":
            untuned = [
                c
                for c in candidates
                if c.get("model_family") == change.get("family")
                and "tuning" not in (c.get("hyperparameters") or {})
            ]
            hit = [
                c.get("candidate_id")
                for c in untuned
                if all((c.get("hyperparameters") or {}).get(k) == v for k, v in change["parameters"].items())
            ]
            row.update(applied=bool(untuned) and len(hit) == len(untuned), candidate_ids=hit)
        elif kind == "family_include":
            row.update(applied=change.get("family") in families)
        elif kind == "family_exclude":
            row.update(applied=change.get("family") not in families)
        elif kind == "class_weighting":
            # Every variant the materialized portfolio implies, and nothing else.
            expected = {
                f"{family}__{suffix}"
                for suffix, family, _hp in class_weight_variants(
                    task_type,
                    open_ingest_portfolio(task_type, overrides),
                    result.get("model_development_plan"),
                    overrides.get("class_weighting"),
                )
            }
            present = {cid for cid in ids if str(cid).endswith(("__balanced", "__weighted"))}
            row.update(
                applied=present == expected and bool(expected) == (change.get("mode") != "none"),
                candidate_ids=sorted(present),
            )
        elif kind == "metric_override":
            primary = metric_plan.get("primary_metric")
            row.update(applied=primary == normalize_metric(change.get("primary_metric")), primary_metric=primary)
        elif kind == "threshold_objective":
            requested = [normalize_metric(item.get("metric")) for item in change.get("constraints") or []]
            observed = [item.get("metric") for item in threshold.get("constraints") or []]
            wants_cost = change.get("cost_false_positive") is not None
            row.update(
                applied=observed == requested and (threshold.get("source") == "cost_matrix") == wants_cost,
                threshold=threshold.get("value"),
                constraint_status=threshold.get("status"),
            )
        else:
            column, transform = change.get("column"), change.get("transform")
            add = kind == "feature_transform_add"
            # Where the transform leaves the column in the modeled set.
            placed = {
                "datetime_extract": column in converted,
                "drop_column": not (column in numeric or column in categorical),
                "keep": column in numeric or column in categorical,
                "impute_median": column in numeric,
                "impute_most_frequent": column in categorical,
            }[transform]
            row.update(applied=placed == add, column=column, transform=transform)
        evidence.append(row)
    return evidence
