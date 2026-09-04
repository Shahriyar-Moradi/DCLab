"""allow semantic leakage LLM purpose

Revision ID: 0028_semantic_leakage_purpose
Revises: 0027_repair_tenant_lineage
Create Date: 2026-09-04
"""

from typing import Sequence, Union

from alembic import op

revision: str = "0028_semantic_leakage_purpose"
down_revision: Union[str, Sequence[str], None] = "0027_repair_tenant_lineage"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_NEW = (
    "purpose IN ('semantic_target', 'semantic_missing_value', "
    "'semantic_column_type', 'semantic_leakage', 'pipeline_audit_routine', "
    "'pipeline_audit_deep')"
)
_OLD = (
    "purpose IN ('semantic_target', 'semantic_missing_value', "
    "'semantic_column_type', 'pipeline_audit_routine', "
    "'pipeline_audit_deep')"
)


def upgrade() -> None:
    op.drop_constraint("ck_llm_invocations_purpose", "llm_invocations", type_="check")
    op.create_check_constraint("ck_llm_invocations_purpose", "llm_invocations", _NEW)


def downgrade() -> None:
    op.drop_constraint("ck_llm_invocations_purpose", "llm_invocations", type_="check")
    op.create_check_constraint("ck_llm_invocations_purpose", "llm_invocations", _OLD)
