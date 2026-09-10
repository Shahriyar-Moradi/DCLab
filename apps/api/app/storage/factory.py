"""Resolve the configured object-storage provider.

This deployment configures exactly one remote provider. Artifact rows still
record provider and bucket; reads must match that record instead of treating
an s3 object key as valid on gcs (or local) because the key string looks usable.
"""

from __future__ import annotations

from typing import Protocol

from app.config import get_settings
from app.domain.data_plane import OBJECT_STORAGE_PROVIDERS
from app.storage.base import ObjectStorage
from app.storage.exceptions import ObjectStorageError, StorageConfigurationError
from app.storage.gcs import GCSStorage
from app.storage.local import LocalStorage
from app.storage.s3 import S3Storage


class ArtifactStorageRef(Protocol):
    provider: str
    bucket: str | None


def get_object_storage() -> ObjectStorage:
    settings = get_settings()
    provider = (settings.object_storage_provider or "local").strip().lower()
    if provider == "local":
        return LocalStorage(root=settings.object_storage_root)
    if provider == "s3":
        return S3Storage(
            bucket=settings.object_storage_bucket,
            region=settings.object_storage_region or None,
        )
    if provider == "gcs":
        return GCSStorage(bucket=settings.object_storage_bucket)
    raise ObjectStorageError(f"unknown object storage provider: {provider}")


class StorageRegistry:
    """Provider-aware lookup over the single configured backend for this process."""

    def __init__(self, configured: ObjectStorage | None = None) -> None:
        self._configured = configured if configured is not None else get_object_storage()
        self._by_provider: dict[str, ObjectStorage] = {
            self._configured.provider: self._configured
        }

    @property
    def providers(self) -> tuple[str, ...]:
        return OBJECT_STORAGE_PROVIDERS

    def configured(self) -> ObjectStorage:
        return self._configured

    def for_provider(
        self,
        provider: str,
        *,
        bucket: str | None = None,
    ) -> ObjectStorage:
        requested = (provider or "").strip().lower()
        storage = self._configured
        actual = (storage.provider or "").strip().lower()
        configured_bucket = getattr(storage, "bucket", None)
        if requested != actual:
            raise StorageConfigurationError(
                artifact_provider=requested or provider,
                configured_provider=actual or storage.provider,
                artifact_bucket=bucket,
                configured_bucket=configured_bucket,
            )
        if (
            bucket
            and configured_bucket is not None
            and bucket != configured_bucket
        ):
            raise StorageConfigurationError(
                artifact_provider=requested,
                configured_provider=actual,
                artifact_bucket=bucket,
                configured_bucket=configured_bucket,
            )
        return storage

    def for_artifact(self, artifact: ArtifactStorageRef) -> ObjectStorage:
        return self.for_provider(artifact.provider, bucket=artifact.bucket)


def storage_registry(storage: ObjectStorage | None = None) -> StorageRegistry:
    return StorageRegistry(configured=storage)


def storage_for_artifact(
    artifact: ArtifactStorageRef,
    *,
    storage: ObjectStorage | None = None,
) -> ObjectStorage:
    return StorageRegistry(configured=storage).for_artifact(artifact)


def storage_for_provider(
    provider: str,
    *,
    bucket: str | None = None,
    storage: ObjectStorage | None = None,
) -> ObjectStorage:
    return StorageRegistry(configured=storage).for_provider(provider, bucket=bucket)
