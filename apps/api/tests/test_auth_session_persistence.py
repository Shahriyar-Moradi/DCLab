"""S0-P02C: session/recovery persistence, lineage, bounded cleanup, upgrades."""

from __future__ import annotations

import threading
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from alembic import command
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import sessionmaker

from app.db.models import (
    DEFAULT_WORKSPACE_ID,
    AuthRecoveryToken,
    AuthSession,
)
from app.domain.ml_jobs import HANDLER_AUTH_SESSION_CLEANUP, JOB_COMPLETED
from app.services.ml_job_service import process_next_job
from app.services.recovery_service import PURPOSE_PASSWORD_RESET, hash_recovery_token
from app.services.session_cleanup_service import (
    cleanup_expired_auth_state,
    enqueue_auth_cleanup,
)
from app.services.session_service import (
    generate_session_token,
    hash_session_token,
    issue_session,
    revoke_session_token,
)
from test_historical_alembic_revisions import _drop_isolated, _isolated_database

PREVIOUS_HEAD = "0058_simulation_workspace"
CURRENT_HEAD = "0059_auth_session_constraints"


def _stale_session(db_session, user, *, moment: datetime, revoked: bool = True) -> AuthSession:
    raw = generate_session_token()
    row = AuthSession(
        id=uuid4(),
        user_id=user.id,
        token_hash=hash_session_token(raw),
        created_at=moment - timedelta(days=40),
        last_seen_at=moment - timedelta(days=40),
        idle_expires_at=moment - timedelta(days=39),
        absolute_expires_at=moment - timedelta(days=38),
        revoked_at=(moment - timedelta(days=35)) if revoked else None,
    )
    db_session.add(row)
    db_session.flush()
    return row


def test_auth_tables_never_store_raw_token_columns():
    session_names = {column.name for column in AuthSession.__table__.columns}
    recovery_names = {column.name for column in AuthRecoveryToken.__table__.columns}
    forbidden = {"token", "raw_token", "secret", "csrf_token"}
    assert not (session_names & forbidden)
    assert not (recovery_names & forbidden)
    assert "token_hash" in session_names
    assert "token_hash" in recovery_names


def test_session_token_hash_is_unique(db_session, client_user):
    first, _raw = issue_session(db_session, client_user)
    second, _other = issue_session(db_session, client_user)
    db_session.flush()
    second.token_hash = first.token_hash
    with pytest.raises(IntegrityError):
        db_session.commit()
    db_session.rollback()


def test_recovery_token_hash_is_unique(db_session, client_user):
    moment = datetime.now(UTC)
    first = AuthRecoveryToken(
        id=uuid4(),
        user_id=client_user.id,
        purpose=PURPOSE_PASSWORD_RESET,
        token_hash=hash_recovery_token("one-recovery-secret"),
        created_at=moment,
        expires_at=moment + timedelta(hours=1),
    )
    second = AuthRecoveryToken(
        id=uuid4(),
        user_id=client_user.id,
        purpose=PURPOSE_PASSWORD_RESET,
        token_hash=hash_recovery_token("one-recovery-secret"),
        created_at=moment,
        expires_at=moment + timedelta(hours=1),
    )
    db_session.add(first)
    db_session.add(second)
    with pytest.raises(IntegrityError):
        db_session.commit()
    db_session.rollback()


def test_token_hash_must_be_sha256_hex(db_session, client_user):
    row, _raw = issue_session(db_session, client_user)
    db_session.flush()
    row.token_hash = "not-a-sha256-hex-digest"
    with pytest.raises(IntegrityError):
        db_session.commit()
    db_session.rollback()


def test_rotation_lineage_cannot_cross_users(db_session, client_user, admin_user):
    victim, victim_raw = issue_session(db_session, client_user)
    db_session.flush()
    attacker, _raw = issue_session(db_session, admin_user, replace_raw=victim_raw)
    db_session.commit()
    db_session.refresh(victim)
    db_session.refresh(attacker)
    assert victim.revoked_at is not None
    assert attacker.rotated_from_id is None
    attacker.rotated_from_id = victim.id
    with pytest.raises(IntegrityError):
        db_session.commit()
    db_session.rollback()


def test_rotation_cannot_point_at_self(db_session, client_user):
    row, _raw = issue_session(db_session, client_user)
    db_session.flush()
    row.rotated_from_id = row.id
    with pytest.raises(IntegrityError):
        db_session.commit()
    db_session.rollback()


def test_same_user_rotation_keeps_lineage(db_session, client_user):
    first, first_raw = issue_session(db_session, client_user)
    db_session.flush()
    second, _raw = issue_session(db_session, client_user, replace_raw=first_raw)
    db_session.commit()
    assert second.rotated_from_id == first.id
    assert second.user_id == first.user_id


def test_deleting_predecessor_nulls_rotated_from_id_only(db_session, client_user):
    first, first_raw = issue_session(db_session, client_user)
    db_session.flush()
    second, _raw = issue_session(db_session, client_user, replace_raw=first_raw)
    db_session.commit()
    db_session.delete(first)
    db_session.commit()
    db_session.refresh(second)
    assert second.rotated_from_id is None
    assert second.user_id == client_user.id
    assert db_session.get(AuthSession, second.id) is not None


def test_expiry_and_revocation_indexes_exist(test_engine):
    inspector = inspect(test_engine)
    session_indexes = {item["name"] for item in inspector.get_indexes("auth_sessions")}
    recovery_indexes = {
        item["name"] for item in inspector.get_indexes("auth_recovery_tokens")
    }
    assert "ix_auth_sessions_idle_expires_at" in session_indexes
    assert "ix_auth_sessions_absolute_expires_at" in session_indexes
    assert "ix_auth_sessions_revoked_at" in session_indexes
    assert "ix_auth_sessions_user_id" in session_indexes
    assert "ix_auth_recovery_tokens_expires_at" in recovery_indexes
    assert "ix_auth_recovery_tokens_consumed_at" in recovery_indexes
    uniques = {
        item["name"] for item in inspector.get_unique_constraints("auth_sessions")
    }
    assert "uq_auth_sessions_token_hash" in uniques
    assert "uq_auth_sessions_id_user_id" in uniques


def test_cleanup_deletes_retained_sessions_and_expired_recovery(
    db_session, client_user
):
    moment = datetime(2026, 6, 1, tzinfo=UTC)
    fresh, _raw = issue_session(db_session, client_user, now=moment)
    stale = _stale_session(db_session, client_user, moment=moment)
    expired_recovery = AuthRecoveryToken(
        id=uuid4(),
        user_id=client_user.id,
        purpose=PURPOSE_PASSWORD_RESET,
        token_hash=hash_recovery_token("expired-recovery"),
        created_at=moment - timedelta(hours=2),
        expires_at=moment - timedelta(minutes=1),
    )
    live_recovery = AuthRecoveryToken(
        id=uuid4(),
        user_id=client_user.id,
        purpose=PURPOSE_PASSWORD_RESET,
        token_hash=hash_recovery_token("live-recovery"),
        created_at=moment,
        expires_at=moment + timedelta(hours=1),
    )
    db_session.add_all([expired_recovery, live_recovery])
    db_session.commit()
    stale_id = stale.id
    fresh_id = fresh.id
    expired_id = expired_recovery.id
    live_id = live_recovery.id
    first = cleanup_expired_auth_state(db_session, now=moment)
    db_session.commit()
    db_session.expire_all()
    assert first.sessions == 1
    assert first.recovery_tokens == 1
    assert db_session.get(AuthSession, stale_id) is None
    assert db_session.get(AuthSession, fresh_id) is not None
    assert db_session.get(AuthRecoveryToken, expired_id) is None
    assert db_session.get(AuthRecoveryToken, live_id) is not None
    second = cleanup_expired_auth_state(db_session, now=moment)
    db_session.commit()
    assert second.deleted == 0


def test_cleanup_is_bounded(db_session, client_user):
    moment = datetime(2026, 6, 1, tzinfo=UTC)
    for _ in range(3):
        _stale_session(db_session, client_user, moment=moment)
    db_session.commit()
    first = cleanup_expired_auth_state(
        db_session, now=moment, limit=2, max_batches=1
    )
    db_session.commit()
    assert first.sessions == 2
    remaining = db_session.query(AuthSession).count()
    assert remaining == 1
    second = cleanup_expired_auth_state(
        db_session, now=moment, limit=2, max_batches=1
    )
    db_session.commit()
    assert second.sessions == 1
    assert db_session.query(AuthSession).count() == 0


def test_durable_cleanup_handler_is_idempotent(db_session, client_user):
    moment = datetime(2026, 6, 1, tzinfo=UTC)
    _stale_session(db_session, client_user, moment=moment)
    db_session.commit()
    first = enqueue_auth_cleanup(db_session, workspace_id=DEFAULT_WORKSPACE_ID)
    second = enqueue_auth_cleanup(db_session, workspace_id=DEFAULT_WORKSPACE_ID)
    db_session.commit()
    assert first.id == second.id
    assert first.handler_key == HANDLER_AUTH_SESSION_CLEANUP
    assert first.payload == {}
    assert "token" not in first.payload
    result = process_next_job(db_session)
    assert result is not None
    assert result.status == JOB_COMPLETED
    assert db_session.query(AuthSession).count() == 0
    again = enqueue_auth_cleanup(db_session, workspace_id=DEFAULT_WORKSPACE_ID)
    db_session.commit()
    replay = process_next_job(db_session)
    assert replay is not None
    assert replay.id == again.id
    assert replay.status == JOB_COMPLETED


def test_concurrent_rotation_and_logout(db_session, test_engine, client_user):
    row, raw = issue_session(db_session, client_user)
    db_session.commit()
    session_id = row.id
    SessionLocal = sessionmaker(bind=test_engine)
    barrier = threading.Barrier(2)
    errors: list[BaseException] = []

    def _rotate() -> None:
        session = SessionLocal()
        try:
            barrier.wait(timeout=10)
            user = session.get(type(client_user), client_user.id)
            issue_session(session, user, replace_raw=raw)
            session.commit()
        except BaseException as exc:  # noqa: BLE001
            errors.append(exc)
            session.rollback()
        finally:
            session.close()

    def _logout() -> None:
        session = SessionLocal()
        try:
            barrier.wait(timeout=10)
            revoke_session_token(session, raw)
            session.commit()
        except BaseException as exc:  # noqa: BLE001
            errors.append(exc)
            session.rollback()
        finally:
            session.close()

    with ThreadPoolExecutor(max_workers=2) as pool:
        rotate_future = pool.submit(_rotate)
        logout_future = pool.submit(_logout)
        rotate_future.result()
        logout_future.result()
    assert errors == []
    db_session.expire_all()
    predecessor = db_session.get(AuthSession, session_id)
    assert predecessor is None or predecessor.revoked_at is not None
    presented = (
        db_session.query(AuthSession)
        .filter(AuthSession.token_hash == hash_session_token(raw))
        .one_or_none()
    )
    if presented is not None:
        assert presented.revoked_at is not None


def test_concurrent_cleanup_is_race_safe(db_session, test_engine, client_user):
    moment = datetime(2026, 6, 1, tzinfo=UTC)
    for _ in range(8):
        _stale_session(db_session, client_user, moment=moment)
    db_session.commit()
    SessionLocal = sessionmaker(bind=test_engine)
    barrier = threading.Barrier(2)

    def _clean() -> int:
        session = SessionLocal()
        try:
            barrier.wait(timeout=10)
            result = cleanup_expired_auth_state(
                session, now=moment, limit=4, max_batches=1
            )
            session.commit()
            return result.sessions
        finally:
            session.close()

    with ThreadPoolExecutor(max_workers=2) as pool:
        counts = list(pool.map(lambda _: _clean(), range(2)))
    assert sum(counts) == 8
    db_session.expire_all()
    assert db_session.query(AuthSession).count() == 0


def _seed_cross_user_rotation(connection) -> str:
    victim_id = uuid4()
    attacker_id = uuid4()
    victim_session = uuid4()
    attacker_session = uuid4()
    now = datetime(2026, 6, 1, tzinfo=UTC)
    connection.execute(
        text(
            "INSERT INTO users (id, email, password_hash, role, full_name, is_active) "
            "VALUES (:id, :email, 'x', 'dclab_admin', 'T', true)"
        ),
        {"id": victim_id, "email": "victim-p02c@test.invalid"},
    )
    connection.execute(
        text(
            "INSERT INTO users (id, email, password_hash, role, full_name, is_active) "
            "VALUES (:id, :email, 'x', 'dclab_admin', 'T', true)"
        ),
        {"id": attacker_id, "email": "attacker-p02c@test.invalid"},
    )
    connection.execute(
        text(
            """
            INSERT INTO auth_sessions (
                id, user_id, token_hash, created_at, last_seen_at,
                idle_expires_at, absolute_expires_at
            ) VALUES (
                :id, :user_id, :token_hash, :now, :now, :now, :now
            )
            """
        ),
        {
            "id": victim_session,
            "user_id": victim_id,
            "token_hash": hash_session_token("victim-raw"),
            "now": now,
        },
    )
    connection.execute(
        text(
            """
            INSERT INTO auth_sessions (
                id, user_id, token_hash, created_at, last_seen_at,
                idle_expires_at, absolute_expires_at, rotated_from_id
            ) VALUES (
                :id, :user_id, :token_hash, :now, :now, :now, :now, :rotated
            )
            """
        ),
        {
            "id": attacker_session,
            "user_id": attacker_id,
            "token_hash": hash_session_token("attacker-raw"),
            "now": now,
            "rotated": victim_session,
        },
    )
    return str(attacker_session)


def test_empty_database_upgrades_to_session_constraint_head(monkeypatch):
    admin_engine, database_name, database_url, alembic_config = _isolated_database(
        monkeypatch, suffix="p02cempty"
    )
    try:
        command.upgrade(alembic_config, "head")
        engine = create_engine(database_url)
        try:
            with engine.connect() as connection:
                version = connection.execute(
                    text("SELECT version_num FROM alembic_version")
                ).scalar()
                assert version == CURRENT_HEAD
                fk_names = {
                    row[0]
                    for row in connection.execute(
                        text(
                            "SELECT conname FROM pg_constraint "
                            "WHERE conrelid = 'auth_sessions'::regclass AND contype = 'f'"
                        )
                    )
                }
                assert "fk_auth_sessions_rotated_from_user" in fk_names
        finally:
            engine.dispose()
    finally:
        _drop_isolated(admin_engine, database_name)


def test_previous_head_upgrade_repairs_cross_user_rotation(monkeypatch):
    admin_engine, database_name, database_url, alembic_config = _isolated_database(
        monkeypatch, suffix="p02cfrom58"
    )
    try:
        command.upgrade(alembic_config, PREVIOUS_HEAD)
        engine = create_engine(database_url)
        try:
            with engine.begin() as connection:
                attacker_session = _seed_cross_user_rotation(connection)
            command.upgrade(alembic_config, "head")
            with engine.begin() as connection:
                version = connection.execute(
                    text("SELECT version_num FROM alembic_version")
                ).scalar()
                assert version == CURRENT_HEAD
                rotated = connection.execute(
                    text(
                        "SELECT rotated_from_id FROM auth_sessions WHERE id = CAST(:id AS uuid)"
                    ),
                    {"id": attacker_session},
                ).scalar()
                assert rotated is None
                with pytest.raises(Exception):
                    connection.execute(
                        text(
                            """
                            UPDATE auth_sessions AS child
                            SET rotated_from_id = parent.id
                            FROM auth_sessions AS parent
                            WHERE child.id = CAST(:child AS uuid)
                              AND parent.id <> child.id
                              AND parent.user_id <> child.user_id
                            """
                        ),
                        {"child": attacker_session},
                    )
        finally:
            engine.dispose()
    finally:
        _drop_isolated(admin_engine, database_name)
