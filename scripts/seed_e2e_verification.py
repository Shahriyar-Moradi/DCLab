"""Seed synthetic identities, tenants and isolated insights for browser verification.

This script is intentionally limited to databases whose name contains
``verify`` or ``e2e``. It never reads or prints provider credentials.
"""

from __future__ import annotations

import argparse
import json
from datetime import UTC, datetime
from urllib.parse import urlsplit

from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, select, text
from sqlalchemy.engine import make_url

from app.config import get_settings
from app.db.models import (
    BusinessDomain,
    BusinessProfile,
    SimulationRun,
    User,
    UserRole,
    Workspace,
    WorkspaceCapability,
    WorkspaceMembership,
    WorkspaceRole,
)
from app.db.session import get_session_factory
from app.services.auth_service import create_user
from app.services.lineage_service import enable_workspace_domain, seed_business_domains
from app.services.workspace_capability_service import BUSINESS_CAPABILITIES

FIXTURE_PASSWORD = "VerificationOnly123!"
ACCOUNTS = (
    ("dclab-admin@verification.invalid", UserRole.DCLAB_ADMIN, None),
    ("dclab-developer@verification.invalid", UserRole.DCLAB_DEVELOPER, None),
    ("business-admin-a@verification.invalid", UserRole.BUSINESS_ADMIN, "business-a"),
    (
        "business-developer-a@verification.invalid",
        UserRole.BUSINESS_DEVELOPER,
        "business-a",
    ),
    ("client-user@verification.invalid", UserRole.CLIENT_USER, "business-a"),
    ("multi-workspace@verification.invalid", UserRole.WORKSPACE_ADMIN, "business-a"),
    # Read-only member (create_user stores a ``viewer`` membership for this role): lets specs prove a viewer cannot write.
    ("viewer-a@verification.invalid", UserRole.VIEWER, "business-a"),
)


def _assert_disposable_database() -> None:
    database = urlsplit(get_settings().database_url).path.rsplit("/", 1)[-1].lower()
    if "verify" not in database and "e2e" not in database:
        raise RuntimeError(
            "Refusing to seed a non-verification database; use a database name "
            "containing 'verify' or 'e2e'."
        )


def _recreate_database() -> None:
    url = make_url(get_settings().database_url)
    database = url.database or ""
    if "verify" not in database.lower() and "e2e" not in database.lower():
        raise RuntimeError("Refusing to recreate a non-verification database.")
    admin = create_engine(
        url.set(database="postgres"), isolation_level="AUTOCOMMIT"
    )
    try:
        with admin.connect() as connection:
            connection.execute(
                text(
                    "SELECT pg_terminate_backend(pid) FROM pg_stat_activity "
                    "WHERE datname = :database AND pid <> pg_backend_pid()"
                ),
                {"database": database},
            )
            connection.execute(text(f'DROP DATABASE IF EXISTS "{database}"'))
            connection.execute(text(f'CREATE DATABASE "{database}"'))
    finally:
        admin.dispose()
    command.upgrade(Config("alembic.ini"), "head")


def _workspace(db, slug: str, name: str) -> Workspace:
    row = db.scalar(select(Workspace).where(Workspace.slug == slug))
    if row is None:
        row = Workspace(slug=slug, name=name)
        db.add(row)
        db.flush()
        db.add(
            BusinessProfile(
                workspace_id=row.id,
                legal_name=name,
                profile_data={"fixture": "e2e-verification"},
            )
        )
        db.flush()
    return row


def _seed_isolated_insights(db, workspaces: dict[str, Workspace]) -> None:
    """Use tiny synthetic rows so the browser proves real BFF tenant filtering."""
    for slug, subject in (
        ("business-a", "S0P04D-ALPHA"),
        ("business-b", "S0P04D-BETA"),
    ):
        payload = {
            "use_case": "churn",
            "model_version": "e2e-synthetic-v1",
            "policy_version": "e2e-synthetic-v1",
            "fusion": "synthetic",
            "heroes": [{
                "external_id": subject,
                "agreement": 0.8,
                "action_key": "email",
                "expected_value": 100.0,
                "incremental_value": 10.0,
                "features": {"email_engagement": 0.2},
            }],
            "sample_decisions": [],
        }
        db.add(SimulationRun(
            workspace_id=workspaces[slug].id,
            use_case="churn",
            model_version=payload["model_version"],
            policy_version=payload["policy_version"],
            fusion=payload["fusion"],
            payload=payload,
            created_at=datetime(2026, 9, 1, tzinfo=UTC),
        ))

    # The same historical shape has no proven owner; it must never surface.
    orphan_payload = {
        "use_case": "churn",
        "model_version": "e2e-archive-v1",
        "policy_version": "e2e-archive-v1",
        "fusion": "synthetic",
        "heroes": [{"external_id": "S0P04D-ARCHIVED"}],
        "sample_decisions": [],
    }
    db.add(SimulationRun(
        workspace_id=None,
        use_case="churn",
        model_version=orphan_payload["model_version"],
        policy_version=orphan_payload["policy_version"],
        fusion=orphan_payload["fusion"],
        payload=orphan_payload,
        created_at=datetime(2026, 9, 2, tzinfo=UTC),
    ))


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--recreate",
        action="store_true",
        help="Drop, recreate, and migrate the guarded verification database.",
    )
    args = parser.parse_args()
    _assert_disposable_database()
    if args.recreate:
        _recreate_database()
    db = get_session_factory()()
    try:
        workspaces = {
            "business-a": _workspace(db, "business-a", "Business A"),
            "business-b": _workspace(db, "business-b", "Business B"),
        }
        seed_business_domains(db)
        operations = db.scalar(
            select(BusinessDomain).where(BusinessDomain.slug == "operations")
        )
        if operations is None:
            operations = BusinessDomain(
                slug="operations",
                name="Operations",
                description="Operations workflows",
                default_config={},
                is_active=True,
            )
            db.add(operations)
            db.flush()

        enable_workspace_domain(
            db, workspace_id=workspaces["business-a"].id, domain_slug="labs"
        )
        enable_workspace_domain(
            db, workspace_id=workspaces["business-a"].id, domain_slug="operations"
        )
        enable_workspace_domain(
            db, workspace_id=workspaces["business-b"].id, domain_slug="labs"
        )

        for capability in BUSINESS_CAPABILITIES:
            db.add(
                WorkspaceCapability(
                    workspace_id=workspaces["business-a"].id,
                    capability=capability,
                    enabled=True,
                    configuration={"fixture": "e2e-verification"},
                )
            )

        users: list[User] = []
        for email, role, workspace_slug in ACCOUNTS:
            if db.scalar(select(User).where(User.email == email)) is not None:
                raise RuntimeError(
                    f"Fixture account already exists: {email}. Recreate the "
                    "disposable database before reseeding."
                )
            workspace_id = (
                workspaces[workspace_slug].id if workspace_slug is not None else None
            )
            users.append(
                create_user(
                    db,
                    email=email,
                    password=FIXTURE_PASSWORD,
                    role=role,
                    full_name=role.value.replace("_", " ").title(),
                    workspace_id=workspace_id,
                )
            )
            if email == "multi-workspace@verification.invalid":
                db.add(
                    WorkspaceMembership(
                        workspace_id=workspaces["business-b"].id,
                        user_id=users[-1].id,
                        role=WorkspaceRole.WORKSPACE_ADMIN.value,
                    )
                )
        _seed_isolated_insights(db, workspaces)
        db.commit()
        print(
            json.dumps(
                {
                    "database": "disposable verification database",
                    "workspaces": {
                        slug: str(workspace.id)
                        for slug, workspace in workspaces.items()
                    },
                    "accounts": [
                        {"email": user.email, "role": user.role} for user in users
                    ],
                    "fixture_password": FIXTURE_PASSWORD,
                },
                sort_keys=True,
            )
        )
        return 0
    finally:
        db.close()


if __name__ == "__main__":
    raise SystemExit(main())
