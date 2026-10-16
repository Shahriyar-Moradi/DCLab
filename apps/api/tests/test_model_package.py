"""P7.1-A: the safe model package (skops + native booster formats + manifest).

Pure tests (no database): every registry family round-trips bit for bit; tampered,
malicious and malformed packages are refused without running code; the quarantine
setting gates the legacy pickle path; scoring with a package equals scoring with
the old pickle on the same rows (same contract errors); the API process never
imports the loader. The end-to-end worker path is in ``test_v1_batch_predictions``.
"""

from __future__ import annotations

import ast
import hashlib
import io
import json
import os
import subprocess
import sys
import zipfile
from pathlib import Path

import joblib
import numpy as np
import pandas as pd
import pytest
from sklearn.pipeline import Pipeline

from app.config import Settings, get_settings, legacy_pickle_models_allowed
from app.engine.experiments.runner import _predict
from app.engine.lab.auto_prepare import build_preprocessor
from app.engine.models.registry import available_families, make_model
from app.engine.serving import model_package as mp
from app.engine.serving import package_checks as checks
from app.engine.serving import package_loader as pl
from app.engine.serving.batch_scoring import ScoringError, score_frame, scoring_spec
from app.services.audience_projection import PUBLIC_FAILURE, public_failure

API_ROOT = Path(__file__).resolve().parents[1]
NUMERIC, CATEGORICAL = ["a", "b", "when"], ["c", "code"]
ACTIONS = [{"step": "datetime_to_unix_seconds", "columns": ["when"], "output_columns": ["when"],
            "parameters": {"formats": {"when": "%Y-%m-%d"}}}]


def _frame(n: int = 180, seed: int = 0) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    frame = pd.DataFrame({
        "a": rng.normal(size=n), "b": rng.normal(size=n),
        "when": rng.integers(1_600_000_000, 1_700_000_000, n).astype(float),
        "c": rng.choice(["x", "y", "z"], n).astype(object), "code": rng.choice(["1", "2", "A"], n).astype(object),
    })
    frame.loc[::17, "a"] = np.nan
    frame.loc[::13, "c"] = None
    return frame


def _target(task: str, frame: pd.DataFrame, *, gap: bool = False) -> np.ndarray:
    rng = np.random.default_rng(1)
    if task == "regression":
        return frame["b"].to_numpy() * 3 + rng.normal(size=len(frame))
    if task == "binary":
        return (frame["b"] + rng.normal(scale=0.5, size=len(frame)) > 0).astype(int).to_numpy()
    codes = rng.integers(0, 4, len(frame))
    if gap == "two":  # a multiclass task whose training pool holds only two classes ({0, 2})
        return np.where(frame["b"].to_numpy() > 0, 0, 2)
    return np.where(codes == 2, 1, codes) if gap else codes  # gap: class 2 absent from the fit rows


def _fitted(task: str, family: str, frame: pd.DataFrame, y: np.ndarray) -> Pipeline:
    pipeline = Pipeline([("prep", build_preprocessor(NUMERIC, CATEGORICAL)),
                         ("model", make_model(family, seed=3, task_type=task))])
    return pipeline.fit(frame, y)


def _package(pipeline: Pipeline, family: str = "logistic_regression", task: str = "binary") -> tuple[bytes, bytes]:
    raw = mp.build_package(
        pipeline, family=family, task_type=task, candidate_id="cand_1",
        training_dtypes={"a": "float64", "c": "object"}, feature_actions=ACTIONS, target="y",
        cv={"metric": "rmse", "strategy": "kfold", "folds": 5, "mean": {"rmse": 1.5, "r2": 0.4}, "std": {"rmse": 0.1}},
    )
    return mp.finalize_package(raw, training_run_id="run-1", feature_set_version_id=None,
                               availability={"a": "known_before_prediction"})


def _load(package: bytes, manifest: bytes) -> pl.LoadedModel:
    return pl.load_model_package(package, manifest_digest=mp.sha256_hex(manifest))


def _rezip(package: bytes, *, replace: dict[str, bytes] | None = None, extra: dict[str, bytes] | None = None) -> bytes:
    with zipfile.ZipFile(io.BytesIO(package)) as archive:
        members = {name: archive.read(name) for name in archive.namelist()}
    members.update(replace or {})
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        for name, data in {**members, **(extra or {})}.items():
            archive.writestr(name, data)
    return buffer.getvalue()


def _cases() -> list[tuple[str, str, bool | str]]:
    cases = [(task, family, False) for task in ("binary", "multiclass", "regression")
             for family in available_families(task)]
    return cases + [("multiclass", family, True) for family in available_families("multiclass")] + [
        ("multiclass", family, "two") for family in available_families("multiclass")]  # boosters train binary


# --- round trip: every family the registry can train --------------------------------------


@pytest.mark.parametrize(("task", "family", "gap"), _cases())
def test_every_registry_family_round_trips_bit_for_bit(task, family, gap):
    """Registry-driven: a new family without a safe package format fails here."""

    frame = _frame()
    pipeline = _fitted(task, family, frame, _target(task, frame, gap=gap))
    package, manifest = _package(pipeline, family, task)
    loaded = _load(package, manifest)
    scoring = _frame(seed=9).assign(c=lambda f: f["c"].where(f.index % 7 != 0, "unseen"))
    n_classes = 4 if task == "multiclass" else None
    classifier = task != "regression"
    # Bit for bit (no tolerance): same probabilities / values and the same labels.
    assert np.array_equal(_predict(pipeline, scoring, classifier, n_classes),
                          _predict(loaded.pipeline, scoring, classifier, n_classes))
    assert np.array_equal(pipeline.predict(scoring), loaded.pipeline.predict(scoring))
    assert list(loaded.pipeline.feature_names_in_) == NUMERIC + CATEGORICAL
    kind = loaded.manifest["model"]["estimator_format"]
    assert kind == {"xgboost": "xgboost_json", "lightgbm": "lightgbm_text", "catboost": "catboost_cbm"}.get(
        family.split("_")[0], "skops")


def test_manifest_records_contract_versions_lineage_and_cv_only():
    frame = _frame()
    pipeline = _fitted("binary", "xgboost", frame, _target("binary", frame))
    package, manifest_bytes = _package(pipeline, "xgboost")
    contents = mp.read_package(package)
    manifest = contents.manifest
    assert contents.manifest_bytes == manifest_bytes and manifest["format"] == mp.PACKAGE_FORMAT
    assert set(manifest["members"]) == {"preprocessor.skops", "estimator.xgb.json"}
    for name, entry in manifest["members"].items():
        assert entry["sha256"] == mp.sha256_hex(contents.members[name])
    versions = manifest["library_versions"]
    assert {"python", "numpy", "scikit-learn", "skops", "xgboost"} <= set(versions) and "lightgbm" not in versions
    assert manifest["dependency_lock_digest"] == mp._digest_json(versions)
    assert (manifest["training_run_id"], manifest["pipeline_run_id"], manifest["candidate_id"]) == (
        "run-1", "run-1", "cand_1")
    contract = manifest["feature_contract"]
    columns = {column["name"]: column for column in contract["columns"]}
    assert [column["name"] for column in contract["columns"]] == NUMERIC + CATEGORICAL == manifest["features"]
    assert columns["a"]["availability"] == "known_before_prediction" and columns["b"]["availability"] == "unknown"
    assert columns["a"]["training_dtype"] == "float64" and columns["b"]["training_dtype"] == "unknown"
    assert columns["c"]["role"] == "categorical" and columns["c"]["allowed_values"] == ["x", "y", "z"]
    assert columns["when"]["date_format"] == "%Y-%m-%d" and columns["when"]["date_format_recorded"] is True
    assert [t["fit_scope"] for t in columns["when"]["transforms"]] == ["partition_structural", "fold_fitted"]
    assert all(column["required"] for column in contract["columns"])
    digest = contract.pop("digest")
    assert digest == mp._digest_json(contract)
    # CV metrics in natural units (an error metric stays positive); nothing from the holdout.
    assert manifest["cv_metrics"]["mean"] == {"rmse": 1.5, "r2": 0.4}
    assert set(manifest["cv_metrics"]) == {"basis", "metric", "strategy", "folds", "mean", "std"}
    # Finalizing copies the members byte for byte.
    raw_members = mp.read_package(package).members
    again, _ = mp.finalize_package(package, training_run_id="run-2", feature_set_version_id="fsv", availability={})
    assert mp.read_package(again).members == raw_members


def test_writer_refuses_types_outside_the_allowlist(monkeypatch):
    frame = _frame()
    pipeline = _fitted("binary", "random_forest", frame, _target("binary", frame))
    monkeypatch.setattr(mp, "SKOPS_TRUSTED_TYPES", ("numpy.dtype",))  # trees need sklearn Tree
    with pytest.raises(mp.ModelPackageError, match="allowlist"):
        _package(pipeline, "random_forest")


# --- integrity: tampering is refused ---------------------------------------------------------


def test_tampered_members_and_manifests_are_refused():
    frame = _frame()
    pipeline = _fitted("binary", "logistic_regression", frame, _target("binary", frame))
    package, manifest = _package(pipeline)
    assert _load(package, manifest).saved_format == "package_v1"  # positive control
    contents = mp.read_package(package)
    estimator = bytearray(contents.members["estimator.skops"])
    estimator[len(estimator) // 2] ^= 0xFF
    tampered = _rezip(package, replace={"estimator.skops": bytes(estimator)})
    with pytest.raises(mp.ModelPackageDigestError):
        mp.read_package(tampered)
    for payload, digest in (
        (tampered, mp.sha256_hex(manifest)),
        (package, "0" * 64),  # the version pins another manifest
        (package, None),
    ):
        with pytest.raises(ScoringError) as caught:
            pl.load_model_package(payload, manifest_digest=digest)
        assert (caught.value.code, caught.value.public_message) == ("unsupported_model", pl.DIGEST_MESSAGE)
    # A re-signed manifest (member digest updated to match the tampered bytes) still differs
    # from the manifest digest the model version references.
    edited = dict(contents.manifest, members={**contents.manifest["members"], "estimator.skops": {
        "sha256": mp.sha256_hex(bytes(estimator)), "size_bytes": len(estimator)}})
    resigned = _rezip(tampered, replace={"manifest.json": json.dumps(edited).encode()})
    mp.read_package(resigned)  # structurally fine...
    with pytest.raises(ScoringError) as caught:
        _load(resigned, manifest)  # ...but not the pinned manifest
    assert caught.value.public_message == pl.DIGEST_MESSAGE


def test_reference_summary_is_additive_and_pinned_by_the_manifest_digest():
    """P7.4-A: the training-partition reference monitoring compares with rides in the manifest.
    Packages without it (saved before monitoring) still read and load; editing it breaks the pin."""

    from app.engine.serving import monitoring as mon

    frame = _frame()
    pipeline = _fitted("binary", "logistic_regression", frame, _target("binary", frame))
    reference = mon.build_reference(frame.loc[:, NUMERIC + CATEGORICAL], numeric=NUMERIC, categorical=CATEGORICAL,
                                    task_type="binary", oof_scores=[np.linspace(0.0, 1.0, 60)], threshold=0.5,
                                    class_labels=None)
    raw = mp.build_package(
        pipeline, family="logistic_regression", task_type="binary", candidate_id="cand_1",
        training_dtypes={"a": "float64"}, feature_actions=ACTIONS, target="y",
        cv={"metric": "roc_auc", "strategy": "kfold", "folds": 5, "mean": {"roc_auc": 0.8}, "std": {}},
        decision_threshold=0.5, reference_summary=reference,
    )
    package, manifest_bytes = mp.finalize_package(raw, training_run_id="run-1", feature_set_version_id=None,
                                                  availability={})
    manifest = mp.read_package(package).manifest
    assert manifest["reference_summary"] == json.loads(json.dumps(reference))  # finalize keeps it
    assert mon.usable_reference(manifest)["n_rows"] == len(frame)
    assert [f["name"] for f in manifest["reference_summary"]["features"]] == manifest["features"]
    assert _load(package, manifest_bytes).saved_format == "package_v1"  # digest-pinned load works
    older, older_manifest = _package(pipeline)  # no reference: a model saved before monitoring
    assert "reference_summary" not in mp.read_package(older).manifest
    assert mon.usable_reference(mp.read_package(older).manifest) is None
    assert _load(older, older_manifest).saved_format == "package_v1"
    edited = dict(manifest, reference_summary={**manifest["reference_summary"], "n_rows": 1})
    resigned = _rezip(package, replace={"manifest.json": json.dumps(edited).encode()})
    mp.read_package(resigned)  # structurally fine, but not the manifest the model version pins
    with pytest.raises(ScoringError) as caught:
        _load(resigned, manifest_bytes)
    assert caught.value.public_message == pl.DIGEST_MESSAGE


@pytest.mark.parametrize("mutation", ["traversal", "nested", "duplicate", "unknown", "no_manifest", "oversize"])
def test_malformed_packages_are_refused(mutation, monkeypatch):
    frame = _frame()
    package, _manifest = _package(_fitted("binary", "majority", frame, _target("binary", frame)), "majority")
    if mutation == "oversize":
        monkeypatch.setattr(mp, "PREPROCESSOR_MAX_BYTES", 64)
        bad = package
    elif mutation == "no_manifest":
        with zipfile.ZipFile(io.BytesIO(package)) as archive:
            kept = {name: archive.read(name) for name in archive.namelist() if name != "manifest.json"}
        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, "w") as archive:
            for name, data in kept.items():
                archive.writestr(name, data)
        bad = buffer.getvalue()
    elif mutation == "duplicate":
        buffer = io.BytesIO(package)
        with pytest.warns(UserWarning, match="Duplicate name"), zipfile.ZipFile(buffer, "a") as archive:
            archive.writestr("manifest.json", b"{}")
        bad = buffer.getvalue()
    else:
        name = {"traversal": "../estimator.skops", "nested": "x/estimator.skops", "unknown": "loader.py"}[mutation]
        bad = _rezip(package, extra={name: b"print('hi')"})
    with pytest.raises(mp.ModelPackageError):
        mp.read_package(bad)
    monkeypatch.undo()
    assert mp.read_package(package)  # the original is fine


def test_size_limits_are_pinned():
    mib = 1 << 20
    assert (mp.PACKAGE_MAX_BYTES, mp.MANIFEST_MAX_BYTES, mp.PREPROCESSOR_MAX_BYTES, mp.ESTIMATOR_MAX_BYTES) == (
        512 * mib, 16 * mib, 64 * mib, 1536 * mib)
    assert (mp.MAX_INFLATION, mp.INFLATION_FLOOR_BYTES, mp.ALLOWED_VALUES_MAX) == (16, 64 * mib, 1000)
    assert mp.NATIVE_ESTIMATOR_MAX_BYTES == 256 * mib
    assert mp.member_limit("manifest.json") == mp.MANIFEST_MAX_BYTES
    assert mp.member_limit("preprocessor.skops") == mp.PREPROCESSOR_MAX_BYTES
    assert mp.member_limit("estimator.skops") == mp.ESTIMATOR_MAX_BYTES
    assert {mp.member_limit(name) for name in ("estimator.xgb.json", "estimator.lgbm.txt", "estimator.cbm")} == {
        mp.NATIVE_ESTIMATOR_MAX_BYTES}
    defaults = Settings(_env_file=None)
    assert (defaults.model_probe_memory_bytes, defaults.model_probe_timeout_seconds, pl.PROBE_OUTPUT_MAX_BYTES) == (
        6 << 30, 120, 1 * mib)
    # The child may write CatBoost's JSON view (~3.5x a cbm) of a model at the native cap.
    assert (pl.CANONICAL_FILE_BYTES, pl.PROBE_FILE_BYTES) == (2 * mp.NATIVE_ESTIMATOR_MAX_BYTES, 4 * mp.NATIVE_ESTIMATOR_MAX_BYTES)


def test_package_and_member_size_limits(monkeypatch):
    frame = _frame()
    package, manifest = _package(_fitted("binary", "majority", frame, _target("binary", frame)), "majority")
    monkeypatch.setattr(mp, "PACKAGE_MAX_BYTES", len(package) - 1)
    with pytest.raises(mp.ModelPackageError, match="larger than the limit"):
        mp.read_package(package)
    monkeypatch.setattr(mp, "PACKAGE_MAX_BYTES", 1024)
    with pytest.raises(mp.ModelPackageError, match="too large"):  # refused when saving, too
        _package(_fitted("binary", "majority", frame, _target("binary", frame)), "majority")
    monkeypatch.undo()
    # An inner skops zip that inflates far beyond its packed size is refused before skops reads it.
    bomb = io.BytesIO()
    with zipfile.ZipFile(bomb, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("schema.json", b"{" + b" " * (4 << 20) + b"}")
    signed, signed_manifest = _signed(package, "estimator.skops", bomb.getvalue())
    monkeypatch.setattr(mp, "INFLATION_FLOOR_BYTES", 0)
    with pytest.raises(ScoringError) as caught:
        _load(signed, signed_manifest)
    assert caught.value.public_message == pl.MODEL_MESSAGE
    assert isinstance(caught.value.__cause__, mp.ModelPackageError)
    monkeypatch.undo()
    assert _load(package, manifest)  # positive control


# --- no code runs on load ------------------------------------------------------------------


class _Exploit:
    """Unpickling or skops-loading this would create ``path`` (the side effect we test)."""

    def __init__(self, path: str):
        self.path = path

    def __reduce__(self):
        return (Path.touch, (Path(self.path),))

    def __setstate__(self, state):
        Path(state["path"]).touch()


def _signed(package: bytes, member: str, data: bytes, *, manifest_patch: dict | None = None) -> tuple[bytes, bytes]:
    """A structurally valid package whose ``member`` is ``data`` (digests updated): the
    manifest digest is the attacker's, so this isolates the load-time guarantees."""

    contents = mp.read_package(package)
    members = {**contents.members, member: data}
    manifest = dict(contents.manifest, members={
        name: {"sha256": mp.sha256_hex(blob), "size_bytes": len(blob)} for name, blob in members.items()})
    manifest.update(manifest_patch or {})
    return mp._assemble(members, manifest)


def test_malicious_payloads_are_refused_without_running_code(tmp_path):
    import skops.io as sio

    frame = _frame()
    package, _manifest = _package(_fitted("binary", "logistic_regression", frame, _target("binary", frame)))
    sentinel = tmp_path / "pwned"
    buffer = io.BytesIO()
    joblib.dump(_Exploit(str(sentinel)), buffer)
    pickled = buffer.getvalue()
    evil_skops = sio.dumps(_Exploit(str(sentinel)))
    function_skops = sio.dumps({"call": os.system})
    cases = [
        ("model artifact is a pickle", pickled, None),
        *[(f"{member} is a pickle", *_signed(package, member, pickled)) for member in
          ("preprocessor.skops", "estimator.skops")],
        ("skops member with an untrusted class", *_signed(package, "estimator.skops", evil_skops)),
        ("skops member with os.system", *_signed(package, "preprocessor.skops", function_skops)),
        # The trust list is code-owned: one written into the manifest is never used.
        ("manifest lists the class as trusted", *_signed(package, "estimator.skops", evil_skops, manifest_patch={
            "skops_trusted_types": [f"{_Exploit.__module__}.{_Exploit.__qualname__}"]})),
    ]
    assert "skops_trusted_types" not in mp.read_package(package).manifest
    for label, payload, manifest in cases:
        digest = mp.sha256_hex(manifest) if manifest else "0" * 64
        with pytest.raises(ScoringError) as caught:
            pl.load_model(payload, expected_digest=mp.sha256_hex(payload), package_format=mp.PACKAGE_FORMAT,
                          manifest_digest=digest)
        assert caught.value.code == "unsupported_model", label
        assert not sentinel.exists(), label
    # Mutation proof: a loader that trusted the class (or unpickled) WOULD run the code.
    sio.loads(evil_skops, trusted=[f"{_Exploit.__module__}.{_Exploit.__qualname__}"])
    assert sentinel.exists()
    sentinel.unlink()
    joblib.load(io.BytesIO(pickled))
    assert sentinel.exists()


# --- quarantine ------------------------------------------------------------------------------


@pytest.mark.parametrize(("variable", "env", "flag", "allowed"), [
    ("DCLAB_ENV", None, None, False),  # unset: the settings default "development" does not count
    ("DCLAB_ENV", "staging", None, False), ("DCLAB_ENV", "developmnet", None, False),
    ("DCLAB_ENV", "prod", None, False), ("DCLAB_ENV", "production", None, False),
    ("DCLAB_ENV", "development", None, True), ("DCLAB_ENV", "test", None, True),
    ("DCLAB_ENV", "dev", None, True), ("DCLAB_ENV", "local", None, True),
    ("ENVIRONMENT", "dev", None, False), ("APP_ENV", "development", None, False),  # aliases never enable pickles
    ("DCLAB_ENV", "production", True, True), ("DCLAB_ENV", None, True, True),
    ("DCLAB_ENV", "test", False, False), ("DCLAB_ENV", "development", False, False),
])
def test_legacy_pickles_are_allowed_only_explicitly(variable, env, flag, allowed, monkeypatch):
    for name in ("DCLAB_ENV", "APP_ENV", "ENVIRONMENT", "LEGACY_PICKLE_MODELS_ENABLED"):
        monkeypatch.delenv(name, raising=False)
    if env is not None:
        monkeypatch.setenv(variable, env)
    settings = Settings(_env_file=None, **({} if flag is None else {"legacy_pickle_models_enabled": flag}))
    assert legacy_pickle_models_allowed(settings) is allowed


def test_only_the_worker_process_loads_models_outside_development(monkeypatch):
    frame = _frame()
    package, manifest = _package(_fitted("binary", "majority", frame, _target("binary", frame)), "majority")
    kwargs = {"expected_digest": mp.sha256_hex(package), "package_format": mp.PACKAGE_FORMAT,
              "manifest_digest": mp.sha256_hex(manifest)}
    monkeypatch.setenv("DCLAB_ENV", "production")
    monkeypatch.setattr("app.config._PROCESS_ROLE", {"worker": False})  # e.g. ML_JOB_DISPATCHER=thread in the API
    with pytest.raises(ScoringError) as caught:
        pl.load_model(package, **kwargs)
    assert (caught.value.code, caught.value.public_message) == ("scoring_failed", pl.WORKER_ONLY_MESSAGE)
    from app.config import mark_worker_process

    mark_worker_process()  # what `dclab worker run` does
    assert pl.load_model(package, **kwargs).saved_format == "package_v1"
    with pytest.raises(ScoringError) as caught:  # the artifact digest is checked first
        pl.load_model(package, **{**kwargs, "expected_digest": "0" * 64})
    assert caught.value.public_message == pl.DIGEST_MESSAGE


def test_legacy_pickle_loads_only_when_allowed(monkeypatch):
    frame = _frame()
    pipeline = _fitted("binary", "random_forest", frame, _target("binary", frame))
    buffer = io.BytesIO()
    joblib.dump(pipeline, buffer)
    digest = mp.sha256_hex(buffer.getvalue())
    for allowed in (True, False):
        monkeypatch.setattr("app.config.get_settings", lambda allowed=allowed: Settings(
            dclab_env="development", legacy_pickle_models_enabled=allowed))
        if allowed:
            loaded = pl.load_model(buffer.getvalue(), expected_digest=digest, package_format=None, manifest_digest=None)
            assert loaded.saved_format == "legacy_quarantined" and loaded.manifest is None
        else:
            with pytest.raises(ScoringError) as caught:
                pl.load_model(buffer.getvalue(), expected_digest=digest, package_format=None, manifest_digest=None)
            assert (caught.value.code, caught.value.public_message) == ("unsupported_model", pl.LEGACY_MESSAGE)
    # Never a pickle fallback for a version recorded as a package.
    monkeypatch.setattr("app.config.get_settings", lambda: Settings(dclab_env="test", legacy_pickle_models_enabled=True))
    with pytest.raises(ScoringError):
        pl.load_model(buffer.getvalue(), expected_digest=digest, package_format=mp.PACKAGE_FORMAT, manifest_digest="0" * 64)


def test_saved_format_marks_every_non_package_artifact_quarantined():
    assert mp.saved_format({"package_format": mp.PACKAGE_FORMAT}) == "package_v1"
    for metadata in (None, {}, {"role": "model"}, {"package_format": "dclab.model_package.v0"},
                     {"model_format": "legacy_pickle", "quarantined": True}):
        assert mp.saved_format(metadata) == "legacy_quarantined"


# --- scoring: package == old pickle path; contract unchanged ------------------------------------


def _result() -> dict:
    return {
        "task": {"task_type": "binary", "target": "y", "entity_id": None,
                 "feature_engineering": {"feature_engineering_actions": ACTIONS}},
        "decision_threshold": {"value": 0.4},
        "scientific_evidence": {"feature_actions": ACTIONS, "missing_value_plan": {"column_decisions": []}},
    }


def test_scoring_with_the_package_equals_the_old_pickle_path_and_keeps_contract_errors():
    frame = _frame()
    pipeline = _fitted("binary", "lightgbm", frame, _target("binary", frame))
    package, manifest = _package(pipeline, "lightgbm")
    from_package = _load(package, manifest)
    buffer = io.BytesIO()
    joblib.dump(pipeline, buffer)
    from_pickle = pl.load_legacy_pickle(buffer.getvalue())  # tests run with legacy models allowed
    rows = _frame(n=40, seed=5).assign(when=lambda f: pd.to_datetime(f["when"], unit="s").dt.strftime("%Y-%m-%d"))
    rows["extra"] = 1
    new_spec, old_spec = scoring_spec(_result(), from_package.pipeline), scoring_spec(_result(), from_pickle.pipeline)
    assert new_spec == old_spec
    mp.check_manifest_contract(from_package.manifest, new_spec)
    new, old = score_frame(rows, new_spec, from_package.pipeline), score_frame(rows, old_spec, from_pickle.pipeline)
    pd.testing.assert_frame_equal(new.predictions, old.predictions, check_exact=True)
    assert new.contract == old.contract and new.contract["ignored_columns"] == ["extra"]
    errors = []
    for spec, model in ((new_spec, from_package.pipeline), (old_spec, from_pickle.pipeline)):
        with pytest.raises(ScoringError) as caught:
            score_frame(rows.drop(columns=["code"]), spec, model)
        errors.append((caught.value.code, caught.value.public_message, caught.value.contract))
    assert errors[0] == errors[1]
    assert errors[0][:2] == ("feature_contract_failed", "the file is missing 1 required column(s): code")


@pytest.mark.parametrize("drift", ["column", "role", "categories", "date_format", "date_column"])
def test_manifest_contract_check_catches_drift(drift):
    frame = _frame()
    pipeline = _fitted("binary", "logistic_regression", frame, _target("binary", frame))
    package, manifest_bytes = _package(pipeline)
    manifest = mp.read_package(package).manifest
    spec = scoring_spec(_result(), _load(package, manifest_bytes).pipeline)
    mp.check_manifest_contract(manifest, spec)  # positive control
    from dataclasses import replace

    mutated = {
        "column": replace(spec, feature_columns=spec.feature_columns + ("z",)),
        "role": replace(spec, numeric_columns=("a", "b", "when", "c"), categorical_columns=("code",)),
        "categories": replace(spec, categories={**spec.categories, "c": ("x", "y")}),
        "date_format": replace(spec, date_formats={"when": "%d/%m/%Y"}),
        "date_column": replace(spec, date_formats={}, legacy_date_columns=frozenset({"when"})),
    }[drift]
    with pytest.raises(mp.ModelPackageError):
        mp.check_manifest_contract(manifest, mutated)


# --- the API process never imports the loader ---------------------------------------------------

_LOADER_MODULES = ("app.engine.serving.package_loader", "skops")


def _module_level_loader_imports(source: str, *, anywhere: bool = False) -> list[str]:
    """``import``/``from`` statements of the loader or skops: top level only (function-local
    imports run only when the worker calls them), or ``anywhere`` in the module."""

    found = []
    tree = ast.parse(source)
    for node in (ast.walk(tree) if anywhere else tree.body):
        names = []
        if isinstance(node, ast.Import):
            names = [alias.name for alias in node.names]
        elif isinstance(node, ast.ImportFrom) and node.module:
            names = [node.module, *(f"{node.module}.{alias.name}" for alias in node.names)]
        found += [name for name in names if name.startswith(_LOADER_MODULES)]
    return found


def test_no_module_imports_the_loader_at_import_time():
    offenders = {}
    for path in (API_ROOT / "app").rglob("*.py"):
        if path.name == "package_loader.py":
            continue
        hits = _module_level_loader_imports(path.read_text(encoding="utf-8"))
        if hits:
            offenders[str(path.relative_to(API_ROOT))] = hits
    assert offenders == {}
    # Mutation proof: the scanner sees a top-level import and ignores a function-local one.
    assert _module_level_loader_imports("from app.engine.serving.package_loader import load_model\n")
    assert _module_level_loader_imports("import skops.io as sio\n")
    assert not _module_level_loader_imports("def f():\n    import skops.io\n")


# The only modules that may import the loader at all (inside functions): the worker's scoring
# job and the runner's save-time round trip (training runs in the worker).
_LOADER_IMPORTERS = {"app/services/batch_prediction_service.py", "app/engine/experiments/runner.py"}


def test_only_worker_paths_import_the_loader_even_inside_functions():
    importers = set()
    for path in (API_ROOT / "app").rglob("*.py"):
        source = path.read_text(encoding="utf-8")
        hits = [name for name in _module_level_loader_imports(source, anywhere=True) if "package_loader" in name]
        if hits and path.name != "package_loader.py":
            importers.add(path.relative_to(API_ROOT).as_posix())
    assert importers == _LOADER_IMPORTERS
    assert not any(name.startswith("app/api/") for name in importers)  # no route module, at any depth
    assert _module_level_loader_imports("def f():\n    from app.engine.serving import package_loader\n",
                                        anywhere=True)  # mutation proof


def test_the_api_process_never_imports_the_loader_or_skops():
    code = (
        "import json, sys\nimport app.main\n"
        "print(json.dumps(sorted(m for m in sys.modules if m == 'app.engine.serving.package_loader' "
        "or m == 'skops' or m.startswith('skops.'))))"
    )
    env = {**os.environ, "PYTHONPATH": os.pathsep.join([str(API_ROOT), os.environ.get("PYTHONPATH", "")])}
    out = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, env=env, timeout=120,
                         cwd=API_ROOT.parents[1])
    assert out.returncode == 0, out.stderr[-2000:]
    assert json.loads(out.stdout.strip().splitlines()[-1]) == []


# --- crafted members end in unsupported_model, never a crash (security review SF-2/SF-3) ------

_CHILD = """
import hashlib, json, sys
import numpy as np
import pandas as pd
from app.engine.serving import package_checks as checks, package_loader as pl
from app.engine.serving.batch_scoring import ScoringError
rows = pd.read_csv(sys.argv[2], dtype={"code": str})
flags = set(sys.argv[3].split(",")) if len(sys.argv) > 3 else set()
if "no-validators" in flags:  # simulate a validator gap: the stored bytes go to the library as they are
    pl._canonical = lambda kind, data, *args, **kwargs: data
if "no-probe" in flags:
    pl._probe = lambda *args, **kwargs: "ok"
if "short-timeout" in flags:
    from app.config import get_settings
    get_settings().model_probe_timeout_seconds = 5
for case in json.loads(open(sys.argv[1]).read()):
    try:
        loaded = pl.load_model_package(open(case["path"], "rb").read(), manifest_digest=case["digest"])
        model = loaded.pipeline
        scores = model.predict_proba(rows) if hasattr(model, "predict_proba") and hasattr(model, "classes_") else model.predict(rows)
        result = "predicted:" + hashlib.sha256(np.ascontiguousarray(scores, dtype=float).tobytes()).hexdigest()[:16]
    except ScoringError as exc:
        result = "refused:" + exc.code
    except Exception as exc:
        result = "error:" + type(exc).__name__
    print(json.dumps({"label": case["label"], "result": result}), flush=True)
"""


def _in_child(tmp_path: Path, cases: list[tuple[str, bytes, bytes]], flags: str = "",
              expect_crash: bool = False) -> dict[str, str]:
    """Load + predict each crafted package in a child process, so a regression shows up as a
    failed assertion (with the case that crashed) instead of killing the test worker."""

    specs = []
    for index, (label, package, manifest) in enumerate(cases):
        path = tmp_path / f"case{index}.zip"
        path.write_bytes(package)
        specs.append({"label": label, "path": str(path), "digest": mp.sha256_hex(manifest)})
    (tmp_path / "cases.json").write_text(json.dumps(specs))
    _frame(n=30, seed=4).to_csv(tmp_path / "rows.csv", index=False)
    env = {**os.environ, "PYTHONPATH": os.pathsep.join([str(API_ROOT), os.environ.get("PYTHONPATH", "")])}
    try:
        out = subprocess.run([sys.executable, "-c", _CHILD, str(tmp_path / "cases.json"), str(tmp_path / "rows.csv"),
                              flags], capture_output=True, text=True, env=env, timeout=600, cwd=API_ROOT.parents[1])
    except subprocess.TimeoutExpired:
        assert expect_crash, "child hung"
        return {}
    results = {}
    for line in out.stdout.splitlines():
        if line.startswith("{"):
            item = json.loads(line)
            results[item["label"]] = item["result"]
    crashed = [label for label, _p, _m in cases if label not in results][:1]
    if expect_crash:
        assert out.returncode != 0, f"expected the unprotected load to crash, got {results}"
        return results
    assert out.returncode == 0, f"child crashed (rc={out.returncode}) on {crashed}: {out.stderr[-1500:]}"
    return results


def _crafted_sklearn_cases() -> list[tuple[str, bytes, bytes]]:
    import skops.io as sio

    frame = _frame()
    cases = []
    def second_internal(nodes):
        return int(np.flatnonzero(nodes["left_child"] != -1)[1])

    edits = {  # tree_oob.py / sk_attacks.py of the security review, through the real forest loader
        "tree child out of range": lambda n: (n["left_child"].__setitem__(0, 10**9), n["right_child"].__setitem__(0, 10**9)),
        "tree split feature out of range": lambda n: n["feature"].__setitem__(0, 10**6),
        "tree self loop": lambda n: n["left_child"].__setitem__(0, 0),
        "tree back edge": lambda n: n["left_child"].__setitem__(second_internal(n), 1),
        "internal node without a right child": lambda n: n["right_child"].__setitem__(0, -1),
    }
    for label, edit in edits.items():
        family = "extra_trees_regressor" if "back edge" in label else "random_forest"
        task = "regression" if family.endswith("regressor") else "binary"
        pipeline = _fitted(task, family, frame, _target(task, frame))
        package, _manifest = _package(pipeline, family, task)
        tree = pipeline.named_steps["model"].estimators_[0].tree_
        state = tree.__getstate__()
        nodes = state["nodes"].copy()
        edit(nodes)
        tree.__setstate__({**state, "nodes": nodes})
        cases.append((label, *_signed(package, "estimator.skops", sio.dumps(pipeline.named_steps["model"]))))
    pipeline = _fitted("binary", "extra_trees", frame, _target("binary", frame))
    package, _manifest = _package(pipeline, "extra_trees")
    pipeline.named_steps["model"].estimators_ = []
    cases.append(("forest without trees", *_signed(package, "estimator.skops", sio.dumps(pipeline.named_steps["model"]))))
    pipeline = _fitted("binary", "gradient_boosting", frame, _target("binary", frame))
    package, _manifest = _package(pipeline, "gradient_boosting")
    model = pipeline.named_steps["model"]
    model.estimators_ = np.repeat(model.estimators_, 3, axis=1)  # 3 trees per stage, 1 output column
    cases.append(("boosting grid wider than its output", *_signed(package, "estimator.skops", sio.dumps(model))))
    pipeline = _fitted("binary", "logistic_regression", frame, _target("binary", frame))
    package, _manifest = _package(pipeline)
    prep = pipeline.named_steps["prep"]
    prep.named_transformers_["cat"].named_steps["onehot"]._n_features_outs = [1, 1]
    cases.append(("encoder output sizes lie", *_signed(package, "preprocessor.skops", sio.dumps(prep))))
    return cases


def _lgbm_edit(text: str, edit) -> str:
    """Edit the first tree of a LightGBM model text and re-tile ``tree_sizes`` to match."""

    import re

    start, end = text.index("\nTree=0\n") + 1, text.index("\nend of trees") + 1
    head, body, tail = text[:start], text[start:end], text[end:]
    sizes = [int(item) for item in re.search(r"tree_sizes=([0-9 ]+)", head).group(1).split()]
    blocks, offset = [], 0
    for size in sizes:
        blocks.append(body[offset : offset + size])
        offset += size
    blocks[0] = edit(blocks[0])
    head = re.sub(r"tree_sizes=[0-9 ]+", "tree_sizes=" + " ".join(str(len(block)) for block in blocks), head, count=1)
    return head + "".join(blocks) + tail


def _first(field: str, value: str):
    import re

    return lambda block: re.sub(rf"^{field}=(\S+)", f"{field}={value}", block, count=1, flags=re.M)


def _xgb_members(task: str = "multiclass") -> tuple[bytes, bytes]:
    frame = _frame()
    family = "xgboost_regressor" if task == "regression" else "xgboost"
    package, _manifest = _package(_fitted(task, family, frame, _target(task, frame)), family, task)
    return package, mp.read_package(package).members["estimator.xgb.json"]


def _lgbm_members(task: str = "multiclass") -> tuple[bytes, str]:
    frame = _frame()
    family = "lightgbm_regressor" if task == "regression" else "lightgbm"
    package, _manifest = _package(_fitted(task, family, frame, _target(task, frame)), family, task)
    return package, mp.read_package(package).members["estimator.lgbm.txt"].decode()


def _xgb_payloads(raw: bytes) -> dict[str, bytes]:
    """B1/B2 of the security re-review plus structural lies (each one tree / one field)."""

    original = json.loads(raw)
    tree = original["learner"]["gradient_booster"]["model"]["trees"][0]
    size = len(tree["split_indices"])

    def edit(change) -> bytes:
        model = json.loads(raw)
        change(model, model["learner"]["gradient_booster"]["model"]["trees"][0])
        return json.dumps(model).encode()

    good_split = json.dumps(tree["split_indices"]).replace(" ", "").encode()
    good_left = json.dumps(tree["left_children"]).replace(" ", "").encode()
    payloads = {f"xgb size_leaf_vector {value}": edit(lambda m, t, v=value: t["tree_param"].__setitem__(
        "size_leaf_vector", v)) for value in ("2", "3", "1000000", "-1")}  # B1
    payloads.update({
        "xgb escaped duplicate split_indices": raw.replace(b'"split_indices":' + good_split, b'"split_indices":'
            + json.dumps([2_000_000_000] * size).encode() + b',"split\\u005findices":' + good_split, 1),  # B2
        "xgb escaped duplicate left_children": raw.replace(b'"left_children":' + good_left, b'"left_children":'
            + json.dumps([10**6] * size).encode() + b',"left\\u005fchildren":' + good_left, 1),
        "xgb duplicate num_feature": raw.replace(b'"learner_model_param":{', b'"learner_model_param":{"num_feature":"1",', 1),
        "xgb unknown key": edit(lambda m, t: t.__setitem__("leaf_vector", [1.0])),
        "xgb NaN split condition": raw.replace(b'"split_conditions":[', b'"split_conditions":[NaN,', 1),
        "xgb float child index": edit(lambda m, t: t["left_children"].__setitem__(0, 1.0)),
        "xgb num_deleted": edit(lambda m, t: t["tree_param"].__setitem__("num_deleted", "999999")),
        "xgb child out of range": edit(lambda m, t: t["left_children"].__setitem__(0, 10**9)),
        "xgb leaf split index out of range": edit(lambda m, t: t["split_indices"].__setitem__(-1, 10**9)),
        "xgb parent does not point back": edit(lambda m, t: t["parents"].__setitem__(1, 5)),
        "xgb input width lies": edit(lambda m, t: m["learner"]["learner_model_param"].__setitem__("num_feature", "999")),
        "xgb tree count lies": edit(lambda m, t: m["learner"]["gradient_booster"]["model"][
            "gbtree_model_param"].__setitem__("num_trees", "999999")),
        "xgb iteration index lies": edit(lambda m, t: m["learner"]["gradient_booster"]["model"][
            "iteration_indptr"].__setitem__(1, 10**6)),
        "xgb objective changed": edit(lambda m, t: m["learner"].__setitem__("objective", {"name": "rank:ndcg"})),
    })
    return payloads


def _lgbm_payloads(text: str) -> dict[str, str]:
    """B3 of the security re-review (integers with exponents/decimals) plus structural lies."""

    sizes_line = next(line for line in text.splitlines() if line.startswith("tree_sizes="))
    payloads = {f"lgbm split_feature {value}": _lgbm_edit(text, _first("split_feature", value))
                for value in ("2000000000e-18", "99999999e-12", "+1", "0x1")}  # B3
    payloads["lgbm split_feature 1000000"] = _lgbm_edit(text, _first("split_feature", "1000000"))  # range bound
    payloads.update({f"lgbm left_child {value}": _lgbm_edit(text, _first("left_child", value))
                     for value in ("1e0", "1.7")})  # B3: cyclic nodes -> predict never ends
    payloads.update({
        "lgbm truncated": text[: len(text) // 2],
        "lgbm tree_sizes count lies": text.replace(sizes_line, sizes_line + " 7", 1),
        "lgbm num_class inflated": text.replace("num_class=4", "num_class=99999", 1),
        "lgbm max_feature_idx inflated": text.replace("max_feature_idx=", "max_feature_idx=9", 1),
        "lgbm duplicate header key": text.replace("num_class=", "num_class=50\nnum_class=", 1),
        "lgbm duplicate tree key": _lgbm_edit(text, lambda block: block.replace("left_child=", "left_child=999\nleft_child=", 1)),
        "lgbm unknown header key": text.replace("label_index=", "average_output\nlabel_index=", 1),
        "lgbm carriage return": text.replace("\nleft_child=", "\r\nleft_child=", 1),
        "lgbm num_leaves inflated": _lgbm_edit(text, _first("num_leaves", "99999999")),
        "lgbm child out of range": _lgbm_edit(text, _first("left_child", "1000000000")),
        "lgbm categorical section": _lgbm_edit(text, _first("num_cat", "1")),
        "lgbm leaf referenced twice": _lgbm_edit(text, _first("left_child", "-1")),
        "lgbm objective changed": text.replace("objective=multiclass", "objective=multiclassova", 1),
    })
    return payloads


def _crafted_native_cases() -> list[tuple[str, bytes, bytes]]:
    xgb_package, xgb_raw = _xgb_members()
    lgbm_package, lgbm_text = _lgbm_members()
    return [(label, *_signed(xgb_package, "estimator.xgb.json", data)) for label, data in _xgb_payloads(xgb_raw).items()] + [
        (label, *_signed(lgbm_package, "estimator.lgbm.txt", text.encode())) for label, text in _lgbm_payloads(lgbm_text).items()]


@pytest.mark.parametrize("task", ["binary", "multiclass", "regression"])
def test_native_validators_rebuild_valid_models_and_refuse_every_payload(task):
    """The validators alone (no library parse): each payload is refused; a real model is
    rebuilt idempotently. Drop the whitelist, the duplicate-key hook or strict integers and
    this fails."""

    frame = _frame()
    width = 3 + frame["c"].nunique() + frame["code"].nunique()  # numeric a, b, when + the one-hot columns
    n_xgb, n_lgbm = (4, 4) if task == "multiclass" else (0, 1)
    _package_bytes, raw = _xgb_members(task)
    canonical = checks.canonical_xgboost_json(raw, width, n_xgb, task)
    assert checks.canonical_xgboost_json(canonical, width, n_xgb, task) == canonical
    _package_bytes, text = _lgbm_members(task)
    rebuilt = checks.canonical_lightgbm_text(text.encode(), width, n_lgbm, task)
    assert checks.canonical_lightgbm_text(rebuilt, width, n_lgbm, task) == rebuilt
    assert b"parameters:" not in rebuilt and b"feature_importances" not in rebuilt  # tail sections dropped
    if task != "multiclass":
        return
    for label, data in _xgb_payloads(raw).items():
        with pytest.raises(checks.PackageCheckError):
            checks.canonical_xgboost_json(data, width, n_xgb, task)
    for label, payload in _lgbm_payloads(text).items():
        with pytest.raises(checks.PackageCheckError):
            checks.canonical_lightgbm_text(payload.encode(), width, n_lgbm, task)


def test_the_probe_refuses_what_a_validator_misses(tmp_path):
    """With the validators disabled (a simulated gap) the sandboxed probe still turns crashing
    and hanging native members into refusals; with the probe ALSO disabled the same payloads
    crash the process -- so removing the probe fails this test."""

    xgb_package, xgb_raw = _xgb_members("binary")
    lgbm_package, lgbm_text = _lgbm_members("binary")
    crashing = [
        ("xgb size_leaf_vector 3", *_signed(xgb_package, "estimator.xgb.json",
                                            _xgb_payloads(xgb_raw)["xgb size_leaf_vector 3"])),
        ("lgbm split_feature 2000000000e-18", *_signed(lgbm_package, "estimator.lgbm.txt", _lgbm_payloads(
            lgbm_text)["lgbm split_feature 2000000000e-18"].encode())),
    ]
    hanging = [("lgbm left_child 1e0", *_signed(lgbm_package, "estimator.lgbm.txt",
                                                  _lgbm_payloads(lgbm_text)["lgbm left_child 1e0"].encode()))]
    results = _in_child(tmp_path, crashing + hanging, flags="no-validators,short-timeout")
    assert results == {label: "refused:unsupported_model" for label, _p, _m in crashing + hanging}
    _in_child(tmp_path, crashing[:1], flags="no-validators,no-probe", expect_crash=True)


def test_crafted_members_are_refused_in_a_child_process(tmp_path):
    cases = _crafted_sklearn_cases() + _crafted_native_cases()
    results = _in_child(tmp_path, cases)
    assert results == {label: "refused:unsupported_model" for label, _p, _m in cases}


def _mutations(data: bytes, seed: int) -> list[tuple[str, bytes]]:
    rng = np.random.default_rng(seed)
    out = [(f"truncated at {share:.0%}", data[: max(1, int(len(data) * share))]) for share in (0.1, 0.5, 0.9, 0.999)]
    for index in range(4):
        flipped = bytearray(data)
        for position in rng.integers(0, len(data), 8):
            flipped[position] = int(rng.integers(0, 256))
        out.append((f"8 byte flips #{index}", bytes(flipped)))
    out.append(("numbers inflated", data.replace(b"1", b"99999999", 3)))
    return out


@pytest.mark.parametrize(("family", "member"), [
    ("xgboost", "estimator.xgb.json"), ("lightgbm", "estimator.lgbm.txt"), ("catboost", "estimator.cbm")])
def test_fuzzed_native_members_never_crash_the_worker(family, member, tmp_path):
    frame = _frame()
    package, _manifest = _package(_fitted("binary", family, frame, _target("binary", frame)), family)
    original = mp.read_package(package).members[member]
    cases = [(f"{family} {label}", *_signed(package, member, data)) for label, data in _mutations(original, seed=7)]
    results = _in_child(tmp_path, cases)
    assert all(value == "refused:unsupported_model" or value.startswith("predicted:") for value in results.values()), results
    # (Cutting only LightGBM's trailing parameter section is harmless: the rebuild drops it.)
    assert all(results[f"{family} truncated at {share:.0%}"] == "refused:unsupported_model" for share in (0.1, 0.5, 0.9))


def test_estimator_and_preprocessor_must_be_what_the_family_trains():
    import skops.io as sio
    from sklearn.preprocessing import StandardScaler

    frame = _frame()
    package, _manifest = _package(_fitted("binary", "random_forest", frame, _target("binary", frame)), "random_forest")
    contents = mp.read_package(package)
    wrong_family = _signed(package, "estimator.skops", contents.members["estimator.skops"], manifest_patch={
        "model": {**contents.manifest["model"], "family": "logistic_regression"}})
    scaler = StandardScaler().fit(frame[["a", "b"]].fillna(0))
    wrong_prep = _signed(package, "preprocessor.skops", sio.dumps(scaler))
    for payload, manifest in (wrong_family, wrong_prep):
        with pytest.raises(ScoringError) as caught:
            _load(payload, manifest)
        assert caught.value.code == "unsupported_model"
        assert isinstance(caught.value.__cause__, mp.ModelPackageError)


# --- the package is saved before the final test (security review SF-4) -------------------------


def test_a_packaging_failure_happens_before_the_final_test(tmp_path, monkeypatch):
    from app.engine.experiments import runner
    from app.engine.types import SearchConfig
    from test_ml2_pipeline_integrity import _integrity_frame, _integrity_task

    def no_safe_format(*_args, **_kwargs):
        raise mp.ModelPackageError("types outside the package allowlist: ['decimal.Decimal'] at /tmp/x")

    monkeypatch.setattr(runner, "build_package", no_safe_format)
    events: list[str] = []
    with pytest.raises(mp.ModelPackageError) as caught:
        runner.run_experiment(_integrity_frame(), _integrity_task(), SearchConfig(strategy="open_ingest",
                              max_candidates=4, seed=42), artifact_dir=tmp_path, on_event=lambda t, _p: events.append(t))
    assert "final_fit_completed" in events and "final_test_started" not in events  # the look is not spent
    # Internal detail stays in the log; the user sees one allowlisted plain sentence.
    assert str(caught.value) == mp.PACKAGE_NOT_SAVED and public_failure(str(caught.value)) == mp.PACKAGE_NOT_SAVED
    assert public_failure(mp.PACKAGE_TOO_LARGE) == mp.PACKAGE_TOO_LARGE
    assert public_failure("types outside the package allowlist at /tmp/x") == PUBLIC_FAILURE
    assert not (tmp_path / mp.PACKAGE_FILENAME).exists()


@pytest.mark.parametrize("kind", ["decimal", "date"])
def test_unsafe_categorical_values_from_parquet_fail_before_training(kind, tmp_path, monkeypatch):
    import datetime
    import decimal
    from dataclasses import replace

    from app.engine.experiments import runner
    from app.engine.types import SearchConfig
    from test_ml2_pipeline_integrity import _integrity_frame, _integrity_task

    values = {"decimal": [decimal.Decimal("1.50"), decimal.Decimal("2.25")],
              "date": [datetime.date(2020, 1, 1), datetime.date(2021, 1, 2)]}[kind]
    frame = _integrity_frame()
    frame["tier"] = pd.Series([values[index % 2] for index in range(len(frame))], dtype=object)
    path = tmp_path / "rows.parquet"
    frame.to_parquet(path)
    frame = pd.read_parquet(path)  # Parquet decimal / date32 columns arrive as Python objects
    holdout_calls: list[int] = []
    monkeypatch.setattr(runner, "_fit_and_score_holdout", lambda *a, **k: holdout_calls.append(1))
    task = replace(_integrity_task(), feature_groups={"features": ["measure", "segment", "tier"]}, column_roles={})
    events: list[str] = []
    with pytest.raises(ValueError) as caught:
        runner.run_experiment(frame, task, SearchConfig(strategy="open_ingest", max_candidates=4, seed=42),
                              artifact_dir=tmp_path, on_event=lambda t, _p: events.append(t))
    assert "candidate_started" not in events  # refused before any candidate is fitted
    assert str(caught.value) == mp.PACKAGE_UNSAFE_CATEGORY  # a plain sentence the user sees as is
    assert public_failure(str(caught.value)) == mp.PACKAGE_UNSAFE_CATEGORY
    assert holdout_calls == []
    assert mp.unsafe_category_types(["x", 1, 2.5, True, np.int64(3), None]) == []


def test_legacy_m1_loader_keeps_member_paths_inside_the_model_dir(tmp_path):
    from app.ml.predict import ModelNotTrainedError, load_model, reset_model_cache

    model_dir = tmp_path / "models"
    model_dir.mkdir()
    joblib.dump({"members": []}, model_dir / "model.joblib")
    sentinel = tmp_path / "pwned"
    joblib.dump(_Exploit(str(sentinel)), tmp_path / "outside.joblib")
    (model_dir / "metadata.json").write_text(json.dumps({"members": [{"id": "m", "artifact": "../outside.joblib"}]}))
    reset_model_cache()
    with pytest.raises(ModelNotTrainedError, match="outside the model directory"):
        load_model(model_dir)
    assert not sentinel.exists()
    reset_model_cache()


# --- the sandboxed probe: crash, hang, oversized output and a scrubbed environment -----------------


@pytest.mark.parametrize("script", ["import os; os.abort()", "import time; time.sleep(30)",
                                    "import sys; sys.stdout.write('x' * 5000)"])
def test_probe_crash_hang_and_oversized_output_are_refusals(script, monkeypatch):
    monkeypatch.setattr(pl, "_CHILD", script)
    monkeypatch.setattr(get_settings(), "model_probe_timeout_seconds", 2)
    monkeypatch.setattr(pl, "PROBE_OUTPUT_MAX_BYTES", 1000)
    with pytest.raises(mp.ModelPackageError, match="child"):
        pl._probe("lightgbm_text", "LGBMClassifier", b"model", np.zeros((2, 3)))


def test_a_timed_out_child_is_killed_with_its_session_and_its_files_removed(tmp_path, monkeypatch):
    marker = tmp_path / "workdir.txt"
    monkeypatch.setattr(pl, "_CHILD", (
        "import os, time; open(os.path.join(os.getcwd(), 'view.json'), 'w').write('x'); "
        f"open({str(marker)!r}, 'w').write(f'{{os.getcwd()}} {{os.getpid()}} {{os.getpgrp()}}'); time.sleep(30)"))
    monkeypatch.setattr(get_settings(), "model_probe_timeout_seconds", 2)
    with pytest.raises(mp.ModelPackageError, match="timed out"):
        pl._probe("catboost_cbm", "CatBoostClassifier", b"model", np.zeros((2, 3)))
    workdir, pid, group = marker.read_text().split()
    assert pid == group  # its own session / process group (killed as a group)
    assert not Path(workdir).exists()  # the temporary directory went with it


def test_probe_runs_without_the_parent_environment(monkeypatch):
    monkeypatch.setenv("DATABASE_URL", "postgresql://user:secret@db/prod")
    monkeypatch.setenv("AUTH_TOKEN_HASH_SECRET", "secret")
    monkeypatch.setattr(pl, "_CHILD", "import json, os, sys; sys.stdout.write(json.dumps(sorted(os.environ)))")
    names = set(json.loads(pl._probe("lightgbm_text", "LGBMClassifier", b"model", np.zeros((2, 3)))))
    assert "DATABASE_URL" not in names and "AUTH_TOKEN_HASH_SECRET" not in names
    assert names <= {"PATH", "OMP_NUM_THREADS", "MALLOC_ARENA_MAX", "TMPDIR", "LC_CTYPE", "__CF_USER_TEXT_ENCODING"}


def test_capped_artifact_reads_stop_at_the_limit(monkeypatch):
    from types import SimpleNamespace

    from app.services import artifact_service

    class Backend:
        read = []

        @staticmethod
        def get(_key):
            raise AssertionError("an uncapped read")

        @staticmethod
        def open(_key):
            from contextlib import contextmanager

            @contextmanager
            def opened():
                stream = io.BytesIO(b"x" * 10_000)
                yield SimpleNamespace(read=lambda size: Backend.read.append(size) or stream.read(size))
            return opened()

    monkeypatch.setattr(artifact_service, "get_artifact", lambda _db, **_k: SimpleNamespace(
        object_key="k", artifact_type="model"))
    monkeypatch.setattr(artifact_service, "storage_for_artifact", lambda _artifact, storage=None: Backend)
    with pytest.raises(artifact_service.ArtifactTooLargeError):
        artifact_service.read_artifact_bytes(None, workspace_id=None, artifact_id=None, max_bytes=100)
    assert Backend.read == [101]  # never more than the cap + 1 byte
    assert artifact_service.read_artifact_bytes(None, workspace_id=None, artifact_id=None, max_bytes=10_000) == b"x" * 10_000


# --- final security round: tiny pools, large CatBoost, worker memory, rebuilt bytes only --------


@pytest.mark.parametrize("task", ["binary", "multiclass", "regression"])
def test_lightgbm_on_a_tiny_training_pool_saves_loads_and_predicts_bit_for_bit(task):
    """Under ~40 rows LightGBM writes one-leaf trees (min_child_samples 20); they must save."""

    frame = _frame(n=30)
    family = "lightgbm_regressor" if task == "regression" else "lightgbm"
    pipeline = _fitted(task, family, frame, _target(task, frame))
    assert "\nnum_leaves=1\n" in pipeline.named_steps["model"].booster_.model_to_string()
    package, manifest = _package(pipeline, family, task)
    loaded = _load(package, manifest)
    n_classes = 4 if task == "multiclass" else None
    assert np.array_equal(_predict(pipeline, frame, task != "regression", n_classes),
                          _predict(loaded.pipeline, frame, task != "regression", n_classes))


def test_a_large_catboost_model_is_not_refused_by_the_probe_answer_cap(tmp_path, monkeypatch):
    """CatBoost's JSON view of a 30-class, 2,000-tree model (~3.5x its cbm, far above the old
    64 MiB answer cap) is validated inside the child; only a small summary comes back."""

    import catboost

    frame = _frame(n=300)
    y = np.arange(len(frame)) % 30
    pipeline = Pipeline([("prep", build_preprocessor(NUMERIC, CATEGORICAL)),
                         ("model", make_model("catboost", seed=3, task_type="multiclass",
                                              hyperparameters={"n_estimators": 20}))]).fit(frame, y)
    view = tmp_path / "view.json"
    pipeline.named_steps["model"].save_model(str(view), format="json")
    model = json.loads(view.read_text())
    model["oblivious_trees"] = (model["oblivious_trees"] * 100)[:2000]  # same trees, 2,000 of them
    view.write_text(json.dumps(model))
    big = catboost.CatBoostClassifier()
    big.load_model(str(view), format="json")
    pipeline.steps[-1] = ("model", big)
    package, manifest = _package(pipeline, "catboost", "multiclass")
    big.save_model(str(view), format="json")
    assert view.stat().st_size > 64 << 20  # the JSON view the old design streamed back through a 64 MiB cap
    monkeypatch.setattr(pl, "PROBE_OUTPUT_MAX_BYTES", 1024)  # the answer is a summary, not the view
    loaded = _load(package, manifest)
    assert loaded.pipeline.named_steps["model"].tree_count_ == 2000


@pytest.mark.parametrize(("kind", "data"), [
    ("xgboost_json", b"[" + b"{}," * 2_000_000 + b"{}]"),
    ("lightgbm_text", b"tree\n" + b"x=\n" * 2_000_000 + b"\nend of trees"),
])
def test_stored_native_bytes_are_parsed_in_the_child_not_the_worker(kind, data):
    """Adversarial shapes that cost ~20x their size to parse: the worker's own memory stays
    near the payload size (the parse happens in the memory-limited child) and they are refused."""

    import tracemalloc

    tracemalloc.start()
    try:
        with pytest.raises(mp.ModelPackageError, match="canonical child failed"):
            pl._canonical(kind, data, 8, 0 if kind == "xgboost_json" else 1, "binary")
        peak = tracemalloc.get_traced_memory()[1]
    finally:
        tracemalloc.stop()
    assert peak < 4 * len(data)  # in-process parsing would need ~20x


@pytest.mark.skipif(not sys.platform.startswith("linux"), reason="RLIMIT_AS is enforced on Linux only (macOS dev)")
def test_the_child_memory_limit_holds_on_linux(monkeypatch):
    monkeypatch.setattr(get_settings(), "model_probe_memory_bytes", 512 << 20)
    data = b"[" + b"[0.5,0.5,0.5,0.5]," * 4_000_000 + b"[]]"  # ~64 MiB that needs >1 GiB to parse
    with pytest.raises(mp.ModelPackageError, match="canonical child failed"):
        pl._canonical("xgboost_json", data, 8, 0, "binary")


def test_the_libraries_load_only_the_rebuilt_bytes(monkeypatch):
    import lightgbm
    import xgboost

    seen: dict[str, list[bytes]] = {"probe": [], "xgboost": [], "lightgbm": []}
    real_probe, real_load, real_booster = pl._probe, xgboost.XGBModel.load_model, lightgbm.Booster

    def probe(kind, name, data, rows, **kwargs):
        seen["probe"].append(bytes(data))
        return real_probe(kind, name, data, rows, **kwargs)

    def load(self, fname):
        seen["xgboost"].append(bytes(fname))
        return real_load(self, fname)

    def booster(*args, model_str=None, **kwargs):
        seen["lightgbm"].append(model_str.encode())
        return real_booster(*args, model_str=model_str, **kwargs)

    monkeypatch.setattr(pl, "_probe", probe)
    monkeypatch.setattr(xgboost.XGBModel, "load_model", load)
    monkeypatch.setattr(lightgbm, "Booster", booster)
    frame = _frame()
    width = 3 + frame["c"].nunique() + frame["code"].nunique()
    for family, member in (("xgboost", "estimator.xgb.json"), ("lightgbm", "estimator.lgbm.txt")):
        package, manifest = _package(_fitted("binary", family, frame, _target("binary", frame)), family)
        stored = mp.read_package(package).members[member]
        rebuild = checks.canonical_xgboost_json(stored, width, 0, "binary") if family == "xgboost" else (
            checks.canonical_lightgbm_text(stored, width, 1, "binary"))
        assert rebuild != stored
        for values in seen.values():
            values.clear()
        _load(package, manifest)
        assert seen["probe"] == [rebuild] and seen[family] == [rebuild]  # never the stored bytes


def test_equivalent_spellings_load_and_predict_identically(tmp_path):
    """Spellings the strict parser reads as the same model (an escaped key name, shuffled keys,
    lying LightGBM tree sizes, a garbage parameter tail) load and predict like the original --
    which is only true if the libraries get the rebuilt bytes, not the stored ones."""

    xgb_package, xgb_raw = _xgb_members("binary")
    lgbm_package, lgbm_text = _lgbm_members("binary")
    model = json.loads(xgb_raw)

    def reverse(value):
        if isinstance(value, dict):
            return {key: reverse(value[key]) for key in reversed(list(value))}
        return [reverse(item) for item in value] if isinstance(value, list) else value

    sizes_line = next(line for line in lgbm_text.splitlines() if line.startswith("tree_sizes="))
    lying = "tree_sizes=" + " ".join("1" for _ in sizes_line.split("=")[1].split())
    end = lgbm_text.index("end of trees") + len("end of trees")
    cases = [
        ("xgb baseline", *_signed(xgb_package, "estimator.xgb.json", xgb_raw)),
        ("xgb escaped key name", *_signed(xgb_package, "estimator.xgb.json",
                                          xgb_raw.replace(b'"split_indices"', b'"split\\u005findices"', 1))),
        ("xgb shuffled keys", *_signed(xgb_package, "estimator.xgb.json", json.dumps(reverse(model)).encode())),
        ("lgbm baseline", *_signed(lgbm_package, "estimator.lgbm.txt", lgbm_text.encode())),
        ("lgbm lying tree sizes", *_signed(lgbm_package, "estimator.lgbm.txt",
                                           lgbm_text.replace(sizes_line, lying, 1).encode())),
        ("lgbm garbage tail", *_signed(lgbm_package, "estimator.lgbm.txt",
                                       (lgbm_text[:end] + "\n\nparameters:\n[[[ not a model ]]]\x01\n").encode())),
    ]
    results = _in_child(tmp_path, cases)
    assert all(value.startswith("predicted:") for value in results.values()), results
    assert results["xgb escaped key name"] == results["xgb shuffled keys"] == results["xgb baseline"]
    assert results["lgbm lying tree sizes"] == results["lgbm garbage tail"] == results["lgbm baseline"]


def test_crafted_catboost_models_are_refused_by_the_json_view_check(tmp_path):
    """Built through CatBoost's own JSON loader, so CatBoost accepts them; only
    check_catboost_json (inside the probe child) refuses them."""

    import catboost

    frame = _frame()
    pipeline = _fitted("binary", "catboost", frame, _target("binary", frame))
    package, _manifest = _package(pipeline, "catboost")
    view = tmp_path / "view.json"
    pipeline.named_steps["model"].save_model(str(view), format="json")
    original = json.loads(view.read_text())
    edits = {
        "catboost feature_index beyond the width": lambda m: m["features_info"]["float_features"][-1].update(
            feature_index=60, flat_feature_index=60),  # small enough for CatBoost, outside our 9 inputs
        "catboost split_index out of range": lambda m: m["oblivious_trees"][0]["splits"][0].__setitem__(
            "split_index", sum(len(f["borders"]) for f in m["features_info"]["float_features"]) + 3),
    }
    cases = []
    for label, edit in edits.items():
        model = json.loads(json.dumps(original))
        edit(model)
        view.write_text(json.dumps(model))
        crafted = catboost.CatBoostClassifier()
        crafted.load_model(str(view), format="json")  # CatBoost itself accepts it
        cbm = tmp_path / "crafted.cbm"
        crafted.save_model(str(cbm), format="cbm")
        cases.append((label, *_signed(package, "estimator.cbm", cbm.read_bytes())))
    assert _in_child(tmp_path, cases) == {label: "refused:unsupported_model" for label in edits}


# --- ML-correctness round: refusal branches, test-row isolation, size limits, threshold ---------


def _run_integrity(tmp_path, events: list[str] | None = None):
    from app.engine.experiments import runner
    from app.engine.types import SearchConfig
    from test_ml2_pipeline_integrity import _integrity_frame, _integrity_task

    return runner.run_experiment(_integrity_frame(), _integrity_task(), SearchConfig(
        strategy="open_ingest", max_candidates=4, seed=42), artifact_dir=tmp_path,
        on_event=(lambda t, _p: events.append(t)) if events is not None else None)


@pytest.mark.parametrize("failure", ["load-back", "prediction mismatch"])
def test_a_package_that_does_not_load_back_identically_fails_before_the_final_test(failure, tmp_path, monkeypatch,
                                                                                   caplog):
    real = pl.load_model_package

    class Shifted:
        def __init__(self, inner):
            self.inner = inner

        def predict_proba(self, X):
            return self.inner.predict_proba(X) + 1e-12

        def predict(self, X):
            return self.inner.predict(X)

    def broken(payload, *, manifest_digest):
        if failure == "load-back":
            raise ScoringError("unsupported_model", pl.MODEL_MESSAGE)
        loaded = real(payload, manifest_digest=manifest_digest)
        return pl.LoadedModel(Shifted(loaded.pipeline), loaded.manifest, loaded.saved_format)

    monkeypatch.setattr(pl, "load_model_package", broken)
    events: list[str] = []
    with caplog.at_level("WARNING", logger="app.engine.experiments.runner"), pytest.raises(mp.ModelPackageError) as caught:
        _run_integrity(tmp_path, events)
    assert str(caught.value) == mp.PACKAGE_NOT_SAVED
    assert "final_fit_completed" in events and "final_test_started" not in events
    expected = "could not be loaded back" if failure == "load-back" else "does not reproduce"
    assert any(expected in record.getMessage() for record in caplog.records)  # cause chain, server side


def test_packaging_never_reads_a_test_row(tmp_path, monkeypatch):
    """Every frame the runner predicts on before ``final_test_started`` -- CV, the final fit's
    training metrics and the package round trip -- holds training rows only."""

    from app.engine.experiments import runner

    real, seen, testing = runner._predict, [], []

    def spy(model, X, *args, **kwargs):
        if not testing:
            seen.append(X)
        return real(model, X, *args, **kwargs)

    monkeypatch.setattr(runner, "_predict", spy)
    events: list[str] = []

    def on_event(event_type, _payload):
        events.append(event_type)
        if event_type == "final_test_started":
            testing.append(True)

    from app.engine.types import SearchConfig
    from test_ml2_pipeline_integrity import _integrity_frame, _integrity_task

    runner.run_experiment(_integrity_frame(), _integrity_task(), SearchConfig(strategy="open_ingest", max_candidates=4,
                          seed=42), artifact_dir=tmp_path, on_event=on_event)
    assert events.index("model_package_saved") < events.index("final_test_started")
    frames = [X for X in seen if isinstance(X, pd.DataFrame) and {"segment", "measure"} <= set(X.columns)]
    assert len(frames) == len(seen) > 0  # every prediction input carries the marked columns
    for X in frames:  # the integrity frame marks every test row with these values
        assert not (X["segment"] == "TEST_ONLY").any() and not (X["measure"] == 1_000_000_000.0).any()


def test_a_forest_above_the_cap_is_refused_from_its_node_counts_before_serializing(monkeypatch):
    frame = _frame()
    pipeline = _fitted("regression", "extra_trees_regressor", frame, _target("regression", frame))
    estimate = mp._estimated_tree_bytes(pipeline.named_steps["model"])
    assert estimate > 0
    monkeypatch.setattr(mp, "ESTIMATOR_MAX_BYTES", estimate - 1)
    monkeypatch.setattr(mp, "_skops_dumps", lambda _obj: pytest.fail("serialized before refusing"))
    with pytest.raises(mp.ModelPackageError) as caught:
        mp.build_package(pipeline, family="extra_trees_regressor",
                         task_type="regression", candidate_id="c", training_dtypes={}, feature_actions=[],
                         target="y", cv={})
    assert str(caught.value) == mp.PACKAGE_TOO_LARGE


def test_forest_tree_overrides_are_checked_against_the_pool_when_the_branch_is_planned():
    from types import SimpleNamespace

    from app.domain.errors import InvalidChangeSetError
    from app.services.experiment_branch_service import _hyperparameter

    def change(trees: int):
        return SimpleNamespace(family="random_forest", parameters={"n_estimators": trees})

    assert _hyperparameter(change(80), 0, "binary", ["random_forest"], n_train=10_000) == {"n_estimators": 80}
    with pytest.raises(InvalidChangeSetError) as caught:
        _hyperparameter(change(2000), 0, "binary", ["random_forest"], n_train=10_000)
    assert "too large to save safely" in str(caught.value) and "at most 1750 trees" in str(caught.value)


def test_the_manifest_carries_the_locked_threshold_and_the_run_records_packaging(tmp_path):
    events: list[str] = []
    result = _run_integrity(tmp_path, events)
    manifest = mp.read_package((tmp_path / mp.PACKAGE_FILENAME).read_bytes()).manifest
    locked = (result.get("decision_threshold") or {}).get("value")
    assert manifest["decision_threshold"] == pytest.approx(locked if locked is not None else 0.5)
    assert "model_package_saved" in events
    assert any(row.get("stage") == "model_packaging" for row in result["execution_stage_timings"])
    # V7 B4: the load-back checks are summarised for persistence, tied to the exact members checked.
    checked = result["package_verification"]
    from test_ml2_pipeline_integrity import _integrity_frame

    n_pool = len(_integrity_frame()) - int(result["best_single"]["n_test_rows"])
    assert checked["parity"] == {"rows": min(mp.PARITY_ROWS, n_pool), "row_source": "training_partition",
                                 "comparison": "exact", "tolerance": 0.0, "max_abs_difference": 0.0,
                                 "output": "positive_class_probability", "passed": True}
    assert (checked["format"], checked["opens"], checked["digests"]) == (mp.VERIFICATION_FORMAT, True, True)
    assert checked["members_digest"] == mp.members_digest(manifest) and "verification" not in manifest


def test_build_time_verification_is_stamped_only_for_the_members_it_checked():
    frame = _frame()
    pipeline = _fitted("binary", "logistic_regression", frame, _target("binary", frame))
    raw = mp.build_package(pipeline, family="logistic_regression", task_type="binary", candidate_id="c",
                           training_dtypes={}, feature_actions=ACTIONS, target="y", cv={})
    summary = mp.verification_summary(mp.read_package(raw).manifest, rows=100, max_abs_difference=0.0,
                                      output="positive_class_probability")
    stamped, manifest_bytes = mp.finalize_package(raw, training_run_id="r", feature_set_version_id=None,
                                                  availability={}, verification=summary)
    contents = mp.read_package(stamped)
    assert contents.manifest["verification"] == summary and contents.members == mp.read_package(raw).members
    assert _load(stamped, manifest_bytes).pipeline is not None  # the stamped manifest still loads
    elsewhere = {**summary, "members_digest": "0" * 64}  # a summary of other members is never stamped
    plain, _ = mp.finalize_package(raw, training_run_id="r", feature_set_version_id=None, availability={},
                                   verification=elsewhere)
    assert "verification" not in mp.read_package(plain).manifest


def test_benchmark_predictions_use_the_model_package(tmp_path):
    from types import SimpleNamespace

    from benchmarks.case_studies.predictions import score_frame as benchmark_score

    result = _run_integrity(tmp_path)
    from test_ml2_pipeline_integrity import _integrity_frame

    frame = _integrity_frame().head(20)
    experiment = SimpleNamespace(id="x", result=result, artifact_dir=str(tmp_path))
    scores = benchmark_score(experiment, frame, task_type="binary")
    payload = (tmp_path / mp.PACKAGE_FILENAME).read_bytes()
    model = _load(payload, mp.read_package(payload).manifest_bytes).pipeline
    features = result["best_single"]["features"]
    assert np.array_equal(scores, model.predict_proba(frame.loc[:, features])[:, 1])
