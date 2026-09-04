"""Deterministic, read-only verification of persisted automatic ML-run evidence."""

from __future__ import annotations

from datetime import datetime
from typing import Any

import pandas as pd

from app.engine.features.encode import coerce_binary_target
from app.services.artifact_store import ArtifactAccess, LocalArtifactAccess

CHECK_PASS = "PASS"
CHECK_WARN = "WARN"
CHECK_FAIL = "FAIL"
CHECK_NOT_VERIFIABLE = "NOT_VERIFIABLE"


def _as_dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _as_list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def _timestamp(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        return datetime.fromisoformat(value)
    except ValueError:
        return None


def _labels_match(expected: Any, actual: Any) -> bool:
    if pd.isna(expected) and pd.isna(actual):
        return True
    if expected == actual:
        return True
    try:
        return float(expected) == float(actual)
    except (TypeError, ValueError):
        return False


def _artifact_target_values(frame: pd.DataFrame, column: str, task_type: str) -> pd.Series:
    series = frame[column]
    if task_type == "binary":
        encoded = coerce_binary_target(series)
        if int(encoded.notna().sum()) == int(series.notna().sum()):
            return encoded
    if task_type == "regression":
        return pd.to_numeric(series, errors="coerce")
    return series


class PipelineVerifier:
    """Verify persisted evidence without trusting the run's completion label."""

    def __init__(self, artifacts: ArtifactAccess | None = None) -> None:
        self.artifacts = artifacts or LocalArtifactAccess()

    def verify(self, report: dict[str, Any]) -> dict[str, Any]:
        checks: list[dict[str, Any]] = []

        def add(
            check_id: str,
            stage: str,
            status: str,
            message: str,
            *evidence_refs: str,
        ) -> None:
            checks.append(
                {
                    "check_id": check_id,
                    "stage": stage,
                    "status": status,
                    "message": message,
                    "evidence_refs": list(evidence_refs),
                }
            )

        run = _as_dict(report.get("run"))
        profile = _as_dict(report.get("raw_profile"))
        target = _as_dict(report.get("target_decision"))
        task = _as_dict(report.get("task"))
        split = _as_dict(report.get("split"))
        cleaning = _as_dict(report.get("cleaning"))
        role_evidence = _as_dict(report.get("column_role_evidence"))
        feature_engineering = _as_dict(report.get("feature_engineering"))
        preprocessing = _as_dict(report.get("preprocessing"))
        candidates = _as_list(report.get("candidate_models"))
        selection = _as_dict(report.get("selection"))
        final_fit = _as_dict(report.get("final_fit"))
        final_test = _as_dict(report.get("final_test_evaluation"))
        predictions = _as_list(report.get("prediction_evidence"))
        artifacts = _as_dict(report.get("artifacts"))

        if str(run.get("status") or "").lower() == "failed":
            add("run_failed", "pipeline", CHECK_FAIL, "The ML run ended in a failed state.", "run.status")

        input_path = artifacts.get("input")
        input_frame: pd.DataFrame | None = None
        if not input_path:
            add("input_artifact_exists", "file_ingestion", CHECK_NOT_VERIFIABLE, "Input artifact path is missing.")
        elif self.artifacts.artifact_exists(str(input_path)):
            add("input_artifact_exists", "file_ingestion", CHECK_PASS, "The uploaded input artifact exists.", "artifacts.input")
            try:
                input_frame = self.artifacts.load_table(str(input_path))
            except Exception as exc:  # noqa: BLE001 - evidence must convert loader errors into a check
                add(
                    "input_artifact_loadable",
                    "file_ingestion",
                    CHECK_FAIL,
                    f"The input artifact could not be loaded as a table: {exc}",
                    "artifacts.input",
                )
            else:
                if input_frame.empty or not len(input_frame.columns):
                    add("input_artifact_loadable", "file_ingestion", CHECK_FAIL, "The input artifact loaded without usable rows or columns.", "artifacts.input")
                else:
                    add("input_artifact_loadable", "file_ingestion", CHECK_PASS, "The input artifact is loadable as a non-empty table.", "artifacts.input")
        else:
            add("input_artifact_exists", "file_ingestion", CHECK_FAIL, "The uploaded input artifact does not exist.", "artifacts.input")
            add("input_artifact_loadable", "file_ingestion", CHECK_NOT_VERIFIABLE, "The absent input artifact cannot be loaded.", "artifacts.input")

        if profile.get("row_count") is None or profile.get("column_count") is None:
            add("file_load_counts_recorded", "file_ingestion", CHECK_NOT_VERIFIABLE, "Loaded row or column count is missing.", "raw_profile")
        elif input_frame is not None and (
            int(profile["row_count"]) != len(input_frame)
            or int(profile["column_count"]) != len(input_frame.columns)
        ):
            add("file_load_counts_recorded", "file_ingestion", CHECK_FAIL, "Persisted load counts differ from the input artifact.", "raw_profile", "artifacts.input")
        else:
            add("file_load_counts_recorded", "file_ingestion", CHECK_PASS, "Loaded row and column counts are persisted and consistent.", "raw_profile")

        profile_columns = [row for row in _as_list(profile.get("columns")) if isinstance(row, dict)]
        profile_names = {str(row.get("name")) for row in profile_columns if row.get("name") is not None}
        expected_names = {str(name) for name in _as_list(profile.get("column_names"))}
        required_column_fields = {
            "name",
            "dtype",
            "missing_count",
            "missing_ratio",
            "unique_count",
            "unique_ratio",
            "constant",
            "high_cardinality",
            "identifier_like",
        }
        missing_profile_fields = [
            str(row.get("name"))
            for row in profile_columns
            if not required_column_fields.issubset(row)
            or ("mean" in row and "skewness" not in row)
        ]
        if not profile_columns:
            add("eda_profile_complete", "profiling", CHECK_NOT_VERIFIABLE, "Per-column EDA profiles are missing.", "raw_profile.columns")
        elif (
            expected_names != profile_names
            or missing_profile_fields
            or (
                input_frame is not None
                and expected_names != {str(name) for name in input_frame.columns}
            )
        ):
            add(
                "eda_profile_complete",
                "profiling",
                CHECK_FAIL,
                f"EDA coverage or required statistics are inconsistent; affected columns: {missing_profile_fields}.",
                "raw_profile.columns",
                "raw_profile.column_names",
            )
        else:
            add("eda_profile_complete", "profiling", CHECK_PASS, "EDA covers the recorded schema and required statistics.", "raw_profile.columns")

        target_column = target.get("target_column", target.get("column"))
        target_task = target.get("task_type")
        task_type = task.get("task_type")
        supported_task = target_task in {"binary", "regression"}
        target_profile = next((row for row in profile_columns if row.get("name") == target_column), {})
        compatible = (
            target_task == task_type
            and task.get("target") == target_column
            and (
                (target_task == "binary" and target_profile.get("unique_count") == 2)
                or (
                    target_task == "regression"
                    and input_frame is not None
                    and target_column in input_frame
                    and pd.api.types.is_numeric_dtype(input_frame[target_column])
                )
            )
        )
        target_fields = {"target_column", "task_type", "source", "confidence", "reason", "candidates", "locked_at"}
        target_locked_at = _timestamp(target.get("locked_at"))
        split_at = _timestamp(split.get("split_at"))
        if not target_fields.issubset(target) or not target_column or not target.get("locked_at"):
            add("target_locked", "target_task_resolution", CHECK_NOT_VERIFIABLE, "Target lock evidence is missing.", "target_decision")
        elif (
            target_column not in expected_names
            or not supported_task
            or not compatible
            or (split_at is not None and (target_locked_at is None or target_locked_at > split_at))
        ):
            add("target_locked", "target_task_resolution", CHECK_FAIL, "The locked target is absent, unsupported, or incompatible with the persisted task.", "target_decision", "task", "raw_profile.column_names")
        else:
            add("target_locked", "target_task_resolution", CHECK_PASS, "Target and supported task are explicitly locked.", "target_decision")

        train_rows = _as_list(split.get("train_source_rows"))
        test_rows = _as_list(split.get("test_source_rows"))
        train_set, test_set = set(train_rows), set(test_rows)
        split_fields = {"split_at", "random_state", "stratify", "strategy", "n_train", "n_test"}
        if not split_fields.issubset(split) or not train_rows or not test_rows:
            add("split_provenance_complete", "splitting", CHECK_NOT_VERIFIABLE, "Train/test provenance or split metadata is missing.", "split")
        elif len(train_set) != len(train_rows) or len(test_set) != len(test_rows) or train_set & test_set:
            add("split_provenance_complete", "splitting", CHECK_FAIL, "Train and final-test provenance overlap.", "split.train_source_rows", "split.test_source_rows")
        elif len(train_rows) != split.get("n_train") or len(test_rows) != split.get("n_test"):
            add("split_provenance_complete", "splitting", CHECK_FAIL, "Split counts do not match provenance counts.", "split")
        elif (
            set(_as_list(split.get("all_source_rows"))) != train_set | test_set
            or len(train_rows) + len(test_rows) != cleaning.get("rows_out")
            or (
                split.get("modeling_row_count") is not None
                and split.get("modeling_row_count") != len(train_rows) + len(test_rows)
            )
        ):
            add("split_provenance_complete", "splitting", CHECK_FAIL, "Split provenance does not cover all modeling rows.", "split.all_source_rows")
        else:
            add("split_provenance_complete", "splitting", CHECK_PASS, "Train/test partitions are disjoint and complete.", "split")

        cleaning_fields = {"transformations", "rows_in", "rows_out", "columns_in", "columns_out"}
        if not cleaning_fields.issubset(cleaning):
            add("cleaning_evidence_consistent", "structural_cleaning", CHECK_NOT_VERIFIABLE, "Cleaning actions or row deltas are missing.", "cleaning")
        elif (
            int(cleaning["rows_out"]) > int(cleaning["rows_in"])
            or not set(_as_list(cleaning.get("columns_out"))) <= set(_as_list(cleaning.get("columns_in")))
        ):
            add("cleaning_evidence_consistent", "structural_cleaning", CHECK_FAIL, "Cleaning row or column deltas are internally inconsistent.", "cleaning")
        else:
            add("cleaning_evidence_consistent", "structural_cleaning", CHECK_PASS, "Cleaning actions and row deltas are internally consistent.", "cleaning")

        missing_plan = _as_dict(cleaning.get("missing_value_plan"))
        decision_rows = set(_as_list(missing_plan.get("evidence_source_rows")))
        if missing_plan.get("decision_partition") != "train" or not decision_rows:
            add("train_only_decision_scope", "train_only_decisions", CHECK_NOT_VERIFIABLE, "Train-only decision provenance is missing.", "cleaning.missing_value_plan")
        elif decision_rows & test_set or not decision_rows <= train_set:
            add("train_only_decision_scope", "train_only_decisions", CHECK_FAIL, "Train-only decisions include non-training provenance.", "cleaning.missing_value_plan.evidence_source_rows")
        else:
            add("train_only_decision_scope", "train_only_decisions", CHECK_PASS, "Modeling decisions use only locked training rows.", "cleaning.missing_value_plan")

        role_rows = [row for row in _as_list(role_evidence.get("columns")) if isinstance(row, dict)]
        role_fields = {
            "column",
            "original_dtype",
            "final_role",
            "source",
            "reason",
            "confidence",
            "validator_verdict",
            "llm_used",
        }
        valid_roles = {"numerical", "categorical", "boolean", "datetime", "identifier", "ignored/free_text", "target"}
        roles_by_column: dict[str, list[str]] = {}
        for row in role_rows:
            roles_by_column.setdefault(str(row.get("column")), []).append(str(row.get("final_role")))
        modeled = [str(name) for values in _as_dict(task.get("feature_groups")).values() for name in _as_list(values)]
        identifier_columns = {name for name, roles in roles_by_column.items() if "identifier" in roles}
        invalid_modeled = [
            name
            for name in modeled
            if len(roles_by_column.get(name, [])) != 1
            or roles_by_column.get(name, [""])[0] not in {"numerical", "categorical", "boolean", "datetime"}
        ]
        role_decision_rows = set(_as_list(role_evidence.get("evidence_source_rows")))
        incomplete_role_rows = [
            str(row.get("column"))
            for row in role_rows
            if not role_fields.issubset(row) or row.get("final_role") not in valid_roles
        ]
        role_coverage_invalid = set(roles_by_column) != expected_names or any(
            len(values) != 1 for values in roles_by_column.values()
        )
        if not role_rows or role_evidence.get("decision_partition") != "train" or not role_decision_rows:
            add("column_roles_valid", "column_roles", CHECK_NOT_VERIFIABLE, "Per-column role evidence is missing.", "column_role_evidence")
        elif (
            incomplete_role_rows
            or role_coverage_invalid
            or role_decision_rows & test_set
            or not role_decision_rows <= train_set
            or target_column in modeled
            or roles_by_column.get(str(target_column)) != ["target"]
            or identifier_columns & set(modeled)
            or invalid_modeled
        ):
            add("column_roles_valid", "column_roles", CHECK_FAIL, f"Modeled role assignments or evidence are invalid: {sorted(set(incomplete_role_rows + invalid_modeled))}.", "column_role_evidence", "task.feature_groups")
        else:
            add("column_roles_valid", "column_roles", CHECK_PASS, "Every modeled feature has one valid role; target and identifiers are excluded.", "column_role_evidence")

        actions = [row for row in _as_list(feature_engineering.get("feature_engineering_actions")) if isinstance(row, dict)]
        action_fields = {
            "transformation",
            "input_columns",
            "output_columns",
            "reason",
            "parameters",
            "learned_from_data",
            "decision_partition",
        }
        invalid_actions = [row for row in actions if not action_fields.issubset(row)]
        declared_outputs = {str(name) for row in actions for name in _as_list(row.get("output_columns"))}
        declared_inputs = {str(name) for row in actions for name in _as_list(row.get("input_columns"))}
        feature_fields = {"original_features", "generated_features", "transformed_features", "removed_features", "feature_engineering_actions"}
        declared_features = (
            set(str(name) for name in _as_list(feature_engineering.get("original_features")))
            | set(str(name) for name in _as_list(feature_engineering.get("generated_features")))
        )
        if not feature_fields.issubset(feature_engineering):
            add("feature_actions_match_schema", "feature_engineering", CHECK_NOT_VERIFIABLE, "Feature-engineering summary evidence is incomplete.", "feature_engineering")
        elif invalid_actions:
            add("feature_actions_match_schema", "feature_engineering", CHECK_FAIL, "Feature-engineering action metadata is incomplete.", "feature_engineering.feature_engineering_actions")
        elif (
            not declared_outputs <= set(modeled)
            or not declared_inputs <= declared_features
            or any(row.get("decision_partition") != "train" for row in actions)
        ):
            add("feature_actions_match_schema", "feature_engineering", CHECK_FAIL, "Declared transformation outputs are absent from the modeled schema.", "feature_engineering", "task.feature_groups")
        else:
            add("feature_actions_match_schema", "feature_engineering", CHECK_PASS, "Feature-engineering declarations match the modeled schema.", "feature_engineering")

        preprocessing_expected = {
            "numeric_columns",
            "categorical_columns",
            "numeric_imputer_strategy",
            "numeric_scaler",
            "categorical_imputer_strategy",
            "categorical_encoder",
            "handle_unknown",
            "fit_partition",
        }
        numeric_preprocessing = set(str(name) for name in _as_list(preprocessing.get("numeric_columns")))
        categorical_preprocessing = set(str(name) for name in _as_list(preprocessing.get("categorical_columns")))
        if not preprocessing_expected.issubset(preprocessing):
            add("preprocessing_config_complete", "preprocessing_setup", CHECK_NOT_VERIFIABLE, "Preprocessing configuration evidence is incomplete.", "preprocessing")
        elif (
            preprocessing.get("handle_unknown") != "ignore"
            or "fold_train_only" not in str(preprocessing.get("fit_partition"))
            or numeric_preprocessing & categorical_preprocessing
            or numeric_preprocessing | categorical_preprocessing != set(modeled)
        ):
            add("preprocessing_config_complete", "preprocessing_setup", CHECK_FAIL, "Preprocessing safety configuration is invalid.", "preprocessing")
        else:
            add("preprocessing_config_complete", "preprocessing_setup", CHECK_PASS, "Expected preprocessing components and train-only fit scope are recorded.", "preprocessing")

        cv_failures: list[str] = []
        cv_missing = False
        trained_candidates = [row for row in candidates if isinstance(row, dict) and row.get("status") == "trained"]
        expected_cv_strategy = "StratifiedKFold" if task_type == "binary" else "KFold"
        for candidate in trained_candidates:
            folds = [row for row in _as_list(candidate.get("folds")) if isinstance(row, dict)]
            actual = candidate.get("actual_folds")
            if not folds or actual is None:
                cv_missing = True
                continue
            if (
                len(folds) != actual
                or candidate.get("requested_folds") != 5
                or candidate.get("cv_strategy") != expected_cv_strategy
                or (actual != 5 and not candidate.get("adaptation_reason"))
            ):
                cv_failures.append(str(candidate.get("candidate_id")))
                continue
            validation_union: set[Any] = set()
            for fold in folds:
                fold_train = set(_as_list(fold.get("train_provenance")))
                fold_validation = set(_as_list(fold.get("validation_provenance")))
                validation_union |= fold_validation
                if (
                    not {"fold_number", "train_row_count", "validation_row_count", "metrics", "fit_duration_ms"}.issubset(fold)
                    or len(fold_train) != fold.get("train_row_count")
                    or len(fold_validation) != fold.get("validation_row_count")
                    or fold.get("fit_duration_ms", 0) <= 0
                    or fold_train & fold_validation
                    or fold_train | fold_validation != train_set
                    or test_set & (fold_train | fold_validation)
                ):
                    cv_failures.append(str(candidate.get("candidate_id")))
                    break
            if validation_union != train_set:
                cv_failures.append(str(candidate.get("candidate_id")))
        if cv_failures:
            add("cross_validation_provenance", "cross_validation", CHECK_FAIL, f"Invalid fold provenance for candidates: {sorted(set(cv_failures))}.", "candidate_models.folds", "split")
        elif cv_missing or not trained_candidates:
            add("cross_validation_provenance", "cross_validation", CHECK_NOT_VERIFIABLE, "Fold provenance is missing for one or more trained candidates.", "candidate_models.folds")
        else:
            add("cross_validation_provenance", "cross_validation", CHECK_PASS, "Every CV fold is disjoint, train-only, and covers the training partition.", "candidate_models.folds", "split")

        expected_candidates = {str(value) for value in _as_list(report.get("expected_candidate_ids"))}
        recorded_candidates = {str(row.get("candidate_id")) for row in candidates if isinstance(row, dict)}
        candidate_fields = {
            "candidate_id",
            "model_family",
            "hyperparameters",
            "feature_set",
            "preprocessing_config",
            "cv_strategy",
            "requested_folds",
            "actual_folds",
            "fold_metrics",
            "cv_mean",
            "cv_std",
            "fit_duration_ms",
            "status",
            "failure_reason",
        }
        incomplete_candidates = [
            str(row.get("candidate_id"))
            for row in candidates
            if not isinstance(row, dict)
            or not candidate_fields.issubset(row)
            or (
                row.get("status") == "trained"
                and (
                    not row.get("fold_metrics")
                    or len(_as_list(row.get("fold_metrics"))) != row.get("actual_folds")
                    or row.get("cv_mean") is None
                    or row.get("cv_std") is None
                )
            )
            or (row.get("status") in {"FAILED", "failed"} and not row.get("failure_reason"))
        ]
        if not expected_candidates:
            add("candidate_audit_complete", "candidate_training", CHECK_NOT_VERIFIABLE, "Expected candidate portfolio is missing.", "expected_candidate_ids")
        elif expected_candidates != recorded_candidates:
            add("candidate_audit_complete", "candidate_training", CHECK_FAIL, "Expected and recorded candidate portfolios differ.", "expected_candidate_ids", "candidate_models")
        elif incomplete_candidates or any(row.get("status") not in {"trained", "FAILED", "failed"} for row in candidates):
            add("candidate_audit_complete", "candidate_training", CHECK_FAIL, f"Candidate audit records are incomplete: {incomplete_candidates}.", "candidate_models")
        else:
            add("candidate_audit_complete", "candidate_training", CHECK_PASS, "Every expected candidate has a trained or failed audit record.", "candidate_models")

        winner_id = selection.get("candidate_id", selection.get("selected_candidate_id"))
        eligible = [row for row in trained_candidates if row.get("candidate_id") in _as_list(selection.get("eligible_candidate_ids"))]
        best_score = max((float(row.get("score", float("-inf"))) for row in eligible), default=None)
        winner_row = next((row for row in eligible if row.get("candidate_id") == winner_id), None)
        if (
            not winner_id
            or not selection.get("selection_metric")
            or selection.get("selection_source") != "cross_validation"
            or not selection.get("locked")
        ):
            add("winner_selected_from_cv", "model_selection", CHECK_NOT_VERIFIABLE, "CV-only winner lock evidence is missing.", "selection")
        elif (
            best_score is None
            or winner_row is None
            or float(winner_row.get("score", float("-inf"))) != best_score
            or selection.get("cv_score") != winner_row.get("score")
        ):
            add("winner_selected_from_cv", "model_selection", CHECK_FAIL, "The locked winner is not the best eligible CV result.", "selection", "candidate_models")
        else:
            add("winner_selected_from_cv", "model_selection", CHECK_PASS, "The winner is the best eligible CV candidate and was locked from CV evidence.", "selection")

        locked_at = _timestamp(selection.get("locked_at"))
        fit_started = _timestamp(final_fit.get("started_at"))
        fit_ended = _timestamp(final_fit.get("ended_at"))
        test_started = _timestamp(final_test.get("started_at"))
        if not all((locked_at, fit_started, fit_ended, test_started)):
            add("final_fit_after_lock", "final_fit", CHECK_NOT_VERIFIABLE, "Final-fit or lock timestamps are missing.", "selection", "final_fit", "final_test_evaluation")
        elif (
            not (locked_at <= fit_started <= fit_ended <= test_started)
            or final_fit.get("candidate_id") != winner_id
            or final_fit.get("fit_partition") != "full_train"
            or final_fit.get("fit_row_count") != split.get("n_train")
        ):
            add("final_fit_after_lock", "final_fit", CHECK_FAIL, "Final fit did not occur after lock and before test evaluation.", "selection", "final_fit", "final_test_evaluation")
        else:
            add("final_fit_after_lock", "final_fit", CHECK_PASS, "The locked winner was refit on full training data before test evaluation.", "selection", "final_fit")

        winner_rows = [row for row in trained_candidates if row.get("candidate_id") == winner_id]
        rejected_with_test = [row.get("candidate_id") for row in trained_candidates if row.get("candidate_id") != winner_id and row.get("test_metrics") is not None]
        if not winner_rows or final_test.get("evaluation_count") != 1:
            add("winner_only_final_test", "final_test_evaluation", CHECK_NOT_VERIFIABLE, "Final winner evaluation evidence is missing.", "final_test_evaluation")
        elif (
            rejected_with_test
            or not winner_rows[0].get("test_metrics")
            or final_test.get("candidate_id") != winner_id
            or final_test.get("test_row_count") != split.get("n_test")
            or final_test.get("metrics") != winner_rows[0].get("test_metrics")
        ):
            add("winner_only_final_test", "final_test_evaluation", CHECK_FAIL, f"Rejected candidates have final-test metrics: {rejected_with_test}.", "candidate_models", "final_test_evaluation")
        else:
            add("winner_only_final_test", "final_test_evaluation", CHECK_PASS, "Only the selected winner has final-test metrics and it was evaluated once.", "candidate_models", "final_test_evaluation")

        prediction_rows = [row for row in predictions if isinstance(row, dict)]
        prediction_sources = [row.get("source_row_index") for row in prediction_rows]
        prediction_truth_mismatches: list[Any] = []
        artifact_targets = None
        if input_frame is not None and target_column in input_frame:
            artifact_targets = _artifact_target_values(
                input_frame,
                target_column,
                str(target.get("task_type") or task.get("task_type") or ""),
            )
            for row in prediction_rows:
                source_row = row.get("source_row_index")
                if isinstance(source_row, int) and 0 <= source_row < len(artifact_targets):
                    expected = artifact_targets.iloc[source_row]
                    actual = row.get("y_true")
                    if not _labels_match(expected, actual):
                        prediction_truth_mismatches.append(source_row)
        if (
            not predictions
            or len(prediction_rows) != len(predictions)
            or any(value is None for value in prediction_sources)
            or any("y_true" not in row for row in prediction_rows)
        ):
            add("prediction_provenance_complete", "prediction_persistence", CHECK_NOT_VERIFIABLE, "Prediction provenance is missing.", "prediction_evidence")
        elif len(predictions) != split.get("n_test") or set(prediction_sources) != test_set or set(prediction_sources) & train_set:
            add("prediction_provenance_complete", "prediction_persistence", CHECK_FAIL, "Predictions do not map exactly to final-test provenance.", "prediction_evidence", "split")
        elif prediction_truth_mismatches:
            add("prediction_provenance_complete", "prediction_persistence", CHECK_FAIL, f"Persisted true labels differ from the input artifact for rows: {prediction_truth_mismatches}.", "prediction_evidence", "artifacts.input")
        else:
            add("prediction_provenance_complete", "prediction_persistence", CHECK_PASS, "Every final-test row has exactly one persisted prediction.", "prediction_evidence", "split")

        required_timing_stages = {
            "file_ingestion",
            "profiling",
            "target_task_resolution",
            "structural_cleaning",
            "splitting",
            "train_only_decisions",
            "column_roles",
            "feature_engineering",
            "preprocessing_setup",
            "cross_validation",
            "candidate_training",
            "model_selection",
            "final_fit",
            "final_test_evaluation",
            "prediction_persistence",
            "artifact_persistence",
        }
        timing_rows = [row for row in _as_list(report.get("stage_timings")) if isinstance(row, dict)]
        timing_by_stage = {str(row.get("stage")): row for row in timing_rows}
        missing_timing_stages = sorted(required_timing_stages - set(timing_by_stage))
        invalid_timing_stages = sorted(
            stage
            for stage in required_timing_stages & set(timing_by_stage)
            if not _timestamp(timing_by_stage[stage].get("started_at"))
            or not _timestamp(timing_by_stage[stage].get("ended_at"))
            or not isinstance(timing_by_stage[stage].get("duration_ms"), (int, float))
            or timing_by_stage[stage].get("duration_ms", 0) <= 0
            or not timing_by_stage[stage].get("status")
        )
        if missing_timing_stages:
            add("stage_timings_complete", "pipeline", CHECK_NOT_VERIFIABLE, f"Required stage timings are missing: {missing_timing_stages}.", "stage_timings")
        elif invalid_timing_stages:
            add("stage_timings_complete", "pipeline", CHECK_FAIL, f"Required stage timings are invalid: {invalid_timing_stages}.", "stage_timings")
        else:
            add("stage_timings_complete", "pipeline", CHECK_PASS, "All required stages have real timing and status evidence.", "stage_timings")

        artifact_keys = ("model", "result", "predictions")
        missing_artifacts = [key for key in artifact_keys if not artifacts.get(key)]
        absent_artifacts = [
            key
            for key in artifact_keys
            if artifacts.get(key) and not self.artifacts.artifact_exists(str(artifacts[key]))
        ]
        if missing_artifacts:
            add("model_artifacts_persisted", "artifact_persistence", CHECK_NOT_VERIFIABLE, f"Artifact paths are missing: {missing_artifacts}.", "artifacts")
        elif absent_artifacts:
            add("model_artifacts_persisted", "artifact_persistence", CHECK_FAIL, f"Persisted artifacts do not exist: {absent_artifacts}.", "artifacts")
        else:
            add("model_artifacts_persisted", "artifact_persistence", CHECK_PASS, "Model, result, and prediction artifacts exist.", "artifacts")

        failures = [row for row in checks if row["status"] == CHECK_FAIL]
        missing = [row for row in checks if row["status"] == CHECK_NOT_VERIFIABLE]
        warnings = [row for row in checks if row["status"] == CHECK_WARN]
        if failures:
            overall = "FAILED"
        elif missing:
            overall = "NOT_VERIFIABLE"
        elif warnings:
            overall = "VERIFIED_WITH_WARNINGS"
        else:
            overall = "VERIFIED"

        stages: list[dict[str, Any]] = []
        for stage in dict.fromkeys(row["stage"] for row in checks):
            stage_checks = [row for row in checks if row["stage"] == stage]
            statuses = {row["status"] for row in stage_checks}
            if CHECK_FAIL in statuses:
                status = "FAILED"
            elif CHECK_NOT_VERIFIABLE in statuses:
                status = "NOT_VERIFIABLE"
            elif CHECK_WARN in statuses:
                status = "VERIFIED_WITH_WARNINGS"
            else:
                status = "VERIFIED"
            stages.append(
                {
                    "stage": stage,
                    "status": status,
                    "check_ids": [row["check_id"] for row in stage_checks],
                }
            )

        return {
            "schema_version": 1,
            "overall_status": overall,
            "checks": checks,
            "stages": stages,
            "warnings": warnings,
            "failures": failures,
            "missing_evidence": missing,
            "summary": (
                "All required deterministic pipeline checks passed."
                if overall == "VERIFIED"
                else f"Deterministic verification finished with status {overall}."
            ),
        }


def verify_pipeline(report: dict[str, Any]) -> dict[str, Any]:
    return PipelineVerifier().verify(report)
