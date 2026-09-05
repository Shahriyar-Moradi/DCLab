"""Local filesystem object storage for tests and development."""

from __future__ import annotations

from contextlib import contextmanager
from pathlib import Path
from typing import BinaryIO, Iterator

from app.storage._hashing import sha256_bytes, write_and_hash
from app.storage.base import ObjectMetadata, ObjectPutResult
from app.storage.exceptions import ObjectNotFoundError


class LocalStorage:
    provider = "local"
    bucket = None

    def __init__(self, root: str | Path) -> None:
        self.root = Path(root)

    def _path(self, key: str) -> Path:
        relative = Path(key)
        if relative.is_absolute() or ".." in relative.parts:
            raise ValueError(f"invalid object key: {key}")
        return self.root / relative

    def put(
        self,
        key: str,
        data: bytes | BinaryIO,
        *,
        content_type: str | None = None,
        metadata: dict[str, str] | None = None,
    ) -> ObjectPutResult:
        del metadata
        path = self._path(key)
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("wb") as handle:
            size_bytes, digest = write_and_hash(handle, data)
        return ObjectPutResult(
            key=key,
            size_bytes=size_bytes,
            content_digest=digest,
            content_type=content_type,
            provider=self.provider,
            bucket=self.bucket,
        )

    def get(self, key: str) -> bytes:
        path = self._path(key)
        if not path.is_file():
            raise ObjectNotFoundError(key)
        return path.read_bytes()

    @contextmanager
    def open(self, key: str) -> Iterator[BinaryIO]:
        path = self._path(key)
        if not path.is_file():
            raise ObjectNotFoundError(key)
        with path.open("rb") as handle:
            yield handle

    def exists(self, key: str) -> bool:
        return self._path(key).is_file()

    def delete(self, key: str) -> None:
        self._path(key).unlink(missing_ok=True)

    def metadata(self, key: str) -> ObjectMetadata:
        path = self._path(key)
        if not path.is_file():
            raise ObjectNotFoundError(key)
        digest = sha256_bytes(path.read_bytes())
        return ObjectMetadata(
            key=key,
            size_bytes=path.stat().st_size,
            content_digest=digest,
            content_type=None,
            provider=self.provider,
            bucket=self.bucket,
        )

    def signed_url(self, key: str, *, expires_in: int = 3600) -> str:
        del expires_in
        path = self._path(key)
        if not path.is_file():
            raise ObjectNotFoundError(key)
        return path.resolve().as_uri()

    def checksum(self, key: str) -> str:
        return sha256_bytes(self.get(key))

    def local_path(self, key: str) -> str | None:
        return str(self._path(key))
