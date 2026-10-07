"""Feature-engineering leakage contract (P5.0-A; ADR 0006 §4 ``FEATURE_TRANSFORM_ALLOWLIST``).

Every FEATURE transform the engine executes on modeled columns, and every one a
human, the rule proposer or an agent may ask for, is a row of
``FEATURE_TRANSFORM_REGISTRY`` declaring its **inputs** and a **fit scope**.
(Label-free structural cleanup that is not a per-column feature transform —
sparse-column drop, identifier/constant/free-text removal, ``domain_fill``,
``coerce_numeric`` in ``engine/lab/auto_prepare.py`` — is not a registry row;
none of it reads the target or the holdout.)

``stateless``
    A per-row function of the row's own inputs. Nothing is fitted or inferred
    from other rows, so it may run anywhere (``drop_column``, ``keep``).
``partition_structural``
    Target-free settings inferred ONCE from the locked training partition
    (every training-partition row, CV validation rows included) and then
    applied unchanged to every other partition and scoring file: never fitted
    on the final holdout, never re-inferred per partition. It is a closed,
    reviewed set (``PARTITION_STRUCTURAL_TRANSFORMS``): ``datetime_extract``
    (whether a column parses as a date is a column-level statistic — name
    token plus a >= 80 % parse rate over the training partition — and the
    parse format is guessed from its first non-null value; the recorded
    ``learned_from_data: False`` flag means "no per-row statistic", see
    ``engine/features/encode.py``) and the leakage auditor's EXCLUSION of
    suspiciously predictive columns (``leakage_exclusion``). A target-free
    scaler or imputer fitted on the training partition before CV is NOT
    structural: it is ``fold_fitted`` and must move inside the Pipeline.
``fold_fitted``
    Fits statistics (median, mode, scale, one-hot vocabulary, target means,
    a feature subset). It lives INSIDE the sklearn Pipeline and is fit on each
    CV fold's training rows only (``build_preprocessor`` → ``SkPipeline`` step
    ``prep`` in ``engine/experiments/runner.py``), never on the whole table and
    never on the final holdout. A transform whose OUTPUT VALUES come from the
    target (``encodes_target``: target encoding) must additionally be
    CROSS-FITTED: K-fold with DISJOINT inner folds, ``CROSS_FIT_MIN_FOLDS`` <=
    K <= ``CROSS_FIT_MAX_FOLDS``, every training row encoded with statistics
    of the other inner folds only. Leave-one-out is NOT cross-fitting (its
    encoding (S - y_i) / (n - 1) separates the target within a category) and
    is refused by the fold bounds. A transform whose FIT reads the target
    without emitting it (``supervised_fit``: SelectKBest) is fold-fitted.
    Selecting on the full table, on the training partition before the folds
    or on holdout rows is a violation.
``as_of_aggregate``
    A history aggregate over earlier rows. It declares the ``as_of`` time
    column, which must be the plan's prediction-time column (never a
    post-outcome timestamp), is group-aware (the group must be the plan's
    entity/split-group column), and uses a STRICT cutoff: only rows whose time
    is strictly before the row's own time (no same-timestamp, no future rows,
    not the row itself). A whole-table aggregate is a violation.

The sanctioned exception: ``leakage_exclusion`` is ``supervised_fit`` on the
training partition. It is admitted only because it is EXCLUSION-ONLY in the
strict sense — per column, fixed thresholds, it can only drop the columns most
associated with the target and never ranks, counts or picks columns for
inclusion (so, in expectation, it makes CV more pessimistic, not optimistic).
The exception is a closed set (``EXCLUSION_ONLY_TRANSFORMS``), never
proposable, and a plan cannot grant it to itself: only the registry rule
decides (``TransformRule.exclusion_only``).

Plan-level rules: agent- and rule-proposed features are tested only as
BRANCHES on the parent's SplitPlan and never see holdout metrics
(``agent_holdout_evidence``); each improve-loop iteration may try at most
``FEATURE_ATTEMPT_BUDGET_PER_LOOP`` feature transforms
(``check_feature_budget`` is the per-iteration hook the loop, P5.4, calls; it
does not bound cumulative CV comparisons per SplitPlan — that is a loop stop
rule).

What this module proves and what it does not: ``validate_feature_plan`` checks
DECLARATIONS. For the registered transforms the declaration is the engine's
own (``spec_for_transform``), so the production guards in the branch service
and the agent tool are tripwires against a drifting declaration, not evidence
that the engine conforms. Conformance is shown by the data-level probes in
``engine/features/probes.py`` run against the real implementations and the
real CV loop in the contract tests. The registry is the single source of the
change-set allowlist (``app.domain.experiment_changes.FEATURE_TRANSFORM_ALLOWLIST``
= the proposable names). Pure: no pandas, no database.

Follow-ups recorded here (P5.1 / P5.4 / UI): the ``proposed_by="agent"`` label
has no effect yet — the service's FeaturePlan never sets ``evidence_scopes``,
split-plan ids or ``attempts_used`` (P5.4 wires them), and an internal agent
proposal a human accepts runs as "human"; the exclusion-only fence is enforced
by NAME (closed sets) — a custom ``register_transform`` registry cannot add a
second exclusion, and the real ``audit_leakage`` is shown exclusion-only by the
data probes in the contract tests, not by the fence; humans may still consult
holdout results (branch comparisons show holdout diffs) — count
``holdout_consulted`` per SplitPlan; as-of inputs should be tied to the auditor's
``known_before_prediction`` / ``known_at_prediction`` availability (a history
feature of a column known only after prediction is still leakage); under a
random (non-temporal) split an as-of aggregate may read earlier HOLDOUT rows —
require the group to be the split group or a temporal split.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from types import MappingProxyType
from typing import Final, Literal

from pydantic import BaseModel, ConfigDict, Field

FitScope = Literal["stateless", "partition_structural", "fold_fitted", "as_of_aggregate"]
# Rows a transform's statistics or settings are fitted on. ``none``: nothing is fitted.
FitRows = Literal["none", "fold_train", "training_partition", "all_rows", "holdout"]
Cutoff = Literal["none", "strictly_before", "at_or_before"]
Proposer = Literal["human", "rule", "agent"]
EvidenceScope = Literal["cv", "train", "holdout"]
ViolationCode = Literal[
    "unknown_transform",
    "fit_scope_mismatch",
    "stateless_with_fit",
    "partition_fit_rows",
    "fit_outside_fold",
    "fit_on_holdout",
    "target_in_inputs",
    "target_encoding_not_out_of_fold",
    "whole_table_aggregate",
    "as_of_with_fit",
    "as_of_column_missing",
    "as_of_column_not_prediction_time",
    "as_of_cutoff_not_strict",
    "as_of_group_missing",
    "as_of_group_not_entity",
    "selection_outside_cv",
    "agent_holdout_evidence",
    "split_plan_mismatch",
    "feature_budget_exceeded",
]

FIT_SCOPES: Final[tuple[FitScope, ...]] = ("stateless", "partition_structural", "fold_fitted", "as_of_aggregate")
_REQUIRED_FIT_ROWS: Final[Mapping[FitScope, FitRows]] = MappingProxyType(
    {"stateless": "none", "partition_structural": "training_partition", "fold_fitted": "fold_train", "as_of_aggregate": "none"}
)
# Closed, reviewed sets: a new structural step or exclusion needs a contract change here.
PARTITION_STRUCTURAL_TRANSFORMS: Final[frozenset[str]] = frozenset({"datetime_extract", "leakage_exclusion"})
EXCLUSION_ONLY_TRANSFORMS: Final[frozenset[str]] = frozenset({"leakage_exclusion"})
# Cross-fitting = K disjoint inner folds; leave-one-out (K = n) is not cross-fitting.
CROSS_FIT_MIN_FOLDS: Final[int] = 2
CROSS_FIT_MAX_FOLDS: Final[int] = 10
# Feature transforms one improve-loop iteration may try (P5.4 calls ``check_feature_budget``).
FEATURE_ATTEMPT_BUDGET_PER_LOOP: Final[int] = 8
FEATURE_CONTRACT_VERSION: Final[str] = "dclab.feature_contract.v1"


class FeatureContractError(ValueError):
    """A transform rule that can never be registered (e.g. a target encoder that is not
    fold-fitted and cross-fitted). Raised at import/registration, not at run time."""


def _cross_fit_ok(folds: int | None) -> bool:
    return folds is not None and CROSS_FIT_MIN_FOLDS <= folds <= CROSS_FIT_MAX_FOLDS


@dataclass(frozen=True)
class TransformRule:
    """Code-owned facts about one transform kind: what it fits on and where it runs.

    ``__post_init__`` is the registration gate: the invariants below make an
    unsafe rule unconstructible, so a non-cross-fitted (or leave-one-out) target
    encoder, a whole-table aggregate, a full-table selector, a "structural"
    scaler or a self-declared exclusion cannot enter the registry.
    ``proposable`` rules are the change-set allowlist; engine-only steps
    (the leakage exclusion) are registered but never proposed.
    """

    transform: str
    fit_scope: FitScope
    engine_step: str
    # Output values come from the target (target encoding): cross-fitting required.
    encodes_target: bool = False
    # The fit reads the target without emitting it (supervised selection).
    supervised_fit: bool = False
    cross_fitted: bool = False
    cross_fit_folds: int | None = None
    aggregates_history: bool = False
    selects_features: bool = False
    excludes_only: bool = False
    proposable: bool = True

    def __post_init__(self) -> None:
        if not self.transform or self.transform.strip() != self.transform:
            raise FeatureContractError("transform name must be a non-blank token")
        if self.fit_scope not in FIT_SCOPES:
            raise FeatureContractError(f"{self.transform}: unknown fit scope {self.fit_scope!r}")
        if self.fit_scope == "partition_structural" and self.transform not in PARTITION_STRUCTURAL_TRANSFORMS:
            raise FeatureContractError(
                f"{self.transform}: partition_structural is a reviewed closed set "
                f"{sorted(PARTITION_STRUCTURAL_TRANSFORMS)}; a fitted step belongs inside the Pipeline"
            )
        if self.cross_fitted and self.fit_scope != "fold_fitted":
            raise FeatureContractError(f"{self.transform}: cross-fitting is a fold_fitted property")
        if self.cross_fitted and not _cross_fit_ok(self.cross_fit_folds):
            raise FeatureContractError(
                f"{self.transform}: cross-fitting needs {CROSS_FIT_MIN_FOLDS}..{CROSS_FIT_MAX_FOLDS} disjoint inner "
                "folds (leave-one-out is not cross-fitting)"
            )
        if self.cross_fit_folds is not None and not self.cross_fitted:
            raise FeatureContractError(f"{self.transform}: cross_fit_folds without cross_fitted")
        if self.excludes_only and (
            not self.selects_features or self.proposable or self.transform not in EXCLUSION_ONLY_TRANSFORMS
        ):
            raise FeatureContractError(
                f"{self.transform}: exclusion-only is the closed, non-proposable set {sorted(EXCLUSION_ONLY_TRANSFORMS)}"
            )
        if self.encodes_target and not (self.fit_scope == "fold_fitted" and self.cross_fitted):
            raise FeatureContractError(
                f"{self.transform}: a transform whose values come from the target must be fold_fitted AND "
                "cross-fitted (K disjoint inner folds)"
            )
        if self.supervised_fit and self.fit_scope != "fold_fitted" and not self.exclusion_only:
            raise FeatureContractError(
                f"{self.transform}: a fit that reads the target must be fold_fitted (inside CV); the only "
                "exception is the exclusion-only leakage audit on the training partition"
            )
        if self.selects_features and self.fit_scope != "fold_fitted" and not self.exclusion_only:
            raise FeatureContractError(f"{self.transform}: feature selection must be fold_fitted (inside CV)")
        if self.aggregates_history != (self.fit_scope == "as_of_aggregate"):
            raise FeatureContractError(
                f"{self.transform}: history aggregates are exactly the as_of_aggregate transforms"
            )

    @property
    def uses_target(self) -> bool:
        return self.encodes_target or self.supervised_fit

    @property
    def exclusion_only(self) -> bool:
        """The sanctioned exception: the closed set of exclusion-only steps on the
        training partition. Decided by the rule alone, never by a plan's spec."""

        return (
            self.excludes_only
            and self.fit_scope == "partition_structural"
            and self.transform in EXCLUSION_ONLY_TRANSFORMS
        )

    @property
    def required_fit_rows(self) -> FitRows:
        return _REQUIRED_FIT_ROWS[self.fit_scope]


def _registry(rules: Iterable[TransformRule]) -> Mapping[str, TransformRule]:
    out: dict[str, TransformRule] = {}
    for rule in rules:
        if rule.transform in out:
            raise FeatureContractError(f"duplicate transform rule {rule.transform!r}")
        out[rule.transform] = rule
    return MappingProxyType(out)


# Exactly what the engine executes and codegen reproduces today (ADR 0006 §4).
# Order is the public allowlist order; each addition needs engine + codegen support.
FEATURE_TRANSFORM_REGISTRY: Final[Mapping[str, TransformRule]] = _registry(
    (
        TransformRule(
            "drop_column",
            "stateless",
            "column left out of the modeled columns (auto_train.branch.apply_role_overrides)",
        ),
        TransformRule(
            "keep",
            "stateless",
            "column kept in its parent treatment (auto_train.branch.apply_role_overrides)",
        ),
        TransformRule(
            "impute_median",
            "fold_fitted",
            "SimpleImputer(strategy='median') + StandardScaler in build_preprocessor, "
            "SkPipeline step 'prep', fit per CV fold (engine/experiments/runner.py)",
        ),
        TransformRule(
            "impute_most_frequent",
            "fold_fitted",
            "SimpleImputer(strategy='most_frequent') + OneHotEncoder in build_preprocessor, "
            "SkPipeline step 'prep', fit per CV fold (engine/experiments/runner.py)",
        ),
        TransformRule(
            "datetime_extract",
            "partition_structural",
            "whether a column parses as a date (name token + >= 80% parse rate over every "
            "training-partition row) and its parse format (guessed from the first non-null value) "
            "are inferred on the locked training partition (engine/lab/auto_prepare."
            "engineer_features) and applied unchanged to validation, holdout and scoring rows "
            "(apply_feature_engineering_actions); values become unix seconds per row",
        ),
        TransformRule(
            "leakage_exclusion",
            "partition_structural",
            "engine/modeling/leakage_auditor.audit_leakage on the training partition only: per "
            "column, fixed thresholds, excludes columns that predict the target too well to be "
            "available at prediction time; exclusion-only (never ranks, counts or picks columns for "
            "inclusion), holdout never read",
            supervised_fit=True,
            selects_features=True,
            excludes_only=True,
            proposable=False,
        ),
    )
)
# The change-set allowlist: the proposable registry names, in registry order.
FEATURE_TRANSFORM_NAMES: Final[tuple[str, ...]] = tuple(
    name for name, rule in FEATURE_TRANSFORM_REGISTRY.items() if rule.proposable
)
MISSING_VALUE_TRANSFORMS: Final[tuple[str, ...]] = ("impute_median", "impute_most_frequent", "drop_column")
assert set(MISSING_VALUE_TRANSFORMS) <= set(FEATURE_TRANSFORM_NAMES)


def register_transform(registry: Mapping[str, TransformRule], rule: TransformRule) -> Mapping[str, TransformRule]:
    """A new registry with ``rule`` added (the module registry is immutable). The rule's
    own invariants already ran (so the closed sets above fence every registry); this
    only refuses duplicates."""

    return _registry((*registry.values(), rule))


class _Frozen(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class FeatureTransformSpec(_Frozen):
    """One concrete transform in a plan: its inputs, output names and declared fit scope."""

    transform: str = Field(min_length=1, max_length=64)
    inputs: tuple[str, ...] = Field(min_length=1, max_length=64)
    outputs: tuple[str, ...] = Field(default=(), max_length=64)
    fit_scope: FitScope
    fit_rows: FitRows = "none"
    encodes_target: bool = False
    supervised_fit: bool = False
    # Target encoders: each training row is encoded with statistics of the OTHER inner
    # folds (K disjoint folds), never with its own target.
    cross_fitted: bool = False
    cross_fit_folds: int | None = Field(default=None, ge=1)
    aggregates_history: bool = False
    selects_features: bool = False
    # Informational only: the exception is granted by the registry rule, never by a spec.
    excludes_only: bool = False
    as_of_column: str | None = None
    group_column: str | None = None
    cutoff: Cutoff = "none"

    @property
    def uses_target(self) -> bool:
        return self.encodes_target or self.supervised_fit


class FeatureViolation(_Frozen):
    code: ViolationCode
    message: str
    transform: str | None = None
    # Position in ``FeaturePlan.transforms``; None for plan-level violations.
    index: int | None = None


class FeaturePlan(_Frozen):
    """The feature transforms one change set (branch) asks for, with who proposed them
    and what evidence they cite. ``attempts_used`` is the loop's running count;
    ``time_column`` is the ProblemSpec's prediction-time column (as-of aggregates
    must cut off on it) and ``group_column`` the entity / split-group column (as-of
    aggregates must group by it)."""

    transforms: tuple[FeatureTransformSpec, ...] = ()
    proposed_by: Proposer = "human"
    evidence_scopes: tuple[EvidenceScope, ...] = ("cv",)
    target_column: str | None = None
    time_column: str | None = None
    group_column: str | None = None
    split_plan_id: str | None = None
    parent_split_plan_id: str | None = None
    attempts_used: int | None = Field(default=None, ge=0)


def spec_for_transform(transform: str, column: str, *, registry: Mapping[str, TransformRule] | None = None) -> FeatureTransformSpec:
    """The engine's own declaration of how a registered transform runs on ``column``.

    Unknown transforms become a spec with the most conservative reading
    (``all_rows``) so ``validate_feature_plan`` reports ``unknown_transform``.
    """

    rule = (registry or FEATURE_TRANSFORM_REGISTRY).get(transform)
    if rule is None:
        return FeatureTransformSpec(transform=transform, inputs=(column,), fit_scope="stateless", fit_rows="all_rows")
    return FeatureTransformSpec(
        transform=transform,
        inputs=(column,),
        outputs=() if transform == "drop_column" else (column,),
        fit_scope=rule.fit_scope,
        fit_rows=rule.required_fit_rows,
        encodes_target=rule.encodes_target,
        supervised_fit=rule.supervised_fit,
        cross_fitted=rule.cross_fitted,
        cross_fit_folds=rule.cross_fit_folds,
        aggregates_history=rule.aggregates_history,
        selects_features=rule.selects_features,
        excludes_only=rule.excludes_only,
    )


def check_feature_budget(
    attempts_used: int, *, requested: int = 1, budget: int = FEATURE_ATTEMPT_BUDGET_PER_LOOP
) -> FeatureViolation | None:
    """The per-iteration budget hook: None while ``attempts_used + requested`` fits in
    ``budget``. It bounds one loop iteration's feature attempts only (every feature
    change counts, removes and keeps included); cumulative CV comparisons per
    SplitPlan are the loop's (P5.4) stop rules."""

    if attempts_used < 0 or requested < 0:
        raise ValueError("attempt counts are non-negative")
    if attempts_used + requested > budget:
        return FeatureViolation(
            code="feature_budget_exceeded",
            message=f"{attempts_used} feature attempts used; {requested} more would exceed the budget of {budget}",
        )
    return None


def _spec_violations(
    index: int, spec: FeatureTransformSpec, plan: FeaturePlan, registry: Mapping[str, TransformRule]
) -> list[FeatureViolation]:
    def bad(code: ViolationCode, message: str) -> FeatureViolation:
        return FeatureViolation(code=code, message=message, transform=spec.transform, index=index)

    out: list[FeatureViolation] = []
    rule = registry.get(spec.transform)
    if rule is None:
        return [bad("unknown_transform", f"{spec.transform!r} is not a registered feature transform")]
    if spec.fit_scope != rule.fit_scope:
        out.append(
            bad("fit_scope_mismatch", f"{spec.transform!r} is {rule.fit_scope}, declared {spec.fit_scope}")
        )
    target = plan.target_column
    if target is not None and target in (*spec.inputs, *spec.outputs, spec.as_of_column, spec.group_column):
        out.append(bad("target_in_inputs", f"the target {target!r} is never a transform input, output, time or group"))
    if spec.fit_rows == "holdout":
        out.append(bad("fit_on_holdout", f"{spec.transform!r} would be fitted on the final holdout"))
    if spec.fit_scope == "stateless" and spec.fit_rows != "none":
        out.append(bad("stateless_with_fit", f"a stateless transform fits nothing (declared {spec.fit_rows})"))
    if spec.fit_scope == "partition_structural" and spec.fit_rows != "training_partition":
        out.append(
            bad(
                "partition_fit_rows",
                f"{spec.transform!r} infers its settings on the training partition only (declared {spec.fit_rows})",
            )
        )
    if spec.fit_scope == "fold_fitted" and spec.fit_rows != "fold_train":
        out.append(
            bad("fit_outside_fold", f"{spec.transform!r} must be fitted inside the Pipeline per CV fold, not on {spec.fit_rows}")
        )
    exclusion_only = rule.exclusion_only  # the rule decides; a spec cannot grant itself the exception
    in_fold = spec.fit_scope == "fold_fitted" and spec.fit_rows == "fold_train"
    cross_fit = spec.cross_fitted and _cross_fit_ok(spec.cross_fit_folds)
    if (spec.encodes_target or rule.encodes_target) and not (in_fold and cross_fit):
        out.append(
            bad(
                "target_encoding_not_out_of_fold",
                f"{spec.transform!r} emits target statistics: it must be fold-fitted and cross-fitted with "
                f"{CROSS_FIT_MIN_FOLDS}..{CROSS_FIT_MAX_FOLDS} disjoint inner folds (never leave-one-out)",
            )
        )
    elif (spec.supervised_fit or rule.supervised_fit) and not in_fold and not exclusion_only:
        out.append(
            bad("target_encoding_not_out_of_fold", f"{spec.transform!r} reads the target: it must be fitted inside the CV folds")
        )
    aggregates = spec.aggregates_history or rule.aggregates_history or spec.fit_scope == "as_of_aggregate"
    if aggregates:
        if spec.fit_scope != "as_of_aggregate" or spec.fit_rows in {"all_rows", "training_partition"}:
            out.append(
                bad("whole_table_aggregate", f"{spec.transform!r} aggregates rows without an as-of cutoff")
            )
        elif spec.fit_rows != "none":
            out.append(bad("as_of_with_fit", f"an as-of aggregate fits nothing (declared {spec.fit_rows})"))
        if not spec.as_of_column:
            out.append(bad("as_of_column_missing", f"{spec.transform!r} needs the as_of time column"))
        elif plan.time_column is None:
            out.append(
                bad("as_of_column_not_prediction_time", f"{spec.transform!r}: the plan has no prediction-time column to cut off on")
            )
        elif spec.as_of_column != plan.time_column:
            out.append(
                bad(
                    "as_of_column_not_prediction_time",
                    f"{spec.transform!r} cuts off on {spec.as_of_column!r}; the prediction-time column is "
                    f"{plan.time_column!r}",
                )
            )
        if spec.cutoff != "strictly_before":
            out.append(
                bad(
                    "as_of_cutoff_not_strict",
                    f"{spec.transform!r} must use rows strictly before the row's time (declared {spec.cutoff})",
                )
            )
        if not spec.group_column:
            out.append(bad("as_of_group_missing", f"{spec.transform!r} must aggregate within the row's group"))
        elif plan.group_column is None:
            out.append(
                bad("as_of_group_not_entity", f"{spec.transform!r}: the plan has no entity / split-group column to group by")
            )
        elif spec.group_column != plan.group_column:
            out.append(
                bad(
                    "as_of_group_not_entity",
                    f"{spec.transform!r} groups by {spec.group_column!r}; the entity column is {plan.group_column!r}",
                )
            )
    if (spec.selects_features or rule.selects_features) and not in_fold and not exclusion_only:
        out.append(bad("selection_outside_cv", f"{spec.transform!r} selects features outside the CV folds"))
    return out


def validate_feature_plan(
    plan: FeaturePlan,
    *,
    registry: Mapping[str, TransformRule] | None = None,
    budget: int = FEATURE_ATTEMPT_BUDGET_PER_LOOP,
) -> tuple[FeatureViolation, ...]:
    """Every violation of the contract in ``plan`` (empty when it is clean).

    Per transform: registry membership and fit scope, fold-local fitting,
    out-of-fold target encoding, strict group-aware as-of cutoffs on the
    prediction-time column, in-CV selection. Per plan: a non-human proposer may
    not cite holdout evidence and must stay on the parent's SplitPlan; the
    attempt budget.
    """

    rules = registry if registry is not None else FEATURE_TRANSFORM_REGISTRY
    out: list[FeatureViolation] = []
    for index, spec in enumerate(plan.transforms):
        out.extend(_spec_violations(index, spec, plan, rules))
    if plan.proposed_by != "human" and "holdout" in plan.evidence_scopes:
        out.append(
            FeatureViolation(
                code="agent_holdout_evidence",
                message=f"a {plan.proposed_by}-proposed feature change may not see or cite final-holdout metrics",
            )
        )
    if plan.parent_split_plan_id is not None and plan.split_plan_id != plan.parent_split_plan_id:
        out.append(
            FeatureViolation(
                code="split_plan_mismatch",
                message="feature changes are tested as branches on the parent's SplitPlan",
            )
        )
    if plan.attempts_used is not None and plan.transforms:
        over = check_feature_budget(plan.attempts_used, requested=len(plan.transforms), budget=budget)
        if over is not None:
            out.append(over)
    return tuple(out)


__all__ = [
    "CROSS_FIT_MAX_FOLDS",
    "CROSS_FIT_MIN_FOLDS",
    "EXCLUSION_ONLY_TRANSFORMS",
    "FEATURE_ATTEMPT_BUDGET_PER_LOOP",
    "FEATURE_CONTRACT_VERSION",
    "FEATURE_TRANSFORM_NAMES",
    "FEATURE_TRANSFORM_REGISTRY",
    "FIT_SCOPES",
    "MISSING_VALUE_TRANSFORMS",
    "PARTITION_STRUCTURAL_TRANSFORMS",
    "Cutoff",
    "EvidenceScope",
    "FeatureContractError",
    "FeaturePlan",
    "FeatureTransformSpec",
    "FeatureViolation",
    "FitRows",
    "FitScope",
    "Proposer",
    "TransformRule",
    "ViolationCode",
    "check_feature_budget",
    "register_transform",
    "spec_for_transform",
    "validate_feature_plan",
]
