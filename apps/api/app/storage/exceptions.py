"""Errors raised by object-storage adapters."""

S3_SDK_INSTALL = 'pip install -e ".[storage-s3]"'
GCS_SDK_INSTALL = 'pip install -e ".[storage-gcs]"'


class ObjectStorageError(RuntimeError):
    """Object storage operation failed."""


class ObjectNotFoundError(ObjectStorageError, FileNotFoundError):
    def __init__(self, key: str) -> None:
        super().__init__(f"object not found: {key}")
        self.key = key


class DigestMismatchError(ObjectStorageError):
    def __init__(self, *, key: str, expected: str, actual: str) -> None:
        super().__init__(
            f"digest mismatch for {key}: expected {expected}, got {actual}"
        )
        self.key = key
        self.expected = expected
        self.actual = actual


class StorageConfigurationError(ObjectStorageError):
    """Configured storage backend does not match the Artifact's recorded provider."""

    def __init__(
        self,
        *,
        artifact_provider: str,
        configured_provider: str,
        artifact_bucket: str | None = None,
        configured_bucket: str | None = None,
    ) -> None:
        if artifact_provider != configured_provider:
            message = (
                f"object storage provider mismatch: artifact is {artifact_provider}, "
                f"configured provider is {configured_provider}"
            )
        else:
            message = (
                f"object storage bucket mismatch for provider {artifact_provider}: "
                f"artifact bucket is {artifact_bucket}, "
                f"configured bucket is {configured_bucket}"
            )
        super().__init__(message)
        self.artifact_provider = artifact_provider
        self.configured_provider = configured_provider
        self.artifact_bucket = artifact_bucket
        self.configured_bucket = configured_bucket
