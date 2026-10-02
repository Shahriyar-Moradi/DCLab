"""Generic Idempotency-Key store for /v1 commands (P3.1-B1, ``idempotency_keys``).

For resources without their own idempotency column. The caller looks the key
up before executing (``find_bound``), then binds it in the SAME transaction as
the resource it creates (``bind``) and commits once. A concurrent duplicate
loses on ``uq_idempotency_keys_scope``: ``bind`` raises
``IdempotencyKeyRaceError``; the caller rolls back its resource and calls
``find_bound`` again, which now replays the winner (or answers 409).
"""

from __future__ import annotations

from dataclasses import dataclass
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.db.models import IdempotencyKey
from app.domain.errors import IdempotencyKeyReusedError
from app.domain.idempotency import (
    PRINCIPAL_KINDS,
    UQ_IDEMPOTENCY_KEYS_SCOPE,
    IdempotencyBinding,
)
from app.services.decision_record_service import unique_violation


class IdempotencyKeyRaceError(Exception):
    """Another request bound the same key concurrently; roll back and look it up again."""


@dataclass(frozen=True)
class KeyScope:
    workspace_id: UUID
    principal_kind: str
    principal_id: UUID
    operation: str

    def __post_init__(self) -> None:
        if self.principal_kind not in PRINCIPAL_KINDS:
            raise ValueError(f"unsupported principal kind: {self.principal_kind}")


def find_bound(
    db: Session, scope: KeyScope, binding: IdempotencyBinding
) -> IdempotencyKey | None:
    """The stored binding for this key, None when unused; 409 on a different request."""

    if binding.key is None:
        return None
    row = db.scalar(
        select(IdempotencyKey).where(
            IdempotencyKey.workspace_id == scope.workspace_id,
            IdempotencyKey.principal_kind == scope.principal_kind,
            IdempotencyKey.principal_id == scope.principal_id,
            IdempotencyKey.operation == scope.operation,
            IdempotencyKey.idempotency_key == binding.key,
        )
    )
    if row is None:
        return None
    if row.request_digest != binding.digest:
        raise IdempotencyKeyReusedError()
    return row


def bind(
    db: Session,
    scope: KeyScope,
    binding: IdempotencyBinding,
    *,
    resource_kind: str,
    resource_id: UUID,
    response_status: int,
) -> None:
    """Insert the key row in the caller's transaction (no commit). Keyless: no-op."""

    if binding.key is None:
        return
    try:
        with db.begin_nested():
            db.add(
                IdempotencyKey(
                    workspace_id=scope.workspace_id,
                    principal_kind=scope.principal_kind,
                    principal_id=scope.principal_id,
                    operation=scope.operation,
                    idempotency_key=binding.key,
                    request_digest=binding.digest,
                    resource_kind=resource_kind,
                    resource_id=resource_id,
                    response_status=response_status,
                )
            )
            db.flush()
    except IntegrityError as exc:
        if unique_violation(exc) == UQ_IDEMPOTENCY_KEYS_SCOPE:
            raise IdempotencyKeyRaceError() from exc
        raise
