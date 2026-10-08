"""Trust-check findings (P4.10-A, P5.1-A): vocabulary, plain-language templates, read model.

The engine (``app.engine.investigate``) produces one typed finding per check; the run
stores them in ``experiments.result["investigation"]`` and every warning/fail finding of a
check mapped in ``CHECK_FINDING_TYPES`` as a ``data_quality_findings`` row
(``evidence.source == "investigate"``) before the scientific evidence lock. The P5.1-A
checks are stored on the result only: ``ck_data_quality_findings_type_valid`` (0070) does
not admit their finding types yet, so they have no rows until a migration widens it.
Messages are rendered here at read time from the stored template keys and numbers, so the
wording is deterministic and never stored twice.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

INVESTIGATION_VERSION = "investigate.v1"
INVESTIGATION_SOURCE = "investigate"

FindingCheck = Literal[
    "target_leakage", "overfit_gap", "duplicate_rows", "class_imbalance", "implausible_score",
    "fold_instability", "calibration", "subgroup_gap", "multicollinearity", "feature_drift", "temporal_shift",
    "missingness_shift", "contamination", "time_travel", "new_feature",
]
FindingStatus = Literal["pass", "warning", "fail", "not_evaluated"]
FindingSeverity = Literal["info", "warning", "error", "critical"]
RecommendationKind = Literal[
    "review_columns", "regularize", "simpler_model", "deduplicate", "class_weights", "collect_more_data",
    "investigate_leakage", "calibrate", "review_subgroups", "drop_correlated", "review_split",
]

FINDING_CHECKS: tuple[str, ...] = (
    "target_leakage",
    "overfit_gap",
    "duplicate_rows",
    "class_imbalance",
    "implausible_score",
    # P5.1-A: from the run's CV evidence (fold metrics, out-of-fold predictions) ...
    "fold_instability",
    "calibration",
    "subgroup_gap",
    "multicollinearity",
    # ... training vs test partition FEATURES (never a test label, prediction or metric) ...
    "feature_drift",
    "temporal_shift",
    "missingness_shift",
    "contamination",
    # ... and probes / the parent's CV.
    "time_travel",
    "new_feature",
)
# not_evaluated: the check's evidence was missing (or the check errored); never a pass.
FINDING_STATUSES: tuple[str, ...] = ("pass", "warning", "fail", "not_evaluated")
STORED_STATUSES = frozenset({"warning", "fail"})  # become data_quality_findings rows

# check -> data_quality_findings.finding_type (duplicates/target_leakage predate P4.10-A).
# Only types ``ck_data_quality_findings_type_valid`` admits (0070); the P5.1-A checks are
# deliberately absent (result-only) until a migration widens the constraint.
CHECK_FINDING_TYPES: dict[str, str] = {
    "target_leakage": "target_leakage",
    "overfit_gap": "overfit_gap",
    "duplicate_rows": "duplicates",
    "class_imbalance": "class_imbalance",
    "implausible_score": "implausible_score",
}

# Holdout scope (P5.1-A review): these checks compare training rows with the test (final holdout)
# partition's FEATURE columns. Their test-derived numbers sit under ``holdout_*`` keys (stripped for
# agents and service tokens by ``agents.tools.shaping.strip_holdout``), and agents read only status,
# severity, recommendation kind and a fixed message. The P5.4 loop proposers must NOT act on them:
# that would be feature selection informed by the holdout. Holdout size / fraction is allowed;
# holdout feature statistics are human-only.
HOLDOUT_FEATURE_CHECKS: frozenset[str] = frozenset({"feature_drift", "temporal_shift", "missingness_shift",
                                                    "contamination"})
# One fixed agent-safe message per check whose human message carries test-row numbers.
AGENT_MESSAGES: dict[str, str] = {
    "feature_drift": "Compares how the model columns are distributed in the training and test rows; the "
    "details are shown to people only.",
    "temporal_shift": "Compares the time periods of the training and test rows; the details are shown to "
    "people only.",
    "missingness_shift": "Compares missing values in the training and test rows; the details are shown to "
    "people only.",
    "contamination": "Looks for test rows that nearly copy a training row or share an entity with one; the "
    "details are shown to people only.",
    "duplicate_rows": "Counts training rows that repeat another training row and test rows identical to a "
    "training row; the numbers are in the evidence for training rows and shown to people only for test rows.",
}
# Stored P4.10-A duplicate evidence named the test side ``test_*`` / ``train_test_*``; read as holdout_*.
LEGACY_EVIDENCE_KEYS: dict[str, str] = {
    "test_rows": "holdout_rows",
    "train_test_duplicate_rows": "holdout_duplicate_rows",
    "train_test_duplicate_fraction": "holdout_duplicate_fraction",
    "expected_train_test_duplicate_rows": "expected_holdout_duplicate_rows",
    "excess_train_test_duplicate_fraction": "excess_holdout_duplicate_fraction",
}
HOLDOUT_COMPARISON = "holdout_comparison"  # per-column rows / lists that depend on test rows

_WHERE = "{metric} {train_score} on the rows it trained on"
MESSAGE_TEMPLATES: dict[str, str] = {
    "check_error": "Not evaluated: this check could not run ({error_type}); the other checks are unaffected.",
    "target_leakage.pass": "No column looks like it gives away the answer: the leakage audit excluded "
    "nothing and flagged none of the {modeled_feature_count} columns the model uses.",
    "target_leakage.excluded": "{excluded_count} column(s) looked like they give away the answer and were "
    "left out of the model: {excluded_columns}. Confirm their values are not known only after the outcome.",
    "target_leakage.flagged": "{flagged_count} column(s) the model uses have a suspicious name or a strong "
    "single-column score: {flagged_columns}. Check that their values are known at prediction time.",
    "target_leakage.kept_risky": "{risky_count} column(s) the model uses carry a medium or higher leakage "
    "risk: {risky_columns}. Review them before trusting this model.",
    "target_leakage.not_evaluated": "Not evaluated: no leakage audit was recorded for this run.",
    "overfit_gap.pass": f"The model scores {_WHERE} and {{cv_score}} on unseen cross-validation folds (gap "
    "{absolute_gap}): not a sign of memorizing the training rows.",
    "overfit_gap.within_noise": f"The model scores {_WHERE} and {{cv_score}} on unseen cross-validation folds "
    "(gap {absolute_gap}), but its fold scores themselves vary by {cv_std}, so the gap is within fold-to-fold "
    "noise.",
    "overfit_gap.gap": f"The model scores {_WHERE} but {{cv_score}} on unseen cross-validation folds (gap "
    "{absolute_gap}, {gap_in_cv_std} times the fold-to-fold spread).",
    "overfit_gap.memorizing": "It may be memorizing the training rows; a more regularized model may "
    "generalize better.",
    "overfit_gap.simpler_matches": "The simpler {simple_family} scores about the same in cross-validation "
    "({simple_cv_score}), so the extra flexibility buys little; prefer the simpler model.",
    "overfit_gap.flexible_by_design": f"The model fits its training rows closely ({_WHERE}) but still beats "
    "the simpler {simple_family} in cross-validation ({cv_score} vs {simple_cv_score}); the cross-validation "
    "score is the honest estimate.",
    "overfit_gap.fit_by_design": f"Training fit is near-perfect by design for {{winner_family}} ({_WHERE}); "
    "the cross-validation score of {cv_score} is the honest estimate.",
    "overfit_gap.retuned": "The final model was re-tuned on all training rows, so its training score comes "
    "from slightly different settings than the folds.",
    "overfit_gap.swapped_metric": "{primary_metric} is not compared here because training rows are scored at "
    "the locked decision threshold and folds at 0.5; {metric} does not depend on a threshold.",
    "overfit_gap.missing_train_score": "Not evaluated: the winning model's {metric} on its own training rows "
    "was not recorded.",
    "overfit_gap.missing_cv_score": "Not evaluated: the winning model's cross-validated {metric} was not "
    "recorded.",
    "overfit_gap.no_comparable_metric": "Not evaluated: {primary_metric} on training rows uses the locked "
    "decision threshold while the folds use 0.5, and no threshold-free metric (ROC AUC, PR AUC, log loss) "
    "was recorded for both.",
    "duplicate_rows.pass": "No duplicate problem: {train_duplicate_rows} training row(s) repeat another "
    "training row and {holdout_duplicate_rows} test row(s) match a training row on the "
    "{feature_count} model columns, no more than chance alone would produce.",
    "duplicate_rows.pass_minor": "{train_duplicate_rows} training row(s) repeat another training row and "
    "{holdout_duplicate_rows} test row(s) match a training row on the {feature_count} model columns: "
    "more than chance alone would produce, but under 1% of the rows.",
    "duplicate_rows.within_train": "{train_duplicate_rows} training rows ({train_duplicate_fraction_pct}) "
    "repeat another training row on all {feature_count} model columns (chance alone explains about "
    "{expected_train_duplicate_rows}). Repeated records weigh twice in training; remove them if they are "
    "the same record.",
    "duplicate_rows.across_split": "{holdout_duplicate_rows} test rows ({holdout_duplicate_fraction_pct}) "
    "are identical to a training row on the model columns (chance alone explains about "
    "{expected_holdout_duplicate_rows}), so the test score partly measures memory. Remove duplicate "
    "records before splitting.",
    "duplicate_rows.independence_caveat": "The chance estimate assumes the model columns vary independently; "
    "columns that move together (such as a city and its region) repeat naturally, so check that these are "
    "the same records before removing them.",
    "duplicate_rows.not_evaluated": "Not evaluated: the run's training and test rows were not available.",
    "class_imbalance.pass": "Classes are reasonably balanced: the smallest of {class_count} classes is "
    "{minority_class_fraction_pct} of the training rows.",
    "class_imbalance.imbalanced": "The smallest of {class_count} classes is only "
    "{minority_class_fraction_pct} of the training rows ({minority_rows} rows; largest-to-smallest ratio "
    "{imbalance_ratio}). Accuracy can look good while that class is mostly missed.",
    "class_imbalance.too_few": "Split over {n_folds} cross-validation folds that is about "
    "{minority_rows_per_fold} rows of that class per fold (under {min_minority_rows_per_fold}), too few for "
    "stable fold metrics.",
    "class_imbalance.weighted": "Class-weighted models were already trained and compared; more examples of "
    "the rare class would help most.",
    "class_imbalance.unweighted": "Try class weights or collect more examples of the rare class.",
    "class_imbalance.not_applicable": "Not applicable: the target is numeric (regression).",
    "class_imbalance.not_evaluated": "Not evaluated: the class counts of the training rows were not recorded.",
    "implausible_score.pass": "The cross-validated {metric} of {cv_score} (baseline {baseline_cv_score}) is "
    "in a believable range.",
    "implausible_score.near_perfect": "The cross-validated {metric} is {cv_score}, close to perfect "
    "(baseline {baseline_cv_score}). Scores this high usually mean a column gives away the answer; check "
    "the strongest columns before trusting it.",
    "implausible_score.error_ratio": "The cross-validated {metric} is {cv_score}, only {error_ratio_pct} of "
    "the baseline's {baseline_cv_score}. An error this small usually means a column gives away the answer; "
    "check the strongest columns before trusting it.",
    "implausible_score.trivial": "The cross-validated {metric} is {cv_score}, but the always-majority "
    "baseline already scores {baseline_cv_score} and the model still leaves {residual_error_ratio_pct} of "
    "the baseline's errors: an easy or imbalanced target, not a sign of leakage.",
    "implausible_score.missing_cv": "Not evaluated: the winning model's cross-validated {metric} was not "
    "recorded.",
    "implausible_score.missing_baseline": "Not evaluated: the dummy baseline's cross-validated {metric} was not "
    "recorded, so the error cannot be put in proportion.",
    "implausible_score.unbounded": "Not evaluated: {metric} has no natural perfect value to compare against.",
    # --- P5.1-A -------------------------------------------------------------------------
    "fold_instability.pass": "The {metric} varies little across the {n_folds} cross-validation folds (from "
    "{worst_fold_score} to {best_fold_score}, spread {cv_std}): the cross-validation estimate is stable.",
    "fold_instability.unstable": "The {metric} swings across the {n_folds} cross-validation folds, from "
    "{worst_fold_score} to {best_fold_score} (spread {cv_std} around {cv_score}), so the cross-validation "
    "score is an uncertain estimate. More rows usually steady it.",
    "fold_instability.within_noise": "Folds of about {fold_rows} rows vary by about {fold_noise_std} by chance "
    "alone, so this spread is expected.",
    "fold_instability.at_baseline": "The worst fold ({worst_fold_score}) is no better than the dummy "
    "baseline's cross-validated {baseline_cv_score}: on some slices of the data the model adds nothing.",
    "fold_instability.not_evaluated": "Not evaluated: the winning model's per-fold {metric} was not recorded.",
    "fold_instability.chance_spread_missing": "Not evaluated: the run did not record how much its regression "
    "fold scores vary by chance, so instability cannot be told from noise.",
    "fold_instability.fold_too_small": "Not evaluated: the smallest cross-validation fold has only "
    "{smallest_fold_rows} rows (under {min_fold_rows}), too few for its score's chance spread to be judged.",
    "fold_instability.too_few_folds": "Not evaluated: {n_folds} fold(s) are too few to measure how much the "
    "cross-validation score varies.",
    "calibration.pass": "Predicted probabilities match what happened on the {rows} out-of-fold rows: average "
    "gap {ece} (expected calibration error), Brier score {brier} against {base_rate_brier} for always "
    "predicting the base rate.",
    "calibration.within_noise": "A gap of {ece} is what {rows} rows produce by chance (about {ece_noise}).",
    "calibration.miscalibrated": "Predicted probabilities are off by {ece} on average on the {rows} "
    "out-of-fold rows (expected calibration error; chance alone explains about {ece_noise}): the model "
    "predicts {mean_predicted} on average where {observed_rate} happened. Recalibrate before reading the "
    "scores as probabilities.",
    "calibration.top_label": "With several classes this compares the confidence in the predicted class with "
    "how often that class is right.",
    "calibration.class_weighted": "The winner uses class weights, which push probabilities toward the rare "
    "class by design; decisions use the locked threshold, so this matters where scores are read as "
    "probabilities.",
    "calibration.not_applicable": "Not applicable: the target is numeric (regression).",
    "calibration.not_evaluated": "Not evaluated: the winning model's out-of-fold predictions were not recorded.",
    "calibration.no_probabilities": "Not evaluated: the winning model outputs class decisions, not "
    "probabilities.",
    "calibration.too_few_rows": "Not evaluated: {rows} out-of-fold rows are too few to measure calibration "
    "(at least {min_rows}).",
    "subgroup_gap.pass": "No subgroup stands out: in the {columns_checked} low-cardinality column(s) compared, "
    "each of the {groups_tested} groups that could be scored is close to its column's average out-of-fold "
    "{metric}. {groups_skipped} group(s) could not be scored (too few rows or too few of one outcome); columns "
    "that could not be compared: {columns_not_compared}.",
    "subgroup_gap.gap": "Performance differs by subgroup: in {worst_column} the weakest of {group_count} "
    "groups ({worst_group_rows} rows) scores {worst_group_score} against {reference_score} on average across "
    "its groups on out-of-fold {metric} (gap {absolute_gap}). Columns with a gap beyond sampling noise (corrected for the "
    "{groups_tested} groups tested): {flagged_columns}. Check whether that group is underrepresented or "
    "behaves differently.",
    "subgroup_gap.error_gap": "Performance differs by subgroup: in {worst_column} the weakest of {group_count} "
    "groups ({worst_group_rows} rows) has an out-of-fold {metric} of {worst_group_score} against "
    "{reference_score} on average across its groups ({relative_gap_pct} worse). Columns with a gap beyond sampling noise (corrected "
    "for the {groups_tested} groups tested): {flagged_columns}.",
    "subgroup_gap.no_columns": "Not evaluated: the model uses no categorical column with 2 to {max_levels} "
    "values and groups of at least {min_group_rows} rows.",
    "subgroup_gap.too_few_class_rows": "Not evaluated: the model's categorical columns have groups large "
    "enough, but none with at least 5 rows of each outcome, so no group's ranking can be scored.",
    "subgroup_gap.not_evaluated": "Not evaluated: the winning model's out-of-fold predictions were not "
    "recorded.",
    "multicollinearity.pass": "No two of the {numeric_feature_count} numeric model columns move together "
    "closely on the training rows (largest correlation {max_abs_correlation}, largest variance inflation "
    "{max_vif}).",
    "multicollinearity.collinear": "{correlated_pair_count} pair(s) of numeric model columns are almost "
    "perfectly correlated on the training rows (up to {max_abs_correlation}) and {high_vif_count} column(s) "
    "are largely explained by the others (variance inflation up to {max_vif}, {max_vif_column}). Columns "
    "that repeat others: {drop_candidates}.",
    "multicollinearity.exact": "Some numeric model columns are exact combinations of others on the training "
    "rows ({dependent_columns}): at least one of them carries no information of its own.",
    "multicollinearity.pairs_only": "With {rows_used} training rows for {columns_checked} numeric columns (fewer "
    "than 2 per column) variance inflation is not judged; pairs of columns (and exact combinations, while there "
    "are more rows than columns) are.",
    "multicollinearity.truncated": "Only the first {columns_checked} of {numeric_feature_count} numeric columns "
    "(by name) were compared.",
    "multicollinearity.linear_model": "The winning linear model's coefficients are unstable when columns "
    "repeat each other; drop one of each pair or use a stronger penalty.",
    "multicollinearity.other_model": "Tree and boosting models are less affected, but the repeated columns "
    "add little; dropping one of each pair simplifies the model.",
    "multicollinearity.not_applicable": "Not evaluated: the model uses fewer than two numeric columns.",
    "multicollinearity.not_evaluated": "Not evaluated: the run's training rows were not available.",
    # Train -> test checks: test-derived numbers live under holdout_* keys / ``holdout_comparison``
    # (holdout scope: people only; agents and tokens get ``AGENT_MESSAGES``).
    "feature_drift.pass": "The {columns_checked} model columns are distributed alike in the training and test "
    "rows (largest population stability index {max_psi}, {max_psi_column}).",
    "feature_drift.drift": "{drifted_count} of {columns_checked} model column(s) are distributed differently in "
    "the test rows than in the training rows: {drifted_columns} (largest population stability index "
    "{max_psi}, {max_psi_column}; above 0.25 is a major shift). The test score may then differ from "
    "cross-validation.",
    "feature_drift.random_split": "With a random split a shift this large is unlikely by chance (it stays "
    "significant after correcting for the {columns_checked} columns compared); check whether the file mixes "
    "sources or was sorted before splitting.",
    "feature_drift.temporal_split": "The test rows are the latest period, so some drift is expected: it shows "
    "how the data changes over time.",
    "feature_drift.group_split": "The test rows are other groups, so drift shows how those groups differ.",
    "feature_drift.not_evaluated": "Not evaluated: the run's training and test rows were not available.",
    "feature_drift.no_readable_columns": "Not evaluated: no model column has readable values in both the "
    "training and the test rows.",
    "split_checks.sampled": "Counts come from a seeded sample of {sample_rows} rows per side.",
    "feature_drift.too_few_rows": "Not evaluated: {holdout_rows} test rows are too few to compare "
    "distributions (at least {min_rows}).",
    "temporal_shift.ordered": "The test rows follow the training rows in time on {time_column} (training "
    "{train_start} to {train_end}, test {holdout_start} to {holdout_end}), so the test score measures the "
    "future.",
    "temporal_shift.overlap": "{holdout_rows_before_train_end_fraction_pct} of the test rows are earlier than "
    "the latest training row on {time_column}: the model trained on rows from after part of its test period. "
    "Re-split by time.",
    "temporal_shift.random_split": "The split ignored time: {holdout_rows_within_train_period_fraction_pct} of "
    "the test rows fall inside the training period on {time_column} ({train_start} to {train_end}), so the "
    "test score measures the same period, not the future. Use a time-based split if the model will predict "
    "forward.",
    "temporal_shift.periods": "Training rows cover {train_start} to {train_end} and test rows "
    "{holdout_start} to {holdout_end} on {time_column}.",
    "temporal_shift.not_applicable": "Not evaluated: the run has no time column.",
    "temporal_shift.unparsable": "Not evaluated: {time_column} could not be read as times.",
    "temporal_shift.not_evaluated": "Not evaluated: the run's training and test rows were not available.",
    "missingness_shift.pass": "Missing values look alike in training and test rows: "
    "{train_rows_with_missing_fraction_pct} vs {holdout_rows_with_missing_fraction_pct} of rows have a "
    "missing model value.",
    "missingness_shift.shift": "{shifted_count} model column(s) have a different share of missing values in "
    "the test rows: {shifted_columns} (largest change {max_missing_shift_fraction_pct}, {max_shift_column}). "
    "Fill-in values learned on training rows may not fit them.",
    "missingness_shift.new_missing": "{new_missing_columns} have no missing values in training but clearly "
    "some in test, so the model never learned them missing.",
    "missingness_shift.not_evaluated": "Not evaluated: the run's training and test rows were not available.",
    "contamination.pass": "No contamination beyond exact duplicates: {near_duplicate_holdout_rows} test "
    "row(s) nearly match a training row (numbers rounded to {significant_digits} significant digits), in line "
    "with how often training rows nearly match each other ({train_near_duplicate_fraction_pct}).",
    "contamination.natural_repeats": "Training rows nearly repeat each other "
    "({train_near_duplicate_fraction_pct}) about as often as test rows nearly repeat training rows "
    "({near_duplicate_holdout_fraction_pct}), and columns this coarse repeat naturally, so near-copies spread "
    "across the split cannot be told apart from natural repeats here; this check cannot rule them out.",
    "contamination.near_copies_dataset": "{train_near_duplicate_fraction_pct} of a sample of training rows "
    "nearly copy another training row at {significant_digits} significant digits and "
    "{train_fine_near_duplicate_fraction_pct} still agree to {fine_digits} significant digits on every model "
    "column, closer than columns this varied usually agree by chance (assuming they vary independently). "
    "Such rows may be re-entered records; if they are, copies on both sides of the split make the test score "
    "partly measure memory. Check them and deduplicate before splitting.",
    "contamination.small_excess": "{near_duplicate_holdout_rows} test row(s) "
    "({near_duplicate_holdout_fraction_pct}) nearly match a training row, slightly more than training rows "
    "match each other ({train_near_duplicate_fraction_pct}) but under the 1-point threshold.",
    "contamination.groups_disjoint": "No {group_column} value appears in both training and test rows.",
    "contamination.near_duplicates": "{near_duplicate_holdout_rows} test rows "
    "({near_duplicate_holdout_fraction_pct}) match a training row once numbers are rounded to "
    "{significant_digits} significant digits and text is normalized, beyond exact copies and beyond how often "
    "training rows nearly match each other ({train_near_duplicate_fraction_pct}, about "
    "{expected_near_duplicate_holdout_rows} rows). Near-copies make the test score partly measure memory.",
    "contamination.shared_groups": "{holdout_rows_sharing_group_fraction_pct} of the test rows share a "
    "{group_column} value with training rows ({holdout_shared_group_count} shared values) although the split "
    "was planned to keep groups apart. Re-split by {group_column}.",
    "contamination.shared_groups_random": "{holdout_rows_sharing_group_fraction_pct} of the test rows share a "
    "{group_column} value with training rows ({holdout_shared_group_count} shared values), so the test score "
    "partly measures entities the model has seen. The split planner judged the repetition too low to group "
    "by; review whether the rows of one {group_column} should stay on one side.",
    "contamination.shared_groups_temporal": "{holdout_rows_sharing_group_fraction_pct} of the test rows belong "
    "to a {group_column} also seen in training: expected when the same entities are observed over time and "
    "the split goes forward in time.",
    "contamination.conflicting_labels": "{train_conflicting_label_rows} training rows "
    "({train_conflicting_label_fraction_pct}) share every model-column value with a row of a different "
    "outcome: the columns cannot tell them apart.",
    "contamination.no_baseline": "Not evaluated: too few training rows to measure how often rows nearly "
    "match by chance (at least {min_rows}).",
    "contamination.not_evaluated": "Not evaluated: the run's training and test rows were not available.",
    "time_travel.pass": "Truncation replay on {probed_rows} training rows: recomputing the as-of features "
    "({as_of_features}) from earlier rows only leaves every value unchanged, so none reads the future.",
    "time_travel.leak": "Moving the as-of cutoff changes {moved_columns} for {affected_rows} of {probed_rows} "
    "probed training rows: these features read rows from after the prediction time. Fix them before "
    "trusting the model.",
    "time_travel.not_applicable": "Not evaluated: the run has no time column to cut off on.",
    "time_travel.no_as_of_features": "Not evaluated: the model uses no as-of (history) features, so there is "
    "nothing a time cutoff could change.",
    "time_travel.probe_unavailable": "Not evaluated: the as-of features could not be recomputed for the replay.",
    "new_feature.pass": "Adding {added_feature} moved the cross-validated {metric} from {parent_cv_score} to "
    "{cv_score} on the parent's split: an ordinary change.",
    "new_feature.outsized": "Adding the single column {added_feature} moved the cross-validated {metric} from "
    "{parent_cv_score} to {cv_score} on the parent's split, removing {residual_closed_fraction_pct} of the "
    "remaining error at once. One column rarely does this honestly; check that it is known at prediction time.",
    "new_feature.same_family": "Both winners are {winner_family} models, so the change is the column's.",
    "new_feature.paired": "The jump holds fold by fold: paired over the {paired_folds} shared folds, t = "
    "{paired_t}.",
    "new_feature.unpaired": "Per-fold scores were not available for both runs, so the jump is compared with the "
    "fold spread ({jump_in_cv_std} times it).",
    "new_feature.family_changed": "Not evaluated: the winning model changed from {parent_winner_family} to "
    "{winner_family}, so the jump is not one column's alone.",
    "new_feature.no_parent": "Not evaluated: this run is not a branch of an earlier experiment.",
    "new_feature.not_single": "Not evaluated: this branch adds {added_count} and removes {removed_count} model "
    "column(s), not exactly one added column.",
    "new_feature.other_split": "Not evaluated: the parent used a different split plan, so the "
    "cross-validation scores are not comparable.",
    "new_feature.missing_cv": "Not evaluated: the cross-validated {metric} of this run or its parent was not "
    "recorded.",
    "new_feature.unbounded": "Not evaluated: {metric} has no natural perfect value to measure the jump against.",
}

_LIST_SHOWN = 5
METRIC_LABELS: dict[str, str] = {
    "roc_auc": "ROC AUC", "roc_auc_ovr": "ROC AUC", "pr_auc": "PR AUC", "accuracy": "accuracy",
    "balanced_accuracy": "balanced accuracy", "f1": "F1", "macro_f1": "macro F1", "weighted_f1": "weighted F1",
    "precision": "precision", "recall": "recall", "r2": "R²", "rmse": "RMSE", "mae": "MAE", "mse": "MSE",
    "mape": "MAPE", "smape": "SMAPE", "log_loss": "log loss", "brier": "Brier score", "brier_score": "Brier score",
    "median_absolute_error": "median absolute error",
}


def _number(value: float) -> str:
    return format(value, ".3g") if abs(value) < 100 else f"{value:,.1f}"


def _display(evidence: Mapping[str, Any]) -> dict[str, str]:
    out: dict[str, str] = {}
    for key, value in evidence.items():
        if isinstance(value, bool):
            out[key] = "yes" if value else "no"
        elif isinstance(value, int):
            out[key] = f"{value:,}"
        elif isinstance(value, float):
            out[key] = _number(value)
            if key.endswith(("_fraction", "_gap", "_ratio")):
                out[f"{key}_pct"] = f"{value * 100:.1f}%"
        elif isinstance(value, (list, tuple)):
            items = [str(item) for item in value]
            shown = ", ".join(items[:_LIST_SHOWN]) or "none"
            out[key] = shown + (f" and {len(items) - _LIST_SHOWN} more" if len(items) > _LIST_SHOWN else "")
        elif value is None:
            out[key] = "n/a"
        elif key in {"metric", "primary_metric"}:
            out[key] = METRIC_LABELS.get(str(value), str(value))
        elif key.endswith("_family"):
            out[key] = str(value).replace("_", " ")
        else:
            out[key] = str(value)
    return out


class _Missing(dict[str, str]):
    def __missing__(self, key: str) -> str:
        return "n/a"


def current_evidence(evidence: Mapping[str, Any]) -> dict[str, Any]:
    """Evidence with legacy P4.10-A test-side keys renamed to their ``holdout_*`` names."""

    return {LEGACY_EVIDENCE_KEYS.get(str(key), str(key)): value for key, value in evidence.items()}


def render_message(message_keys: Iterable[str], evidence: Mapping[str, Any]) -> str:
    """Deterministic plain-language message: the templates of ``message_keys`` with the
    evidence numbers filled in (unknown keys render as nothing; missing numbers as n/a).
    ``holdout_comparison`` values are readable by the templates (human view)."""

    current = current_evidence(evidence)
    nested = current.get(HOLDOUT_COMPARISON)
    values = _Missing({**(_display(nested) if isinstance(nested, Mapping) else {}), **_display(current)})
    return " ".join(MESSAGE_TEMPLATES[key].format_map(values) for key in message_keys if key in MESSAGE_TEMPLATES)


def agent_finding(check: str, message: str, evidence: Mapping[str, Any]) -> tuple[str, dict[str, Any]]:
    """What an agent or service token may read of one finding: the evidence without any
    holdout-scoped key (``strip_holdout``, the one shaping rule) and, for a check whose human
    message carries test-row numbers, its fixed ``AGENT_MESSAGES`` text instead."""

    from app.agents.tools.shaping import strip_holdout

    stripped = strip_holdout(current_evidence(evidence))
    return AGENT_MESSAGES.get(check, message), stripped


# --- /v1 read model -----------------------------------------------------------------


class ExperimentFindingRead(BaseModel):
    model_config = ConfigDict(extra="forbid")

    check: FindingCheck
    status: FindingStatus
    severity: FindingSeverity
    message: str = Field(description="Plain-language explanation with the numbers filled in.")
    evidence: dict[str, Any] = Field(
        default_factory=dict,
        description="Numbers and column names behind the check: training rows and CV folds; for the split "
        "checks also test-row feature statistics under holdout_* keys (holdout scope: shown to people only, "
        "removed for agents and service tokens). Never a final-holdout label, prediction or metric. Column "
        "names are user data.",
    )
    recommendation_kind: RecommendationKind | None = None


class ExperimentFindingsSummary(BaseModel):
    model_config = ConfigDict(extra="forbid")

    passed: int = 0
    warnings: int = 0
    failures: int = 0
    not_evaluated: int = 0


class ExperimentFindingsRead(BaseModel):
    """The trust checks of one run (five core checks; runs since P5.1-A add ten more).
    ``investigated`` is false for runs that finished before P4.10-A (or have not
    finished): ``checks`` is then empty. A check that could not run is ``not_evaluated``
    (counted separately, never as passed)."""

    model_config = ConfigDict(extra="forbid")

    experiment_id: UUID
    investigated: bool
    version: str | None = None
    checks: list[ExperimentFindingRead] = Field(default_factory=list)
    summary: ExperimentFindingsSummary = Field(default_factory=ExperimentFindingsSummary)


def _read_row(row: Mapping[str, Any], agent: bool) -> ExperimentFindingRead:
    check, keys = str(row["check"]), row.get("message_keys") or []
    evidence = dict(row.get("evidence") or {})
    if agent:  # rendered from the stripped evidence; fixed text where test-row numbers would show
        evidence = agent_finding(check, "", evidence)[1]
        message = AGENT_MESSAGES.get(check) or render_message(keys, evidence)
    else:
        message = render_message(keys, evidence)
    return ExperimentFindingRead(check=row["check"], status=row["status"], severity=row["severity"],
                                 message=message, evidence=evidence,
                                 recommendation_kind=row.get("recommendation_kind"))


def findings_read(experiment_id: UUID, investigation: Any, *, agent: bool = False) -> ExperimentFindingsRead:
    """Read model from ``experiments.result["investigation"]`` (``None`` for old runs).

    ``agent``: the agent / service-token view -- evidence without holdout-scoped keys and
    messages rendered from that stripped evidence (fixed ``AGENT_MESSAGES`` text for the
    checks whose messages carry test-row numbers). People (Studio sessions) get full detail."""

    stored = investigation if isinstance(investigation, Mapping) else {}
    rows = [row for row in stored.get("checks") or [] if isinstance(row, Mapping)]
    order = {check: index for index, check in enumerate(FINDING_CHECKS)}
    checks = [
        _read_row(row, agent)
        for row in sorted(rows, key=lambda item: order.get(str(item.get("check")), len(order)))
        if row.get("check") in order
    ]
    summary = ExperimentFindingsSummary(
        passed=sum(1 for item in checks if item.status == "pass"),
        warnings=sum(1 for item in checks if item.status == "warning"),
        failures=sum(1 for item in checks if item.status == "fail"),
        not_evaluated=sum(1 for item in checks if item.status == "not_evaluated"),
    )
    return ExperimentFindingsRead(
        experiment_id=experiment_id,
        investigated=bool(checks),
        version=stored.get("version") if checks else None,
        checks=checks,
        summary=summary,
    )
