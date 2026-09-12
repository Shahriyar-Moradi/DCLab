"""Session and recovery token hashing. Raw secrets are never stored or logged."""

from __future__ import annotations

import hashlib
import hmac

from app.config import Settings, get_settings


def digest_token(raw: str, secret: str) -> str:
    if secret:
        return hmac.new(
            secret.encode("utf-8"), raw.encode("utf-8"), hashlib.sha256
        ).hexdigest()
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def token_hash(raw: str, settings: Settings | None = None) -> str:
    cfg = settings or get_settings()
    return digest_token(raw, cfg.auth_token_hash_secret.strip())


def token_hash_candidates(raw: str, settings: Settings | None = None) -> tuple[str, ...]:
    """Current HMAC/SHA-256, optional previous HMAC, and unkeyed SHA-256."""
    cfg = settings or get_settings()
    seen: list[str] = []

    def add(secret: str) -> None:
        digest = digest_token(raw, secret)
        if digest not in seen:
            seen.append(digest)

    add(cfg.auth_token_hash_secret.strip())
    previous = cfg.auth_token_hash_secret_previous.strip()
    if previous:
        add(previous)
    add("")
    return tuple(seen)
