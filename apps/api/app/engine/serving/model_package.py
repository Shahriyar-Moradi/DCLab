"""Model package format v1 (P7.1-A): the locked winner saved without pickle.

One zip per model version: ``manifest.json``, ``preprocessor.skops`` (the fitted
``prep`` ColumnTransformer, skops) and one estimator member — ``estimator.skops``
(scikit-learn families), ``estimator.xgb.json`` (XGBoost native JSON),
``estimator.lgbm.txt`` (LightGBM native text) or ``estimator.cbm`` (CatBoost
native binary). The manifest lists each member's SHA-256, the library versions
and their digest, the feature contract (ordered columns, roles, training dtypes,
allowed values, date formats, availability, fit scopes) and the winner's
cross-validation metrics in natural units (no final-holdout figures).

This module writes packages and reads their structure (bounded, no extraction to
disk, member digests checked). It never deserializes a model: loading is
``package_loader``, imported only by the worker.
"""

from __future__ import annotations

import hashlib
import io
import json
import math
import platform
import tempfile
import zipfile
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import numpy as np

PACKAGE_FORMAT = "dclab.model_package.v1"
CONTRACT_FORMAT = "dclab.model_package.feature_contract.v1"
PACKAGE_FILENAME = "model_package.zip"
MANIFEST_MEMBER = "manifest.json"
PREPROCESSOR_MEMBER = "preprocessor.skops"
ESTIMATOR_MEMBERS = {
    "skops": "estimator.skops",
    "xgboost_json": "estimator.xgb.json",
    "lightgbm_text": "estimator.lgbm.txt",
    "catboost_cbm": "estimator.cbm",
}
ALLOWED_MEMBERS = frozenset({MANIFEST_MEMBER, PREPROCESSOR_MEMBER, *ESTIMATOR_MEMBERS.values()})
# Boosting wrapper classes the loader may construct (by this exact name, never by import path).
BOOSTER_CLASSES = frozenset(
    {"XGBClassifier", "XGBRegressor", "LGBMClassifier", "LGBMRegressor", "CatBoostClassifier", "CatBoostRegressor"}
)
# The only types a skops member may need beyond skops' own defaults (scikit-learn
# estimators, builtins, numpy arrays). Writer and loader share this list; the loader
# never reads a trust list from the package itself.
SKOPS_TRUSTED_TYPES = ("numpy.dtype", "sklearn.tree._tree.Tree")
# Size bounds (bytes), from measured packages: default boosters, linear and dummy models stay
# under 1 MiB; boosters at the largest allowed overrides on 50k rows: XGBoost 2,000 rounds x
# depth 8 = 22 MiB, LightGBM 2,000 rounds x 5 classes = 33 MiB, CatBoost 2,000 = 6 MiB (native
# cap 256 MiB, ~8x). The largest are fully grown forests (skops): about 92 bytes per
# (training row x tree) at worst (one leaf per row), e.g. ExtraTrees regression on 100k rows
# (80 trees) = 315 MiB packed / 1.07 GiB unpacked. So the 1.5 GiB estimator cap is reached at
# ~218k pool rows with the default 80 trees, ~58k with 300 (tuning's upper bound) and ~8.7k
# with 2,000 (a branch override, which the branch service now refuses up front). A forest above
# the cap is refused from its node counts before it is serialized, and always before the final
# test; it is never loaded partially. (Follow-up: drop such candidates before selection.)
_MIB = 1 << 20
PACKAGE_MAX_BYTES = 512 * _MIB  # the stored zip (also checked on the artifact row before reading)
MANIFEST_MAX_BYTES = 16 * _MIB
PREPROCESSOR_MAX_BYTES = 64 * _MIB
ESTIMATOR_MAX_BYTES = 1536 * _MIB  # skops estimators (forests)
NATIVE_ESTIMATOR_MAX_BYTES = 256 * _MIB  # XGBoost JSON, LightGBM text, CatBoost cbm
MAX_INFLATION = 16  # unpacked bytes per packed byte, on top of INFLATION_FLOOR_BYTES
INFLATION_FLOOR_BYTES = 64 * _MIB
ALLOWED_VALUES_MAX = 1000
FOREST_BYTES_PER_ROW_TREE = 92  # worst case, measured (see above)
_TREE_NODE_BYTES = 64  # scikit-learn's node record
# Run failures shown to the user word for word (audience_projection.PUBLIC_FAILURE_REASONS).
PACKAGE_TOO_LARGE = "The trained model is too large to save safely; try fewer rows or a boosting model."
PACKAGE_NOT_SAVED = "The trained model could not be saved in the safe model format."
PACKAGE_UNSAFE_CATEGORY = (
    "A categorical column holds values a saved model cannot store, such as dates or decimals read from "
    "Parquet; save such columns as text or numbers and upload again."
)
PUBLIC_PACKAGE_FAILURES = frozenset({PACKAGE_TOO_LARGE, PACKAGE_NOT_SAVED, PACKAGE_UNSAFE_CATEGORY})
_EPOCH_STEP, _EPOCH_TRANSFORMATION = "datetime_to_unix_seconds", "datetime_to_epoch"
_ZIP_TIME = (1980, 1, 1, 0, 0, 0)  # fixed member timestamps: same members -> same bytes


class ModelPackageError(ValueError):
    """The package cannot be written or fails a structural/integrity check (internal text)."""


class ModelPackageDigestError(ModelPackageError):
    """A member's bytes differ from the SHA-256 the manifest records for it."""


@dataclass(frozen=True)
class PackageContents:
    manifest: dict[str, Any]
    manifest_bytes: bytes
    members: dict[str, bytes]


def sha256_hex(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def failure_chain(exc: BaseException | None, depth: int = 6) -> str:
    """``Type: message <- Type: message ...`` along ``__cause__``/``__context__``, for SERVER
    logs, so a refusal (generic text for the user) stays diagnosable."""

    parts = []
    while exc is not None and len(parts) < depth:
        parts.append(f"{type(exc).__name__}: {str(exc)[:300]}")
        exc = exc.__cause__ or exc.__context__
    return " <- ".join(parts)


def member_limit(name: str) -> int:
    if name == MANIFEST_MEMBER:
        return MANIFEST_MAX_BYTES
    if name == PREPROCESSOR_MEMBER:
        return PREPROCESSOR_MAX_BYTES
    return ESTIMATOR_MAX_BYTES if name == ESTIMATOR_MEMBERS["skops"] else NATIVE_ESTIMATOR_MAX_BYTES


def check_unpacked(infos: list[zipfile.ZipInfo], packed_bytes: int, limit: int) -> None:
    """Bound what a zip (the package, or a skops member) may inflate to, from its declared
    sizes (``zipfile`` never reads past them): the limit, and a bounded inflation ratio."""

    unpacked = sum(info.file_size for info in infos)
    if unpacked > limit or unpacked > MAX_INFLATION * packed_bytes + INFLATION_FLOOR_BYTES:
        raise ModelPackageError("package larger than the limit when unpacked")
    if any(info.flag_bits & 0x1 or info.compress_type not in (zipfile.ZIP_STORED, zipfile.ZIP_DEFLATED) for info in infos):
        raise ModelPackageError("unsupported member encoding")


# Values a categorical input may hold so its encoder saves with skops (no other types).
SAFE_CATEGORY_TYPES = (str, bytes, bool, int, float, np.str_, np.bool_, np.integer, np.floating)


def unsafe_category_types(values: Any) -> list[str]:
    """Type names of non-missing values the package format cannot store (e.g. Decimal,
    datetime.date, Timestamp objects read from Parquet)."""

    found = {type(value).__name__ for value in values if value is not None and not isinstance(value, SAFE_CATEGORY_TYPES)}
    return sorted(found)


def _plain(value: Any) -> Any:
    """JSON-safe scalar (numpy -> Python, non-finite -> None)."""

    if isinstance(value, np.generic):
        value = value.item()
    if isinstance(value, float) and not math.isfinite(value):
        return None
    return value if value is None or isinstance(value, (str, int, float, bool)) else str(value)


def _digest_json(payload: Any) -> str:
    return sha256_hex(json.dumps(payload, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8"))


# --- write (training) ---------------------------------------------------------------


def _skops_dumps(obj: Any) -> bytes:
    import skops.io as sio

    blob = sio.dumps(obj)
    extra = sorted(set(sio.get_untrusted_types(data=blob)) - set(SKOPS_TRUSTED_TYPES))
    if extra:
        raise ModelPackageError(f"types outside the package allowlist: {extra}")
    return blob


def _saved_to_file(model: Any, suffix: str, **kwargs: Any) -> bytes:
    with tempfile.TemporaryDirectory(prefix="dclab-package-") as tmp:  # private 0700 dir, removed after
        path = Path(tmp) / f"estimator{suffix}"
        model.save_model(str(path), **kwargs)
        return path.read_bytes()


def _estimator_member(model: Any) -> tuple[str, bytes, dict[str, Any]]:
    """(member name, bytes, manifest ``model`` facts) for the fitted ``model`` step."""

    from app.engine.models.registry import ContiguousLabelClassifier

    class_codes = None
    inner = model
    if isinstance(model, ContiguousLabelClassifier):
        inner, class_codes = model.estimator_, [_plain(code) for code in model.classes_]
    library = type(inner).__module__.split(".")[0]
    if isinstance(model, ContiguousLabelClassifier) and library != "xgboost":
        raise ModelPackageError("only XGBoost is wrapped for contiguous labels")
    name = type(inner).__name__
    if library == "sklearn":
        if _estimated_tree_bytes(model) > ESTIMATOR_MAX_BYTES:  # refuse before serializing it
            raise ModelPackageError(PACKAGE_TOO_LARGE)
        return ESTIMATOR_MEMBERS["skops"], _skops_dumps(model), {"estimator_format": "skops"}
    if name not in BOOSTER_CLASSES:
        raise ModelPackageError(f"no safe package format for {type(inner).__module__}.{name}")
    facts: dict[str, Any] = {"estimator_class": name, "contiguous_label_codes": class_codes}
    if library == "xgboost":
        data, kind = _saved_to_file(inner, ".json"), "xgboost_json"
    elif library == "lightgbm":
        data, kind = inner.booster_.model_to_string().encode("utf-8"), "lightgbm_text"
        if name == "LGBMClassifier":
            facts["class_codes"] = [_plain(code) for code in inner.classes_]
    else:
        data, kind = _saved_to_file(inner, ".cbm", format="cbm"), "catboost_cbm"
        if name == "CatBoostClassifier":
            facts["class_codes"] = [_plain(code) for code in inner.classes_]
    return ESTIMATOR_MEMBERS[kind], data, {"estimator_format": kind, **facts}


def _estimated_tree_bytes(model: Any) -> int:
    """Serialized size of a forest / boosting ensemble's trees from their node counts."""

    trees = getattr(model, "estimators_", None)
    if trees is None:
        return 0
    total = 0
    for estimator in np.ravel(np.asarray(trees, dtype=object)):
        tree = getattr(estimator, "tree_", None)
        if tree is not None:
            total += int(tree.node_count) * (_TREE_NODE_BYTES + 8 * int(tree.n_outputs) * int(tree.max_n_classes))
    return total


def library_versions(estimator_format: str) -> dict[str, str | None]:
    from app.engine.models.registry import _package_version

    names = ["numpy", "scipy", "scikit-learn", "skops"]
    names += {"xgboost_json": ["xgboost"], "lightgbm_text": ["lightgbm"], "catboost_cbm": ["catboost"]}.get(
        estimator_format, []
    )
    return {"python": platform.python_version(), **{name: _package_version(name) for name in names}}


def _date_plan(feature_actions: Any, features: list[str]) -> dict[str, tuple[bool, str | None]]:
    from app.engine.serving.batch_scoring import _date_plan as plan

    epoch = [
        action
        for action in (feature_actions if isinstance(feature_actions, list) else [])
        if isinstance(action, dict)
        and (action.get("step") == _EPOCH_STEP or action.get("transformation") == _EPOCH_TRANSFORMATION)
    ]
    return plan(tuple(epoch), set(features))


def _transforms(role: str, is_date: bool) -> list[dict[str, str]]:
    from app.engine.features.contract import spec_for_transform

    names = (["datetime_extract"] if is_date else []) + [
        "impute_median" if role == "numeric" else "impute_most_frequent"
    ]
    return [{"transform": name, "fit_scope": spec_for_transform(name, "column").fit_scope} for name in names]


def feature_contract(
    pipeline: Any,
    *,
    training_dtypes: Mapping[str, str],
    feature_actions: Any,
    target: str | None,
) -> dict[str, Any]:
    """The pipeline's input contract as scoring replays it (same derivation as
    ``batch_scoring.scoring_spec``): every input column is required; extra columns
    and the target are ignored and listed; values are converted as in training."""

    from app.engine.serving.batch_scoring import ScoringError, _pipeline_contract

    # Column names and allowed values are user data stored raw (JSON-escaped only): any
    # screen that shows them must render them as plain text, never as markup.
    try:
        features, numeric, _categorical, categories = _pipeline_contract(pipeline)
    except ScoringError as exc:
        raise ModelPackageError("the pipeline has no replayable input contract") from exc
    dates = _date_plan(feature_actions, features)
    columns = []
    for name in features:
        role = "numeric" if name in numeric else "categorical"
        entry: dict[str, Any] = {
            "name": name,
            "role": role,
            "training_dtype": str(training_dtypes.get(name) or "unknown"),
            "required": True,
            "allowed_values": None,
            "allowed_values_count": None,
            "availability": "unknown",
            "transforms": _transforms(role, name in dates),
        }
        if name in categories:
            values = [_plain(value) for value in categories[name]]
            entry["allowed_values"], entry["allowed_values_count"] = values[:ALLOWED_VALUES_MAX], len(values)
        if name in dates:
            entry["date_format_recorded"], entry["date_format"] = dates[name]
        columns.append(entry)
    contract = {
        "format": CONTRACT_FORMAT,
        "columns": columns,
        "target_column": target,
        "unknown_columns": "ignored_and_listed",
        "target_column_policy": "ignored_if_present",
        "unseen_categories": "encoded_as_unknown",
        "value_conversion": "replayed_from_training",
    }
    contract["digest"] = _digest_json(contract)
    return contract


def _assemble(members: Mapping[str, bytes], manifest: dict[str, Any]) -> tuple[bytes, bytes]:
    manifest_bytes = (json.dumps(manifest, sort_keys=True, indent=2, allow_nan=False) + "\n").encode("utf-8")
    for name, data in [(MANIFEST_MEMBER, manifest_bytes), *members.items()]:
        if len(data) > member_limit(name):
            raise ModelPackageError(PACKAGE_TOO_LARGE)
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        for name, data in [(MANIFEST_MEMBER, manifest_bytes), *sorted(members.items())]:
            info = zipfile.ZipInfo(name, date_time=_ZIP_TIME)
            info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = 0o600 << 16
            archive.writestr(info, data)
    if len(buffer.getvalue()) > PACKAGE_MAX_BYTES:
        raise ModelPackageError(PACKAGE_TOO_LARGE)
    return buffer.getvalue(), manifest_bytes


def build_package(
    pipeline: Any,
    *,
    family: str,
    task_type: str,
    candidate_id: str,
    training_dtypes: Mapping[str, str],
    feature_actions: Any,
    target: str | None,
    cv: Mapping[str, Any],
    decision_threshold: float | None = None,
) -> bytes:
    """Package the fitted winner ``Pipeline([("prep", ...), ("model", ...)])``.
    Raises ``ModelPackageError`` when a part has no safe format (fail closed).
    ``decision_threshold``: the binary threshold locked from CV evidence (scoring labels with it).
    ``created_at`` is part of the manifest, so identical re-runs produce different digests."""

    steps = getattr(pipeline, "named_steps", None)
    if not isinstance(steps, Mapping) or list(steps) != ["prep", "model"]:
        raise ModelPackageError("expected a fitted Pipeline with steps prep and model")
    contract = feature_contract(
        pipeline, training_dtypes=training_dtypes, feature_actions=feature_actions, target=target
    )
    estimator_name, estimator_bytes, model_facts = _estimator_member(steps["model"])
    members = {PREPROCESSOR_MEMBER: _skops_dumps(steps["prep"]), estimator_name: estimator_bytes}
    versions = library_versions(model_facts["estimator_format"])
    manifest = {
        "format": PACKAGE_FORMAT,
        "created_at": datetime.now(UTC).isoformat(),
        "training_run_id": None,
        "pipeline_run_id": None,
        "feature_set_version_id": None,
        "candidate_id": candidate_id,
        "features": [column["name"] for column in contract["columns"]],
        "task_type": task_type,
        "decision_threshold": _plain(decision_threshold) if task_type == "binary" else None,
        "model": {"family": family, **model_facts},
        "members": {name: {"sha256": sha256_hex(data), "size_bytes": len(data)} for name, data in members.items()},
        "library_versions": versions,
        "dependency_lock_digest": _digest_json(versions),
        "feature_contract": contract,
        "cv_metrics": {
            "basis": "cross_validation_folds_of_the_training_partition",
            "metric": cv.get("metric"),
            "strategy": cv.get("strategy"),
            "folds": cv.get("folds"),
            "mean": {str(k): _plain(v) for k, v in dict(cv.get("mean") or {}).items()},
            "std": {str(k): _plain(v) for k, v in dict(cv.get("std") or {}).items()},
        },
    }
    return _assemble(members, manifest)[0]


# --- read (persistence, worker) ------------------------------------------------------


def read_package(payload: bytes) -> PackageContents:
    """Structural and integrity checks only (no model is deserialized): bounded sizes,
    a fixed member allowlist (no paths, no duplicates, no encryption), the manifest
    format, and every member's SHA-256 against the manifest."""

    if not isinstance(payload, (bytes, bytearray)) or len(payload) > PACKAGE_MAX_BYTES:
        raise ModelPackageError("package missing or larger than the limit")
    try:
        archive = zipfile.ZipFile(io.BytesIO(payload))
    except zipfile.BadZipFile as exc:
        raise ModelPackageError("not a model package") from exc
    with archive:
        infos = archive.infolist()
        names = [info.filename for info in infos]
        if len(names) != len(set(names)) or not set(names) <= ALLOWED_MEMBERS or MANIFEST_MEMBER not in names:
            raise ModelPackageError("unexpected package members")
        if any(info.file_size > member_limit(info.filename) for info in infos):
            raise ModelPackageError("package member larger than the limit")
        check_unpacked(infos, len(payload), MANIFEST_MAX_BYTES + PREPROCESSOR_MAX_BYTES + ESTIMATOR_MAX_BYTES)
        try:
            data = {name: archive.read(name) for name in names}
        except (zipfile.BadZipFile, OSError, RuntimeError) as exc:  # CRC mismatch, truncated stream
            raise ModelPackageError("damaged package member") from exc
    manifest_bytes = data.pop(MANIFEST_MEMBER)
    try:
        manifest = json.loads(manifest_bytes.decode("utf-8"))
    except (UnicodeDecodeError, ValueError) as exc:
        raise ModelPackageError("manifest is not JSON") from exc
    if not isinstance(manifest, dict) or manifest.get("format") != PACKAGE_FORMAT:
        raise ModelPackageError("unknown package format")
    listed = manifest.get("members")
    model = manifest.get("model")
    if not isinstance(listed, dict) or set(listed) != set(data) or not isinstance(model, dict):
        raise ModelPackageError("manifest does not list the package members")
    for name, payload_bytes in data.items():
        entry = listed[name]
        if not isinstance(entry, dict) or entry.get("sha256") != sha256_hex(payload_bytes):
            raise ModelPackageDigestError(f"member {name} does not match its recorded digest")
    if PREPROCESSOR_MEMBER not in data or ESTIMATOR_MEMBERS.get(str(model.get("estimator_format"))) not in data:
        raise ModelPackageError("package is missing its preprocessor or estimator")
    if len(data) != 2:
        raise ModelPackageError("unexpected package members")
    return PackageContents(manifest=manifest, manifest_bytes=manifest_bytes, members=data)


def finalize_package(
    payload: bytes,
    *,
    training_run_id: str,
    feature_set_version_id: str | None,
    availability: Mapping[str, str],
) -> tuple[bytes, bytes]:
    """(package, manifest bytes) with the run's lineage and each column's prediction-time
    availability stamped into the manifest. Members are copied byte for byte."""

    contents = read_package(payload)
    manifest = json.loads(contents.manifest_bytes.decode("utf-8"))
    manifest["training_run_id"] = manifest["pipeline_run_id"] = training_run_id
    manifest["feature_set_version_id"] = feature_set_version_id
    contract = manifest["feature_contract"]
    for column in contract["columns"]:
        column["availability"] = str(availability.get(column["name"]) or "unknown")
    contract.pop("digest", None)
    contract["digest"] = _digest_json(contract)
    return _assemble(contents.members, manifest)


def check_manifest_contract(manifest: Mapping[str, Any], spec: Any) -> None:
    """The scoring replay plan (``ScoringSpec`` from the loaded pipeline and the locked
    evidence) must be exactly the contract recorded at training. Raises on any drift."""

    columns = (manifest.get("feature_contract") or {}).get("columns") or []
    by_name = {str(column.get("name")): column for column in columns if isinstance(column, dict)}
    if [str(column.get("name")) for column in columns] != list(spec.feature_columns):
        raise ModelPackageError("feature columns differ from the manifest")
    roles = {name: column.get("role") for name, column in by_name.items()}
    if [name for name in spec.numeric_columns if roles.get(name) != "numeric"] or [
        name for name in spec.categorical_columns if roles.get(name) != "categorical"
    ]:
        raise ModelPackageError("column roles differ from the manifest")
    for name in spec.categorical_columns:
        values = [_plain(value) for value in spec.categories.get(name, ())]
        recorded = by_name[name]
        if recorded.get("allowed_values_count") != len(values) or recorded.get("allowed_values") != values[
            :ALLOWED_VALUES_MAX
        ]:
            raise ModelPackageError(f"allowed values of {name} differ from the manifest")
    planned = {name: (True, fmt) for name, fmt in spec.date_formats.items()}
    planned.update({name: (False, None) for name in spec.legacy_date_columns})
    recorded_dates = {
        name: (bool(column.get("date_format_recorded")), column.get("date_format"))
        for name, column in by_name.items()
        if "date_format_recorded" in column
    }
    if planned != recorded_dates:
        raise ModelPackageError("date formats differ from the manifest")


def saved_format(artifact_metadata: Mapping[str, Any] | None) -> str:
    """``package_v1`` for a model artifact written as a package, else ``legacy_quarantined``
    (joblib/pickle files saved before P7.1-A carry no package format)."""

    metadata = artifact_metadata if isinstance(artifact_metadata, Mapping) else {}
    return "package_v1" if metadata.get("package_format") == PACKAGE_FORMAT else "legacy_quarantined"
