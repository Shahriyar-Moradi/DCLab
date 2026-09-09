"""Resolve the configured object-storage provider."""

from __future__ import annotations

from app.config import get_settings
from app.storage.base import ObjectStorage
from app.storage.exceptions import ObjectStorageError
from app.storage.gcs import GCSStorage
from app.storage.local import LocalStorage
from app.storage.s3 import S3Storage


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
