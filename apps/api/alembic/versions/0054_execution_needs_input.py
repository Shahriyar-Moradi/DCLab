"""execution needs_input waiting status

Revision ID: 0054_execution_needs_input
Revises: 0053_pipeline_run_branch
Create Date: 2026-09-09

Ambiguous target selection is a resumable control-plane wait, not a
scientific failure. Adds ``needs_input`` to ExecutionRequest.status and
the coarse ClientLabUpload.client_status CHECK. This revision does not
add a second state machine or a generic human-in-the-loop framework.
"""

from __future__ import annotations

from typing import Sequence, Union

from alembic import op

from alembic_frozen.rev_0054_execution_needs_input import (
    CK_CLIENT_LAB_UPLOADS_CLIENT_STATUS,
    CK_EXECUTION_REQUEST_STATUS,
)

revision: str = "0054_execution_needs_input"
down_revision: Union[str, Sequence[str], None] = "0053_pipeline_run_branch"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_PREVIOUS_EXECUTION_STATUS = (
    "status IN ('accepted', 'running', 'completed', 'failed')"
)
_PREVIOUS_CLIENT_STATUS = (
    "client_status IN ('queued', 'processing', 'completed', 'failed')"
)


def upgrade() -> None:
    op.drop_constraint(
        "ck_execution_requests_status", "execution_requests", type_="check"
    )
    op.create_check_constraint(
        "ck_execution_requests_status",
        "execution_requests",
        CK_EXECUTION_REQUEST_STATUS,
    )
    op.drop_constraint(
        "ck_client_lab_uploads_client_status",
        "client_lab_uploads",
        type_="check",
    )
    op.create_check_constraint(
        "ck_client_lab_uploads_client_status",
        "client_lab_uploads",
        CK_CLIENT_LAB_UPLOADS_CLIENT_STATUS,
    )


def downgrade() -> None:
    op.drop_constraint(
        "ck_client_lab_uploads_client_status",
        "client_lab_uploads",
        type_="check",
    )
    op.create_check_constraint(
        "ck_client_lab_uploads_client_status",
        "client_lab_uploads",
        _PREVIOUS_CLIENT_STATUS,
    )
    op.drop_constraint(
        "ck_execution_requests_status", "execution_requests", type_="check"
    )
    op.create_check_constraint(
        "ck_execution_requests_status",
        "execution_requests",
        _PREVIOUS_EXECUTION_STATUS,
    )
