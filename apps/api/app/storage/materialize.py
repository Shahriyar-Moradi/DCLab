"""Stream an object-storage blob to a local path the current process can read.

Workers never depend on another node's filesystem. Local providers may yield
the provider path in place. Remote providers download into process-local
temporary storage and delete that copy on exit.
"""

from __future__ import annotations

import os
import tempfile
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

from app.storage._hashing import sha256_file, write_and_hash
from app.storage.base import ObjectStorage
from app.storage.exceptions import DigestMismatchError, ObjectNotFoundError

OBJECT_URI_PREFIX = "object://"
_WORK_DIR_ENV = "DCLAB_DATASET_WORK_DIR"


def object_location_uri(provider: str, key: str) -> str:
    """Legacy Dataset.location / ClientLabUpload.stored_path when no local path exists."""

    return f"{OBJECT_URI_PREFIX}{provider}/{key}"


def parse_object_location(location: str) -> tuple[str, str] | None:
    if not location.startswith(OBJECT_URI_PREFIX):
        return None
    rest = location[len(OBJECT_URI_PREFIX) :]
    provider, sep, key = rest.partition("/")
    if not sep or not provider or not key:
        return None
    return provider, key


def resolve_work_dir(work_dir: str | Path | None = None) -> Path | None:
    if work_dir is not None:
        return Path(work_dir)
    raw = os.environ.get(_WORK_DIR_ENV)
    if raw:
        return Path(raw)
    return None


def _suffix_for(key: str, filename: str | None, suffix: str | None) -> str:
    if suffix:
        return suffix if suffix.startswith(".") else f".{suffix}"
    name = filename or key
    return Path(name).suffix


@contextmanager
def materialize_object(
    storage: ObjectStorage,
    key: str,
    *,
    expected_digest: str | None = None,
    suffix: str | None = None,
    filename: str | None = None,
    work_dir: str | Path | None = None,
) -> Iterator[Path]:
    """Yield a filesystem path for `key` without buffering the object in memory.

    Local providers reuse `storage.local_path` and leave that file in place.
    Remote providers stream into a temporary file under `work_dir` (or the
    process temp dir) and delete it when the context exits.
    """

    local = storage.local_path(key)
    if local is not None:
        path = Path(local)
        if not path.is_file():
            raise ObjectNotFoundError(key)
        actual = sha256_file(path)
        if expected_digest and actual != expected_digest:
            raise DigestMismatchError(key=key, expected=expected_digest, actual=actual)
        yield path
        return

    parent = resolve_work_dir(work_dir)
    if parent is not None:
        parent.mkdir(parents=True, exist_ok=True)
    fd, name = tempfile.mkstemp(
        prefix="dclab-ds-",
        suffix=_suffix_for(key, filename, suffix),
        dir=str(parent) if parent is not None else None,
    )
    tmp_path = Path(name)
    try:
        with os.fdopen(fd, "wb") as dest, storage.open(key) as src:
            _, actual = write_and_hash(dest, src)
        if expected_digest and actual != expected_digest:
            raise DigestMismatchError(key=key, expected=expected_digest, actual=actual)
        yield tmp_path
    finally:
        tmp_path.unlink(missing_ok=True)
