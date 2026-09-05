"""S3 object storage. boto3 is imported only inside this adapter."""

from __future__ import annotations

import io
from contextlib import contextmanager
from typing import Any, BinaryIO, Iterator

from app.storage._hashing import as_bytes, sha256_bytes
from app.storage.base import ObjectMetadata, ObjectPutResult
from app.storage.exceptions import ObjectNotFoundError, ObjectStorageError


def _client_error_code(exc: BaseException) -> str | None:
    response = getattr(exc, "response", None)
    if not isinstance(response, dict):
        return None
    error = response.get("Error") or {}
    code = error.get("Code")
    return str(code) if code is not None else None


def _is_not_found(exc: BaseException) -> bool:
    code = _client_error_code(exc)
    if code in {"404", "NoSuchKey", "NotFound"}:
        return True
    name = type(exc).__name__
    return name in {"NoSuchKey", "404"} or "Not Found" in str(exc)


class S3Storage:
    provider = "s3"

    def __init__(
        self,
        bucket: str,
        *,
        region: str | None = None,
        client: Any | None = None,
    ) -> None:
        if not bucket:
            raise ObjectStorageError("S3Storage requires a bucket")
        self.bucket = bucket
        self.region = region
        self._injected_client = client

    def _client(self) -> Any:
        if self._injected_client is not None:
            return self._injected_client
        try:
            import boto3
        except ImportError as exc:
            raise ObjectStorageError(
                "boto3 is required for S3Storage when no client is injected"
            ) from exc
        kwargs = {}
        if self.region:
            kwargs["region_name"] = self.region
        return boto3.client("s3", **kwargs)

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
        extra: dict[str, str] = {"sha256": digest, **(metadata or {})}
        params: dict[str, Any] = {
            "Bucket": self.bucket,
            "Key": key,
            "Body": body,
            "Metadata": extra,
        }
        if content_type:
            params["ContentType"] = content_type
        self._client().put_object(**params)
        return ObjectPutResult(
            key=key,
            size_bytes=len(body),
            content_digest=digest,
            content_type=content_type,
            provider=self.provider,
            bucket=self.bucket,
        )

    def get(self, key: str) -> bytes:
        try:
            response = self._client().get_object(Bucket=self.bucket, Key=key)
        except Exception as exc:
            if _is_not_found(exc):
                raise ObjectNotFoundError(key) from exc
            raise
        body = response["Body"]
        if hasattr(body, "read"):
            return body.read()
        return bytes(body)

    @contextmanager
    def open(self, key: str) -> Iterator[BinaryIO]:
        yield io.BytesIO(self.get(key))

    def exists(self, key: str) -> bool:
        try:
            self._client().head_object(Bucket=self.bucket, Key=key)
        except Exception as exc:
            if _is_not_found(exc):
                return False
            raise
        return True

    def delete(self, key: str) -> None:
        self._client().delete_object(Bucket=self.bucket, Key=key)

    def metadata(self, key: str) -> ObjectMetadata:
        try:
            response = self._client().head_object(Bucket=self.bucket, Key=key)
        except Exception as exc:
            if _is_not_found(exc):
                raise ObjectNotFoundError(key) from exc
            raise
        meta = response.get("Metadata") or {}
        digest = meta.get("sha256")
        length = response.get("ContentLength")
        size_bytes = int(length) if length is not None else 0
        return ObjectMetadata(
            key=key,
            size_bytes=size_bytes,
            content_digest=digest,
            content_type=response.get("ContentType"),
            provider=self.provider,
            bucket=self.bucket,
        )

    def signed_url(self, key: str, *, expires_in: int = 3600) -> str:
        return self._client().generate_presigned_url(
            "get_object",
            Params={"Bucket": self.bucket, "Key": key},
            ExpiresIn=expires_in,
        )

    def checksum(self, key: str) -> str:
        meta = self.metadata(key)
        if meta.content_digest:
            return meta.content_digest
        return sha256_bytes(self.get(key))

    def local_path(self, key: str) -> str | None:
        del key
        return None
