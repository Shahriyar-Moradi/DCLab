"""Worker handler registry: handler_key -> callable.

``labs.auto_train`` and ``auth.session_cleanup`` are shipped handlers. Future
capabilities register here without a second queue table. MCP jobs are not
registered.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Protocol
from uuid import UUID

from sqlalchemy.orm import Session

from app.db.models import MlJob
from app.domain.errors import UnknownJobHandlerError
from app.domain.ml_jobs import HANDLER_AUTH_SESSION_CLEANUP, HANDLER_LABS_AUTO_TRAIN

Heartbeat = Callable[[], None]


class JobHandler(Protocol):
    def __call__(
        self,
        db: Session,
        job: MlJob,
        *,
        on_heartbeat: Heartbeat | None = None,
    ) -> None: ...


_REGISTRY: dict[str, JobHandler] = {}


def register_handler(key: str, handler: JobHandler | None = None):
    """Register ``handler_key`` -> callable. Usable as a decorator or call."""

    def _register(func: JobHandler) -> JobHandler:
        _REGISTRY[key] = func
        return func

    if handler is not None:
        return _register(handler)
    return _register


def unregister_handler(key: str) -> None:
    _REGISTRY.pop(key, None)


def get_handler(key: str) -> JobHandler:
    handler = _REGISTRY.get(key)
    if handler is None:
        raise UnknownJobHandlerError(f"unsupported ml job handler {key!r}")
    return handler


def registered_handler_keys() -> frozenset[str]:
    return frozenset(_REGISTRY)


@register_handler(HANDLER_LABS_AUTO_TRAIN)
def handle_labs_auto_train(
    db: Session,
    job: MlJob,
    *,
    on_heartbeat: Heartbeat | None = None,
) -> None:
    from app.services.auto_train_service import run_auto_train_job

    upload_id: UUID | None = job.upload_id or job.target_id
    if upload_id is None:
        raise ValueError("labs.auto_train requires upload_id")
    run_auto_train_job(db, upload_id, on_heartbeat=on_heartbeat)


@register_handler(HANDLER_AUTH_SESSION_CLEANUP)
def handle_auth_session_cleanup(
    db: Session,
    job: MlJob,
    *,
    on_heartbeat: Heartbeat | None = None,
) -> None:
    from app.services.session_cleanup_service import cleanup_expired_auth_state

    if on_heartbeat is not None:
        on_heartbeat()
    cleanup_expired_auth_state(db)
    if on_heartbeat is not None:
        on_heartbeat()
