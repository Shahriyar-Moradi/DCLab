"""Change one capability only in a guarded disposable E2E database."""

from __future__ import annotations

import argparse
from urllib.parse import urlsplit

from sqlalchemy import delete, select

from app.config import get_settings
from app.db.models import Workspace, WorkspaceCapability
from app.db.session import get_session_factory
from app.services.workspace_capability_service import BUSINESS_CAPABILITIES


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("workspace_slug")
    parser.add_argument("capability", choices=BUSINESS_CAPABILITIES)
    parser.add_argument("state", choices=("missing", "false", "true"))
    args = parser.parse_args()

    database = urlsplit(get_settings().database_url).path.rsplit("/", 1)[-1].lower()
    if "verify" not in database and "e2e" not in database:
        raise RuntimeError("Refusing to modify a non-verification database.")

    db = get_session_factory()()
    try:
        workspace = db.scalar(
            select(Workspace).where(Workspace.slug == args.workspace_slug)
        )
        if workspace is None:
            raise RuntimeError("Verification workspace not found.")
        db.execute(
            delete(WorkspaceCapability).where(
                WorkspaceCapability.workspace_id == workspace.id,
                WorkspaceCapability.capability == args.capability,
            )
        )
        if args.state != "missing":
            db.add(
                WorkspaceCapability(
                    workspace_id=workspace.id,
                    capability=args.capability,
                    enabled=args.state == "true",
                    configuration={"fixture": "e2e-verification"},
                )
            )
        db.commit()
        return 0
    finally:
        db.close()


if __name__ == "__main__":
    raise SystemExit(main())
