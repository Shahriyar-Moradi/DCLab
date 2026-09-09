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
