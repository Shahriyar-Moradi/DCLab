"""Load a stored model for batch scoring (P7.1-A). WORKER ONLY.

The API process never imports this module (asserted in ``test_model_package``):
``batch_prediction_service`` imports it inside the worker job body, and ``load_model``
refuses outside the worker process unless the environment is explicitly development.
A package is loaded only after its bytes matched the model artifact's digest and its
manifest matched the model version's manifest digest. scikit-learn parts load with
skops and the code-owned ``SKOPS_TRUSTED_TYPES`` (never ``trusted=True``, never a list
read from the package); boosters load from their native formats into wrapper classes
chosen from a fixed name map. ``package_checks`` refuses inconsistent members (tree
indices, encoder state, booster formats) before any row is predicted. Native boosters are
rebuilt and probed in short-lived child processes (memory, file-size and time limits; their
own session), which isolate crashes and memory use but not secrets: a child shares the
worker's uid, filesystem and network, so its environment is reduced to what the libraries
need. Children write only inside a private temporary directory that is always removed.

Deployment check (P7.1-A): the child limits were validated on macOS only. The API/worker
image must run ``test_model_package.py`` -- including its Linux-only memory-limit test --
before any deployment; MODEL_PROBE_MEMORY_BYTES / MODEL_PROBE_TIMEOUT_SECONDS are the
ops-tunable safety valve (an address-space limit can break some library imports).

Model versions saved before the package format (joblib/pickle) are quarantined:
``load_legacy_pickle`` refuses them unless ``LEGACY_PICKLE_MODELS_ENABLED`` allows
it (allowed only when set true, or when DCLAB_ENV is explicitly a development value).
"""

from __future__ import annotations

import io
import json
import logging
import zipfile
from dataclasses import dataclass
from typing import Any

import numpy as np
from sklearn.base import BaseEstimator
from sklearn.pipeline import Pipeline

from app.engine.serving import package_checks as checks
from app.engine.serving.batch_scoring import ScoringError
from app.engine.serving.codes import SCORING_FAILED, UNSUPPORTED_MODEL
from app.engine.serving.model_package import (
    ESTIMATOR_MEMBERS,
    NATIVE_ESTIMATOR_MAX_BYTES,
    PACKAGE_FORMAT,
    PACKAGE_MAX_BYTES,
    PREPROCESSOR_MEMBER,
    SKOPS_TRUSTED_TYPES,
    check_unpacked,
    member_limit,
    ModelPackageDigestError,
    ModelPackageError,
    read_package,
    sha256_hex,
)

logger = logging.getLogger(__name__)

MODEL_MESSAGE = "this model version's stored pipeline cannot be used for batch scoring"
DIGEST_MESSAGE = "the stored model does not match its recorded digest"
LEGACY_MESSAGE = (
    "this model version was saved in an older format that this server does not load; "
    "train it again to score new data"
)
WORKER_ONLY_MESSAGE = "models are loaded only by the scoring worker on this server"
_SKOPS_MAX_MEMBERS = 10_000


class LightGBMBoosterModel(BaseEstimator):
    """A LightGBM native booster with the scikit-learn wrapper's predict rules
    (``LGBMClassifier``/``LGBMRegressor``). Built from the package, never unpickled."""

    def __init__(self, booster: Any = None, classes: Any = None):
        self.booster = booster
        self.classes = classes

    def fit(self, X: Any, y: Any = None) -> LightGBMBoosterModel:
        raise NotImplementedError("a packaged model is never refit")

    def __sklearn_is_fitted__(self) -> bool:
        return self.booster is not None

    @property
    def classes_(self) -> np.ndarray:
        if self.classes is None:
            raise AttributeError("classes_")
        return np.asarray(self.classes)

    def predict_proba(self, X: Any) -> np.ndarray:
        result = self.booster.predict(X)
        return np.vstack((1.0 - result, result)).transpose() if result.ndim == 1 else result

    def predict(self, X: Any) -> np.ndarray:
        if self.classes is None:
            return self.booster.predict(X)
        return self.classes_[np.argmax(self.predict_proba(X), axis=1)]


@dataclass(frozen=True)
class LoadedModel:
    pipeline: Any
    manifest: dict[str, Any] | None
    saved_format: str


def _skops_load(blob: bytes, member: str) -> Any:
    import skops.io as sio

    # skops reads its zip in memory; bound what it may inflate before handing it over.
    try:
        with zipfile.ZipFile(io.BytesIO(blob)) as inner:
            infos = inner.infolist()
    except zipfile.BadZipFile as exc:
        raise ModelPackageError("skops member is not a skops file") from exc
    if len(infos) > _SKOPS_MAX_MEMBERS:
        raise ModelPackageError("skops member has too many entries")
    check_unpacked(infos, len(blob), member_limit(member))
    return sio.loads(blob, trusted=list(SKOPS_TRUSTED_TYPES))


def _codes(model: dict[str, Any], key: str) -> list[int] | None:
    codes = model.get(key)
    if codes is None:
        return None
    if not isinstance(codes, list) or len(codes) < 2 or not all(type(code) is int for code in codes):
        raise ModelPackageError("class codes are not a list of integers")
    return codes


def _booster(model: dict[str, Any], data: bytes, width: int, task_type: str, rows: np.ndarray) -> Any:
    """A native booster. XGBoost/LightGBM: a memory-limited child parses the stored bytes strictly
    and returns the rebuilt model; a second child loads and predicts the rebuilt bytes; only then
    does this process load exactly those bytes (never the stored ones). CatBoost: the probe child
    loads the stored ``cbm`` and validates CatBoost's JSON view of it before this process does."""

    kind, name = model.get("estimator_format"), model.get("estimator_class")
    if kind == "xgboost_json" and name in {"XGBClassifier", "XGBRegressor"}:
        import xgboost

        codes = _codes(model, "contiguous_label_codes")
        # The library's mode follows the FITTED classes: a multiclass task whose training pool
        # held two classes trains a binary booster (num_class 0).
        many = bool(codes and len(codes) > 2)
        canonical = _canonical(kind, data, width, len(codes) if many else 0, _native_task(task_type, many))
        _probe(kind, name, canonical, rows)
        estimator = getattr(xgboost, name)()  # name is one of the two literals above
        estimator.load_model(bytearray(canonical))
        if codes is None:
            return estimator
        from app.engine.models.registry import ContiguousLabelClassifier

        wrapper = ContiguousLabelClassifier()
        wrapper.classes_, wrapper.estimator_ = np.asarray(codes), estimator
        return wrapper
    if kind == "lightgbm_text" and name in {"LGBMClassifier", "LGBMRegressor"}:
        import lightgbm

        codes = _codes(model, "class_codes") if name == "LGBMClassifier" else None
        if (name == "LGBMClassifier") != (codes is not None) or (codes and task_type == "regression"):
            raise ModelPackageError("LightGBM classes do not match the task")
        many = bool(codes and len(codes) > 2)  # two fitted classes: LightGBM trains in binary mode
        canonical = _canonical(kind, data, width, len(codes) if many else 1, _native_task(task_type, many))
        _probe(kind, name, canonical, rows)
        return LightGBMBoosterModel(lightgbm.Booster(model_str=canonical.decode("ascii")), codes)
    if kind == "catboost_cbm" and name in {"CatBoostClassifier", "CatBoostRegressor"}:
        import catboost

        codes = _codes(model, "class_codes")
        dimension = len(codes) if codes and len(codes) > 2 else 1
        summary = json.loads(_probe(kind, name, data, rows, width=width, dimension=dimension))
        estimator = getattr(catboost, name)()
        estimator.load_model(blob=data, format="cbm")
        checks.check_catboost(estimator, width, int(summary["trees"]))
        return estimator
    raise ModelPackageError("unknown estimator format")


def _native_task(task_type: str, many_classes: bool) -> str:
    if task_type == "regression":
        return "regression"
    return "multiclass" if many_classes else "binary"


# The child: limits first (memory, file size; must hold on Linux), then one job. It isolates
# crashes, hangs and memory use; it does NOT isolate secrets -- it shares the worker's uid,
# filesystem and network -- so its environment is reduced to what the libraries need.
_CHILD = """
import io, json, os, resource, sys
mode, root, memory, file_size, cpu = sys.argv[1], sys.argv[2], int(sys.argv[3]), int(sys.argv[4]), int(sys.argv[5])
# CPU seconds too: a child orphaned by a dying worker still ends on its own.
for limit, value in ((resource.RLIMIT_AS, memory), (resource.RLIMIT_FSIZE, file_size), (resource.RLIMIT_CPU, cpu)):
    try:
        hard = resource.getrlimit(limit)[1]
        value = value if hard == resource.RLIM_INFINITY else min(value, hard)
        resource.setrlimit(limit, (value, value))
    except (ValueError, OSError):
        if sys.platform.startswith("linux"):
            sys.exit(3)  # where the OS supports the limits they must hold; macOS (dev) runs without
if sys.platform.startswith("linux"):
    try:
        with open("/proc/self/oom_score_adj", "w") as handle:
            handle.write("1000")  # the kernel kills this child first under memory pressure
    except OSError:
        pass
sys.path.insert(0, root)
request = json.loads(sys.stdin.buffer.readline())
data = sys.stdin.buffer.read()
kind, name = request["kind"], request["name"]
out = sys.stdout.buffer
if mode == "canonical":
    from app.engine.serving import package_checks as checks
    rebuild = checks.canonical_xgboost_json if kind == "xgboost_json" else checks.canonical_lightgbm_text
    out.write(rebuild(data, request["width"], request["n_classes"], request["task_type"]))
    sys.exit(0)
import numpy as np
size = request["model_size"]
blob, rows = data[:size], np.load(io.BytesIO(data[size:]), allow_pickle=False)
summary = "ok"
if kind == "xgboost_json":
    import xgboost
    model = getattr(xgboost, name)()
    model.load_model(bytearray(blob))
    model.predict_proba(rows) if name == "XGBClassifier" else model.predict(rows)
elif kind == "lightgbm_text":
    import lightgbm
    lightgbm.Booster(model_str=blob.decode("ascii")).predict(rows)
else:
    import catboost
    from app.engine.serving.package_checks import check_catboost_json
    model = getattr(catboost, name)()
    model.load_model(blob=blob, format="cbm")
    path = os.path.join(request["workdir"], "view.json")
    model.save_model(path, format="json")
    with open(path, encoding="utf-8") as handle:
        trees = check_catboost_json(handle.read(), request["width"], request["dimension"])
    model.predict_proba(rows, thread_count=1) if name == "CatBoostClassifier" else model.predict(rows, thread_count=1)
    summary = json.dumps({"trees": trees})
out.write(summary.encode())
"""
# Memory (address space) and time limits: settings MODEL_PROBE_MEMORY_BYTES (default 6 GiB) and
# MODEL_PROBE_TIMEOUT_SECONDS (default 120). Per-file write limit inside the child: the rebuilt model (canonical mode) or CatBoost's JSON
# view of a model at the native cap (~3.5x the cbm size; probe mode).
CANONICAL_FILE_BYTES = 2 * NATIVE_ESTIMATOR_MAX_BYTES
PROBE_FILE_BYTES = 4 * NATIVE_ESTIMATOR_MAX_BYTES
PROBE_OUTPUT_MAX_BYTES = 1 << 20  # the probe answers "ok" or a small summary


def _run_child(mode: str, request: dict[str, Any], payload: bytes, *, file_bytes: int, output_max: int) -> bytes:
    """Run one child job; a crash, hang (timeout), refused limit or oversized answer refuses."""

    import os
    import signal
    import subprocess
    import sys
    import tempfile
    from pathlib import Path

    from app.config import get_settings

    settings = get_settings()
    timeout = settings.model_probe_timeout_seconds
    root = str(Path(__file__).resolve().parents[3])  # apps/api: the child imports only package_checks
    env = {"PATH": os.environ.get("PATH", ""), "OMP_NUM_THREADS": "1", "MALLOC_ARENA_MAX": "2"}
    command = [sys.executable, "-I", "-c", _CHILD, mode, root, str(settings.model_probe_memory_bytes), str(file_bytes),
               str(int(timeout) + 10)]
    with tempfile.TemporaryDirectory(prefix="dclab-probe-") as workdir:  # removed even after a kill
        stdin = (json.dumps({**request, "workdir": workdir}) + "\n").encode() + payload
        with open(os.path.join(workdir, "answer"), "w+b") as answer:
            process = subprocess.Popen(command, stdin=subprocess.PIPE, stdout=answer, stderr=subprocess.DEVNULL,
                                       env={**env, "TMPDIR": workdir}, cwd=workdir, start_new_session=True)
            try:
                process.communicate(input=stdin, timeout=timeout)
            except subprocess.TimeoutExpired as exc:
                raise ModelPackageError(f"model {mode} child timed out") from exc
            finally:
                if process.poll() is None:  # the whole session: the child and anything it started
                    os.killpg(process.pid, signal.SIGKILL)
                    process.wait()
            if process.returncode != 0:
                raise ModelPackageError(f"model {mode} child failed (exit {process.returncode})")
            if answer.seek(0, os.SEEK_END) > output_max:
                raise ModelPackageError(f"model {mode} child answer larger than the limit")
            answer.seek(0)
            return answer.read()


def _canonical(kind: str, data: bytes, width: int, n_classes: int, task_type: str) -> bytes:
    """The rebuilt XGBoost JSON / LightGBM text, parsed and validated in a memory-limited child
    (pure Python: ``package_checks.canonical_*``), so this process never parses stored bytes."""

    request = {"kind": kind, "name": "", "width": width, "n_classes": n_classes, "task_type": task_type}
    return _run_child("canonical", request, data, file_bytes=CANONICAL_FILE_BYTES, output_max=CANONICAL_FILE_BYTES)


def _probe(kind: str, name: str, data: bytes, rows: np.ndarray, *, width: int = 0, dimension: int = 1) -> str:
    """Load ``data`` and predict ``rows`` in a child (CatBoost: also validate its JSON view there).
    Returns ``ok`` or, for CatBoost, ``{"trees": n}``."""

    buffer = io.BytesIO()
    np.save(buffer, np.asarray(rows, dtype=np.float64), allow_pickle=False)
    request = {"kind": kind, "name": name, "model_size": len(data), "width": width, "dimension": dimension}
    answer = _run_child("probe", request, data + buffer.getvalue(), file_bytes=PROBE_FILE_BYTES,
                        output_max=PROBE_OUTPUT_MAX_BYTES)
    return answer.decode("utf-8")


def _probe_rows(prep: Any, manifest: dict[str, Any], count: int = 64) -> np.ndarray:
    """Booster inputs for the probe: rows built from the manifest's contract (categorical
    values from its allowed values; numbers at several scales and extremes) through ``prep``."""

    rng = np.random.default_rng(0)
    frame = {}
    for column in manifest["feature_contract"]["columns"]:
        if column.get("role") == "categorical":
            values = [value for value in column.get("allowed_values") or [] if value is not None] or ["missing"]
            frame[column["name"]] = [values[int(i)] for i in rng.integers(0, len(values), count)]
        else:
            scales = np.repeat([1.0, 1e3, 1e9], count // 3 + 1)[:count]
            frame[column["name"]] = rng.normal(0, 1, count) * scales
    import pandas as pd

    rows = pd.DataFrame(frame).astype({name: object for name, v in frame.items() if isinstance(v, list)})
    return np.asarray(prep.transform(rows), dtype=np.float64)


def load_model_package(payload: bytes, *, manifest_digest: str | None) -> LoadedModel:
    """The packaged winner as a scikit-learn ``Pipeline``, after every consistency check in
    ``package_checks`` passed. Raises ``ScoringError`` (``unsupported_model``)."""

    try:
        contents = read_package(payload)
        if not manifest_digest or sha256_hex(contents.manifest_bytes) != manifest_digest:
            raise ScoringError(UNSUPPORTED_MODEL, DIGEST_MESSAGE)
        manifest = contents.manifest
        model, task_type = manifest["model"], str(manifest.get("task_type"))
        features = [str(name) for name in manifest.get("features") or []]
        expected = checks.expected_signature(str(model.get("family")), task_type, model)
        prep = _skops_load(contents.members[PREPROCESSOR_MEMBER], PREPROCESSOR_MEMBER)
        width = checks.check_preprocessor(prep, features)
        member = ESTIMATOR_MEMBERS[str(model["estimator_format"])]
        data = contents.members[member]
        if model["estimator_format"] == "skops":
            estimator = _skops_load(data, member)
            checks.check_sklearn_estimator(estimator, width)
        else:
            estimator = _booster(model, data, width, task_type, _probe_rows(prep, manifest))
        if checks.estimator_signature(estimator) != expected:
            raise ModelPackageError("estimator is not the class its family trains")
    except ScoringError:
        raise
    except ModelPackageDigestError as exc:
        raise ScoringError(UNSUPPORTED_MODEL, DIGEST_MESSAGE) from exc
    except Exception as exc:  # noqa: BLE001 - untrusted types, inconsistent members, library errors
        raise ScoringError(UNSUPPORTED_MODEL, MODEL_MESSAGE) from exc
    return LoadedModel(Pipeline([("prep", prep), ("model", estimator)]), manifest, "package_v1")


def load_legacy_pickle(payload: bytes) -> LoadedModel:
    """QUARANTINED: unpickle a model saved before the package format. Refused (with a
    warning) unless the setting allows legacy models; the caller warns when it is used."""

    from app.config import get_settings, legacy_pickle_models_allowed

    if not legacy_pickle_models_allowed(get_settings()):
        logger.warning("refused a quarantined legacy pickle model (LEGACY_PICKLE_MODELS_ENABLED is not true)")
        raise ScoringError(UNSUPPORTED_MODEL, LEGACY_MESSAGE)
    if len(payload) > PACKAGE_MAX_BYTES:
        raise ScoringError(UNSUPPORTED_MODEL, MODEL_MESSAGE)
    import joblib

    try:
        pipeline = joblib.load(io.BytesIO(payload))
    except Exception as exc:  # noqa: BLE001
        raise ScoringError(UNSUPPORTED_MODEL, MODEL_MESSAGE) from exc
    return LoadedModel(pipeline, None, "legacy_quarantined")


def load_model(
    payload: bytes, *, expected_digest: str, package_format: str | None, manifest_digest: str | None
) -> LoadedModel:
    """The scoring entry point. Outside an explicit development environment only the
    worker process (``dclab worker run``) loads models, so an API process running jobs
    in-process (``ML_JOB_DISPATCHER=thread``/``inline``) refuses. The bytes must match the
    model artifact's digest; a version recorded as a package never falls back to pickle."""

    from app.config import dclab_env_is_development, is_worker_process

    if not is_worker_process() and not dclab_env_is_development():
        logger.warning("refused to load a model outside the worker process")
        raise ScoringError(SCORING_FAILED, WORKER_ONLY_MESSAGE)
    if not expected_digest or sha256_hex(payload) != expected_digest:
        raise ScoringError(UNSUPPORTED_MODEL, DIGEST_MESSAGE)
    if package_format == PACKAGE_FORMAT:
        return load_model_package(payload, manifest_digest=manifest_digest)
    return load_legacy_pickle(payload)
