from __future__ import annotations

import os
from collections.abc import Generator

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session, sessionmaker

TEST_DB_NAME = "decisionai_test"
ADMIN_URL = os.environ.get(
    "DATABASE_URL", "postgresql://postgres:postgres@localhost:5432/decisionai"
)
TEST_URL = ADMIN_URL.rsplit("/", 1)[0] + f"/{TEST_DB_NAME}"


def _ensure_database() -> None:
    from sqlalchemy.engine.url import make_url

    url = make_url(ADMIN_URL)
    admin_engine = create_engine(url.set(database="postgres"), isolation_level="AUTOCOMMIT")
    with admin_engine.connect() as conn:
        exists = conn.execute(
            text("SELECT 1 FROM pg_database WHERE datname = :name"), {"name": TEST_DB_NAME}
        ).scalar()
        if not exists:
            conn.execute(text(f'CREATE DATABASE "{TEST_DB_NAME}"'))
    admin_engine.dispose()


@pytest.fixture(scope="session")
def test_engine():
    _ensure_database()
    os.environ["DATABASE_URL"] = TEST_URL
    from app.config import get_settings
    from app.db.session import get_engine

    get_settings.cache_clear()
    get_engine.__globals__  # engine is lazy via get_settings
    from app.db import models  # noqa: F401
    from app.db.base import Base
    from app.db.models import DEFAULT_WORKSPACE_ID, DEFAULT_WORKSPACE_SLUG

    engine = create_engine(TEST_URL, pool_pre_ping=True)
    Base.metadata.drop_all(engine)
    Base.metadata.create_all(engine)
    # create_all doesn't run data migrations, so seed the same well-known default
    # workspace the 0005_workspaces migration creates in real environments — every
    # Opportunity/Prediction/Decision row defaults its workspace_id FK to this row.
    with engine.begin() as conn:
        conn.execute(
            text("INSERT INTO workspaces (id, slug, name) VALUES (:id, :slug, :name)"),
            {"id": DEFAULT_WORKSPACE_ID, "slug": DEFAULT_WORKSPACE_SLUG, "name": "Default"},
        )
    yield engine
    engine.dispose()


@pytest.fixture()
def db_session(test_engine) -> Generator[Session, None, None]:
    SessionLocal = sessionmaker(bind=test_engine)
    session = SessionLocal()
    try:
        yield session
    finally:
        session.close()
        from app.db.models import DEFAULT_WORKSPACE_ID

        with test_engine.begin() as conn:
            conn.execute(text(
                "TRUNCATE TABLE experiment_candidates, experiments, dataset_profiles, "
                "prediction_tasks, datasets, environments, simulation_runs, "
                "lab_decision_records, client_lab_run_audits, client_lab_runs, "
                "client_lab_uploads, "
                "decisions, predictions, opportunities, users RESTART IDENTITY CASCADE"
            ))
            # Keep the well-known default workspace; drop any extra workspaces a
            # test created so slugs don't collide across test functions.
            conn.execute(
                text("DELETE FROM workspaces WHERE id != :default_id"),
                {"default_id": DEFAULT_WORKSPACE_ID},
            )


@pytest.fixture()
def client(test_engine, db_session: Session) -> Generator[TestClient, None, None]:
    from app.db.session import get_db
    from app.main import app

    def _override() -> Generator[Session, None, None]:
        yield db_session

    app.dependency_overrides[get_db] = _override
    with TestClient(app) as test_client:
        yield test_client
    app.dependency_overrides.clear()


ADMIN_PASSWORD = "admin-pass-123"
CLIENT_PASSWORD = "client-pass-123"


@pytest.fixture()
def admin_user(db_session: Session):
    from app.db.models import UserRole
    from app.services.auth_service import create_user

    user = create_user(
        db_session,
        email="admin@dclab.test",
        password=ADMIN_PASSWORD,
        role=UserRole.DCLAB_ADMIN,
        full_name="DCLab Admin",
    )
    db_session.commit()
    return user


@pytest.fixture()
def client_user(db_session: Session):
    from app.db.models import DEFAULT_WORKSPACE_ID, UserRole
    from app.services.auth_service import create_user

    user = create_user(
        db_session,
        email="user@client.test",
        password=CLIENT_PASSWORD,
        role=UserRole.CLIENT_USER,
        full_name="Client User",
        workspace_id=DEFAULT_WORKSPACE_ID,
    )
    db_session.commit()
    return user


@pytest.fixture()
def admin_token(admin_user) -> str:
    from app.services.auth_service import create_access_token

    return create_access_token(admin_user)


@pytest.fixture()
def client_token(client_user) -> str:
    from app.services.auth_service import create_access_token

    return create_access_token(client_user)


def _authed_client(db_session: Session, token: str) -> Generator[TestClient, None, None]:
    """Its own TestClient instance (not the shared `client` fixture) so that a
    single test can hold an `auth_client` and an `admin_client` at the same time
    without one's Authorization header clobbering the other's — they used to
    share one TestClient and mutate the same headers dict in place."""
    from app.db.session import get_db
    from app.main import app

    def _override() -> Generator[Session, None, None]:
        yield db_session

    app.dependency_overrides[get_db] = _override
    with TestClient(app) as test_client:
        test_client.headers.update({"Authorization": f"Bearer {token}"})
        yield test_client
    app.dependency_overrides.clear()


@pytest.fixture()
def auth_client(db_session, client_token) -> Generator[TestClient, None, None]:
    """TestClient authenticated as a client_user, for the /app tree."""
    yield from _authed_client(db_session, client_token)


@pytest.fixture()
def admin_client(db_session, admin_token) -> Generator[TestClient, None, None]:
    """TestClient authenticated as a dclab_admin, for the /admin tree."""
    yield from _authed_client(db_session, admin_token)


@pytest.fixture()
def sample_csv_bytes() -> bytes:
    return (
        "external_id,customer_id,amount,currency,stage,source,owner_id,created_at,"
        "close_date,last_contact_days_ago,engagement_score,sales_rep_available,"
        "industry,num_interactions,converted\n"
        "opp_1,cust_1,100000,AED,proposal,inbound,rep_1,2026-01-15,2026-09-01,5,0.88,true,telecom,14,1\n"
        "opp_2,cust_2,8000,AED,prospecting,outbound,rep_2,2026-03-01,2026-10-01,40,0.21,true,retail,2,0\n"
        "opp_3,cust_3,45000,AED,negotiation,referral,rep_1,2026-02-10,2026-08-01,4,0.91,true,saas,18,1\n"
    ).encode()
