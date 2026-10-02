"""Versioned Python templates for ModelBuildReproductionSpec.

Templates emit only sklearn/pandas that is equivalent to persisted evidence.
Internal DCLab transforms become an explicit helper requirement instead of
fabricated logic. Same spec digest always yields the same source.

The assembled script is standalone (P2.4-B): it loads an authorized local copy
of the dataset version, partitions holdout and outer folds by the run's stored
SplitPlan map (verified against ``assignment_digest``), re-runs every trained
candidate's fold-local Pipeline and compares the CV scores with the persisted
ones. Runs without a SplitPlan keep re-deriving folds with the planned splitter.
"""

from __future__ import annotations

import hashlib
import math
from collections import OrderedDict
from typing import Any, Callable

from app.domain.model_build_reproduction import (
    AUTHORIZED_DATASET_PATH_PLACEHOLDER,
    AUTHORIZED_SPLIT_ASSIGNMENT_PATH_PLACEHOLDER,
    DATASET_PATH_ENV,
    GENERATOR_VERSION,
    SPLIT_ASSIGNMENT_PATH_ENV,
    ModelBuildReproductionSpec,
    ModelBuildStageCode,
    ReproductionCandidate,
    ReproductionPreprocessingStep,
)
from app.engine.evaluation.metrics import LOWER_IS_BETTER
from app.engine.modeling.validation_planner import (
    GROUP_KFOLD,
    KFOLD,
    STRATIFIED_GROUP_KFOLD,
    STRATIFIED_KFOLD,
    TIME_SERIES_SPLIT,
)
from app.engine.search.generator import DUMMY_FAMILIES

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


def _is_int(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def _py_literal(value: Any) -> str:
    if value is None:
        return "None"
    if isinstance(value, bool):
        return "True" if value else "False"
    if _is_int(value):
        return str(value)
    if isinstance(value, float):
        if math.isnan(value):
            return "float('nan')"
        if math.isinf(value):
            return "float('inf')" if value > 0 else "float('-inf')"
        return repr(value)
    if isinstance(value, str):
        return repr(value)
    if isinstance(value, (list, tuple)):
        return "[" + ", ".join(_py_literal(item) for item in value) + "]"
    if isinstance(value, dict):
        # Integer keys (fold numbers, class codes) stay integers; others are strings.
        parts = [
            f"{key if _is_int(key) else _py_literal(str(key))}: {_py_literal(item)}"
            for key, item in sorted(
                value.items(),
                key=lambda pair: (0, pair[0], "") if _is_int(pair[0]) else (1, 0, str(pair[0])),
            )
        ]
        return "{" + ", ".join(parts) + "}"
    return repr(str(value))


def _comment_text(value: Any, limit: int = 300) -> str:
    """Untrusted text (column names, reasons) on one comment line: no line breaks."""

    return " ".join(str(value).split())[:limit]


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


# --- standalone execution helpers (P2.4-B) -------------------------------------

# Emitted verbatim into plan runs. Mirrors parse_split_assignment and
# validation_planner.folds_from_assignment (fold 0 = TimeSeriesSplit warm-up).
SPLIT_ASSIGNMENT_HELPERS = f'''def read_split_assignment(payload, expected_digest):
    """Stored SplitPlan map -> (train row -> outer fold, holdout rows). Fails closed."""
    if hashlib.sha256(payload).hexdigest() != expected_digest:
        raise RuntimeError("split assignment bytes do not match ASSIGNMENT_DIGEST")
    text = payload.decode("utf-8")
    lines = text[:-1].split("\\n") if text.endswith("\\n") else []
    if not lines or lines[0] != "source_row,partition,fold":
        raise RuntimeError("split assignment header is not canonical")
    train_folds, holdout_rows = {{}}, set()
    for line in lines[1:]:
        row, partition, fold = line.split(",")
        if partition == "holdout" and fold == "":
            holdout_rows.add(int(row))
        elif partition == "train" and fold.isdigit():
            train_folds[int(row)] = int(fold)
        else:
            raise RuntimeError("malformed split assignment line")
    return train_folds, holdout_rows


def outer_folds(fold_of, strategy, n_folds):
    """(fold number, train positions, validation positions) from the stored fold map.

    Group/time invariants were proven when the map was stored; ASSIGNMENT_DIGEST pins it.
    """
    fold_of = np.asarray(fold_of, dtype=int)
    expected = list(range(1, int(n_folds) + 1))
    if sorted({{int(value) for value in fold_of if int(value) > 0}}) != expected:
        raise RuntimeError("stored fold map does not match ACTUAL_FOLDS")
    if strategy != {TIME_SERIES_SPLIT!r} and bool((fold_of == 0).any()):
        raise RuntimeError("stored fold map leaves training rows unvalidated")
    folds = []
    for number in expected:
        validation = np.flatnonzero(fold_of == number)
        # Expanding window: TimeSeriesSplit trains fold k on every earlier row.
        if strategy == {TIME_SERIES_SPLIT!r}:
            training = np.flatnonzero(fold_of < number)
        else:
            training = np.flatnonzero(fold_of != number)
        folds.append((number, training, validation))
    return folds
'''

_BINARY_METRICS = '''DEFAULT_THRESHOLD = 0.5


def score_metrics(y_values, scores, threshold=DEFAULT_THRESHOLD):
    """Binary metrics as app.engine.evaluation.metrics.classification_metrics computes them."""
    labels = np.asarray(y_values)
    raw = np.asarray(scores, dtype=float)
    p = np.clip(raw, 1e-7, 1 - 1e-7)
    decided = (raw >= threshold).astype(int)
    try:
        roc = float(roc_auc_score(labels, p))
    except ValueError:
        roc = 0.5
    if not np.isfinite(roc):
        roc = 0.5
    base_rate = float(labels.mean()) if len(labels) else 0.0
    try:
        pr = float(average_precision_score(labels, p))
    except ValueError:
        pr = base_rate
    if not np.isfinite(pr):
        pr = base_rate
    return {
        "accuracy": float(accuracy_score(labels, decided)),
        "roc_auc": roc,
        "pr_auc": pr,
        "precision": float(precision_score(labels, decided, zero_division=0)),
        "recall": float(recall_score(labels, decided, zero_division=0)),
        "f1": float(f1_score(labels, decided, zero_division=0)),
        "log_loss": float(log_loss(labels, p, labels=[0, 1])),
        "balanced_accuracy": float(balanced_accuracy_score(labels, decided)),
        "brier": float(brier_score_loss(labels, p)),
        "brier_score": float(brier_score_loss(labels, p)),
    }
'''

_MULTICLASS_METRICS = '''def score_metrics(y_values, proba, threshold=None):
    """Multiclass metrics as app.engine.evaluation.metrics.multiclass_metrics computes them."""
    labels = np.asarray(y_values).astype(int)
    p = np.clip(np.asarray(proba, dtype=float).reshape(len(labels), N_CLASSES), 1e-7, 1.0)
    p = p / p.sum(axis=1, keepdims=True)
    decided = p.argmax(axis=1)
    codes = list(range(N_CLASSES))
    present = sorted(set(int(value) for value in labels))
    aucs = []
    for code in present:
        positives = labels == code
        if positives.all() or not positives.any():
            continue
        aucs.append(float(roc_auc_score(positives.astype(int), p[:, code])))
    scored = present or codes
    return {
        "accuracy": float(accuracy_score(labels, decided)),
        "balanced_accuracy": float(balanced_accuracy_score(labels, decided)) if len(labels) else 0.0,
        "macro_f1": float(f1_score(labels, decided, labels=scored, average="macro", zero_division=0)),
        "weighted_f1": float(f1_score(labels, decided, labels=scored, average="weighted", zero_division=0)),
        "macro_precision": float(precision_score(labels, decided, labels=scored, average="macro", zero_division=0)),
        "macro_recall": float(recall_score(labels, decided, labels=scored, average="macro", zero_division=0)),
        "log_loss": float(log_loss(labels, p, labels=codes)),
        "roc_auc_ovr": float(np.mean(aucs)) if aucs else 0.5,
    }
'''

_REGRESSION_METRICS = '''def score_metrics(y_values, predictions, threshold=None):
    """Regression metrics as app.engine.evaluation.metrics.regression_metrics computes them."""
    actual = np.asarray(y_values, dtype=float)
    p = np.asarray(predictions, dtype=float)
    mse = float(mean_squared_error(actual, p))
    return {
        "mae": float(mean_absolute_error(actual, p)),
        "mse": mse,
        "rmse": float(mse ** 0.5),
        "r2": float(r2_score(actual, p)) if len(actual) > 1 else 0.0,
        "mape": float(np.mean(np.abs((actual - p) / np.clip(np.abs(actual), 1e-6, None)))) * 100,
        "smape": float(np.mean(2 * np.abs(actual - p) / np.clip(np.abs(actual) + np.abs(p), 1e-6, None))) * 100,
        "median_absolute_error": float(median_absolute_error(actual, p)),
    }
'''

_METRIC_SOURCES: dict[str, tuple[str, str]] = {
    "binary": (
        "from sklearn.metrics import accuracy_score, average_precision_score, "
        "balanced_accuracy_score, brier_score_loss, f1_score, log_loss, precision_score, "
        "recall_score, roc_auc_score",
        _BINARY_METRICS,
    ),
    "multiclass": (
        "from sklearn.metrics import accuracy_score, balanced_accuracy_score, f1_score, "
        "log_loss, precision_score, recall_score, roc_auc_score",
        _MULTICLASS_METRICS,
    ),
    "regression": (
        "from sklearn.metrics import mean_absolute_error, mean_squared_error, "
        "median_absolute_error, r2_score",
        _REGRESSION_METRICS,
    ),
}

_PREDICT_SOURCES: dict[str, str] = {
    "binary": '''def predict_scores(model, features):
    """Positive-class probability (app.engine.experiments.runner._predict)."""
    if hasattr(model, "predict_proba"):
        proba = model.predict_proba(features)
        return np.asarray(proba[:, 1] if proba.shape[1] > 1 else proba[:, 0], dtype=float)
    return np.asarray(model.predict(features), dtype=float)
''',
    "multiclass": '''def predict_scores(model, features):
    """(n, N_CLASSES) probabilities aligned to label codes; an unseen class keeps zeros."""
    out = np.zeros((len(features), N_CLASSES), dtype=float)
    if hasattr(model, "predict_proba"):
        classes = np.asarray(model.classes_, dtype=int)
        out[:, classes] = np.asarray(model.predict_proba(features), dtype=float)
    else:
        predicted = np.asarray(model.predict(features), dtype=int)
        out[np.arange(len(predicted)), predicted] = 1.0
    return out
''',
    "regression": '''def predict_scores(model, features):
    return np.asarray(model.predict(features), dtype=float)
''',
}

_CONTIGUOUS_LABEL_CLASSIFIER = '''class ContiguousLabelClassifier(ClassifierMixin, BaseEstimator):
    """Fit on labels re-coded to 0..m-1; classes_ keeps the original codes (DCLab registry)."""

    def __init__(self, estimator=None):
        self.estimator = estimator

    def fit(self, X, y, **fit_params):
        self.classes_, codes = np.unique(np.asarray(y), return_inverse=True)
        self.estimator_ = clone(self.estimator).fit(X, codes, **fit_params)
        return self

    def predict_proba(self, X):
        return self.estimator_.predict_proba(X)

    def predict(self, X):
        return self.classes_[np.asarray(self.estimator_.predict(X), dtype=int)]
'''

# Registry families wrapped in a scaler Pipeline (app.engine.models.registry.make_model).
_SCALED_FAMILIES = {
    "logistic_regression": "clf",
    "linear_regression": "m",
    "ridge": "m",
    "lasso": "m",
    "elasticnet": "m",
}


def _task_kind(spec: ModelBuildReproductionSpec) -> str | None:
    task = spec.task.task_type
    if task == "multiclass" and not spec.task.class_labels:
        return None
    return task if task in _METRIC_SOURCES else None


def _scope_columns(spec: ModelBuildReproductionSpec, scope: str) -> list[str]:
    for step in spec.preprocessing:
        if step.column_scope == scope and _step_columns(step):
            return _step_columns(step)
    return []


def _candidate_columns(
    spec: ModelBuildReproductionSpec, candidate: ReproductionCandidate
) -> tuple[list[str], list[str]]:
    if candidate.numerical_columns or candidate.categorical_columns:
        return list(candidate.numerical_columns), list(candidate.categorical_columns)
    return _scope_columns(spec, "numerical"), _scope_columns(spec, "categorical")


def _estimator_params(params: dict[str, Any]) -> dict[str, Any]:
    """Constructor values as make_model passes them (digit class_weight keys -> class codes)."""

    out = dict(params)
    weights = out.get("class_weight")
    if isinstance(weights, dict):
        out["class_weight"] = {
            int(key) if str(key).isdigit() else str(key): float(value)
            for key, value in weights.items()
        }
    return out


# Holdout strategies whose training-row order the script reproduces exactly.
_ORDERED_HOLDOUTS = {"stratified_random", "random", "group_disjoint"}


def _partition_reproducible(spec: ModelBuildReproductionSpec) -> bool:
    split = spec.split_assignment
    if split is not None:
        return split.partitioned_by == "split_plan_assignment" or spec.holdout_plan.strategy in _ORDERED_HOLDOUTS
    # Plan-less runs: only the holdouts and splitters emitted as standalone sklearn.
    # (A plan-less TimeSeriesSplit is time-sorted by the engine first; not emitted.)
    return (
        spec.holdout_plan.strategy in _ORDERED_HOLDOUTS
        and spec.validation_plan.strategy in _SPLITTERS
        and spec.validation_plan.strategy != TIME_SERIES_SPLIT
    )


def _executable(spec: ModelBuildReproductionSpec) -> bool:
    """True when the assembled script can re-run CV and the refit standalone."""

    return bool(
        _task_kind(spec)
        and spec.task.target_column
        and spec.holdout_plan.strategy
        and spec.validation_plan.strategy
        and spec.validation_plan.actual_folds
        and _partition_reproducible(spec)
        and spec.preprocessing
        and all(_is_library_class(step.transformer_class) for step in spec.preprocessing)
        and spec.winner.implementation_class
        and _is_library_class(spec.winner.implementation_class)
        and any(c.fingerprint == spec.winner.fingerprint for c in spec.candidates)
    )


def standalone_cv_supported(spec: ModelBuildReproductionSpec) -> bool:
    return _executable(spec)


def run_identity_lines(spec: ModelBuildReproductionSpec) -> list[str]:
    """Split-plan and branch identity for the assembled document header."""

    lines: list[str] = []
    split = spec.split_assignment
    if split is not None:
        lines += [
            f"SPLIT_PLAN_ID = {_py_literal(str(split.split_plan_id))}",
            f"ASSIGNMENT_DIGEST = {_py_literal(split.assignment_digest)}",
            f"# Set {SPLIT_ASSIGNMENT_PATH_ENV} to a local copy of this SplitPlan's split_assignment",
            "# artifact (canonical CSV keyed by source row); its sha256 must equal ASSIGNMENT_DIGEST.",
        ]
    branch = spec.branch
    if branch is not None:
        lines += [
            "",
            "# Branch run: the effective configuration below is what this run recorded.",
            "# It is applied by the candidate definitions, metric plan and decision threshold;",
            "# nothing is re-derived from ancestor runs.",
            f"PARENT_EXPERIMENT_ID = {_py_literal(str(branch.parent_pipeline_run_id) if branch.parent_pipeline_run_id else None)}",
            f"CHANGE_SET_DIGEST = {_py_literal(branch.change_set_digest)}",
            f"CHANGE_SET = {_py_literal(branch.change_set)}",
            f"BRANCH_OVERRIDES = {_py_literal(branch.effective_overrides)}",
            f"BRANCH_OBJECTIVE = {_py_literal(branch.objective)}",
            f"APPLIED_CHANGES = {_py_literal(branch.applied_changes)}",
        ]
    return lines


def _render_ingestion(spec: ModelBuildReproductionSpec) -> tuple[str, list[str], bool]:
    dataset = spec.dataset
    loader, helpers = _load_fn(dataset.source_type)
    lines = [
        "import hashlib",
        "import os",
        "from pathlib import Path",
        "",
        "import numpy as np",
        "import pandas as pd",
        "",
        f"# Set {DATASET_PATH_ENV} (or replace the placeholder) to an authorized local copy",
        "# of this dataset version. Never substitute a persisted Dataset.location,",
        "# object-store key, or credential.",
        f"DATASET_PATH = Path(os.environ.get({_py_literal(DATASET_PATH_ENV)}, "
        f"{_py_literal(AUTHORIZED_DATASET_PATH_PLACEHOLDER)}))",
        f"CONTENT_DIGEST = {_py_literal(dataset.content_digest)}",
        f"SCHEMA_DIGEST = {_py_literal(dataset.schema_digest)}",
        f"SOURCE_TYPE = {_py_literal(dataset.source_type)}",
        f"DATASET_VERSION = {_py_literal(dataset.version)}",
        "",
        "if CONTENT_DIGEST and hashlib.sha256(DATASET_PATH.read_bytes()).hexdigest() != CONTENT_DIGEST:",
        '    raise RuntimeError("dataset bytes do not match the recorded CONTENT_DIGEST")',
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


def _target_coding_lines(spec: ModelBuildReproductionSpec) -> tuple[list[str], list[str]]:
    task = spec.task.task_type
    if task == "binary":
        return [
            "# The prepared dataset version stores the binary target coded 0/1 (coerce_binary_target).",
            "if not pd.api.types.is_numeric_dtype(frame[TARGET_COLUMN]):",
            '    raise RuntimeError("binary target is not 0/1 coded in this dataset version")',
            "frame = frame[frame[TARGET_COLUMN].isin([0, 1])].copy()",
            "frame[TARGET_COLUMN] = frame[TARGET_COLUMN].astype(int)",
        ], []
    if task == "multiclass" and spec.task.class_labels:
        return [
            "# Label code i is CLASS_LABELS[i]: the run coded the full label set before the split.",
            f"CLASS_LABELS = {_py_literal(spec.task.class_labels)}",
            "N_CLASSES = len(CLASS_LABELS)",
            "frame = frame.dropna(subset=[TARGET_COLUMN]).copy()",
            "frame[TARGET_COLUMN] = frame[TARGET_COLUMN].map({label: code for code, label in enumerate(CLASS_LABELS)})",
            "if frame[TARGET_COLUMN].isna().any():",
            '    raise RuntimeError("target has labels outside CLASS_LABELS")',
            "frame[TARGET_COLUMN] = frame[TARGET_COLUMN].astype(int)",
        ], []
    if task == "multiclass":
        helper = _helper("_encode_multiclass_target (app.engine.experiments.runner)")
        return [f"# {helper}"], [helper]
    if task == "regression":
        return [
            "frame[TARGET_COLUMN] = pd.to_numeric(frame[TARGET_COLUMN], errors='coerce')",
            "frame = frame.dropna(subset=[TARGET_COLUMN]).copy()",
        ], []
    return [], []


def _render_target(spec: ModelBuildReproductionSpec) -> tuple[str, list[str], bool]:
    if not spec.task.target_column and not spec.task.task_type:
        return _join(_comment_missing("target and task")), [], False
    coding, helpers = _target_coding_lines(spec) if spec.task.target_column else ([], [])
    lines = [
        f"TARGET_COLUMN = {_py_literal(spec.task.target_column)}",
        f"TASK_TYPE = {_py_literal(spec.task.task_type)}",
        f"SEED = {spec.task.seed}",
        "",
        *coding,
        "y = frame[TARGET_COLUMN]",
        "X = frame.drop(columns=[TARGET_COLUMN])",
    ]
    return _join(lines), helpers, True


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
            "# Datetime columns were converted to unix seconds row by row (stateless) before",
            "# this dataset version was written; the stored values are already numeric.",
            f"# {helper}",
            f"datetime_columns = {_py_literal(spec.datetime_columns)}",
        ]
    else:
        lines.append("# No datetime conversions were persisted for this run.")
    return _join(lines), helpers, True


def _assignment_partition_lines(spec: ModelBuildReproductionSpec) -> tuple[list[str], list[str]]:
    split = spec.split_assignment
    assert split is not None
    plan = spec.holdout_plan
    helpers: list[str] = []
    lines = [
        f"SOURCE_ROW_COLUMN = {_py_literal(split.source_row_column)}",
        f"SPLIT_PLAN_ID = {_py_literal(str(split.split_plan_id))}",
        f"SPLIT_PLAN_VERSION = {_py_literal(split.version)}",
        f"ASSIGNMENT_DIGEST = {_py_literal(split.assignment_digest)}",
        f"ASSIGNMENT_TRAIN_ROWS = {split.train_row_count}",
        f"ASSIGNMENT_HOLDOUT_ROWS = {split.holdout_row_count}",
        f"PARTITIONED_BY = {_py_literal(split.partitioned_by)}",
        f"# Set {SPLIT_ASSIGNMENT_PATH_ENV} (or replace the placeholder) to a local copy of the",
        "# SplitPlan's split_assignment artifact. Never substitute an object-store key.",
        f"SPLIT_ASSIGNMENT_PATH = Path(os.environ.get({_py_literal(SPLIT_ASSIGNMENT_PATH_ENV)}, "
        f"{_py_literal(AUTHORIZED_SPLIT_ASSIGNMENT_PATH_PLACEHOLDER)}))",
        "",
        SPLIT_ASSIGNMENT_HELPERS.rstrip(),
        "",
        "",
        "# The holdout and the outer folds come from the stored map, never re-derived.",
        "train_folds, holdout_rows = read_split_assignment(SPLIT_ASSIGNMENT_PATH.read_bytes(), ASSIGNMENT_DIGEST)",
        "if (len(train_folds), len(holdout_rows)) != (ASSIGNMENT_TRAIN_ROWS, ASSIGNMENT_HOLDOUT_ROWS):",
        '    raise RuntimeError("split assignment counts differ from the split plan")',
        "source_rows = frame[SOURCE_ROW_COLUMN].astype(int)",
        "if source_rows.duplicated().any() or set(source_rows) != set(train_folds) | holdout_rows:",
        '    raise RuntimeError("dataset rows differ from the split plan\'s row set")',
    ]
    if split.partitioned_by == "holdout_plan_resplit" and plan.strategy in {"stratified_random", "random"}:
        stratify = "frame[TARGET_COLUMN]" if plan.strategy == "stratified_random" else "None"
        lines += [
            "# This run re-split with its HoldoutPlan (same seed) and trained on that row order;",
            "# membership must equal the stored map, so the map stays authoritative.",
            "try:",
            "    train_frame, holdout_frame = train_test_split(",
            f"        frame, test_size=HOLDOUT_TEST_SIZE, random_state=HOLDOUT_RANDOM_STATE, stratify={stratify}",
            "    )",
            "except ValueError:",
            "    train_frame, holdout_frame = train_test_split(",
            "        frame, test_size=HOLDOUT_TEST_SIZE, random_state=HOLDOUT_RANDOM_STATE, stratify=None",
            "    )",
            "if set(holdout_frame[SOURCE_ROW_COLUMN].astype(int)) != holdout_rows:",
            '    raise RuntimeError("holdout rows differ from the stored split assignment")',
        ]
    else:
        if split.partitioned_by == "holdout_plan_resplit" and plan.strategy not in _ORDERED_HOLDOUTS:
            helper = _helper("split_train_test_holdout row order (app.engine.validation.splits)")
            helpers.append(helper)
            lines.append(
                "# Membership follows the map; the run's training row order came from "
                f"{_comment_text(plan.strategy)}, so CV is not re-run standalone for this run."
            )
            lines.append(f"# {helper}")
        lines += [
            "in_holdout = source_rows.isin(holdout_rows).to_numpy()",
            "train_frame = frame.loc[~in_holdout].copy()",
            "holdout_frame = frame.loc[in_holdout].copy()",
        ]
    return lines, helpers


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
        f"HOLDOUT_RANDOM_STATE = {int(plan.random_state or 42) if plan.random_state is not None else spec.task.seed}",
        "",
        "# Lock the final holdout before candidate search. Do not score it until winner lock.",
    ]
    if spec.split_assignment is not None:
        partition, partition_helpers = _assignment_partition_lines(spec)
        helpers.extend(partition_helpers)
        imports = ["import hashlib", "import os", "from pathlib import Path", "", "import numpy as np"]
        if any("train_test_split(" in line for line in partition):
            imports.append("from sklearn.model_selection import train_test_split")
        return _join([*imports, "", *lines, *partition]), helpers, True
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
            "    n_splits=1, test_size=HOLDOUT_TEST_SIZE, random_state=HOLDOUT_RANDOM_STATE",
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
            "# No SplitPlan was recorded for this run: re-derive the holdout from its HoldoutPlan.",
            "try:",
            "    train_frame, holdout_frame = train_test_split(",
            f"        frame, test_size=HOLDOUT_TEST_SIZE, random_state=HOLDOUT_RANDOM_STATE, stratify={stratify}",
            "    )",
            "except ValueError:",
            "    train_frame, holdout_frame = train_test_split(",
            "        frame, test_size=HOLDOUT_TEST_SIZE, random_state=HOLDOUT_RANDOM_STATE, stratify=None",
            "    )",
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
    constants = [
        f"VALIDATION_STRATEGY = {_py_literal(plan.strategy)}",
        f"REQUESTED_FOLDS = {_py_literal(plan.requested_folds)}",
        f"ACTUAL_FOLDS = {_py_literal(plan.actual_folds)}",
        f"VALIDATION_GROUP_COLUMN = {_py_literal(plan.group_column)}",
        f"VALIDATION_TIME_COLUMN = {_py_literal(plan.time_column)}",
    ]
    if spec.split_assignment is not None and spec.holdout_plan.strategy is not None:
        lines = [
            *constants,
            "# Outer folds are the stored SplitPlan folds of each training row (in this order).",
            "fold_of = np.asarray([train_folds[int(row)] for row in train_frame[SOURCE_ROW_COLUMN]], dtype=int)",
            "cv_folds = outer_folds(fold_of, VALIDATION_STRATEGY, ACTUAL_FOLDS)",
        ]
        return _join(lines), [], True
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
    if plan.strategy == TIME_SERIES_SPLIT:
        helper = _helper("iter_validation_folds time ordering (app.engine.modeling.validation_planner)")
        helpers.append(helper)
        constants.append(f"# {helper}")
    imported = _import_line(path)
    groups = ""
    if plan.strategy in {GROUP_KFOLD, STRATIFIED_GROUP_KFOLD}:
        groups = ", groups=train_frame[VALIDATION_GROUP_COLUMN]"
    lines = [
        imported or f"# {path}",
        "",
        *constants,
        "# No SplitPlan was recorded for this run: folds follow the persisted strategy",
        "# and experiment.seed via build_splitter.",
        f"cv = {_ctor(path, params)}",
    ]
    if spec.holdout_plan.strategy is not None:
        lines.append(
            "cv_folds = [(number, train_idx, val_idx) for number, (train_idx, val_idx) "
            f"in enumerate(cv.split(X_train, y_train{groups}), start=1)]"
        )
    return _join(lines), helpers, True


def _render_metric_plan(spec: ModelBuildReproductionSpec) -> tuple[str, list[str], bool]:
    metric = spec.metric_plan.primary_metric
    if not metric:
        return _join(_comment_missing("MetricPlan")), [], False
    lines = [f"PRIMARY_METRIC = {_py_literal(metric)}"]
    kind = _task_kind(spec)
    if kind is not None:
        imports, source = _METRIC_SOURCES[kind]
        lines += [
            f"LOWER_IS_BETTER = {_py_literal(sorted(LOWER_IS_BETTER))}",
            "",
            "import numpy as np",
            imports,
            "",
            "",
            source.rstrip(),
        ]
        return _join(lines), [], True
    helpers: list[str] = []
    mapped = _SKLEARN_METRICS.get(metric)
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
            f"# sequence={step.sequence} scope={_comment_text(step.column_scope)} "
            f"class={step.transformer_class} params={_comment_text(_py_literal(_constructor_params(step)))}"
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
        name = _comment_text(feature.name, 120)
        for transform in feature.transformations:
            if transform.transformation_type in _PASSTHROUGH:
                continue
            if transform.transformer_class and _is_library_class(transform.transformer_class):
                lines.append(
                    f"# {name}: {transform.transformer_class}"
                    f"({_comment_text(_call_kwargs(transform.parameters))})"
                )
                continue
            label = _DCLAB_TRANSFORMS.get(
                transform.transformation_type,
                transform.transformer_class or transform.transformation_type,
            )
            helper = _helper(label)
            if helper not in helpers:
                helpers.append(helper)
            lines.append(f"# {name}: {_comment_text(helper)}")
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
    arguments = {"numerical": "numerical_columns", "categorical": "categorical_columns"}
    body: list[str] = []
    for scope, columns, steps in _group_preprocessing(spec.preprocessing):
        inner: list[str] = []
        usable = True
        for index, step in enumerate(steps, start=1):
            if not _is_library_class(step.transformer_class):
                helper = _helper(step.transformer_class or step.transformer_type)
                helpers.append(helper)
                usable = False
                continue
            imported = _import_line(step.transformer_class)
            if imported and imported not in imports:
                imports.append(imported)
            params = _constructor_params(step)
            if step.transformer_class.startswith("sklearn.preprocessing.OneHotEncoder") and (
                "sparse_output" not in params and "sparse" not in params
            ):
                # The runner's encoder is dense (auto_prepare._one_hot_encoder).
                params["sparse_output"] = False
            alias = step.transformer_type or f"step_{index}"
            inner.append(f"({_py_literal(alias)}, {_ctor(step.transformer_class, params)})")
        fit_scope = steps[0].fit_scope if steps else "train"
        if not usable:
            body.append(f"    # {_comment_text(_helper(scope))} fit_scope={_comment_text(fit_scope)}")
            continue
        column_ref = arguments.get(scope, _py_literal(list(columns)))
        body += [
            f"    if {column_ref}:  # fit_scope={_comment_text(fit_scope)}",
            "        transformers.append((",
            f"            {_py_literal(scope)},",
            "            Pipeline(steps=[" + ", ".join(inner) + "]),",
            f"            {column_ref},",
            "        ))",
        ]
    lines = [
        *imports,
        "",
        "",
        "def make_preprocessor(numerical_columns, categorical_columns):",
        '    """Fitted inside each training fold only; never on holdout rows."""',
        "    transformers = []",
        *body,
        "    return ColumnTransformer(transformers=transformers)",
        "",
        "",
        f"NUMERICAL_COLUMNS = {_py_literal(_scope_columns(spec, 'numerical'))}",
        f"CATEGORICAL_COLUMNS = {_py_literal(_scope_columns(spec, 'categorical'))}",
        "preprocessor = make_preprocessor(NUMERICAL_COLUMNS, CATEGORICAL_COLUMNS)",
    ]
    return _join(lines), helpers, True


def _estimator_factory(spec: ModelBuildReproductionSpec) -> tuple[list[str], list[str]]:
    imports: list[str] = []
    branches: list[str] = []
    seen: set[str] = set()
    multiclass = spec.task.task_type == "multiclass"
    wrapped = False
    for candidate in spec.candidates:
        family, path = candidate.model_family, candidate.implementation_class or ""
        if family in seen or not _is_library_class(path):
            continue
        seen.add(family)
        imported = _import_line(path)
        if imported and imported not in imports:
            imports.append(imported)
        name = path.rsplit(".", 1)[-1]
        if family in _SCALED_FAMILIES:
            for line in ("from sklearn.pipeline import Pipeline", "from sklearn.preprocessing import StandardScaler"):
                if line not in imports:
                    imports.append(line)
            expr = (
                f"Pipeline([('scaler', StandardScaler()), "
                f"({_py_literal(_SCALED_FAMILIES[family])}, {name}(**params))])"
            )
        elif family == "xgboost" and multiclass:
            wrapped = True
            expr = f"ContiguousLabelClassifier({name}(**{{**params, 'eval_metric': 'mlogloss'}}))"
        elif family == "xgboost":
            expr = f"{name}(**{{'scale_pos_weight': 1.0, **params}})"
        else:
            expr = f"{name}(**params)"
        branches += [f"    if family == {_py_literal(family)}:", f"        return {expr}"]
    lines = [*imports, ""]
    if wrapped:
        lines += ["from sklearn.base import BaseEstimator, ClassifierMixin, clone", "", "", _CONTIGUOUS_LABEL_CLASSIFIER.rstrip(), ""]
    lines += [
        "",
        "def build_estimator(family, params):",
        '    """Estimator as app.engine.models.registry.make_model builds it for this family."""',
        *branches,
        "    raise ValueError(f'no standalone template for model family {family!r}')",
    ]
    return lines, []


def _render_candidates(spec: ModelBuildReproductionSpec) -> tuple[str, list[str], bool]:
    if not spec.candidates:
        return _join(_comment_missing("candidates")), [], False
    helpers: list[str] = []
    factory, _unused = _estimator_factory(spec)
    kind = _task_kind(spec)
    lines = [
        "# Instantiated from persisted implementation_class and applied hyperparameters.",
        "# Do not add unpersisted estimator wrappers.",
        *factory,
        "",
    ]
    if kind is not None:
        lines += ["", _PREDICT_SOURCES[kind].rstrip(), ""]
    lines += [
        "",
        "candidates = {}",
        "candidate_specs = {}",
    ]
    for candidate in spec.candidates:
        path = candidate.implementation_class or ""
        lines.append("")
        lines.append(f"# family={_comment_text(candidate.model_family)} fingerprint={candidate.fingerprint}")
        if candidate.library_version:
            lines.append(
                f"# library={_comment_text(candidate.implementation_library)} "
                f"version={_comment_text(candidate.library_version)}"
            )
        if not path or not _is_library_class(path):
            helper = _helper(path or candidate.model_family)
            helpers.append(helper)
            lines.append(f"# {_comment_text(helper)}")
            continue
        numerical, categorical = _candidate_columns(spec, candidate)
        record = {
            "candidate_id": str(candidate.id),
            "family": candidate.model_family,
            "status": (candidate.status or "").lower() or None,
            "numerical_columns": numerical,
            "categorical_columns": categorical,
            "hyperparameters": _estimator_params(candidate.hyperparameters),
            "fold_hyperparameters": {
                int(number): _estimator_params(params)
                for number, params in candidate.fold_hyperparameters.items()
                if str(number).isdigit()
            },
            "persisted_cv_score": candidate.cv_score,
            "persisted_fold_scores": {
                int(number): score
                for number, score in candidate.cv_fold_scores.items()
                if str(number).isdigit()
            },
        }
        fingerprint = _py_literal(candidate.fingerprint)
        lines += [
            f"candidate_specs[{fingerprint}] = {_py_literal(record)}",
            f"candidates[{fingerprint}] = build_estimator("
            f"candidate_specs[{fingerprint}]['family'], candidate_specs[{fingerprint}]['hyperparameters'])",
        ]
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
    scores = OrderedDict(
        (candidate.fingerprint, candidate.cv_score)
        for candidate in spec.candidates
        if candidate.cv_score is not None
    )
    if not _executable(spec):
        helper = _helper("classification_metrics/regression_metrics (app.engine.evaluation.metrics)")
        lines = [
            "# Cross-validation uses the training partition only.",
            "# The final holdout must not be used for candidate selection.",
            f"# {helper}",
            "# This run lacks evidence for a standalone CV re-run.",
            "",
            "# Persisted CV aggregate scores used for ranking (not holdout):",
            f"PERSISTED_CV_SCORES = {_py_literal(dict(scores))}",
        ]
        return _join(lines), [helper], True
    lines = [
        "from sklearn.pipeline import Pipeline",
        "",
        "# Cross-validation uses the training partition only.",
        "# The final holdout must not be used for candidate selection.",
        "# Each fold fits a fresh Pipeline (preprocessing + estimator) on its training rows;",
        "# nested-tuned candidates use the constructor values the run recorded for that fold.",
        "# Persisted CV aggregate scores used for ranking (not holdout):",
        f"PERSISTED_CV_SCORES = {_py_literal(dict(scores))}",
        "CV_TOLERANCE = 1e-6",
        "y_train_values = y_train.to_numpy()",
        "reproduced_fold_scores = {}",
        "reproduced_cv_scores = {}",
        "cv_differences = {}",
        "for fingerprint, record in candidate_specs.items():",
        "    if record['status'] != 'trained':",
        "        continue  # failed or skipped candidates have no CV evidence",
        "    columns = record['numerical_columns'] + record['categorical_columns']",
        "    fold_scores = {}",
        "    for fold_number, train_idx, val_idx in cv_folds:",
        "        params = record['fold_hyperparameters'].get(fold_number, record['hyperparameters'])",
        "        fold_pipeline = Pipeline([",
        "            ('prep', make_preprocessor(record['numerical_columns'], record['categorical_columns'])),",
        "            ('model', build_estimator(record['family'], params)),",
        "        ])",
        "        fold_pipeline.fit(X_train.iloc[train_idx][columns], y_train_values[train_idx])",
        "        fold_predictions = predict_scores(fold_pipeline, X_train.iloc[val_idx][columns])",
        "        fold_scores[fold_number] = score_metrics(y_train_values[val_idx], fold_predictions)[PRIMARY_METRIC]",
        "    reproduced_fold_scores[fingerprint] = fold_scores",
        "    reproduced_cv_scores[fingerprint] = float(np.mean(list(fold_scores.values())))",
        "    persisted = record['persisted_fold_scores']",
        "    gaps = [abs(fold_scores[number] - persisted[number]) for number in persisted if number in fold_scores]",
        "    if record['persisted_cv_score'] is not None:",
        "        gaps.append(abs(reproduced_cv_scores[fingerprint] - record['persisted_cv_score']))",
        "    cv_differences[fingerprint] = max(gaps) if gaps else None",
        "    if cv_differences[fingerprint] is not None and cv_differences[fingerprint] > CV_TOLERANCE:",
        "        print(f'CV {PRIMARY_METRIC} of {fingerprint} differs from the run by {cv_differences[fingerprint]:.3g}')",
    ]
    return _join(lines), [], True


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
    if _executable(spec):
        lines += [
            "",
            "# The same policy on the re-run scores: best learned candidate (baselines never win).",
            f"BASELINE_FAMILIES = {_py_literal(sorted(DUMMY_FAMILIES))}",
            "orientation = -1.0 if PRIMARY_METRIC in LOWER_IS_BETTER else 1.0",
            "eligible = {",
            "    fingerprint: orientation * score",
            "    for fingerprint, score in reproduced_cv_scores.items()",
            "    if candidate_specs[fingerprint]['family'] not in BASELINE_FAMILIES",
            "}",
            "REPRODUCED_WINNER_FINGERPRINT = max(eligible, key=eligible.get) if eligible else None",
            "CV_REPRODUCED = all(gap is None or gap <= CV_TOLERANCE for gap in cv_differences.values())",
            "if not CV_REPRODUCED or REPRODUCED_WINNER_FINGERPRINT != WINNER_FINGERPRINT:",
            '    raise RuntimeError("re-run CV does not reproduce the persisted scores or the locked winner")',
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
        lines.append(f"# reason: {_comment_text(spec.winner.reason)}")
    return _join(lines), [], True


def _render_refit(spec: ModelBuildReproductionSpec) -> tuple[str, list[str], bool]:
    if spec.winner.implementation_class is None:
        return _join(_comment_missing("final refit")), [], False
    if _executable(spec):
        winner = next(c for c in spec.candidates if c.fingerprint == spec.winner.fingerprint)
        numerical, categorical = _candidate_columns(spec, winner)
        lines = [
            "from sklearn.pipeline import Pipeline",
            _import_line(spec.winner.implementation_class) or "",
            "",
            "# Refit the locked winner on the full training partition only.",
            f"MODEL_VERSION_DIGEST = {_py_literal(spec.final_refit.content_digest)}",
            f"# library={_comment_text(spec.winner.implementation_library)} "
            f"version={_comment_text(spec.winner.library_version)}",
            f"WINNER_HYPERPARAMETERS = {_py_literal(_estimator_params(spec.winner.hyperparameters))}",
            f"WINNER_NUMERICAL_COLUMNS = {_py_literal(numerical)}",
            f"WINNER_CATEGORICAL_COLUMNS = {_py_literal(categorical)}",
            "WINNER_COLUMNS = WINNER_NUMERICAL_COLUMNS + WINNER_CATEGORICAL_COLUMNS",
            "estimator = build_estimator(WINNER_FAMILY, WINNER_HYPERPARAMETERS)",
            "pipeline = Pipeline([",
            "    ('prep', make_preprocessor(WINNER_NUMERICAL_COLUMNS, WINNER_CATEGORICAL_COLUMNS)),",
            "    ('model', estimator),",
            "])",
            "pipeline.fit(X_train[WINNER_COLUMNS], y_train.to_numpy())",
        ]
        return _join(lines), [], True
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
    if _executable(spec):
        threshold = spec.decision_threshold
        lines = [
            "# Evaluate the locked winner once. Do not change selection using these scores.",
            f"HOLDOUT_METRICS = {_py_literal(spec.final_holdout.metrics)}",
            "# Decision threshold locked on out-of-fold training rows before the holdout was scored.",
            f"DECISION_THRESHOLD = {_py_literal(threshold if threshold is not None else 0.5)}",
            "holdout_scores = predict_scores(pipeline, X_holdout[WINNER_COLUMNS])",
            "reproduced_holdout_metrics = score_metrics(y_holdout.to_numpy(), holdout_scores, threshold=DECISION_THRESHOLD)",
            "# Holdout labels and row values are not emitted.",
        ]
        return _join(lines), [], True
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
