"""Jev L1 review items are agent_proposals rows; the run ``plan`` input is single use.

Revision ID: 0075_semantic_review_proposals
Revises: 0074_agent_run_release_guard
Create Date: 2026-10-05

P6.9-A step A2 (ADR 0008 §2, §3, §7, § Consequences; ADR 0009 §2.3). Additive:

- ``agent_proposals.semantic_answer_id`` (nullable; composite FK to
  ``semantic_decision_answers (workspace_id, id)``; one proposal per answer) and
  ``agent_proposals.run_id`` becomes nullable: CHECK ``ck_agent_proposals_source`` —
  exactly one of ``run_id`` / ``semantic_answer_id``; a semantic answer's proposal is a
  ``SemanticReviewProposal`` (new value of ``ck_agent_proposals_type``) at level 1. A Jev
  L1 disagreement at a decision point is such a ``proposed`` row; P6.6-A accepts or
  rejects it like an agent proposal. The transition trigger is unchanged (the new column
  is immutable like every non-decision column).
- ``execution_requests.plan_proposal_id`` (nullable; composite FK to
  ``agent_proposals (workspace_id, id)``, SET NULL on retention delete) with a partial
  unique index: the request that consumed a ``plan`` proposal (single use).

Locking: ADD COLUMN (nullable, no default) and DROP NOT NULL are catalog-only; one
transaction under ``lock_timeout = 5s`` holds the ADD CONSTRAINT locks until commit (the
NOT VALID + VALIDATE form only keeps the scans uniform; both tables are small today). The
``execution_requests`` index is built in that transaction (not CONCURRENTLY): a short
write block on a growing table, acceptable at this size. Downgrade locks both tables,
then refuses while a semantic review proposal or a consumed plan exists (no 0074 form).
"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0075_semantic_review_proposals"
down_revision: Union[str, Sequence[str], None] = "0074_agent_run_release_guard"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_TYPES_0074 = (
    "'ExperimentPlanProposal', 'ExperimentReviewProposal', 'DatasetInvestigationProposal', "
    "'ImprovementActionProposal', 'ToolCallProposal', 'ReleaseProposal'"
)
_TYPES = _TYPES_0074 + ", 'SemanticReviewProposal'"
# Identical literal to app/domain/agent_records.py CK_AGENT_PROPOSALS_SOURCE.
_SOURCE = (
    "num_nonnulls(run_id, semantic_answer_id) = 1 "
    "AND (semantic_answer_id IS NOT NULL) = (proposal_type = 'SemanticReviewProposal') "
    "AND (semantic_answer_id IS NULL OR level_at_proposal = 1)"
)


def upgrade() -> None:
    op.execute("SET LOCAL lock_timeout = '5s'")
    op.add_column("agent_proposals", sa.Column("semantic_answer_id", sa.UUID(), nullable=True))
    op.alter_column("agent_proposals", "run_id", existing_type=sa.UUID(), nullable=True)
    op.drop_constraint("ck_agent_proposals_type", "agent_proposals", type_="check")
    op.execute(f"ALTER TABLE agent_proposals ADD CONSTRAINT ck_agent_proposals_type "
               f"CHECK (proposal_type IN ({_TYPES})) NOT VALID")
    op.execute("ALTER TABLE agent_proposals VALIDATE CONSTRAINT ck_agent_proposals_type")
    op.execute(f"ALTER TABLE agent_proposals ADD CONSTRAINT ck_agent_proposals_source CHECK ({_SOURCE}) NOT VALID")
    op.execute("ALTER TABLE agent_proposals VALIDATE CONSTRAINT ck_agent_proposals_source")
    op.execute(
        "ALTER TABLE agent_proposals ADD CONSTRAINT fk_agent_proposals_workspace_semantic_answer "
        "FOREIGN KEY (workspace_id, semantic_answer_id) "
        "REFERENCES semantic_decision_answers (workspace_id, id) NOT VALID"
    )
    op.execute("ALTER TABLE agent_proposals VALIDATE CONSTRAINT fk_agent_proposals_workspace_semantic_answer")
    op.create_index(
        "uq_agent_proposals_semantic_answer", "agent_proposals", ["workspace_id", "semantic_answer_id"],
        unique=True, postgresql_where=sa.text("semantic_answer_id IS NOT NULL"),
    )

    op.add_column("execution_requests", sa.Column("plan_proposal_id", sa.UUID(), nullable=True))
    op.execute(
        "ALTER TABLE execution_requests ADD CONSTRAINT fk_execution_requests_workspace_plan_proposal "
        "FOREIGN KEY (workspace_id, plan_proposal_id) REFERENCES agent_proposals (workspace_id, id) "
        "ON DELETE SET NULL (plan_proposal_id) NOT VALID"
    )
    op.execute("ALTER TABLE execution_requests VALIDATE CONSTRAINT fk_execution_requests_workspace_plan_proposal")
    op.create_index(
        "uq_execution_requests_plan_proposal", "execution_requests", ["workspace_id", "plan_proposal_id"],
        unique=True, postgresql_where=sa.text("plan_proposal_id IS NOT NULL"),
    )


def downgrade() -> None:
    op.execute("SET LOCAL lock_timeout = '5s'")
    # No plan may be consumed (nor a review item written) between the check and the DDL.
    op.execute("LOCK TABLE agent_proposals, execution_requests IN SHARE ROW EXCLUSIVE MODE")
    bind = op.get_bind()
    if bind.execute(sa.text(
        "SELECT EXISTS (SELECT 1 FROM agent_proposals WHERE run_id IS NULL) "
        "OR EXISTS (SELECT 1 FROM execution_requests WHERE plan_proposal_id IS NOT NULL)"
    )).scalar():
        raise RuntimeError(
            "0075 downgrade refused: semantic review proposals or consumed plans exist "
            "(they have no 0074 form); remove them first"
        )
    op.drop_index("uq_execution_requests_plan_proposal", table_name="execution_requests")
    op.drop_constraint("fk_execution_requests_workspace_plan_proposal", "execution_requests", type_="foreignkey")
    op.drop_column("execution_requests", "plan_proposal_id")
    op.drop_index("uq_agent_proposals_semantic_answer", table_name="agent_proposals")
    op.drop_constraint("fk_agent_proposals_workspace_semantic_answer", "agent_proposals", type_="foreignkey")
    op.drop_constraint("ck_agent_proposals_source", "agent_proposals", type_="check")
    op.drop_constraint("ck_agent_proposals_type", "agent_proposals", type_="check")
    op.create_check_constraint("ck_agent_proposals_type", "agent_proposals", f"proposal_type IN ({_TYPES_0074})")
    op.alter_column("agent_proposals", "run_id", existing_type=sa.UUID(), nullable=False)
    op.drop_column("agent_proposals", "semantic_answer_id")
