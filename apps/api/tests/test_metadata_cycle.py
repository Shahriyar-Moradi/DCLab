"""SQLAlchemy metadata must be sortable without foreign-key cycle warnings.

The datasets / ingestion_runs / execution_requests / experiments tables
reference each other. Every edge in that loop must have at least one FK that is
emitted with ``use_alter`` so Alembic autogenerate and ``create_all`` can order
tables deterministically (P0.1-B).
"""

from __future__ import annotations

import warnings

from sqlalchemy.exc import SAWarning

from app.db import models as _models  # noqa: F401
from app.db.base import Base


def test_metadata_tables_sort_without_cycle_warning() -> None:
    with warnings.catch_warnings():
        warnings.simplefilter("error", SAWarning)
        tables = Base.metadata.sorted_tables
    assert len(tables) == len(Base.metadata.tables)
