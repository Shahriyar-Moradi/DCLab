from __future__ import annotations

import hashlib
from pathlib import Path
from typing import BinaryIO

DEFAULT_CHUNK_SIZE = 1024 * 1024


def as_bytes(data: bytes | bytearray | memoryview | BinaryIO) -> bytes:
    if isinstance(data, memoryview):
        return data.tobytes()
    if isinstance(data, (bytes, bytearray)):
        return bytes(data)
    return data.read()


def sha256_bytes(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def sha256_file(path: str | Path, *, chunk_size: int = DEFAULT_CHUNK_SIZE) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        while chunk := handle.read(chunk_size):
            digest.update(chunk)
    return digest.hexdigest()


def write_and_hash(
    destination,
    data: bytes | bytearray | memoryview | BinaryIO,
    *,
    chunk_size: int = DEFAULT_CHUNK_SIZE,
) -> tuple[int, str]:
    digest = hashlib.sha256()
    size = 0
    if isinstance(data, memoryview):
        payload = data.tobytes()
        destination.write(payload)
        digest.update(payload)
        return len(payload), digest.hexdigest()
    if isinstance(data, (bytes, bytearray)):
        payload = bytes(data)
        destination.write(payload)
        digest.update(payload)
        return len(payload), digest.hexdigest()
    while chunk := data.read(chunk_size):
        destination.write(chunk)
        digest.update(chunk)
        size += len(chunk)
    return size, digest.hexdigest()
