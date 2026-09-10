"""Canonical dataset loading: Dataset.artifact_id → Artifact → ObjectStorage.

`materialize_dataset` is the application abstraction used by preview, ingest
profiling, and training. Bytes stay in object storage; PostgreSQL holds
lineage only. `Dataset.location` is legacy compatibility.
"""

from __future__ import annotations

from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

from sqlalchemy.orm import Session

from app.db.models import Artifact, ClientLabUpload, Dataset
from app.storage._hashing import sha256_file
from app.storage.base import ObjectStorage
from app.storage.exceptions import DigestMismatchError, ObjectNotFoundError, ObjectStorageError
from app.storage.factory import storage_for_artifact, storage_for_provider
from app.storage.materialize import (
    materialize_object,
    parse_object_location,
)


def _verify_legacy_digest(path: Path, expected_digest: str | None) -> None:
    if not expected_digest:
        return
    actual = sha256_file(path)
    if actual != expected_digest:
        raise DigestMismatchError(key=str(path), expected=expected_digest, actual=actual)


@contextmanager
def materialize_dataset(
    dataset: Dataset,
    *,
    db: Session | None = None,
    artifact: Artifact | None = None,
    storage: ObjectStorage | None = None,
    work_dir: str | Path | None = None,
) -> Iterator[Path]:
    """Yield a local path for `dataset` and delete any remote temp copy on exit."""

    row = artifact
    if row is None and dataset.artifact_id is not None:
        if db is None:
            raise ObjectStorageError("materialize_dataset requires db or artifact")
        row = db.get(Artifact, dataset.artifact_id)
        if row is None:
            raise ObjectNotFoundError(str(dataset.artifact_id))

    if row is not None:
        backend = storage_for_artifact(row, storage=storage)
        expected = row.content_digest or dataset.content_digest
        filename = Path(row.object_key).name
        with materialize_object(
            backend,
            row.object_key,
            expected_digest=expected,
            filename=filename,
            work_dir=work_dir,
        ) as path:
            yield path
        return

    location = dataset.location or ""
    parsed = parse_object_location(location)
    if parsed is not None:
        provider, key = parsed
        backend = storage_for_provider(provider, storage=storage)
        with materialize_object(
            backend,
            key,
            expected_digest=dataset.content_digest,
            filename=key,
            work_dir=work_dir,
        ) as path:
            yield path
        return

    path = Path(location)
    if not path.is_file():
        raise ObjectStorageError(
            f"dataset {dataset.id} has no Artifact and location is not a local file"
        )
    _verify_legacy_digest(path, dataset.content_digest)
    yield path


@contextmanager
def materialize_client_upload(
    db: Session,
    upload: ClientLabUpload,
    *,
    storage: ObjectStorage | None = None,
    work_dir: str | Path | None = None,
) -> Iterator[Path]:
    """Prefer Dataset.artifact_id; fall back to upload artifact, then stored_path."""

    dataset: Dataset | None = None
    if upload.dataset_id is not None:
        dataset = db.get(Dataset, upload.dataset_id)
    if dataset is not None and (
        dataset.artifact_id is not None or parse_object_location(dataset.location or "")
    ):
        with materialize_dataset(
            dataset, db=db, storage=storage, work_dir=work_dir
        ) as path:
            yield path
        return

    if upload.artifact_id is not None:
        artifact = db.get(Artifact, upload.artifact_id)
        if artifact is not None:
            backend = storage_for_artifact(artifact, storage=storage)
            with materialize_object(
                backend,
                artifact.object_key,
                expected_digest=artifact.content_digest,
                filename=upload.original_filename or artifact.object_key,
                work_dir=work_dir,
            ) as path:
                yield path
            return

    legacy = Path(upload.stored_path)
    if legacy.is_file():
        yield legacy
        return
    raise ObjectStorageError(
        f"upload {upload.id} has no Artifact and stored_path is not a local file"
    )
