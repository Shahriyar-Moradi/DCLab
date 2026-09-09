"""Frozen Alembic copies. Historical revisions import from here, never from live app modules.

These modules are immutable once a revision that uses them has been applied in
any environment. Change live ``app.domain`` / ``app.db`` helpers in a NEW
revision instead of editing these files.
"""
