"""Errors raised by object-storage adapters."""


class ObjectStorageError(RuntimeError):
    """Object storage operation failed."""


class ObjectNotFoundError(ObjectStorageError, FileNotFoundError):
    def __init__(self, key: str) -> None:
        super().__init__(f"object not found: {key}")
        self.key = key
