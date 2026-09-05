from __future__ import annotations

import hashlib
from typing import BinaryIO


def as_bytes(data: bytes | bytearray | memoryview | BinaryIO) -> bytes:
    if isinstance(data, memoryview):
        return data.tobytes()
    if isinstance(data, (bytes, bytearray)):
        return bytes(data)
    return data.read()


def sha256_bytes(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def write_and_hash(
    destination,
    data: bytes | bytearray | memoryview | BinaryIO,
    *,
    chunk_size: int = 1024 * 1024,
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
