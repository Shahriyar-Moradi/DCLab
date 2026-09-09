"""Application-level object storage. Core services depend on this, not SDKs."""

from __future__ import annotations

from contextlib import AbstractContextManager
from dataclasses import dataclass
from typing import BinaryIO, Protocol


@dataclass(frozen=True)
class ObjectPutResult:
    key: str
    size_bytes: int
    content_digest: str
    content_type: str | None
    provider: str
    bucket: str | None


@dataclass(frozen=True)
class ObjectMetadata:
    key: str
    size_bytes: int
    content_digest: str | None
    content_type: str | None
    provider: str
    bucket: str | None


class ObjectStorage(Protocol):
    provider: str
    bucket: str | None

    def put(
        self,
        key: str,
        data: bytes | BinaryIO,
        *,
        content_type: str | None = None,
        metadata: dict[str, str] | None = None,
    ) -> ObjectPutResult: ...

    def get(self, key: str) -> bytes: ...

    def open(self, key: str) -> AbstractContextManager[BinaryIO]: ...

    def exists(self, key: str) -> bool: ...

    def delete(self, key: str) -> None: ...

    def metadata(self, key: str) -> ObjectMetadata: ...

    def signed_url(self, key: str, *, expires_in: int = 3600) -> str: ...

    def checksum(self, key: str) -> str: ...

    def local_path(self, key: str) -> str | None:
        """Filesystem path when the provider stores files locally; otherwise None."""
        return None
