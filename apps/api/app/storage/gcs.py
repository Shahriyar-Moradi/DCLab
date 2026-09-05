"""GCS object storage. google.cloud.storage is imported only inside this adapter."""

from __future__ import annotations

import io
from contextlib import contextmanager
from typing import Any, BinaryIO, Iterator

from app.storage._hashing import as_bytes, sha256_bytes
from app.storage.base import ObjectMetadata, ObjectPutResult
from app.storage.exceptions import ObjectNotFoundError, ObjectStorageError


class GCSStorage:
    provider = "gcs"

    def __init__(self, bucket: str, *, client: Any | None = None) -> None:
        if not bucket:
            raise ObjectStorageError("GCSStorage requires a bucket")
        self.bucket = bucket
        self._injected_client = client

    def _client(self) -> Any:
        if self._injected_client is not None:
            return self._injected_client
        try:
            from google.cloud import storage as gcs
        except ImportError as exc:
            raise ObjectStorageError(
                "google.cloud.storage is required for GCSStorage when no client is injected"
            ) from exc
        return gcs.Client()

    def _blob(self, key: str) -> Any:
        return self._client().bucket(self.bucket).blob(key)

    def put(
        self,
        key: str,
        data: bytes | BinaryIO,
        *,
        content_type: str | None = None,
        metadata: dict[str, str] | None = None,
    ) -> ObjectPutResult:
        body = as_bytes(data)
        digest = sha256_bytes(body)
        blob = self._blob(key)
        blob.metadata = {"sha256": digest, **(metadata or {})}
        blob.upload_from_string(body, content_type=content_type)
        return ObjectPutResult(
            key=key,
            size_bytes=len(body),
            content_digest=digest,
            content_type=content_type,
            provider=self.provider,
            bucket=self.bucket,
        )

    def get(self, key: str) -> bytes:
        blob = self._blob(key)
        try:
            if hasattr(blob, "exists") and blob.exists() is False:
                raise ObjectNotFoundError(key)
            payload = blob.download_as_bytes()
        except ObjectNotFoundError:
            raise
        except Exception as exc:
            if type(exc).__name__ in {"NotFound", "404"} or "No such object" in str(exc):
                raise ObjectNotFoundError(key) from exc
            raise
        return payload

    @contextmanager
    def open(self, key: str) -> Iterator[BinaryIO]:
        yield io.BytesIO(self.get(key))

    def exists(self, key: str) -> bool:
        blob = self._blob(key)
        if hasattr(blob, "exists"):
            return bool(blob.exists())
        try:
            self.get(key)
        except ObjectNotFoundError:
            return False
        return True

    def delete(self, key: str) -> None:
        blob = self._blob(key)
        if hasattr(blob, "delete"):
            blob.delete()

    def metadata(self, key: str) -> ObjectMetadata:
        blob = self._blob(key)
        if hasattr(blob, "exists") and blob.exists() is False:
            raise ObjectNotFoundError(key)
        if hasattr(blob, "reload"):
            try:
                blob.reload()
            except Exception as exc:
                if type(exc).__name__ in {"NotFound", "404"}:
                    raise ObjectNotFoundError(key) from exc
                raise
        meta = getattr(blob, "metadata", None) or {}
        size = getattr(blob, "size", None)
        return ObjectMetadata(
            key=key,
            size_bytes=int(size) if size is not None else 0,
            content_digest=meta.get("sha256"),
            content_type=getattr(blob, "content_type", None),
            provider=self.provider,
            bucket=self.bucket,
        )

    def signed_url(self, key: str, *, expires_in: int = 3600) -> str:
        blob = self._blob(key)
        if hasattr(blob, "generate_signed_url"):
            return blob.generate_signed_url(expiration=expires_in)
        return f"https://storage.googleapis.com/{self.bucket}/{key}"

    def checksum(self, key: str) -> str:
        meta = self.metadata(key)
        if meta.content_digest:
            return meta.content_digest
        return sha256_bytes(self.get(key))

    def local_path(self, key: str) -> str | None:
        del key
        return None
