"""Versioned Python templates for ModelBuildReproductionSpec.

Templates emit only sklearn/pandas that is equivalent to persisted evidence.
Internal DCLab transforms become an explicit helper requirement instead of
fabricated logic. Same spec digest always yields the same source.
"""

from __future__ import annotations

import hashlib
from collections import OrderedDict
from typing import Any, Callable

from app.domain.model_build_reproduction import (
    AUTHORIZED_DATASET_PATH_PLACEHOLDER,
    GENERATOR_VERSION,
    ModelBuildReproductionSpec,
    ModelBuildStageCode,
    ReproductionCandidate,
    ReproductionPreprocessingStep,
)
from app.engine.modeling.validation_planner import (
    GROUP_KFOLD,
    KFOLD,
    STRATIFIED_GROUP_KFOLD,
    STRATIFIED_KFOLD,
    TIME_SERIES_SPLIT,
)

STAGE_SPECS: tuple[tuple[str, str], ...] = (
    ("ingestion", "Ingestion"),
    ("profiling_eda", "Profiling / EDA"),
    ("target_task", "Target + task"),
    ("structural_cleaning", "Structural cleaning"),
    ("final_holdout_plan", "Final holdout plan"),
    ("holdout_lock", "Holdout lock"),
    ("problem_profile", "Train-only ProblemProfile"),
    ("validation_plan", "ValidationPlan"),
    ("metric_plan", "MetricPlan"),
    ("leakage_audit", "Leakage audit"),
    ("missing_value_decisions", "Missing-value decisions"),
    ("feature_engineering", "Feature engineering"),
    ("preprocessing", "Preprocessing"),
    ("candidate_generation", "Candidate generation"),
    ("cv_training", "CV training"),
    ("candidate_comparison", "Candidate comparison"),
    ("winner_lock", "Winner lock"),
    ("final_refit", "Final refit"),
    ("final_holdout", "Final holdout"),
    ("artifact_reproducibility_persistence", "Artifact / reproducibility persistence"),
    ("deterministic_verification", "Deterministic verification"),
)

_SAFE_MODULES = (
    "sklearn.",
    "xgboost.",
    "lightgbm.",
    "catboost.",
)

_SKLEARN_METRICS = {
    "roc_auc": ("sklearn.metrics", "roc_auc_score"),
    "accuracy": ("sklearn.metrics", "accuracy_score"),
    "f1": ("sklearn.metrics", "f1_score"),
    "log_loss": ("sklearn.metrics", "log_loss"),
    "precision": ("sklearn.metrics", "precision_score"),
    "recall": ("sklearn.metrics", "recall_score"),
    "balanced_accuracy": ("sklearn.metrics", "balanced_accuracy_score"),
    "average_precision": ("sklearn.metrics", "average_precision_score"),
    "pr_auc": ("sklearn.metrics", "average_precision_score"),
    "r2": ("sklearn.metrics", "r2_score"),
    "mae": ("sklearn.metrics", "mean_absolute_error"),
    "mse": ("sklearn.metrics", "mean_squared_error"),
    "rmse": ("sklearn.metrics", "mean_squared_error"),
}

_SPLITTERS = {
    STRATIFIED_KFOLD: "sklearn.model_selection.StratifiedKFold",
    KFOLD: "sklearn.model_selection.KFold",
    STRATIFIED_GROUP_KFOLD: "sklearn.model_selection.StratifiedGroupKFold",
    GROUP_KFOLD: "sklearn.model_selection.GroupKFold",
    TIME_SERIES_SPLIT: "sklearn.model_selection.TimeSeriesSplit",
}

_PASSTHROUGH = {"identity", "passthrough"}
_DCLAB_TRANSFORMS = {
    "datetime_extract": "encode_datetime_columns (app.engine.features.encode)",
    "datetime_to_unix_seconds": "encode_datetime_columns (app.engine.features.encode)",
    "datetime_to_epoch": "encode_datetime_columns (app.engine.features.encode)",
    "coerce_numeric_like": "coerce_numeric_like (app.engine.lab.auto_prepare)",
    "drop_row": "clean_frame (app.engine.lab.auto_prepare)",
}


def _source_digest(source: str) -> str:
    return hashlib.sha256(source.encode("utf-8")).hexdigest()


def _helper(name: str) -> str:
    return f"Requires DCLab helper {name} [{GENERATOR_VERSION}]"


def _py_literal(value: Any) -> str:
    if value is None:
        return "None"
    if isinstance(value, bool):
        return "True" if value else "False"
    if isinstance(value, int) and not isinstance(value, bool):
        return str(value)
    if isinstance(value, float):
        return repr(value)
    if isinstance(value, str):
        return repr(value)
    if isinstance(value, (list, tuple)):
        return "[" + ", ".join(_py_literal(item) for item in value) + "]"
    if isinstance(value, dict):
        parts = [
            f"{_py_literal(str(key))}: {_py_literal(item)}"
            for key, item in sorted(value.items(), key=lambda pair: str(pair[0]))
        ]
        return "{" + ", ".join(parts) + "}"
    return repr(str(value))


def _call_kwargs(params: dict[str, Any]) -> str:
    if not params:
        return ""
    return ", ".join(
        f"{key}={_py_literal(value)}"
        for key, value in sorted(params.items(), key=lambda pair: pair[0])
        if key.isidentifier()
    )


def _split_class(path: str) -> tuple[str, str] | None:
    if "." not in path:
        return None
    module, name = path.rsplit(".", 1)
    if not name.isidentifier() or not module:
        return None
    return module, name


def _is_library_class(path: str) -> bool:
    return bool(path) and path.startswith(_SAFE_MODULES)


def _header(spec: ModelBuildReproductionSpec, key: str) -> list[str]:
    return [
        "# DCLab model-build reproduction",
        f"# generator={GENERATOR_VERSION}",
        f"# spec_digest={spec.spec_digest}",
        f"# stage={key}",
        "# This section is generated from canonical persisted evidence only.",
        "# It is not a notebook and does not embed raw rows, credentials, or Dataset.location.",
    ]


def _join(lines: list[str]) -> str:
    text = "\n".join(line.rstrip() for line in lines).rstrip() + "\n"
    return text


def strip_stage_header(source: str) -> str:
    """Drop the per-stage generator banner so assembled notebooks stay one document."""

    lines = source.splitlines()
    if not lines or not lines[0].startswith("# DCLab model-build reproduction"):
        text = source.rstrip() + "\n" if source else ""
        return text
    blank = next((index for index, line in enumerate(lines) if line == ""), None)
    rest = lines[blank + 1 :] if blank is not None else lines
    return ("\n".join(rest).rstrip() + "\n") if rest else ""


def _comment_missing(subject: str) -> list[str]:
    return [f"# No canonical evidence for {subject}."]


def _load_fn(source_type: str) -> tuple[str, list[str]]:
    kind = (source_type or "csv").lower()
    if kind in {"csv", "text", "file"}:
        return "pd.read_csv", []
    if kind in {"parquet", "pq"}:
        return "pd.read_parquet", []
    return (
        "pd.read_csv",
        [_helper(f"dataset loader for source_type={source_type!r}")],
    )


def _ctor(path: str, params: dict[str, Any]) -> str:
    split = _split_class(path)
    name = split[1] if split is not None else path
    kwargs = _call_kwargs(params)
    return f"{name}({kwargs})" if kwargs else f"{name}()"


def _import_line(path: str) -> str | None:
    split = _split_class(path)
    if split is None or not _is_library_class(path):
        return None
    module, name = split
    return f"from {module} import {name}"


def _constructor_params(step: ReproductionPreprocessingStep) -> dict[str, Any]:
    return {
        key: value
        for key, value in step.parameters.items()
        if key != "columns"
    }


def _step_columns(step: ReproductionPreprocessingStep) -> list[str]:
    value = step.parameters.get("columns")
    if isinstance(value, list) and all(isinstance(item, str) for item in value):
        return list(value)
    return []


def _group_preprocessing(
    steps: list[ReproductionPreprocessingStep],
) -> list[tuple[str, tuple[str, ...], list[ReproductionPreprocessingStep]]]:
    groups: list[tuple[str, tuple[str, ...], list[ReproductionPreprocessingStep]]] = []
    for step in steps:
        columns = tuple(_step_columns(step))
        key = (step.column_scope, columns)
        if groups and groups[-1][0] == key[0] and groups[-1][1] == columns:
            groups[-1][2].append(step)
        else:
            groups.append((step.column_scope, columns, [step]))
    return groups


def _estimator_lines(candidate: ReproductionCandidate) -> tuple[list[str], list[str]]:
    helpers: list[str] = []
    path = candidate.implementation_class or ""
    lines: list[str] = []
    if candidate.library_version:
        lines.append(
            f"# library={candidate.implementation_library} version={candidate.library_version}"
        )
    if not path:
        helpers.append(_helper(f"model family {candidate.model_family}"))
        lines.append(
            f"# Requires DCLab helper for model family {candidate.model_family!r}"
        )
        return lines, helpers
    if not _is_library_class(path):
        helpers.append(_helper(path or candidate.model_family))
        lines.append(f"# Requires DCLab helper {path or candidate.model_family}")
        return lines, helpers
    imported = _import_line(path)
    if imported:
        lines.append(imported)
    lines.append(f"estimator = {_ctor(path, candidate.hyperparameters)}")
    return lines, helpers


def _render_ingestion(spec: ModelBuildReproductionSpec) -> tuple[str, list[str], bool]:
    dataset = spec.dataset
    loader, helpers = _load_fn(dataset.source_type)
    lines = [
        "from pathlib import Path",
        "import pandas as pd",
        "",
        "# Replace the placeholder with an authorized local path to this dataset version.",
        "# Never substitute a persisted Dataset.location, object-store key, or credential.",
        f"DATASET_PATH = Path({_py_literal(AUTHORIZED_DATASET_PATH_PLACEHOLDER)})",
        f"CONTENT_DIGEST = {_py_literal(dataset.content_digest)}",
        f"SCHEMA_DIGEST = {_py_literal(dataset.schema_digest)}",
        f"SOURCE_TYPE = {_py_literal(dataset.source_type)}",
        f"DATASET_VERSION = {_py_literal(dataset.version)}",
        "",
        f"frame = {loader}(DATASET_PATH)",
        "schema_columns = [",
        *[
            "    "
            + _py_literal(
                {
                    "name": column.name,
                    "ordinal_position": column.ordinal_position,
                    "physical_dtype": column.physical_dtype,
                    "semantic_type": column.semantic_type,
                    "role": column.role,
                }
            )
            + ","
            for column in dataset.columns
        ],
        "]",
    ]
    if helpers:
        lines = [f"# {helpers[0]}", *lines]
    return _join(lines), helpers, True


def _render_profiling(spec: ModelBuildReproductionSpec) -> tuple[str, list[str], bool]:
    lines = [
        "# Profiling evidence is persisted as finding identifiers and type counts.",
        "# Raw sample rows, value histograms, and customer records are not generated.",
        f"# dataset_content_digest = {spec.dataset.content_digest}",
        f"# schema_digest = {spec.dataset.schema_digest}",
        f"# column_count = {spec.dataset.column_count}",
        f"# row_count = {spec.dataset.row_count}",
    ]
    return _join(lines), [], True


def _render_target(spec: ModelBuildReproductionSpec) -> tuple[str, list[str], bool]:
    if not spec.task.target_column and not spec.task.task_type:
        return _join(_comment_missing("target and task")), [], False
    lines = [
        f"TARGET_COLUMN = {_py_literal(spec.task.target_column)}",
        f"TASK_TYPE = {_py_literal(spec.task.task_type)}",
        f"SEED = {spec.task.seed}",
        "",
        "y = frame[TARGET_COLUMN]",
        "X = frame.drop(columns=[TARGET_COLUMN])",
    ]
    return _join(lines), [], True


def _render_structural(spec: ModelBuildReproductionSpec) -> tuple[str, list[str], bool]:
    helpers: list[str] = []
    lines = ["# Structural cleaning from persisted preparation decisions."]
    if spec.dropped_columns:
        lines += [
            f"dropped_columns = {_py_literal(spec.dropped_columns)}",
            "frame = frame.drop(columns=dropped_columns, errors='ignore')",
        ]
    else:
        lines.append("dropped_columns = []")
    if spec.datetime_columns:
        helper = _helper(_DCLAB_TRANSFORMS["datetime_extract"])
        helpers.append(helper)
        lines += [
            "",
            f"# {helper}",
            f"datetime_columns = {_py_literal(spec.datetime_columns)}",
        ]
    else:
        lines.append("# No datetime conversions were persisted for this run.")
    return _join(lines), helpers, True


def _render_holdout_plan(spec: ModelBuildReproductionSpec) -> tuple[str, list[str], bool]:
    plan = spec.holdout_plan
    if plan.strategy is None:
        return _join(_comment_missing("HoldoutPlan")), [], False
    helpers: list[str] = []
    lines = [
        f"TARGET_COLUMN = {_py_literal(spec.task.target_column)}",
        f"HOLDOUT_STRATEGY = {_py_literal(plan.strategy)}",
        f"HOLDOUT_TEST_SIZE = {_py_literal(plan.test_size)}",
        f"HOLDOUT_GROUP_COLUMN = {_py_literal(plan.group_column)}",
        f"HOLDOUT_TIME_COLUMN = {_py_literal(plan.time_column)}",
        f"HOLDOUT_PLAN_DIGEST = {_py_literal(plan.plan_digest)}",
        f"SEED = {spec.task.seed}",
        "",
        "# Lock the final holdout before candidate search. Do not score it until winner lock.",
    ]
    if plan.strategy == "temporal_future":
        helper = _helper("split_train_test_holdout (app.engine.validation.splits)")
        helpers.append(helper)
        lines.append(f"# {helper}")
        lines.append("# Temporal cut logic is not emitted as standalone sklearn.")
    elif plan.strategy == "group_disjoint":
        lines = [
            "from sklearn.model_selection import GroupShuffleSplit",
            "",
            *lines,
            "holdout_splitter = GroupShuffleSplit(",
            "    n_splits=1, test_size=HOLDOUT_TEST_SIZE, random_state=SEED",
            ")",
            "train_idx, holdout_idx = next(",
            "    holdout_splitter.split(frame, groups=frame[HOLDOUT_GROUP_COLUMN])",
            ")",
            "train_frame = frame.iloc[train_idx].copy()",
            "holdout_frame = frame.iloc[holdout_idx].copy()",
        ]
    elif plan.strategy in {"stratified_random", "random"}:
        stratify = "frame[TARGET_COLUMN]" if plan.strategy == "stratified_random" else "None"
        lines = [
            "from sklearn.model_selection import train_test_split",
            "",
            *lines,
            "train_frame, holdout_frame = train_test_split(",
            "    frame,",
            "    test_size=HOLDOUT_TEST_SIZE,",
            "    random_state=SEED,",
            f"    stratify={stratify},",
            ")",
        ]
    else:
        helper = _helper(f"holdout strategy {plan.strategy}")
        helpers.append(helper)
        lines.append(f"# {helper}")
    return _join(lines), helpers, True


def _render_holdout_lock(spec: ModelBuildReproductionSpec) -> tuple[str, list[str], bool]:
    if spec.holdout_plan.strategy is None:
        return _join(_comment_missing("holdout lock")), [], False
    lines = [
        "# Holdout rows stay unused until final holdout evaluation.",
        "X_train = train_frame.drop(columns=[TARGET_COLUMN])",
        "y_train = train_frame[TARGET_COLUMN]",
        "X_holdout = holdout_frame.drop(columns=[TARGET_COLUMN])",
        "y_holdout = holdout_frame[TARGET_COLUMN]",
        "del holdout_frame  # keep the partition locked; do not inspect labels during search",
    ]
    return _join(lines), [], True


def _render_problem_profile(spec: ModelBuildReproductionSpec) -> tuple[str, list[str], bool]:
    lines = [
        f"TASK_TYPE = {_py_literal(spec.task.task_type)}",
        f"MODELED_FEATURE_COUNT = {len(spec.modeled_features)}",
        f"EXCLUDED_FEATURE_COUNT = {len(spec.leakage_exclusions)}",
        f"DROPPED_COLUMN_COUNT = {len(spec.dropped_columns)}",
        "# ProblemProfile is train-partition evidence only.",
    ]
    return _join(lines), [], True


def _render_validation_plan(spec: ModelBuildReproductionSpec) -> tuple[str, list[str], bool]:
    plan = spec.validation_plan
    if plan.strategy is None or plan.actual_folds is None:
        return _join(_comment_missing("ValidationPlan")), [], False
    path = _SPLITTERS.get(plan.strategy)
    helpers: list[str] = []
    if path is None:
        helper = _helper(f"validation strategy {plan.strategy}")
        helpers.append(helper)
        return _join([f"# {helper}"]), helpers, True
    params: dict[str, Any] = {"n_splits": plan.actual_folds}
    if plan.strategy == GROUP_KFOLD:
        pass
    elif plan.strategy != TIME_SERIES_SPLIT:
        params["shuffle"] = True if plan.shuffle else False
        if plan.random_state is not None:
            params["random_state"] = plan.random_state
    imported = _import_line(path)
    lines = [
        imported or f"# {path}",
        "",
        f"VALIDATION_STRATEGY = {_py_literal(plan.strategy)}",
        f"REQUESTED_FOLDS = {_py_literal(plan.requested_folds)}",
        f"ACTUAL_FOLDS = {_py_literal(plan.actual_folds)}",
        f"VALIDATION_GROUP_COLUMN = {_py_literal(plan.group_column)}",
        f"VALIDATION_TIME_COLUMN = {_py_literal(plan.time_column)}",
        "# Splitter kwargs follow persisted strategy + experiment.seed via build_splitter.",
        f"cv = {_ctor(path, params)}",
    ]
    return _join(lines), helpers, True


def _render_metric_plan(spec: ModelBuildReproductionSpec) -> tuple[str, list[str], bool]:
    metric = spec.metric_plan.primary_metric
    if not metric:
        return _join(_comment_missing("MetricPlan")), [], False
    mapped = _SKLEARN_METRICS.get(metric)
    helpers: list[str] = []
    lines = [f"PRIMARY_METRIC = {_py_literal(metric)}"]
    if mapped is None:
        helper = _helper(f"metric {metric} (app.engine.evaluation.metrics)")
        helpers.append(helper)
        lines.append(f"# {helper}")
    else:
        module, name = mapped
        lines += [
            f"from {module} import {name}",
            f"selection_scorer = {name}",
        ]
        if metric == "rmse":
            lines.append("# Persisted metric name is rmse; sklearn mean_squared_error needs squared=False.")
            helper = _helper("rmse via mean_squared_error(squared=False)")
            helpers.append(helper)
            lines.append(f"# {helper}")
    return _join(lines), helpers, True


def _render_leakage(spec: ModelBuildReproductionSpec) -> tuple[str, list[str], bool]:
    names = [item.column for item in spec.leakage_exclusions]
    lines = [
        f"leakage_exclusions = {_py_literal(names)}",
        "frame = frame.drop(columns=leakage_exclusions, errors='ignore')",
        "X_train = X_train.drop(columns=leakage_exclusions, errors='ignore')",
        "# Exclusions are applied before candidate generation. Holdout follows the same columns.",
    ]
    return _join(lines), [], True


def _render_missing(spec: ModelBuildReproductionSpec) -> tuple[str, list[str], bool]:
    imputers = [
        step
        for step in spec.preprocessing
        if step.transformer_type == "impute"
    ]
    if not imputers:
        return _join(
            [
                "# Missing-value imputers are recorded as preprocessing steps when present.",
                *_comment_missing("missing-value imputers"),
            ]
        ), [], False
    lines = [
        "# Missing-value strategies are applied by the fitted preprocessor below.",
        "# Do not impute using holdout statistics.",
        *[
            f"# sequence={step.sequence} scope={step.column_scope} "
            f"class={step.transformer_class} params={_py_literal(_constructor_params(step))}"
            for step in imputers
        ],
    ]
    return _join(lines), [], True


def _render_features(spec: ModelBuildReproductionSpec) -> tuple[str, list[str], bool]:
    helpers: list[str] = []
    lines = [
        f"modeled_features = {_py_literal(spec.modeled_features)}",
        f"datetime_columns = {_py_literal(spec.datetime_columns)}",
    ]
    for feature in spec.feature_recipe:
        if feature.status != "modeled":
            continue
        for transform in feature.transformations:
            if transform.transformation_type in _PASSTHROUGH:
                continue
            if transform.transformer_class and _is_library_class(transform.transformer_class):
                lines.append(
                    f"# {feature.name}: {transform.transformer_class}"
                    f"({_call_kwargs(transform.parameters)})"
                )
                continue
            name = _DCLAB_TRANSFORMS.get(
                transform.transformation_type,
                transform.transformer_class or transform.transformation_type,
            )
            helper = _helper(name)
            if helper not in helpers:
                helpers.append(helper)
            lines.append(f"# {feature.name}: {helper}")
    if spec.datetime_columns and not any("encode_datetime_columns" in item for item in helpers):
        helper = _helper(_DCLAB_TRANSFORMS["datetime_extract"])
        helpers.append(helper)
        lines.append(f"# {helper}")
    if not spec.modeled_features:
        return _join(_comment_missing("feature recipe")), helpers, False
    return _join(lines), helpers, True


def _render_preprocessing(spec: ModelBuildReproductionSpec) -> tuple[str, list[str], bool]:
    if not spec.preprocessing:
        return _join(_comment_missing("preprocessing steps")), [], False
    helpers: list[str] = []
    imports: list[str] = [
        "from sklearn.compose import ColumnTransformer",
        "from sklearn.pipeline import Pipeline",
    ]
    transformers: list[str] = []
    for scope, columns, steps in _group_preprocessing(spec.preprocessing):
        inner: list[str] = []
        usable = True
        for index, step in enumerate(steps, start=1):
            if step.transformer_class.startswith("sklearn.preprocessing.OneHotEncoder"):
                if "sparse_output" not in step.parameters and "sparse" not in step.parameters:
                    helper = _helper("_one_hot_encoder (app.engine.lab.auto_prepare)")
                    if helper not in helpers:
                        helpers.append(helper)
            if not _is_library_class(step.transformer_class):
                helper = _helper(step.transformer_class or step.transformer_type)
                helpers.append(helper)
                usable = False
                inner.append(f"# {helper}")
                continue
            imported = _import_line(step.transformer_class)
            if imported and imported not in imports:
                imports.append(imported)
            alias = step.transformer_type or f"step_{index}"
            inner.append(
                "            ("
                f"{_py_literal(alias)}, "
                f"{_ctor(step.transformer_class, _constructor_params(step))}),"
            )
        fit_scope = steps[0].fit_scope if steps else "train"
        if not usable:
            transformers.append(
                f"        # {_helper(scope)} fit_scope={fit_scope}"
            )
            continue
        col_literal = _py_literal(list(columns))
        if len(steps) == 1:
            step = steps[0]
            transformers.append(
                "        ("
                f"{_py_literal(scope)}, "
                f"{_ctor(step.transformer_class, _constructor_params(step))}, "
                f"{col_literal}),"
            )
        else:
            transformers.append(
                "        ("
                f"{_py_literal(scope)}, Pipeline(steps=[\n"
                + "\n".join(inner)
                + f"\n        ]), {col_literal}),"
            )
        if fit_scope:
            transformers.append(f"        # fit_scope={fit_scope}")
    lines = [
        *imports,
        "",
        "# Fitted on the training partition only. Do not refit using holdout rows.",
        "preprocessor = ColumnTransformer(",
        "    transformers=[",
        *transformers,
        "    ]",
        ")",
    ]
    return _join(lines), helpers, True


def _render_candidates(spec: ModelBuildReproductionSpec) -> tuple[str, list[str], bool]:
    if not spec.candidates:
        return _join(_comment_missing("candidates")), [], False
    helpers: list[str] = []
    lines = [
        "# Instantiated from persisted implementation_class and applied hyperparameters.",
        "# Do not add unpersisted estimator wrappers.",
        "candidates = {}",
    ]
    for candidate in spec.candidates:
        path = candidate.implementation_class or ""
        lines.append("")
        lines.append(f"# family={candidate.model_family} fingerprint={candidate.fingerprint}")
        if candidate.library_version:
            lines.append(
                f"# library={candidate.implementation_library} version={candidate.library_version}"
            )
        if not path or not _is_library_class(path):
            helper = _helper(path or candidate.model_family)
            helpers.append(helper)
            lines.append(f"# {helper}")
            continue
        imported = _import_line(path)
        if imported:
            lines.append(imported)
        lines.append(
            f"candidates[{_py_literal(candidate.fingerprint)}] = {_ctor(path, candidate.hyperparameters)}"
        )
    winner = spec.winner
    if winner.implementation_class and _is_library_class(winner.implementation_class):
        lines += [
            "",
            f"WINNER_FINGERPRINT = {_py_literal(winner.fingerprint)}",
            "estimator = candidates[WINNER_FINGERPRINT]",
        ]
    return _join(lines), helpers, True


def _render_cv(spec: ModelBuildReproductionSpec) -> tuple[str, list[str], bool]:
    if spec.validation_plan.strategy is None or not spec.candidates:
        return _join(_comment_missing("CV configuration")), [], False
    helpers: list[str] = []
    scores = OrderedDict(
        (candidate.fingerprint, candidate.cv_score)
        for candidate in spec.candidates
        if candidate.cv_score is not None
    )
    groups = ""
    if spec.validation_plan.strategy in {GROUP_KFOLD, STRATIFIED_GROUP_KFOLD}:
        groups = ", groups=train_frame[VALIDATION_GROUP_COLUMN]"
    helper = _helper("classification_metrics/regression_metrics (app.engine.evaluation.metrics)")
    helpers.append(helper)
    lines = [
        "from sklearn.base import clone",
        "from sklearn.pipeline import Pipeline",
        "",
        "# Cross-validation uses the training partition only.",
        "# The final holdout must not be used for candidate selection.",
        f"# {helper}",
        "for fingerprint, estimator in candidates.items():",
        f"    for train_idx, val_idx in cv.split(X_train, y_train{groups}):",
        "        pipeline = Pipeline([('prep', clone(preprocessor)), ('model', clone(estimator))])",
        "        pipeline.fit(X_train.iloc[train_idx], y_train.iloc[train_idx])",
        "        # Score val_idx with the persisted primary metric helper. Do not use holdout rows.",
        "",
        "# Persisted CV aggregate scores used for ranking (not holdout):",
        f"PERSISTED_CV_SCORES = {_py_literal(dict(scores))}",
    ]
    if spec.validation_plan.strategy == TIME_SERIES_SPLIT:
        time_helper = _helper(
            "iter_validation_folds time ordering (app.engine.modeling.validation_planner)"
        )
        helpers.append(time_helper)
        lines.insert(3, f"# {time_helper}")
    return _join(lines), helpers, True


def _render_comparison(spec: ModelBuildReproductionSpec) -> tuple[str, list[str], bool]:
    if spec.winner.candidate_id is None:
        return _join(_comment_missing("candidate comparison")), [], False
    lines = [
        "# Rank candidates from persisted CV aggregates only.",
        "# Never read X_holdout or y_holdout in this stage.",
        f"SELECTION_METRIC = {_py_literal(spec.winner.selection_metric)}",
        f"SELECTION_POLICY = {_py_literal(spec.winner.selection_policy)}",
        f"PERSISTED_CV_SCORES = {_py_literal({c.fingerprint: c.cv_score for c in spec.candidates})}",
        f"WINNER_FINGERPRINT = {_py_literal(spec.winner.fingerprint)}",
        f"RUNNER_UP_FINGERPRINT = {_py_literal(spec.winner.runner_up_fingerprint)}",
        "assert WINNER_FINGERPRINT not in {None, ''}",
    ]
    return _join(lines), [], True


def _render_winner(spec: ModelBuildReproductionSpec) -> tuple[str, list[str], bool]:
    if spec.winner.candidate_id is None:
        return _join(_comment_missing("winner identity")), [], False
    lines = [
        "# Winner identity locked from CV. Holdout evaluation happens later.",
        f"WINNER_CANDIDATE_ID = {_py_literal(str(spec.winner.candidate_id))}",
        f"WINNER_FINGERPRINT = {_py_literal(spec.winner.fingerprint)}",
        f"WINNER_FAMILY = {_py_literal(spec.winner.model_family)}",
        f"WINNER_CLASS = {_py_literal(spec.winner.implementation_class)}",
        f"SELECTED_SCORE = {_py_literal(spec.winner.selected_score)}",
        f"SELECTION_METRIC = {_py_literal(spec.winner.selection_metric)}",
        f"SELECTION_POLICY = {_py_literal(spec.winner.selection_policy)}",
    ]
    if spec.winner.reason:
        lines.append(f"# reason: {spec.winner.reason[:300]}")
    return _join(lines), [], True


def _render_refit(spec: ModelBuildReproductionSpec) -> tuple[str, list[str], bool]:
    if spec.winner.implementation_class is None:
        return _join(_comment_missing("final refit")), [], False
    helpers: list[str] = []
    ctor_lines, ctor_helpers = _estimator_lines(
        ReproductionCandidate(
            id=spec.winner.candidate_id or spec.pipeline_run_id,
            fingerprint=spec.winner.fingerprint or "",
            model_family=spec.winner.model_family or "",
            algorithm=spec.winner.algorithm or "",
            implementation_library=spec.winner.implementation_library,
            implementation_class=spec.winner.implementation_class,
            library_version=spec.winner.library_version,
            hyperparameters=spec.winner.hyperparameters,
            is_winner=True,
        )
    )
    helpers.extend(ctor_helpers)
    lines = [
        "from sklearn.pipeline import Pipeline",
        "",
        "# Refit the locked winner on the full training partition only.",
        f"MODEL_VERSION_DIGEST = {_py_literal(spec.final_refit.content_digest)}",
        *ctor_lines,
        "pipeline = Pipeline([('prep', preprocessor), ('model', estimator)])",
        "pipeline.fit(X_train, y_train)",
    ]
    return _join(lines), helpers, True


def _render_holdout_eval(spec: ModelBuildReproductionSpec) -> tuple[str, list[str], bool]:
    if spec.winner.candidate_id is None:
        return _join(_comment_missing("final holdout evaluation")), [], False
    metrics_helper = _helper(
        "classification_metrics/regression_metrics (app.engine.evaluation.metrics)"
    )
    predict_helper = _helper("_predict (app.engine.experiments.runner)")
    lines = [
        "# Evaluate the locked winner once. Do not change selection using these scores.",
        f"HOLDOUT_METRICS = {_py_literal(spec.final_holdout.metrics)}",
        f"# {metrics_helper}",
        f"# {predict_helper}",
        "# Apply the locked pipeline to X_holdout. Holdout labels and row values are not emitted.",
    ]
    return _join(lines), [metrics_helper, predict_helper], True


def _render_artifacts(spec: ModelBuildReproductionSpec) -> tuple[str, list[str], bool]:
    lines = [
        "# Artifact bytes and object-store keys are not generated.",
        f"# model_version_digest = {spec.final_refit.content_digest}",
        f"# selected_fingerprint = {spec.final_refit.selected_fingerprint}",
        f"# generator = {GENERATOR_VERSION}",
        f"# spec_digest = {spec.spec_digest}",
    ]
    return _join(lines), [], True


def _render_verification(spec: ModelBuildReproductionSpec) -> tuple[str, list[str], bool]:
    lines = [
        "# Deterministic verification status is persisted separately.",
        "# This generator does not fabricate audit checks or embed verification payloads.",
        f"# spec_digest = {spec.spec_digest}",
    ]
    return _join(lines), [], True


_RENDERERS: dict[str, Callable[[ModelBuildReproductionSpec], tuple[str, list[str], bool]]] = {
    "ingestion": _render_ingestion,
    "profiling_eda": _render_profiling,
    "target_task": _render_target,
    "structural_cleaning": _render_structural,
    "final_holdout_plan": _render_holdout_plan,
    "holdout_lock": _render_holdout_lock,
    "problem_profile": _render_problem_profile,
    "validation_plan": _render_validation_plan,
    "metric_plan": _render_metric_plan,
    "leakage_audit": _render_leakage,
    "missing_value_decisions": _render_missing,
    "feature_engineering": _render_features,
    "preprocessing": _render_preprocessing,
    "candidate_generation": _render_candidates,
    "cv_training": _render_cv,
    "candidate_comparison": _render_comparison,
    "winner_lock": _render_winner,
    "final_refit": _render_refit,
    "final_holdout": _render_holdout_eval,
    "artifact_reproducibility_persistence": _render_artifacts,
    "deterministic_verification": _render_verification,
}


def render_stage_code(spec: ModelBuildReproductionSpec) -> list[ModelBuildStageCode]:
    rows: list[ModelBuildStageCode] = []
    for sequence, (key, title) in enumerate(STAGE_SPECS, start=1):
        renderer = _RENDERERS[key]
        body, helpers, available = renderer(spec)
        unique_helpers = list(dict.fromkeys(helpers))
        source = _join([*_header(spec, key), "", body.rstrip()])
        status = "supported" if available else "not_available"
        rows.append(
            ModelBuildStageCode(
                key=key,
                sequence=sequence,
                title=title,
                generator_version=GENERATOR_VERSION,
                spec_digest=spec.spec_digest,
                source=source,
                digest=_source_digest(source),
                helper_requirements=unique_helpers,
                code_generation_support_status=status,
            )
        )
    return rows
