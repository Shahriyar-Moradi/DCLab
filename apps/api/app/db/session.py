from collections.abc import Generator

from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from app.config import get_settings

_engine = None
_engine_url = None
_SessionLocal = None


def get_engine():
    global _engine, _engine_url, _SessionLocal
    url = get_settings().database_url
    if _engine is None or _engine_url != url:
        _engine = create_engine(url, pool_pre_ping=True)
        _engine_url = url
        _SessionLocal = sessionmaker(bind=_engine, autocommit=False, autoflush=False, class_=Session)
    return _engine


def get_session_factory():
    get_engine()
    return _SessionLocal


def get_db() -> Generator[Session, None, None]:
    db = get_session_factory()()
    try:
        yield db
    finally:
        db.close()


# Backwards-compatible names used by health checks.
engine = None  # populated lazily via get_engine()
