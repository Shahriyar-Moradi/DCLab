"""Canonical scientific candidate configuration fingerprint.

Scheme ``dclab.candidate_config.v2`` hashes applied scientific configuration
only. Database row IDs, timestamps, and PipelineRun IDs are excluded so
equivalent configurations fingerprint identically.

Historical ``experiment_candidates.fingerprint`` values are not rewritten.
They remain unique under ``(experiment_id, fingerprint)`` until a candidate
is regenerated under this scheme.
"""

from __future__ import annotations

import hashlib
import json
import re
from typing import Any, Sequence

from app.engine.models.registry import applied_hyperparameters, implementation_for_family
from app.engine.types import TaskSpec

CANDIDATE_CONFIG_FINGERPRINT_SCHEME = "dclab.candidate_config.v2"
FINGERPRINT_HEX_LENGTH = 40

_UUID_VALUE = re.compile(
    r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$"
)
_IDENTITY_KEYS = frozenset(
    {
        "id",
        "pipeline_run_id",
        "experiment_id",
        "workspace_id",
        "project_id",
        "dataset_id",
        "artifact_id",
        "ingestion_run_id",
        "candidate_id",
        "feature_set_version_id",
        "model_version_id",
        "created_at",
        "updated_at",
        "started_at",
        "completed_at",
        "ended_at",
        "locked_at",
        "timestamp",
    }
)


def candidate_fingerprint(payload: dict[str, Any]) -> str:
    encoded = json.dumps(payload, sort_keys=True, default=str, separators=(",", ":"))
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()[:FINGERPRINT_HEX_LENGTH]


def _is_identity_field(key: str, value: Any) -> bool:
    name = str(key)
    if name in _IDENTITY_KEYS:
        return True
    if name.endswith("_id") and isinstance(value, str) and _UUID_VALUE.match(value.strip()):
        return True
    return False


def _strip_identity(value: Any) -> Any:
    if isinstance(value, dict):
        return {
            str(key): _strip_identity(item)
            for key, item in value.items()
            if not _is_identity_field(str(key), item)
        }
    if isinstance(value, (list, tuple)):
        return [_strip_identity(item) for item in value]
    return value


def _as_plan_dict(value: Any) -> dict[str, Any]:
    if value is None:
        return {}
    if isinstance(value, dict):
        return dict(value)
    to_dict = getattr(value, "to_dict", None)
    if callable(to_dict):
        payload = to_dict()
        return dict(payload) if isinstance(payload, dict) else {}
    return {}


def scientific_plan_digest(plan: Any) -> str | None:
    payload = _strip_identity(_as_plan_dict(plan))
    if not payload:
        return None
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def _nested_plan(development: dict[str, Any], key: str, override: Any) -> Any:
    if override is not None:
        return override
    nested = development.get(key)
    return nested if isinstance(nested, dict) or nested is not None else None


def _canonical_preprocessing(preprocessing: dict[str, Any] | str | None) -> dict[str, Any] | str:
    if preprocessing is None:
        return "default"
    if isinstance(preprocessing, str):
        return preprocessing.strip() or "default"
    if isinstance(preprocessing, dict):
        cleaned = _strip_identity(preprocessing)
        return cleaned if cleaned else "default"
    return "default"


def scientific_candidate_config_payload(
    *,
    features: Sequence[str],
    family: str,
    seed: int,
    task: TaskSpec | None = None,
    task_type: str | None = None,
    target: str | None = None,
    evaluation_metric: str | None = None,
    dataset_version: str | None = "v1",
    dataset_content_digest: str | None = None,
    hyperparameters: dict[str, Any] | None = None,
    preprocessing: dict[str, Any] | str | None = None,
    holdout_plan: Any = None,
    development_plan: Any = None,
    validation_plan: Any = None,
    metric_plan: Any = None,
    feature_set_version_digest: str | None = None,
) -> dict[str, Any]:
    resolved_task_type = task_type or (task.task_type if task is not None else None)
    resolved_target = target if target is not None else (task.target if task is not None else None)
    resolved_metric = evaluation_metric
    if resolved_metric is None and task is not None:
        resolved_metric = task.evaluation_metric
    development = _as_plan_dict(development_plan)
    library, implementation_class, library_version = implementation_for_family(family)
    applied = applied_hyperparameters(family, seed=int(seed), hyperparameters=hyperparameters)
    return {
        "scheme": CANDIDATE_CONFIG_FINGERPRINT_SCHEME,
        "dataset_version": dataset_version or None,
        "dataset_content_digest": dataset_content_digest or None,
        "task_type": resolved_task_type,
        "target": resolved_target,
        "evaluation_metric": resolved_metric,
        "features": sorted(str(name) for name in features),
        "feature_set_version_digest": feature_set_version_digest or None,
        "model_family": family,
        "implementation": {
            "library": library,
            "class": implementation_class,
            "library_version": library_version,
        },
        "hyperparameters": _strip_identity(applied),
        "preprocessing": _canonical_preprocessing(preprocessing),
        "seed": int(seed),
        "holdout_plan_digest": scientific_plan_digest(holdout_plan),
        "validation_plan_digest": scientific_plan_digest(
            _nested_plan(development, "validation_plan", validation_plan)
        ),
        "metric_plan_digest": scientific_plan_digest(
            _nested_plan(development, "metric_plan", metric_plan)
        ),
        "model_development_plan_digest": scientific_plan_digest(development_plan),
    }


def scientific_candidate_fingerprint(
    *,
    features: Sequence[str],
    family: str,
    seed: int,
    task: TaskSpec | None = None,
    task_type: str | None = None,
    target: str | None = None,
    evaluation_metric: str | None = None,
    dataset_version: str | None = "v1",
    dataset_content_digest: str | None = None,
    hyperparameters: dict[str, Any] | None = None,
    preprocessing: dict[str, Any] | str | None = None,
    holdout_plan: Any = None,
    development_plan: Any = None,
    validation_plan: Any = None,
    metric_plan: Any = None,
    feature_set_version_digest: str | None = None,
) -> str:
    payload = scientific_candidate_config_payload(
        features=features,
        family=family,
        seed=seed,
        task=task,
        task_type=task_type,
        target=target,
        evaluation_metric=evaluation_metric,
        dataset_version=dataset_version,
        dataset_content_digest=dataset_content_digest,
        hyperparameters=hyperparameters,
        preprocessing=preprocessing,
        holdout_plan=holdout_plan,
        development_plan=development_plan,
        validation_plan=validation_plan,
        metric_plan=metric_plan,
        feature_set_version_digest=feature_set_version_digest,
    )
    return candidate_fingerprint(_strip_identity(payload))
