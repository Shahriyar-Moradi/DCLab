"""P5.0-A: the feature-engineering leakage contract.

Registry invariants (a non-cross-fitted or leave-one-out target encoder, a
"structural" scaler and a self-declared exclusion are unregisterable), typed
plan validation (fail on a whole-table target encoder, a whole-table or
non-strict or post-outcome as-of aggregate, an outside-CV selection, an agent
feature citing holdout; pass on the out-of-fold / strictly-before / in-fold
equivalents), the mutation-style data probes against the engine's REAL steps
(``build_preprocessor`` imputers and one-hot, ``SelectKBest``, ``audit_leakage``)
and against deliberately broken implementations, the real open-ingest CV loop
(what rows each preprocessor fit sees, with a mutation proof), a conformance
test over every registered transform, the branch-service and agent-tool
wiring, and the ``combinations.py`` finding. No database.
"""

from __future__ import annotations

import dataclasses
import inspect
import tempfile
from pathlib import Path
from uuid import uuid4

import numpy as np
import pandas as pd
import pytest
from sklearn.compose import ColumnTransformer
from sklearn.exceptions import NotFittedError
from sklearn.feature_selection import SelectKBest, f_classif
from sklearn.impute import SimpleImputer
from sklearn.metrics import roc_auc_score
from sklearn.pipeline import Pipeline as SkPipeline
from sklearn.preprocessing import OneHotEncoder
from sklearn.utils.validation import check_is_fitted

from app.agents.tools.catalog import ToolError, catalog
from app.domain.errors import InvalidChangeSetError
from app.domain.experiment_changes import FEATURE_CHANGE_KINDS, FEATURE_TRANSFORM_ALLOWLIST
from app.domain.run_plans import ExperimentPlan
from app.engine.experiments import runner
from app.engine.features import combinations
from app.engine.features.contract import (
    CROSS_FIT_MAX_FOLDS,
    EXCLUSION_ONLY_TRANSFORMS,
    FEATURE_ATTEMPT_BUDGET_PER_LOOP,
    FEATURE_TRANSFORM_NAMES,
    FEATURE_TRANSFORM_REGISTRY,
    MISSING_VALUE_TRANSFORMS,
    PARTITION_STRUCTURAL_TRANSFORMS,
    FeatureContractError,
    FeaturePlan,
    FeatureTransformSpec,
    TransformRule,
    check_feature_budget,
    register_transform,
    spec_for_transform,
    validate_feature_plan,
)
from app.engine.features.encode import infer_datetime_format
from app.engine.features.probes import (
    fitted_state_probe,
    fold_influence_probe,
    future_influence_probe,
    perturb_rows,
    self_target_influence_probe,
)
from app.engine.lab.auto_prepare import (
    apply_feature_engineering_actions,
    build_preprocessor,
    engineer_features,
    split_column_roles,
)
from app.engine.modeling.leakage_auditor import audit_leakage
from app.engine.search.generator import assemble_candidates
from app.engine.types import SearchConfig, TaskSpec
from app.services import experiment_branch_service as branch_service
from app.services.auto_train import column_roles as column_roles_stage
from app.services.auto_train.branch import BranchRun, apply_role_overrides
from app.services.experiment_branch_service import ParentContext, materialize_branch, parse_change_set

# --- fixtures -----------------------------------------------------------------


def _frame(n: int = 240, seed: int = 11, missing: float = 0.15) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    cat = rng.choice(["a", "b", "c", "d", "e"], size=n, p=[0.24, 0.22, 0.2, 0.18, 0.16]).astype(object)
    x1 = rng.normal(size=n)
    x2 = rng.normal(size=n)
    logits = 1.2 * x1 + 0.9 * (cat == "a") - 0.9 * (cat == "e")
    y = (rng.random(n) < 1 / (1 + np.exp(-logits))).astype(int)
    group = rng.choice(["g1", "g2", "g3", "g4"], size=n)
    # Timestamps with deliberate ties so same-timestamp rows exist within groups.
    t = pd.Timestamp("2026-01-01") + pd.to_timedelta(rng.integers(0, 60, size=n), unit="D")
    v = rng.normal(loc=10, scale=3, size=n)
    frame = pd.DataFrame({"c": cat, "x1": x1, "x2": x2, "y": y, "g": group, "t": t, "v": v})
    if missing:
        # Missing cells on every other row (the probes' OBSERVED half), so an imputer's
        # statistic visibly reaches the observed rows.
        gaps = np.flatnonzero(rng.random(n) < missing * 2)
        gaps = gaps[gaps % 2 == 1]
        frame.loc[gaps, "x1"] = np.nan
        frame.loc[gaps, "c"] = np.nan
    return frame


def _folds(n: int, k: int = 4):
    rows = np.arange(n)
    parts = np.array_split(rows, k)
    return [(np.setdiff1d(rows, part), part) for part in parts]


def _codes(violations):
    return [item.code for item in violations]


def _spec(**fields) -> FeatureTransformSpec:
    base = dict(transform="impute_median", inputs=("x1",), outputs=("x1",), fit_scope="fold_fitted", fit_rows="fold_train")
    return FeatureTransformSpec(**{**base, **fields})


OOF_TARGET_ENCODER = TransformRule(
    "target_encode", "fold_fitted", "cross-fitted target means inside the Pipeline",
    encodes_target=True, cross_fitted=True, cross_fit_folds=5,
)
HISTORY_MEAN = TransformRule(
    "history_mean", "as_of_aggregate", "strictly-before group mean", aggregates_history=True
)
IN_FOLD_SELECTOR = TransformRule(
    "select_k_best", "fold_fitted", "SelectKBest inside the Pipeline", supervised_fit=True, selects_features=True
)
EXTENDED = register_transform(
    register_transform(register_transform(FEATURE_TRANSFORM_REGISTRY, OOF_TARGET_ENCODER), HISTORY_MEAN),
    IN_FOLD_SELECTOR,
)
AS_OF = dict(fit_scope="as_of_aggregate", fit_rows="none", aggregates_history=True, as_of_column="t", group_column="g",
             cutoff="strictly_before", inputs=("v",), outputs=("v_hist",), transform="history_mean")


# --- registry -----------------------------------------------------------------


def test_allowlist_is_the_proposable_registry_and_every_transform_declares_a_fit_scope():
    assert FEATURE_TRANSFORM_ALLOWLIST == FEATURE_TRANSFORM_NAMES
    assert FEATURE_TRANSFORM_ALLOWLIST == (
        "drop_column", "keep", "impute_median", "impute_most_frequent", "datetime_extract",
    )
    scopes = {name: rule.fit_scope for name, rule in FEATURE_TRANSFORM_REGISTRY.items()}
    assert scopes == {
        "drop_column": "stateless",
        "keep": "stateless",
        "impute_median": "fold_fitted",
        "impute_most_frequent": "fold_fitted",
        "datetime_extract": "partition_structural",
        "leakage_exclusion": "partition_structural",
    }
    exclusion = FEATURE_TRANSFORM_REGISTRY["leakage_exclusion"]
    assert (exclusion.proposable, exclusion.exclusion_only, exclusion.supervised_fit, exclusion.encodes_target) == (
        False, True, True, False,
    )
    assert FEATURE_CHANGE_KINDS == ("feature_transform_add", "feature_transform_remove")
    assert set(MISSING_VALUE_TRANSFORMS) < set(FEATURE_TRANSFORM_NAMES)
    assert ExperimentPlan(missing_values={"x": "impute_median"}).missing_values == {"x": "impute_median"}
    with pytest.raises(ValueError):
        ExperimentPlan(missing_values={"x": "keep"})
    with pytest.raises(TypeError):
        FEATURE_TRANSFORM_REGISTRY["new"] = OOF_TARGET_ENCODER  # type: ignore[index]


@pytest.mark.parametrize(
    "fields, reason",
    [
        (dict(fit_scope="stateless", encodes_target=True), "values come from the target"),
        (dict(fit_scope="as_of_aggregate", encodes_target=True, aggregates_history=True), "values come from the target"),
        # B1: fold-fitted but NOT cross-fitted — each training row would see its own target.
        (dict(fit_scope="fold_fitted", encodes_target=True), "values come from the target"),
        (dict(fit_scope="fold_fitted", encodes_target=True, cross_fitted=False), "values come from the target"),
        # Leave-one-out or an unbounded "cross-fit" is not cross-fitting.
        (dict(fit_scope="fold_fitted", encodes_target=True, cross_fitted=True), "disjoint inner"),
        (dict(fit_scope="fold_fitted", encodes_target=True, cross_fitted=True, cross_fit_folds=1), "disjoint inner"),
        (dict(fit_scope="fold_fitted", encodes_target=True, cross_fitted=True, cross_fit_folds=CROSS_FIT_MAX_FOLDS + 1),
         "disjoint inner"),
        (dict(fit_scope="fold_fitted", cross_fit_folds=5), "without cross_fitted"),
        # A supervised fit on the training partition is only the closed exclusion-only set.
        (dict(fit_scope="partition_structural", supervised_fit=True, selects_features=True), "reviewed closed set"),
        (dict(fit_scope="partition_structural", cross_fitted=True), "reviewed closed set"),
        # A target-free scaler fitted on the training partition before CV is not structural.
        (dict(fit_scope="partition_structural"), "reviewed closed set"),
        (dict(fit_scope="stateless", selects_features=True), "feature selection"),
        (dict(fit_scope="stateless", supervised_fit=True), "reads the target"),
        (dict(fit_scope="stateless", excludes_only=True, selects_features=True), "exclusion-only"),
        (dict(fit_scope="stateless", aggregates_history=True), "history aggregates"),
        (dict(fit_scope="as_of_aggregate", aggregates_history=False), "history aggregates"),
        (dict(fit_scope="whole_table"), "unknown fit scope"),
    ],
)
def test_unsafe_transform_rules_cannot_be_registered(fields, reason):
    with pytest.raises(FeatureContractError, match=reason):
        TransformRule("bad", engine_step="n/a", **fields)
    # Mutating a safe rule into an unsafe one re-runs the gate.
    with pytest.raises(FeatureContractError):
        dataclasses.replace(OOF_TARGET_ENCODER, cross_fitted=False, cross_fit_folds=None)
    with pytest.raises(FeatureContractError, match="duplicate"):
        register_transform(FEATURE_TRANSFORM_REGISTRY, dataclasses.replace(OOF_TARGET_ENCODER, transform="keep"))


def test_exclusion_only_is_a_closed_non_proposable_set_no_rule_or_spec_can_self_grant():
    base = dict(engine_step="n/a", supervised_fit=True, selects_features=True, excludes_only=True, proposable=False)
    # A top-k selector named anything else cannot claim the exception, even non-proposable
    # (refused by the closed structural set, and by the closed exclusion set in any scope).
    with pytest.raises(FeatureContractError, match="closed"):
        TransformRule("drop_all_but_top_k_by_auc", "partition_structural", **base)
    with pytest.raises(FeatureContractError, match="exclusion-only"):
        TransformRule("drop_all_but_top_k_by_auc", "fold_fitted", **base)
    # The real name cannot claim it as a proposable change.
    with pytest.raises(FeatureContractError, match="exclusion-only"):
        TransformRule("leakage_exclusion", "partition_structural", **{**base, "proposable": True})
    # The exception needs the structural scope: the same flags in another scope are an
    # ordinary fold-fitted supervised selector, not an exclusion (item 12 mutant).
    in_fold = TransformRule("leakage_exclusion", "fold_fitted", **base)
    assert in_fold.exclusion_only is False and in_fold.required_fit_rows == "fold_train"
    assert EXCLUSION_ONLY_TRANSFORMS == {"leakage_exclusion"} and PARTITION_STRUCTURAL_TRANSFORMS >= EXCLUSION_ONLY_TRANSFORMS
    # A spec declaring the flags on a transform whose rule is not an exclusion gets no exception.
    self_granted = _spec(transform="datetime_extract", fit_scope="partition_structural", fit_rows="training_partition",
                         supervised_fit=True, selects_features=True, excludes_only=True)
    assert set(_codes(validate_feature_plan(FeaturePlan(transforms=(self_granted,))))) == {
        "target_encoding_not_out_of_fold", "selection_outside_cv",
    }
    # register_transform registries are fenced by the same gate.
    with pytest.raises(FeatureContractError):
        register_transform(EXTENDED, TransformRule("standard_scaler", "partition_structural", "n/a"))


# --- typed plan validation ----------------------------------------------------


def test_registered_transforms_satisfy_the_contract_by_construction():
    plan = FeaturePlan(
        transforms=tuple(spec_for_transform(name, "x1") for name in FEATURE_TRANSFORM_REGISTRY),
        target_column="y", time_column="t", group_column="g",
    )
    assert validate_feature_plan(plan) == ()
    median = spec_for_transform("impute_median", "x1")
    assert (median.fit_scope, median.fit_rows, median.outputs) == ("fold_fitted", "fold_train", ("x1",))
    assert spec_for_transform("drop_column", "x1").outputs == ()
    dates = spec_for_transform("datetime_extract", "signup")
    assert (dates.fit_scope, dates.fit_rows) == ("partition_structural", "training_partition")
    # The declaration copies the rule, never invents cross-fitting from the target flag.
    te = spec_for_transform("target_encode", "c", registry=EXTENDED)
    assert (te.encodes_target, te.cross_fitted, te.cross_fit_folds) == (True, True, 5)
    selector = spec_for_transform("select_k_best", "x1", registry=EXTENDED)
    assert (selector.supervised_fit, selector.encodes_target, selector.selects_features) == (True, False, True)
    assert _codes(validate_feature_plan(FeaturePlan(transforms=(spec_for_transform("log1p", "x1"),)))) == [
        "unknown_transform"
    ]


def test_legit_feature_plans_pass():
    oof = _spec(transform="target_encode", encodes_target=True, cross_fitted=True, cross_fit_folds=5,
                outputs=("c_te",), inputs=("c",))
    history = _spec(**AS_OF)
    selection = _spec(transform="select_k_best", inputs=("x1", "x2"), outputs=("x1",), supervised_fit=True,
                      selects_features=True)
    plan = FeaturePlan(transforms=(oof, history, selection), target_column="y", time_column="t", group_column="g",
                       proposed_by="agent", split_plan_id="sp-1", parent_split_plan_id="sp-1", attempts_used=0)
    assert validate_feature_plan(plan, registry=EXTENDED) == ()


@pytest.mark.parametrize(
    "spec, expected",
    [
        # A target encoder fit on all rows (statistics include the row's own target).
        (_spec(transform="target_encode", inputs=("c",), encodes_target=True, fit_rows="all_rows"),
         {"fit_outside_fold", "target_encoding_not_out_of_fold"}),
        # Fold-fitted but not cross-fitted: a training row sees its own target.
        (_spec(transform="target_encode", inputs=("c",), encodes_target=True, cross_fitted=False),
         {"target_encoding_not_out_of_fold"}),
        # Leave-one-out "cross-fitting" (K = n rows) is not cross-fitting.
        (_spec(transform="target_encode", inputs=("c",), encodes_target=True, cross_fitted=True, cross_fit_folds=180),
         {"target_encoding_not_out_of_fold"}),
        # A whole-table aggregate used as a feature.
        (_spec(transform="history_mean", inputs=("v",), fit_scope="as_of_aggregate", fit_rows="all_rows",
               aggregates_history=True),
         {"whole_table_aggregate", "as_of_column_missing", "as_of_cutoff_not_strict", "as_of_group_missing"}),
        # ... or computed on the training partition without a cutoff (item 12 mutant).
        (_spec(**{**AS_OF, "fit_rows": "training_partition"}), {"whole_table_aggregate"}),
        # An as-of aggregate "fitted" per fold is a confused declaration, not a pass.
        (_spec(**{**AS_OF, "fit_rows": "fold_train"}), {"as_of_with_fit"}),
        # An as-of aggregate that includes the current row / same-timestamp rows.
        (_spec(**{**AS_OF, "cutoff": "at_or_before"}), {"as_of_cutoff_not_strict"}),
        # An as-of aggregate cutting off on a post-outcome timestamp instead of prediction time.
        (_spec(**{**AS_OF, "as_of_column": "closed_at"}), {"as_of_column_not_prediction_time"}),
        # ... or grouping by something other than the entity / split group.
        (_spec(**{**AS_OF, "group_column": "region"}), {"as_of_group_not_entity"}),
        # A strictly-before aggregate that is not group-aware.
        (_spec(**{**AS_OF, "group_column": None}), {"as_of_group_missing"}),
        # Feature selection on the full table / on the training partition before folding.
        (_spec(transform="select_k_best", inputs=("x1", "x2"), supervised_fit=True, selects_features=True,
               fit_rows="all_rows"),
         {"fit_outside_fold", "selection_outside_cv", "target_encoding_not_out_of_fold"}),
        (_spec(transform="select_k_best", inputs=("x1", "x2"), supervised_fit=True, selects_features=True,
               fit_rows="training_partition"),
         {"fit_outside_fold", "selection_outside_cv", "target_encoding_not_out_of_fold"}),
        # Fitting anything on the holdout.
        (_spec(fit_rows="holdout"), {"fit_on_holdout", "fit_outside_fold"}),
        (_spec(transform="datetime_extract", fit_scope="partition_structural", fit_rows="holdout"),
         {"fit_on_holdout", "partition_fit_rows"}),
        # Declarations that contradict the registry.
        (_spec(transform="keep", fit_scope="stateless", fit_rows="all_rows"), {"stateless_with_fit"}),
        (_spec(transform="impute_median", fit_scope="stateless", fit_rows="none"), {"fit_scope_mismatch"}),
        (_spec(transform="datetime_extract", fit_scope="stateless", fit_rows="none"), {"fit_scope_mismatch"}),
        # The target is never an input, output, as-of or group column.
        (_spec(inputs=("y",)), {"target_in_inputs"}),
        (_spec(outputs=("y",)), {"target_in_inputs"}),
        (_spec(**{**AS_OF, "group_column": "y"}), {"target_in_inputs", "as_of_group_not_entity"}),
    ],
)
def test_leaking_feature_specs_fail(spec, expected):
    plan = FeaturePlan(transforms=(spec,), target_column="y", time_column="t", group_column="g")
    violations = validate_feature_plan(plan, registry=EXTENDED)
    assert set(_codes(violations)) == expected
    assert all(item.index == 0 and item.transform == spec.transform for item in violations)


def test_as_of_aggregates_fail_closed_without_prediction_time_or_entity_columns():
    plan = FeaturePlan(transforms=(_spec(**AS_OF),), target_column="y")  # neither column known
    assert _codes(validate_feature_plan(plan, registry=EXTENDED)) == [
        "as_of_column_not_prediction_time", "as_of_group_not_entity",
    ]
    with_time = FeaturePlan(transforms=(_spec(**AS_OF),), target_column="y", time_column="t")
    assert _codes(validate_feature_plan(with_time, registry=EXTENDED)) == ["as_of_group_not_entity"]
    bound = FeaturePlan(transforms=(_spec(**AS_OF),), target_column="y", time_column="t", group_column="g")
    assert validate_feature_plan(bound, registry=EXTENDED) == ()


def test_agent_features_are_branches_without_holdout_and_within_budget():
    keep = spec_for_transform("keep", "x1")
    agent = FeaturePlan(transforms=(keep,), proposed_by="agent", evidence_scopes=("cv", "holdout"))
    assert _codes(validate_feature_plan(agent)) == ["agent_holdout_evidence"]
    rule = FeaturePlan(transforms=(keep,), proposed_by="rule", evidence_scopes=("holdout",))
    assert _codes(validate_feature_plan(rule)) == ["agent_holdout_evidence"]
    # The contract has no rule for a HUMAN citing holdout evidence (branch comparisons show
    # holdout diffs today); counting ``holdout_consulted`` per SplitPlan is a P5.4/UI follow-up.
    assert "agent_holdout_evidence" not in _codes(validate_feature_plan(
        FeaturePlan(transforms=(keep,), evidence_scopes=("cv", "holdout"))))
    other_plan = FeaturePlan(transforms=(keep,), proposed_by="agent", split_plan_id="sp-2", parent_split_plan_id="sp-1")
    assert _codes(validate_feature_plan(other_plan)) == ["split_plan_mismatch"]
    no_plan = FeaturePlan(transforms=(keep,), proposed_by="agent", split_plan_id=None, parent_split_plan_id="sp-1")
    assert _codes(validate_feature_plan(no_plan)) == ["split_plan_mismatch"]
    assert FEATURE_ATTEMPT_BUDGET_PER_LOOP == 8
    assert check_feature_budget(7) is None
    assert check_feature_budget(8).code == "feature_budget_exceeded"
    assert check_feature_budget(6, requested=3, budget=8).code == "feature_budget_exceeded"
    assert check_feature_budget(0, requested=2, budget=2) is None
    exhausted = FeaturePlan(transforms=(keep, spec_for_transform("keep", "x2")), proposed_by="rule", attempts_used=7)
    assert _codes(validate_feature_plan(exhausted)) == ["feature_budget_exceeded"]
    assert validate_feature_plan(exhausted.model_copy(update={"attempts_used": 6})) == ()
    with pytest.raises(ValueError):
        check_feature_budget(-1)


# --- mutation-style data probes: target encoding --------------------------------


def _leaky_target_encoder(frame, folds):
    means = frame.groupby("c")["y"].mean()
    return pd.DataFrame({"c_te": frame["c"].map(means).to_numpy()})


def _out_of_fold_target_encoder(frame, folds):
    out = np.full(len(frame), np.nan)
    for train_rows, val_rows in folds:
        means = frame.iloc[train_rows].groupby("c")["y"].mean()
        out[val_rows] = frame.iloc[val_rows]["c"].map(means).to_numpy()
    return pd.DataFrame({"c_te": out})


def _own_target_feature(frame, folds):
    """A validation row 'encoded' with its own label: train-fold means blended with own y."""

    out = _out_of_fold_target_encoder(frame, folds)["c_te"].to_numpy()
    return pd.DataFrame({"c_te": 0.5 * out + 0.5 * frame["y"].to_numpy()})


def test_probe_catches_a_target_encoder_fit_on_all_rows_and_passes_out_of_fold():
    frame = _frame()
    folds = _folds(len(frame))
    leaked = fold_influence_probe(_leaky_target_encoder, frame, folds, perturb=["y"], check_perturbed_rows=True)
    assert leaked.leaked and leaked.columns == ("c_te",) and leaked.affected == (0, 1, 2, 3) and leaked.tested == 4
    # A validation row reading its OWN target is only visible on the perturbed rows (item 5).
    assert not fold_influence_probe(_own_target_feature, frame, folds, perturb=["y"]).leaked
    assert fold_influence_probe(_own_target_feature, frame, folds, perturb=["y"], check_perturbed_rows=True).leaked
    clean = fold_influence_probe(_out_of_fold_target_encoder, frame, folds, perturb=["y"], check_perturbed_rows=True)
    assert not clean.leaked and clean.to_dict()["affected"] == []


def _naive_in_fold_target_encoder(frame, train_rows):
    """Fit on the fold's training rows, transform the same rows: a row sees its own target."""

    train = frame.iloc[train_rows]
    means = train.groupby("c")["y"].mean()
    return pd.DataFrame({"c_te": train["c"].map(means).to_numpy()})


def _leave_one_out_target_encoder(frame, train_rows):
    """(S_c - y_i) / (n_c - 1): the row's own target is out, every peer's is in."""

    train = frame.iloc[train_rows].reset_index(drop=True)
    sums = train.groupby("c")["y"].transform("sum")
    counts = train.groupby("c")["y"].transform("count")
    return pd.DataFrame({"c_te": ((sums - train["y"]) / (counts - 1).replace(0, np.nan)).to_numpy()})


def _cross_fitted_target_encoder(inner: int = 5, shuffle: bool = False):
    """Nested out-of-fold: each training row is encoded with the OTHER inner folds' targets."""

    def encode(frame, train_rows):
        train = frame.iloc[train_rows].reset_index(drop=True)
        n = len(train)
        order = np.random.default_rng(0).permutation(n) if shuffle else np.arange(n)
        out = np.full(n, np.nan)
        for held in np.array_split(order, inner):
            fit_rows = np.setdiff1d(np.arange(n), held)
            means = train.iloc[fit_rows].groupby("c")["y"].mean()
            out[held] = train.iloc[held]["c"].map(means).to_numpy()
        return pd.DataFrame({"c_te": out})

    return encode


@pytest.mark.parametrize("seed", range(8))
def test_probe_catches_non_cross_fitted_and_leave_one_out_target_encoders_on_training_rows(seed):
    frame = _frame(seed=100 + seed)
    train_rows = _folds(len(frame))[0][0]
    kwargs = dict(target="y", peer_column="c", seed=seed)
    naive = self_target_influence_probe(_naive_in_fold_target_encoder, frame, train_rows, **kwargs)
    assert naive.leaked and "c_te" in naive.columns and naive.tested == 24
    loo = self_target_influence_probe(_leave_one_out_target_encoder, frame, train_rows, **kwargs)
    assert loo.leaked and loo.tested == 24 and any(c.startswith("<every peer moved") for c in loo.columns), loo.to_dict()
    # Without the peer check LOO slips through: the definition and the probe need both halves.
    assert not self_target_influence_probe(_leave_one_out_target_encoder, frame, train_rows, target="y", seed=seed).leaked
    for inner, shuffle in ((2, False), (5, False), (10, False), (5, True)):
        clean = self_target_influence_probe(_cross_fitted_target_encoder(inner, shuffle), frame, train_rows, **kwargs)
        assert not clean.leaked, (inner, shuffle, clean.to_dict())


# --- mutation-style data probes: the engine's real fold-fitted steps -------------


def _median_state(fit_rows_of):
    def fit(frame, train_rows):
        imputer = SimpleImputer(strategy="median").fit(frame.iloc[fit_rows_of(frame, train_rows)][["x1"]])
        return {"statistics_": imputer.statistics_}

    return fit


def _mode_state(fit_rows_of):
    def fit(frame, train_rows):
        imputer = SimpleImputer(strategy="most_frequent").fit(frame.iloc[fit_rows_of(frame, train_rows)][["c"]])
        return {"statistics_": imputer.statistics_}

    return fit


def _onehot_state(fit_rows_of):
    def fit(frame, train_rows):
        enc = OneHotEncoder(handle_unknown="ignore").fit(frame.iloc[fit_rows_of(frame, train_rows)][["c"]].fillna("missing"))
        return {"categories_": enc.categories_[0]}

    return fit


def _selector_state(fit_rows_of):
    def fit(frame, train_rows):
        rows = frame.iloc[fit_rows_of(frame, train_rows)]
        selector = SelectKBest(f_classif, k=1).fit(rows[["x1", "x2"]].fillna(0.0), rows["y"])
        return {"scores_": selector.scores_, "support": selector.get_support()}

    return fit


def _fold_train(frame, train_rows):
    return train_rows


def _all_rows(frame, train_rows):
    return np.arange(len(frame))


@pytest.mark.parametrize(
    "name, state, perturb, new_levels",
    [
        ("impute_median", _median_state, ["x1"], False),
        ("impute_most_frequent", _mode_state, ["c"], False),
        ("one_hot_vocabulary", _onehot_state, ["c"], True),
        ("select_k_best", _selector_state, ["y"], False),
    ],
)
def test_fitted_state_probe_catches_each_step_fit_outside_the_fold(name, state, perturb, new_levels):
    frame = _frame()
    folds = _folds(len(frame))
    leaked = fitted_state_probe(state(_all_rows), frame, folds, perturb=perturb, new_levels=new_levels)
    assert leaked.leaked and leaked.tested == 4, (name, leaked.to_dict())
    clean = fitted_state_probe(state(_fold_train), frame, folds, perturb=perturb, new_levels=new_levels)
    assert not clean.leaked, (name, clean.to_dict())


def _imputer_outputs(strategy: str, column: str, fit_rows_of):
    def produce(frame, folds):
        out = pd.DataFrame({column: np.full(len(frame), np.nan, dtype=object)})
        for train_rows, val_rows in folds:
            imputer = SimpleImputer(strategy=strategy).fit(frame.iloc[fit_rows_of(frame, train_rows)][[column]])
            out.iloc[val_rows, 0] = imputer.transform(frame.iloc[val_rows][[column]])[:, 0]
        return out if strategy == "most_frequent" else out.astype(float)

    return produce


def test_output_probe_catches_imputers_fit_outside_the_fold_through_the_missing_cells():
    frame = _frame()
    folds = _folds(len(frame))
    for strategy, column in (("median", "x1"), ("most_frequent", "c")):
        leaked = fold_influence_probe(_imputer_outputs(strategy, column, _all_rows), frame, folds, perturb=[column])
        assert leaked.leaked and leaked.columns == (column,), (strategy, leaked.to_dict())
        clean = fold_influence_probe(_imputer_outputs(strategy, column, _fold_train), frame, folds, perturb=[column])
        assert not clean.leaked, (strategy, clean.to_dict())


def _selected_values(fit_rows_of):
    """A real selector that emits only the selected column's values (no scores)."""

    def produce(frame, folds):
        out = pd.DataFrame({"selected": np.full(len(frame), np.nan)})
        for train_rows, val_rows in folds:
            rows = frame.iloc[fit_rows_of(frame, train_rows)]
            selector = SelectKBest(f_classif, k=1).fit(rows[["x1", "x2"]].fillna(0.0), rows["y"])
            out.iloc[val_rows, 0] = selector.transform(frame.iloc[val_rows][["x1", "x2"]].fillna(0.0))[:, 0]
        return out

    return produce


def test_output_probe_catches_full_table_selection_when_the_decision_flips():
    """Even rows follow x1, odd rows x2 (plus a few more x1 rows so x1 wins on the full
    table); the probe perturbs the even half of a fold, which flips a full-table pick."""

    rng = np.random.default_rng(3)
    n = 240
    x1, x2 = rng.normal(size=n), rng.normal(size=n)
    index = np.arange(n)
    follow_x1 = (index % 2 == 0) | (index % 12 == 1)
    y = np.where(follow_x1, x1 > 0, x2 > 0).astype(int)
    frame = pd.DataFrame({"x1": x1, "x2": x2, "y": y})
    folds = _folds(n)
    assert fold_influence_probe(_selected_values(_all_rows), frame, folds, perturb=["y"]).leaked
    assert not fold_influence_probe(_selected_values(_fold_train), frame, folds, perturb=["y"]).leaked


def _prep_frame(prep, rows: pd.DataFrame) -> pd.DataFrame:
    values = np.asarray(prep.transform(rows[["x1", "c"]]), dtype=float)
    return pd.DataFrame(values, columns=list(prep.get_feature_names_out()), index=rows.index)


def _preprocessor_fit_per_fold(frame, folds):
    """The engine's preprocessor fit on each fold's training rows (as the CV loop does)."""

    parts = []
    for train_rows, val_rows in folds:
        prep = build_preprocessor(["x1"], ["c"]).fit(frame.iloc[train_rows][["x1", "c"]])
        parts.append(_prep_frame(prep, frame.iloc[val_rows]))
    return pd.concat(parts).sort_index().fillna(0.0)


def _preprocessor_fit_on_all_rows(frame, folds):
    """The mutation: the same preprocessor fit once on the whole table before the folds."""

    prep = build_preprocessor(["x1"], ["c"]).fit(frame[["x1", "c"]])
    return _prep_frame(prep, frame)


def test_probe_catches_the_real_preprocessor_fit_outside_the_fold():
    frame = _frame()
    folds = _folds(len(frame))
    leaked = fold_influence_probe(_preprocessor_fit_on_all_rows, frame, folds, perturb=["x1", "c"])
    assert leaked.leaked and {"num__x1"} <= set(leaked.columns), leaked.to_dict()
    clean = fold_influence_probe(_preprocessor_fit_per_fold, frame, folds, perturb=["x1", "c"])
    assert not clean.leaked, clean.to_dict()


# --- the real open-ingest CV loop: which rows does each preprocessor fit see? --------


def _runner_frame(n: int = 220, seed: int = 3) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    tenure = rng.integers(1, 72, n)
    monthly = rng.uniform(20, 120, n)  # continuous and unique: identifies a row in any fit
    contract = rng.choice(["Month-to-month", "One year", "Two year"], n)
    churn_p = np.where(contract == "Month-to-month", 0.6, 0.15)
    churn = rng.binomial(1, churn_p)
    return pd.DataFrame({"tenure": tenure, "MonthlyCharges": monthly, "contract": contract,
                         "churn": np.where(churn == 1, "Yes", "No")})


def _spy_preprocessor_fits(monkeypatch) -> list[frozenset[float]]:
    """Record the MonthlyCharges values (one per row) every ColumnTransformer fit sees."""

    seen: list[frozenset[float]] = []
    original_fit, original_fit_transform = ColumnTransformer.fit, ColumnTransformer.fit_transform

    def fit(self, X, y=None, **params):
        seen.append(frozenset(float(v) for v in X["MonthlyCharges"]))
        return original_fit(self, X, y, **params)

    def fit_transform(self, X, y=None, **params):
        seen.append(frozenset(float(v) for v in X["MonthlyCharges"]))
        return original_fit_transform(self, X, y, **params)

    monkeypatch.setattr(ColumnTransformer, "fit", fit)
    monkeypatch.setattr(ColumnTransformer, "fit_transform", fit_transform)
    return seen


def _run_open_ingest(frame: pd.DataFrame) -> dict:
    columns = [c for c in frame.columns if c != "churn"]
    num_cols, cat_cols = split_column_roles(frame, columns)
    task = TaskSpec(id="feature_contract_cv", name="t", task_type="binary", target="churn", entity_id=None,
                    prediction_time_column=None, evaluation_metric="roc_auc",
                    feature_groups={"features": num_cols + cat_cols}, validation_strategy="stratified",
                    column_roles={"numerical": num_cols, "categorical": cat_cols})
    config = SearchConfig(strategy="open_ingest", max_candidates=2, seed=42, families=["logistic_regression"])
    with tempfile.TemporaryDirectory() as tmp:
        return runner.run_experiment(frame, task, config, artifact_dir=Path(tmp), dataset_version="v1")


def _assert_fits_are_fold_local(result: dict, frame: pd.DataFrame, seen: list[frozenset[float]]) -> None:
    split = result["split"]
    train_rows = [int(i) for i in split["train_source_rows"]]
    test_rows = [int(i) for i in split["test_source_rows"]]
    assert set(train_rows).isdisjoint(test_rows) and len(train_rows) + len(test_rows) == len(frame)
    pool = frozenset(float(v) for v in frame.iloc[train_rows]["MonthlyCharges"])
    holdout = frozenset(float(v) for v in frame.iloc[test_rows]["MonthlyCharges"])
    assert seen, "no preprocessor fit was observed"
    for rows in seen:
        assert rows <= pool, "a preprocessor fit saw a row outside the training pool"
        assert rows.isdisjoint(holdout), "a preprocessor fit saw a holdout row"
    cv_fits = {rows for rows in seen if rows != pool}
    assert pool in seen, "the final refit on the whole training pool was not observed"
    # Every CV-time fit is the pool minus exactly one validation fold; the folds partition the pool.
    validation_folds = [pool - rows for rows in cv_fits]
    trained = [row for row in result["candidates"] if row["status"] == "trained"]
    assert trained and all("n_folds" in row for row in trained)
    n_folds = trained[0]["n_folds"]
    assert len(validation_folds) == n_folds, (len(validation_folds), n_folds)
    assert all(fold for fold in validation_folds)
    assert sum(len(fold) for fold in validation_folds) == len(pool)
    assert frozenset().union(*validation_folds) == pool


def test_the_real_cv_loop_fits_the_preprocessor_on_each_folds_training_rows_only(monkeypatch):
    frame = _runner_frame()
    seen = _spy_preprocessor_fits(monkeypatch)
    result = _run_open_ingest(frame)
    assert result["status"] == "COMPLETED"
    assert result["scientific_evidence"]["preprocessing_fit_scope"] == "fold_train"
    _assert_fits_are_fold_local(result, frame, seen)


def test_mutation_proof_a_cv_fit_on_all_training_rows_is_detected(monkeypatch):
    """Break the runner so every CV-time fit sees the whole training pool: the check fails."""

    original = runner.iter_validation_folds

    def all_rows_folds(plan, frame, y):
        for fold in original(plan, frame, y):
            yield dataclasses.replace(fold, train_index=np.arange(len(frame)), train_count=len(frame))

    monkeypatch.setattr(runner, "iter_validation_folds", all_rows_folds)
    frame = _runner_frame()
    seen = _spy_preprocessor_fits(monkeypatch)
    result = _run_open_ingest(frame)
    assert result["status"] == "COMPLETED"
    with pytest.raises(AssertionError):
        _assert_fits_are_fold_local(result, frame, seen)


# --- mutation-style data probes: as-of aggregates ----------------------------------


def _history(frame: pd.DataFrame, *, strict: bool, grouped: bool = True, future: bool = False) -> pd.DataFrame:
    out = np.full(len(frame), np.nan)
    times = frame["t"].to_numpy()
    values = frame["v"].to_numpy()
    groups = frame["g"].to_numpy()
    for i in range(len(frame)):
        same_group = (groups == groups[i]) if grouped else np.ones(len(frame), dtype=bool)
        if future:
            mask = same_group
        elif strict:
            mask = same_group & (times < times[i])
        else:
            mask = same_group & (times <= times[i])
        out[i] = values[mask].mean() if mask.any() else np.nan
    return pd.DataFrame({"v_hist": out})


def _history_count(frame: pd.DataFrame, *, strict: bool) -> pd.DataFrame:
    times = frame["t"].to_numpy()
    groups = frame["g"].to_numpy()
    counts = [int((((groups == groups[i]) & ((times < times[i]) if strict else (times <= times[i])))).sum())
              for i in range(len(frame))]
    return pd.DataFrame({"n_prev": counts})


def _is_last_event(frame: pd.DataFrame) -> pd.DataFrame:
    times = frame["t"].to_numpy()
    groups = frame["g"].to_numpy()
    last = [bool(not ((groups == groups[i]) & (times > times[i])).any()) for i in range(len(frame))]
    return pd.DataFrame({"is_last": np.asarray(last, dtype=float)})


def _sorted_cumsum_shift(frame: pd.DataFrame) -> pd.DataFrame:
    """The classic tie bug: sort by time, cumulative mean, shift(1) — earlier SAME-timestamp
    rows of the group leak into the row."""

    ordered = frame.sort_values(["g", "t"], kind="stable")
    prev_sum = ordered.groupby("g")["v"].cumsum().shift(1).where(ordered["g"].eq(ordered["g"].shift(1)))
    prev_n = ordered.groupby("g").cumcount()
    out = (prev_sum / prev_n.replace(0, np.nan)).reindex(frame.index)
    return pd.DataFrame({"v_hist": out.to_numpy()})


def _history_strict_vectorised(frame: pd.DataFrame) -> pd.DataFrame:
    """Strictly-before group mean without the O(n^2) loop: per (group, timestamp) sums and
    counts, cumulative within the group, minus the row's own timestamp bucket."""

    rows = frame[["g", "t", "v"]].copy()
    rows["_i"] = np.arange(len(rows))
    buckets = rows.groupby(["g", "t"])["v"].agg(["sum", "count"]).reset_index().sort_values(["g", "t"], kind="stable")
    buckets["prev_sum"] = buckets.groupby("g")["sum"].cumsum() - buckets["sum"]
    buckets["prev_n"] = buckets.groupby("g")["count"].cumsum() - buckets["count"]
    buckets["v_hist"] = buckets["prev_sum"] / buckets["prev_n"].replace(0, np.nan)
    merged = rows.merge(buckets[["g", "t", "v_hist"]], on=["g", "t"], how="left").sort_values("_i")
    return pd.DataFrame({"v_hist": merged["v_hist"].to_numpy()})


def test_truncation_replay_catches_whole_table_non_strict_tie_and_time_derived_leaks():
    frame = _frame(n=120, missing=0.0)
    kwargs = dict(time_column="t", perturb=["v"], group_column="g")
    pd.testing.assert_frame_equal(_history_strict_vectorised(frame), _history(frame, strict=True))
    whole_table = future_influence_probe(lambda f: _history(f, strict=False, future=True), frame, **kwargs)
    assert whole_table.leaked and whole_table.columns == ("v_hist",) and whole_table.tested == len(frame)
    at_or_before = future_influence_probe(lambda f: _history(f, strict=False), frame, **kwargs)
    assert at_or_before.leaked  # the row itself and same-timestamp rows are read
    assert future_influence_probe(_sorted_cumsum_shift, frame, time_column="t", group_column="g").leaked
    assert future_influence_probe(lambda f: _history_count(f, strict=False), frame, time_column="t").leaked
    assert future_influence_probe(_is_last_event, frame, time_column="t").leaked
    strictly_before = future_influence_probe(lambda f: _history(f, strict=True), frame, **kwargs)
    assert not strictly_before.leaked, strictly_before.to_dict()
    assert not future_influence_probe(lambda f: _history_count(f, strict=True), frame, time_column="t").leaked
    # Self-read without a time leak: strictly-before history that also folds in the row's own value.
    self_reading = lambda f: pd.DataFrame({"v_hist": _history(f, strict=True)["v_hist"].fillna(0) + f["v"].to_numpy()})
    assert future_influence_probe(self_reading, frame, time_column="t").leaked is False
    assert future_influence_probe(self_reading, frame, **kwargs).leaked


def test_truncation_replay_above_the_exhaustive_size_still_covers_ties_and_group_edges():
    rng = np.random.default_rng(12)
    big = _frame(n=2600, seed=12, missing=0.0)
    big["t"] = pd.Timestamp("2026-01-01") + pd.to_timedelta(rng.integers(0, 60 * 24 * 365, size=len(big)), unit="min")
    # Rows 10..29 copy the timestamps of rows 0..19: twenty same-timestamp pairs across groups.
    big.loc[10:29, "t"] = big.loc[0:19, "t"].to_numpy()
    kwargs = dict(time_column="t", perturb=["v"], group_column="g")
    sampled = future_influence_probe(_history_strict_vectorised, big, **kwargs)
    assert not sampled.leaked and 24 < sampled.tested < len(big)
    assert future_influence_probe(_sorted_cumsum_shift, big, time_column="t", group_column="g").leaked


def test_perturbation_changes_every_touched_cell_within_existing_levels():
    frame = _frame(n=40, missing=0.0)
    frame.loc[3, "x1"] = np.nan
    frame.loc[5, "y"] = np.nan
    frame["k"] = np.arange(40) % 4  # a low-cardinality integer column: classes, not a quantity
    rows = np.array([0, 3, 5, 7])
    mutated = perturb_rows(frame, rows, ["x1", "y", "c", "k"])
    flipped = mutated.loc[rows, "y"].astype(float).to_numpy()
    assert flipped[[0, 1, 3]].tolist() == (1 - frame.loc[[0, 3, 7], "y"]).tolist() and flipped[2] in (0.0, 1.0)
    assert (mutated.loc[rows, "c"] != frame.loc[rows, "c"]).all()
    assert set(mutated["c"]) <= set(frame["c"])  # no invented levels
    assert (mutated.loc[rows, "k"] != frame.loc[rows, "k"]).all() and set(mutated["k"]) <= {0, 1, 2, 3}
    changed = mutated.loc[rows, "x1"].ne(frame.loc[rows, "x1"]) | frame.loc[rows, "x1"].isna()
    assert changed.all() and mutated.loc[3, "x1"] == mutated.loc[3, "x1"]  # the NaN became a number
    untouched = frame.index.difference(rows)
    pd.testing.assert_frame_equal(mutated.loc[untouched], frame.loc[untouched], check_dtype=False)
    # new_levels only applies to discrete columns; numerics keep their numeric scheme.
    novel = perturb_rows(frame, rows, ["x1", "c"], new_levels=True)
    assert pd.api.types.is_float_dtype(novel["x1"]) and novel.loc[rows, "c"].str.endswith("__probe").all()
    with pytest.raises(ValueError, match="no finite value"):
        perturb_rows(frame.assign(empty=np.nan), rows, ["empty"])
    with pytest.raises(ValueError, match="single level"):
        perturb_rows(frame.assign(one="only"), rows, ["one"])
    with pytest.raises(ValueError, match="nothing could be tested"):
        fold_influence_probe(lambda f, folds: f[["x1"]], frame, [(np.arange(1, 40), np.array([0]))], perturb=["x1"])


# --- the sanctioned exception: exclusion-only leakage audit, probed on data ---------


def _audit_decisions(train: pd.DataFrame) -> dict[str, str]:
    audit = audit_leakage(train, target="y", task_type="binary")
    return {risk.column: ("exclude" if risk.action == "exclude" else "keep") for risk in audit.risks}


def _top_k_mutant(train: pd.DataFrame, k: int = 2) -> dict[str, str]:
    """'Drop all but the k most target-associated columns': exclusion by rank, not threshold."""

    scores = {}
    for name in train.columns:
        if name == "y" or not pd.api.types.is_numeric_dtype(train[name]):
            continue
        values = train[name].fillna(train[name].median())
        scores[name] = abs(roc_auc_score(train["y"], values) - 0.5)
    top = sorted(scores, key=scores.get, reverse=True)[:k]
    return {name: ("keep" if name in top else "exclude") for name in scores}


def _exclusion_only_probes(decide, train: pd.DataFrame, seed: int = 9) -> dict[str, bool]:
    """(a) pure-noise columns never change an existing decision; (b) perturbing one KEPT
    column (toward the target or toward noise) never turns an excluded column into a kept one."""

    rng = np.random.default_rng(seed)
    base = decide(train)
    with_noise = train.assign(**{f"noise_{i}": rng.normal(size=len(train)) for i in range(3)})
    noise_ok = all(decide(with_noise)[name] == base[name] for name in base)
    monotone_ok = True
    for name, decision in base.items():
        if decision != "keep" or not pd.api.types.is_numeric_dtype(train[name]):
            continue
        toward_target = train.assign(**{name: train["y"] * 3.0 + rng.normal(scale=0.3, size=len(train))})
        toward_noise = train.assign(**{name: rng.normal(size=len(train))})
        for variant in (toward_target, toward_noise):
            after = decide(variant)
            if any(base[other] == "exclude" and after.get(other) == "keep" for other in base if other != name):
                monotone_ok = False
    return {"noise_invariant": noise_ok, "no_inclusion_by_perturbation": monotone_ok}


def _audit_frame() -> pd.DataFrame:
    frame = _frame(missing=0.0).drop(columns=["t", "g"])
    rng = np.random.default_rng(5)
    return frame.assign(leak=frame["y"], weak=0.3 * frame["y"] + rng.normal(size=len(frame)))


def test_leakage_audit_is_exclusion_only_on_data_and_a_top_k_rule_is_not():
    train = _audit_frame()
    real = _audit_decisions(train)
    assert real["leak"] == "exclude" and real["x2"] == "keep"
    assert _exclusion_only_probes(_audit_decisions, train) == {"noise_invariant": True, "no_inclusion_by_perturbation": True}
    mutant = _top_k_mutant(train)
    assert mutant["leak"] == "keep" and "exclude" in mutant.values()
    assert _exclusion_only_probes(_top_k_mutant, train)["no_inclusion_by_perturbation"] is False
    assert "holdout" not in inspect.signature(audit_leakage).parameters


# --- conformance: every registered transform through the probes ------------------


def _date_frame(n: int = 120, seed: int = 5) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    days = pd.Timestamp("2025-01-03") + pd.to_timedelta(rng.integers(0, 400, size=n), unit="D")
    return pd.DataFrame({"signup_date": days.strftime("%Y/%m/%d"), "x1": rng.normal(size=n)})


def _role_overrides(columns: dict) -> tuple[list[str], list[str]]:
    branch = BranchRun(experiment_id=uuid4(), parent_id=uuid4(), split_plan=None, target_column="y",
                       task_type="binary", overrides={"columns": columns}, objective=None)
    return apply_role_overrides(branch, _frame(), ["x1", "x2"], ["c"], allowed_predictors={"x1", "x2", "c"},
                                identifiers=set())


@pytest.mark.parametrize("name", list(FEATURE_TRANSFORM_REGISTRY))
def test_registered_transform_implementations_conform_to_their_declared_scope(name):
    rule = FEATURE_TRANSFORM_REGISTRY[name]
    frame = _frame()
    folds = _folds(len(frame))
    if name == "drop_column":
        # Stateless: the real override reader removes the column from the modeled roles, reading no data.
        assert _role_overrides({"x2": {"treatment": "drop"}}) == (["x1"], ["c"])
        assert "frame" not in inspect.signature(apply_role_overrides).parameters
    elif name == "keep":
        assert _role_overrides({"x2": {"treatment": "keep"}}) == (["x1", "x2"], ["c"])
        # A kept role is a role (dtype check only), never a fitted statistic.
        assert _role_overrides({"x2": {"treatment": "numeric"}}) == (["x1", "x2"], ["c"])
    elif name == "impute_median":
        assert rule.fit_scope == "fold_fitted"
        assert not fitted_state_probe(_median_state(_fold_train), frame, folds, perturb=["x1"]).leaked
        assert not fold_influence_probe(_preprocessor_fit_per_fold, frame, folds, perturb=["x1", "c"]).leaked
    elif name == "impute_most_frequent":
        assert rule.fit_scope == "fold_fitted"
        assert not fitted_state_probe(_mode_state(_fold_train), frame, folds, perturb=["c"]).leaked
        assert not fitted_state_probe(_onehot_state(_fold_train), frame, folds, perturb=["c"], new_levels=True).leaked
    elif name == "datetime_extract":
        # partition_structural: the parse decision (a column-level statistic over the training
        # partition) and the format (first non-null value) are inferred once and replayed; the
        # recorded ``learned_from_data: False`` flag means "no per-row statistic" (contract docstring).
        dates = _date_frame()
        train, holdout = dates.iloc[:80], dates.iloc[80:]
        _engineered, actions = engineer_features(train, ["signup_date", "x1"])
        assert [a["columns"] for a in actions] == [["signup_date"]] and actions[0]["learned_from_data"] is False
        assert actions[0]["decision_partition"] == "train"
        assert actions[0]["parameters"]["formats"]["signup_date"] == infer_datetime_format(train["signup_date"]) == "%Y/%m/%d"
        shifted = holdout.assign(signup_date=holdout["signup_date"].str.replace("/", "-"))
        replayed = apply_feature_engineering_actions(holdout, actions)["signup_date"]
        assert replayed.notna().all()
        assert apply_feature_engineering_actions(shifted, actions)["signup_date"].isna().all()  # format not re-guessed
    elif name == "leakage_exclusion":
        train = _audit_frame()
        assert rule.exclusion_only and not rule.proposable
        assert _exclusion_only_probes(_audit_decisions, train) == {"noise_invariant": True, "no_inclusion_by_perturbation": True}
    else:  # pragma: no cover - a new registry row needs its conformance case
        pytest.fail(f"no conformance case for {name!r}")


# --- engine wiring ---------------------------------------------------------------


def test_fold_fitted_transforms_live_inside_the_unfitted_pipeline():
    row = {"numerical_cols": ["x1"], "categorical_cols": ["c"], "model_family": "logistic_regression",
           "random_seed": 0, "hyperparameters": {}}
    pipe = runner._row_pipeline(row, TaskSpec(id="t", name="t", task_type="binary"))()
    assert isinstance(pipe, SkPipeline) and pipe.steps[0][0] == "prep"
    with pytest.raises(NotFittedError):
        check_is_fitted(pipe.named_steps["prep"])
    named = {name: step for name, step, _columns in pipe.named_steps["prep"].transformers}
    assert named["num"].named_steps["imputer"].strategy == "median"
    assert named["cat"].named_steps["imputer"].strategy == "most_frequent"
    for name in ("impute_median", "impute_most_frequent"):
        assert FEATURE_TRANSFORM_REGISTRY[name].fit_scope == "fold_fitted"
        assert "per CV fold" in FEATURE_TRANSFORM_REGISTRY[name].engine_step


def test_open_ingest_group_choice_is_not_a_data_dependent_selection():
    """combinations.py enumerates names only; the open-ingest stage builds exactly one group."""

    assert combinations.generate_group_combinations(["features"]) == [("features",)]
    source = inspect.getsource(combinations)
    assert "pandas" not in source and "numpy" not in source  # never sees data
    # The stage that calls it builds a single group from the modeled columns (its input needs a
    # live RunContext, so the invariant is pinned on the stage's source here).
    stage = inspect.getsource(column_roles_stage.run_column_roles)
    assert 'groups_map = {"features": num_cols + cat_cols}' in stage
    assert "generate_group_combinations(\n        list(groups_map.keys())" in stage
    task = TaskSpec(id="t", name="t", task_type="binary", target="y", entity_id=None, prediction_time_column=None,
                    feature_groups={"features": ["x1", "x2", "c"]}, column_roles={"numerical": ["x1", "x2"], "categorical": ["c"]})
    candidates = assemble_candidates(task, SearchConfig(strategy="open_ingest", max_feature_group_combinations=32))
    assert candidates and {c.feature_groups for c in candidates} == {("features",)}
    assert {c.features for c in candidates} == {("x1", "x2", "c")}


# --- branch service and agent tool wiring ------------------------------------------


def _context(**fields) -> ParentContext:
    defaults = dict(
        experiment=None, upload=None, workflow_run=None, task_type="binary", target_column="y", overrides={},
        objective=None, portfolio=["logistic_regression"], dataset_columns={"y", "x1", "c"}, reserved_columns={"y"},
        leakage_excluded=set(), identifiers=set(), dropped=set(), numeric={"x1"}, categorical={"c"},
        datetime_converted=set(), numeric_dtype={"x1"}, observed_classes=["0", "1"], time_column="t", group_column="g",
    )
    return ParentContext(**{**defaults, **fields})


def test_branch_validation_runs_the_feature_contract(monkeypatch):
    changes = parse_change_set([{"kind": "feature_transform_add", "column": "x1", "transform": "impute_median"}])
    overrides, _ = materialize_branch(changes, _context())
    assert overrides["columns"] == {"x1": {"treatment": "numeric"}}
    # The proposer only feeds the plan-level rules; the materialized overrides are identical.
    assert materialize_branch(changes, _context(), proposed_by="agent")[0] == overrides

    # Mutation: the engine's declaration of impute_median drifts to a whole-table fit.
    def drifted(transform, column, *, registry=None):
        spec = spec_for_transform(transform, column, registry=registry)
        return spec.model_copy(update={"fit_rows": "all_rows"}) if transform == "impute_median" else spec

    monkeypatch.setattr(branch_service, "spec_for_transform", drifted)
    with pytest.raises(InvalidChangeSetError) as caught:
        materialize_branch(changes, _context())
    assert (caught.value.reason, caught.value.path) == ("fit_outside_fold", "changes[0].transform")
    # Existing column errors keep precedence over the contract.
    with pytest.raises(InvalidChangeSetError) as caught:
        materialize_branch(parse_change_set([{"kind": "feature_transform_add", "column": "y", "transform": "keep"}]), _context())
    assert caught.value.reason == "reserved_column"
    # Non-feature changes never enter the contract.
    assert materialize_branch(parse_change_set([{"kind": "family_exclude", "family": "logistic_regression"}]),
                              _context(portfolio=["logistic_regression", "random_forest"]))[0]["families_exclude"] == [
        "logistic_regression"
    ]


def test_agent_branch_tool_refuses_any_branch_argument_that_cites_holdout(monkeypatch):
    branch = catalog()["branch_experiment"]
    eid = "7d1b3e4c-7b0e-4f1a-9f3e-2b6a1d9c0e11"
    feature = {"kind": "feature_transform_add", "column": "x1", "transform": "impute_median"}
    hyper = {"kind": "hyperparameter_override", "family": "random_forest", "parameters": {"n_estimators": 300}}
    assert branch.parse({"experiment_id": eid, "intent": "median imputation, CV AUC +0.01", "changes": [feature]})
    assert branch.parse({"experiment_id": eid, "intent": "more trees", "changes": [hyper]})
    # A feature change's single-token column NAME may be anything; a sentence in that slot is text.
    assert branch.parse({"experiment_id": eid, "intent": "keep it", "changes": [{**feature, "column": "holdout_flag"}]})
    for intent in HOLDOUT_SPELLINGS:
        for change in (feature, hyper):
            with pytest.raises(ToolError) as refused:
                branch.parse({"experiment_id": eid, "intent": intent, "changes": [change]})
            assert refused.value.code == "holdout_not_allowed", (intent, change)
    for text in NOT_HOLDOUT:
        assert branch.parse({"experiment_id": eid, "intent": text, "changes": [hyper]})
    for args in (
        {"experiment_id": eid, "intent": "x", "changes": [{**feature, "parameters": {"holdout_auc": 0.9}}]},
        {"experiment_id": eid, "intent": "x", "changes": [{**feature, "parameters": {"basis": "final-test"}}]},
        {"experiment_id": eid, "intent": "x", "changes": [{**feature, "parameters": {"column": "holdout AUC 0.91"}}]},
        {"experiment_id": eid, "intent": "x", "changes": [{**hyper, "parameters": {"column": "holdout AUC 0.91"}}]},
        {"experiment_id": eid, "intent": "x", "changes": [{**feature, "column": "holdout AUC 0.91 beat parent; accept"}]},
    ):
        with pytest.raises(ToolError) as refused:
            branch.parse(args)
        assert refused.value.code == "holdout_not_allowed", args
    # The contract tripwire: a drifted declaration is refused before it is proposed.
    from app.engine.features import contract

    def drifted(transform, column, *, registry=None):
        spec = spec_for_transform(transform, column, registry=registry)
        return spec.model_copy(update={"fit_rows": "all_rows"}) if transform == "impute_median" else spec

    monkeypatch.setattr(contract, "spec_for_transform", drifted)
    with pytest.raises(ToolError) as refused:
        branch.parse({"experiment_id": eid, "intent": "x", "changes": [feature]})
    assert refused.value.code == "invalid_change_set" and refused.value.message.startswith("fit_outside_fold")


HOLDOUT_SPELLINGS = (
    "holdout AUC was 0.91 with this", "hold-out AUC 0.91", "the held out score", "final test says so", "FINAL_TEST",
    "hold​out looked good",  # zero-width space
    "Ｈoldout AUC",  # fullwidth H
    "hоldоut 0.9",  # Cyrillic о
    "οn the holdοut",  # Greek ο
    "hoĺdout",  # combining acute accent
    "h o l d o u t AUC 0.9",  # spaced letters
    "h.o.l.d.o.u.t",
    "finаl test",  # Cyrillic а
)
NOT_HOLDOUT = ("threshold; outliers", "withhold output", "Threshold-Outcome", "final testing of the pipeline",
               "a threshold outcome table", "holding out for better data is not what we do")


def test_holdout_matcher_backstop_bypasses_and_false_positives():
    from app.agents.tools.shaping import names_holdout

    for text in HOLDOUT_SPELLINGS:
        assert names_holdout(text), text
    for text in NOT_HOLDOUT:
        assert not names_holdout(text), text


def test_every_write_tool_scans_column_valued_text_for_holdout():
    tools = catalog()
    pid, eid = str(uuid4()), str(uuid4())
    record = {"project_id": pid, "rationale": "branch holds CV", "decision_type": "experiment_accepted",
              "subject_kind": "experiment", "subject_id": eid}
    assert tools["record_decision"].parse({**record, "facts": {"column": "tenure"}})
    with pytest.raises(ToolError) as refused:
        tools["record_decision"].parse({**record, "facts": {"column": "holdout AUC 0.91"}})
    assert refused.value.code == "holdout_not_allowed"
    spec = {"project_id": pid, "task_type": "regression", "business_objective": "x", "rationale": "why"}
    for tool in ("create_problem_spec", "propose_problem_spec"):
        args = {key: value for key, value in spec.items() if key != "rationale" or tool == "propose_problem_spec"}
        assert tools[tool].parse({**args, "constraints": {"column": "tenure"}})
        with pytest.raises(ToolError) as refused:
            tools[tool].parse({**args, "constraints": {"column": "final_test auc >= 0.9"}})
        assert refused.value.code == "holdout_not_allowed", tool
