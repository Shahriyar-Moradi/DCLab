"""Provider-aware object storage: match Artifact.provider, never reinterpret."""

from __future__ import annotations

import hashlib
from types import SimpleNamespace
from uuid import uuid4

import pytest

from app.storage.exceptions import (
    DigestMismatchError,
    ObjectNotFoundError,
    StorageConfigurationError,
)
from app.storage.factory import StorageRegistry, storage_for_artifact, storage_for_provider
from app.storage.local import LocalStorage
from app.storage.materialize import materialize_object
from test_dataset_materialization import RemoteLikeStorage


class GcsLikeStorage(RemoteLikeStorage):
    provider = "gcs"
    bucket = "gcs-bucket"


def test_registry_resolves_matching_local_provider(tmp_path):
    storage = LocalStorage(tmp_path / "store")
    registry = StorageRegistry(configured=storage)
    assert registry.for_provider("local") is storage
    artifact = SimpleNamespace(provider="local", bucket=None)
    assert registry.for_artifact(artifact) is storage
    assert storage_for_artifact(artifact, storage=storage) is storage


def test_s3_artifact_is_rejected_on_gcs_backend(tmp_path):
    gcs = GcsLikeStorage(tmp_path / "gcs")
    registry = StorageRegistry(configured=gcs)
    with pytest.raises(StorageConfigurationError, match="artifact is s3") as mismatch:
        registry.for_provider("s3", bucket="other")
    assert mismatch.value.artifact_provider == "s3"
    assert mismatch.value.configured_provider == "gcs"
    with pytest.raises(StorageConfigurationError, match="provider mismatch"):
        storage_for_provider("s3", storage=gcs)


def test_bucket_mismatch_on_same_provider_is_rejected(tmp_path):
    storage = RemoteLikeStorage(tmp_path / "s3")
    with pytest.raises(StorageConfigurationError, match="bucket mismatch"):
        StorageRegistry(configured=storage).for_provider("s3", bucket="other-bucket")


def test_correct_provider_materializes_object(tmp_path):
    api_store = tmp_path / "api-host-A"
    worker_tmp = tmp_path / "worker-B"
    api_store.mkdir()
    worker_tmp.mkdir()
    payload = b"channel,spend\nemail,40\n"
    storage = RemoteLikeStorage(api_store)
    put = storage.put("tenants/a/file.csv", payload)
    backend = storage_for_artifact(
        SimpleNamespace(provider=put.provider, bucket=put.bucket),
        storage=storage,
    )
    with materialize_object(
        backend,
        put.key,
        expected_digest=put.content_digest,
        filename="file.csv",
        work_dir=worker_tmp,
    ) as path:
        assert path.is_file()
        assert path.read_bytes() == payload
        assert path.is_relative_to(worker_tmp)


def test_provider_mismatch_does_not_open_or_leave_temp_files(tmp_path):
    api_store = tmp_path / "api-host-A"
    worker_tmp = tmp_path / "worker-B"
    api_store.mkdir()
    worker_tmp.mkdir()
    s3 = RemoteLikeStorage(api_store)
    put = s3.put("tenants/a/file.csv", b"channel,spend\nemail,40\n")
    gcs = GcsLikeStorage(tmp_path / "gcs")
    with pytest.raises(StorageConfigurationError):
        backend = storage_for_artifact(
            SimpleNamespace(provider=put.provider, bucket=put.bucket),
            storage=gcs,
        )
        with materialize_object(backend, put.key, work_dir=worker_tmp):
            raise AssertionError("must not materialize on provider mismatch")
    assert [p for p in worker_tmp.rglob("*") if p.is_file()] == []
    assert (api_store / put.key).is_file()


def test_missing_object_raises(tmp_path):
    worker_tmp = tmp_path / "worker-B"
    worker_tmp.mkdir()
    storage = RemoteLikeStorage(tmp_path / "api-host-A")
    with pytest.raises(ObjectNotFoundError, match="missing.csv"):
        with materialize_object(
            storage,
            "tenants/a/missing.csv",
            filename="missing.csv",
            work_dir=worker_tmp,
        ):
            raise AssertionError("must not yield a missing object")
    assert [p for p in worker_tmp.rglob("*") if p.is_file()] == []


def test_digest_mismatch_deletes_temp_and_keeps_canonical_object(tmp_path):
    api_store = tmp_path / "api-host-A"
    worker_tmp = tmp_path / "worker-B"
    api_store.mkdir()
    worker_tmp.mkdir()
    storage = RemoteLikeStorage(api_store)
    put = storage.put("tenants/a/file.csv", b"channel,spend\nemail,40\n")
    with pytest.raises(DigestMismatchError):
        with materialize_object(
            storage,
            put.key,
            expected_digest="0" * 64,
            filename="file.csv",
            work_dir=worker_tmp,
        ):
            raise AssertionError("must not yield on digest mismatch")
    assert (api_store / put.key).is_file()
    assert [p for p in worker_tmp.rglob("*") if p.is_file()] == []


def test_remote_temp_copy_is_deleted_after_materialize(tmp_path):
    api_store = tmp_path / "api-host-A"
    worker_tmp = tmp_path / "worker-B"
    api_store.mkdir()
    worker_tmp.mkdir()
    payload = b"a,b\n1,2\n"
    digest = hashlib.sha256(payload).hexdigest()
    storage = RemoteLikeStorage(api_store)
    put = storage.put(f"workspaces/{uuid4()}/file.csv", payload)
    materialized = None
    with materialize_object(
        storage,
        put.key,
        expected_digest=digest,
        filename="file.csv",
        work_dir=worker_tmp,
    ) as path:
        materialized = path
        assert path.is_file()
    assert materialized is not None
    assert not materialized.exists()
    assert (api_store / put.key).is_file()
    assert [p for p in worker_tmp.rglob("*") if p.is_file()] == []
