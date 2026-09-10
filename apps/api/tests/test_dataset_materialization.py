"""Object-storage-native dataset materialization.

API host store A and worker temp B are distinct. Canonical bytes live in the
object provider. Workers stream into B, train, and delete B.
"""

from __future__ import annotations

import hashlib
from contextlib import contextmanager
from pathlib import Path
from uuid import uuid4

import pandas as pd
import pytest

from adaptive_modeling.fixtures import ordinary_binary
from adaptive_modeling.production import disable_background_job, post_labs_csv
from app.db.models import Artifact, ClientLabUpload, Dataset
from app.engine.data.loaders import load_table
from app.services.auto_train_service import run_auto_train_job
from app.services.dataset_materialization import materialize_client_upload, materialize_dataset
from app.storage.base import ObjectMetadata, ObjectPutResult
from app.storage.exceptions import DigestMismatchError
from app.storage.local import LocalStorage
from app.storage.materialize import materialize_object, object_location_uri


class RemoteLikeStorage:
    """Filesystem-backed object store that pretends to be remote (no local_path)."""

    provider = "s3"
    bucket = "fake-bucket"

    def __init__(self, root: str | Path) -> None:
        self.root = Path(root)
        self._inner = LocalStorage(root)

    def put(self, key, data, *, content_type=None, metadata=None):
        result = self._inner.put(
            key, data, content_type=content_type, metadata=metadata
        )
        return ObjectPutResult(
            key=result.key,
            size_bytes=result.size_bytes,
            content_digest=result.content_digest,
            content_type=result.content_type,
            provider=self.provider,
            bucket=self.bucket,
        )

    def get(self, key: str) -> bytes:
        raise AssertionError("materialize must stream via open(), not get()")

    def open(self, key: str):
        return self._inner.open(key)

    def exists(self, key: str) -> bool:
        return self._inner.exists(key)

    def delete(self, key: str) -> None:
        self._inner.delete(key)

    def metadata(self, key: str) -> ObjectMetadata:
        meta = self._inner.metadata(key)
        return ObjectMetadata(
            key=meta.key,
            size_bytes=meta.size_bytes,
            content_digest=meta.content_digest,
            content_type=meta.content_type,
            provider=self.provider,
            bucket=self.bucket,
        )

    def signed_url(self, key: str, *, expires_in: int = 3600) -> str:
        return self._inner.signed_url(key, expires_in=expires_in)

    def checksum(self, key: str) -> str:
        return self._inner.checksum(key)

    def local_path(self, key: str) -> str | None:
        del key
        return None


def _patch_object_storage(monkeypatch, storage: RemoteLikeStorage) -> None:
    monkeypatch.setattr(
        "app.services.client_lab_upload_service.get_object_storage",
        lambda: storage,
    )
    monkeypatch.setattr(
        "app.storage.factory.get_object_storage",
        lambda: storage,
    )


def test_local_materialize_reuses_provider_path_and_does_not_delete(tmp_path):
    api_store = tmp_path / "api-host-A"
    worker_tmp = tmp_path / "worker-B"
    api_store.mkdir()
    worker_tmp.mkdir()
    payload = b"channel,spend\nemail,40\n"
    storage = LocalStorage(api_store)
    put = storage.put("tenants/a/file.csv", payload)
    local = Path(storage.local_path(put.key))
    with materialize_object(
        storage,
        put.key,
        expected_digest=put.content_digest,
        work_dir=worker_tmp,
    ) as path:
        assert path.resolve() == local.resolve()
        assert path.is_relative_to(api_store)
        assert list(worker_tmp.iterdir()) == []
    assert local.is_file()
    assert local.read_bytes() == payload


def test_remote_materialize_streams_to_worker_tmp_then_deletes(tmp_path):
    api_store = tmp_path / "api-host-A"
    worker_tmp = tmp_path / "worker-B"
    api_store.mkdir()
    worker_tmp.mkdir()
    payload = b"channel,spend\nemail,40\n"
    digest = hashlib.sha256(payload).hexdigest()
    storage = RemoteLikeStorage(api_store)
    put = storage.put("tenants/a/file.csv", payload)
    canonical = api_store / put.key
    assert canonical.is_file()
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
        assert path.is_relative_to(worker_tmp)
        assert path.resolve() != canonical.resolve()
        assert load_table(path).shape[0] == 1
    assert materialized is not None
    assert not materialized.exists()
    assert canonical.is_file()
    assert canonical.read_bytes() == payload
    assert [p for p in worker_tmp.rglob("*") if p.is_file()] == []


def test_remote_digest_mismatch_deletes_temp_and_keeps_object(tmp_path):
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


def test_worker_trains_from_fake_remote_without_api_filesystem(
    auth_client, db_session, tmp_path, monkeypatch
):
    api_store = tmp_path / "api-host-A"
    worker_tmp = tmp_path / "worker-B"
    api_store.mkdir()
    worker_tmp.mkdir()
    storage = RemoteLikeStorage(api_store)
    _patch_object_storage(monkeypatch, storage)
    disable_background_job(monkeypatch)

    created = post_labs_csv(
        auth_client,
        ordinary_binary(n=80),
        filename="remote_train.csv",
        target="outcome",
    )
    assert created.status_code == 200, created.text
    upload = db_session.get(ClientLabUpload, created.json()["id"])
    assert upload is not None
    dataset = db_session.get(Dataset, upload.dataset_id)
    artifact = db_session.get(Artifact, upload.artifact_id)
    assert dataset is not None
    assert artifact is not None
    assert upload.stored_path.startswith("object://s3/")
    assert dataset.location == upload.stored_path
    assert dataset.location == object_location_uri(artifact.provider, artifact.object_key)
    assert not Path(upload.stored_path).is_file()
    canonical = api_store / artifact.object_key
    assert canonical.is_file()
    assert storage.local_path(artifact.object_key) is None

    bogus_api_path = tmp_path / "api-node-only" / "missing.csv"
    upload.stored_path = str(bogus_api_path)
    db_session.commit()

    seen: dict[str, object] = {}

    @contextmanager
    def _watch_materialize(db, row, **kwargs):
        kwargs.setdefault("work_dir", worker_tmp)
        with materialize_client_upload(db, row, storage=storage, **kwargs) as path:
            seen["path"] = path
            seen["exists"] = path.is_file()
            seen["under_worker"] = path.is_relative_to(worker_tmp)
            seen["under_api_store"] = path.is_relative_to(api_store)
            yield path

    monkeypatch.setenv("DCLAB_DATASET_WORK_DIR", str(worker_tmp))
    monkeypatch.setattr(
        "app.services.auto_train_service.materialize_client_upload",
        _watch_materialize,
    )
    run_auto_train_job(db_session, upload.id)
    db_session.refresh(upload)
    assert upload.pipeline_status == "completed", upload.pipeline_log
    assert seen.get("exists") is True
    assert seen.get("under_worker") is True
    assert seen.get("under_api_store") is False
    materialized = seen["path"]
    assert isinstance(materialized, Path)
    assert not materialized.exists()
    assert canonical.is_file()
    assert [p for p in worker_tmp.rglob("*") if p.is_file()] == []
    assert not bogus_api_path.is_file()


def test_materialize_dataset_prefers_artifact_over_legacy_location(
    db_session, tmp_path
):
    api_store = tmp_path / "api-host-A"
    worker_tmp = tmp_path / "worker-B"
    api_store.mkdir()
    worker_tmp.mkdir()
    storage = RemoteLikeStorage(api_store)
    payload = b"a,b\n1,2\n3,4\n"
    key = f"workspaces/{uuid4()}/artifacts/{uuid4()}/probe.csv"
    put = storage.put(key, payload)

    from app.db.models import DEFAULT_WORKSPACE_ID
    from app.services.artifact_service import record_artifact
    from app.services.lab_service import ingest_dataset, seed_dogfood

    env = seed_dogfood(db_session)
    stale = tmp_path / "stale.csv"
    stale.write_text("stale,col\n0,0\n", encoding="utf-8")
    artifact = record_artifact(
        db_session,
        artifact_id=uuid4(),
        workspace_id=DEFAULT_WORKSPACE_ID,
        artifact_type="dataset",
        put=put,
        mime_type="text/csv",
    )
    dataset = ingest_dataset(
        db_session,
        environment=env,
        name="probe",
        location=str(stale),
        source_type="csv",
        workspace_id=DEFAULT_WORKSPACE_ID,
        artifact_id=artifact.id,
    )

    with materialize_dataset(
        dataset, db=db_session, storage=storage, work_dir=worker_tmp
    ) as path:
        frame = load_table(path)
        assert list(frame.columns) == ["a", "b"]
        assert len(frame) == 2
        assert path.is_relative_to(worker_tmp)
    assert not path.exists()
    assert stale.read_text(encoding="utf-8").startswith("stale")
