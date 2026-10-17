"""Business-view questions: the data team asks the workspace's business users; they reply.

Revision ID: 0090_business_questions
Revises: 0089_monitoring_thresholds
Create Date: 2026-10-17

D7a. Expand-only: two new tables, and two notification kinds added to two existing CHECKs.

- ``business_questions``: one question per row, tenant composite FKs to the project and to the optional
  link (a model version, a prediction list or a monitoring week; at most one). Text is capped and holds no
  control characters other than tab / line feed (``ck_business_questions_text``); ``status`` is open |
  answered | closed and ``closed_at`` / ``closed_by_user_id`` are set exactly when closed. A guard trigger
  freezes text, asker, project, link and ``created_at``, makes ``closed`` final and requires the next
  ``version`` on every change (the ``If-Match`` of a close).
- ``business_question_replies``: append-only replies (UPDATE refused; DELETE only under the retention /
  workspace-deletion guard of the 0071 functions; ``created_at`` forced on insert), tenant composite FK to
  the question, ``author_side`` business | data_team.
- ``ck_notification_preferences_kind`` / ``ck_notification_outbox_kind`` accept ``question_asked`` and
  ``question_answered`` (webhook tables unchanged: these kinds are never sent to customer systems). Each
  CHECK is re-added NOT VALID then validated (the repo convention, 0069 / 0082 / 0089).

All under ``lock_timeout``. Literals are identical in ``app/domain/business_questions.py``,
``app/domain/notifications.py`` and ``app/db/integrity.py``.

Downgrade drops the triggers, the function and both tables (questions and replies are gone), deletes
notification rows and preferences of the two kinds, and restores the narrower CHECKs. Rolling the
application back does not need it: older code never reads the new tables or enqueues the new kinds.
"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0090_business_questions"
down_revision: Union[str, Sequence[str], None] = "0089_monitoring_thresholds"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_CK_TEXT = r"char_length(btrim(text, E' \t\n')) BETWEEN 1 AND 2000 AND text !~ '[\x01-\x08\x0b-\x1f\x7f-\x9f]'"
_CK_STATUS = "status IN ('open', 'answered', 'closed')"
_CK_CLOSED = "(status = 'closed') = (closed_at IS NOT NULL) AND (closed_at IS NULL) = (closed_by_user_id IS NULL)"
_CK_SUBJECT = "num_nonnulls(model_version_id, batch_prediction_id, monitoring_window_id) <= 1"
_CK_SIDE = "author_side IN ('business', 'data_team')"
_OLD_KINDS = "'decision_needed', 'run_finished', 'run_failed', 'scheduled_scoring_result', 'drift_alert', 'quota_warning'"
_NEW_KINDS = f"{_OLD_KINDS}, 'question_asked', 'question_answered'"
_KIND_CHECKS = (  # (table, constraint, new definition, old definition)
    ("notification_preferences", "ck_notification_preferences_kind", f"kind IN ({_NEW_KINDS})",
     f"kind IN ({_OLD_KINDS})"),
    ("notification_outbox", "ck_notification_outbox_kind", f"kind IN ({_NEW_KINDS}, 'ping')",
     f"kind IN ({_OLD_KINDS}, 'ping')"),
)
_GUARD = """
CREATE OR REPLACE FUNCTION guard_business_question_update()
RETURNS trigger AS $$
BEGIN
    IF ROW(NEW.id, NEW.workspace_id, NEW.project_id, NEW.asked_by_user_id, NEW.model_version_id,
           NEW.batch_prediction_id, NEW.monitoring_window_id, NEW.text, NEW.created_at)
       IS DISTINCT FROM ROW(OLD.id, OLD.workspace_id, OLD.project_id, OLD.asked_by_user_id, OLD.model_version_id,
           OLD.batch_prediction_id, OLD.monitoring_window_id, OLD.text, OLD.created_at) THEN
        RAISE EXCEPTION 'business_questions: the question is frozen';
    END IF;
    IF OLD.status = 'closed' THEN
        RAISE EXCEPTION 'business_questions: a closed question is final';
    END IF;
    IF NEW.version <> OLD.version + 1 THEN
        RAISE EXCEPTION 'business_questions: every change takes the next version';
    END IF;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql
"""
_TRIGGERS = (
    "CREATE TRIGGER business_questions_guard BEFORE UPDATE ON business_questions "
    "FOR EACH ROW EXECUTE FUNCTION guard_business_question_update()",
    "CREATE TRIGGER business_question_replies_append_only BEFORE UPDATE OR DELETE ON business_question_replies "
    "FOR EACH ROW EXECUTE FUNCTION prevent_mutation_except_retention()",
    "CREATE TRIGGER business_question_replies_created_at_now BEFORE INSERT ON business_question_replies "
    "FOR EACH ROW EXECUTE FUNCTION force_created_at_now()",
)


def _tenant_fk(column: str, table: str) -> sa.ForeignKeyConstraint:
    return sa.ForeignKeyConstraint(["workspace_id", column], [f"{table}.workspace_id", f"{table}.id"],
                                   name=f"fk_business_questions_{column}")


def _swap_check(table: str, name: str, definition: str) -> None:
    op.drop_constraint(name, table, type_="check")
    op.execute(sa.text(f"ALTER TABLE {table} ADD CONSTRAINT {name} CHECK ({definition}) NOT VALID"))
    op.execute(sa.text(f"ALTER TABLE {table} VALIDATE CONSTRAINT {name}"))


def upgrade() -> None:
    op.execute("SET LOCAL lock_timeout = '5s'")
    uuid = postgresql.UUID(as_uuid=True)
    now = sa.text("now()")
    op.create_table(
        "business_questions",
        sa.Column("id", uuid, primary_key=True),
        sa.Column("workspace_id", uuid, sa.ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False),
        sa.Column("project_id", uuid, nullable=False),
        sa.Column("asked_by_user_id", uuid, sa.ForeignKey("users.id"), nullable=False),
        sa.Column("model_version_id", uuid, nullable=True),
        sa.Column("batch_prediction_id", uuid, nullable=True),
        sa.Column("monitoring_window_id", uuid, nullable=True),
        sa.Column("text", sa.String(length=2000), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("closed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("closed_by_user_id", uuid, sa.ForeignKey("users.id"), nullable=True),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=now, nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=now, nullable=False),
        sa.UniqueConstraint("workspace_id", "id", name="uq_business_questions_workspace_id"),
        sa.ForeignKeyConstraint(["workspace_id", "project_id"], ["projects.workspace_id", "projects.id"],
                                name="fk_business_questions_workspace_project"),
        _tenant_fk("model_version_id", "model_versions"),
        _tenant_fk("batch_prediction_id", "batch_predictions"),
        _tenant_fk("monitoring_window_id", "monitoring_windows"),
        sa.CheckConstraint(_CK_TEXT, name="ck_business_questions_text"),
        sa.CheckConstraint(_CK_STATUS, name="ck_business_questions_status"),
        sa.CheckConstraint(_CK_CLOSED, name="ck_business_questions_closed"),
        sa.CheckConstraint(_CK_SUBJECT, name="ck_business_questions_subject"),
        sa.CheckConstraint("version >= 1", name="ck_business_questions_version"),
    )
    op.create_index("ix_business_questions_project_created_at", "business_questions",
                    ["workspace_id", "project_id", sa.text("created_at DESC")])
    op.create_index("ix_business_questions_status_updated_at", "business_questions",
                    ["workspace_id", "status", sa.text("updated_at DESC")])
    op.create_index("ix_business_questions_asked_by_user_id", "business_questions", ["asked_by_user_id"])
    op.create_index("ix_business_questions_closed_by_user_id", "business_questions", ["closed_by_user_id"],
                    postgresql_where=sa.text("closed_by_user_id IS NOT NULL"))
    for column in ("model_version_id", "batch_prediction_id", "monitoring_window_id"):
        op.create_index(f"ix_business_questions_{column}", "business_questions", ["workspace_id", column],
                        postgresql_where=sa.text(f"{column} IS NOT NULL"))
    op.create_table(
        "business_question_replies",
        sa.Column("id", uuid, primary_key=True),
        sa.Column("workspace_id", uuid, sa.ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False),
        sa.Column("question_id", uuid, nullable=False),
        sa.Column("author_user_id", uuid, sa.ForeignKey("users.id"), nullable=False),
        sa.Column("author_side", sa.String(length=16), nullable=False),
        sa.Column("text", sa.String(length=2000), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=now, nullable=False),
        sa.UniqueConstraint("workspace_id", "id", name="uq_business_question_replies_workspace_id"),
        sa.ForeignKeyConstraint(["workspace_id", "question_id"],
                                ["business_questions.workspace_id", "business_questions.id"],
                                name="fk_business_question_replies_workspace_question"),
        sa.CheckConstraint(_CK_TEXT, name="ck_business_question_replies_text"),
        sa.CheckConstraint(_CK_SIDE, name="ck_business_question_replies_side"),
    )
    op.create_index("ix_business_question_replies_question_created_at", "business_question_replies",
                    ["workspace_id", "question_id", "created_at"])
    op.create_index("ix_business_question_replies_author_user_id", "business_question_replies", ["author_user_id"])
    op.execute(sa.text(_GUARD))
    for statement in _TRIGGERS:
        op.execute(sa.text(statement))
    for table, name, new, _old in _KIND_CHECKS:
        _swap_check(table, name, new)


def downgrade() -> None:
    op.execute("SET LOCAL lock_timeout = '5s'")
    op.execute(sa.text("DELETE FROM notification_outbox WHERE kind IN ('question_asked', 'question_answered')"))
    op.execute(sa.text("DELETE FROM notification_preferences WHERE kind IN ('question_asked', 'question_answered')"))
    for table, name, _new, old in _KIND_CHECKS:
        _swap_check(table, name, old)
    for trigger, table in (("business_question_replies_created_at_now", "business_question_replies"),
                           ("business_question_replies_append_only", "business_question_replies"),
                           ("business_questions_guard", "business_questions")):
        op.execute(sa.text(f"DROP TRIGGER IF EXISTS {trigger} ON {table}"))
    op.drop_table("business_question_replies")
    op.drop_table("business_questions")
    op.execute(sa.text("DROP FUNCTION IF EXISTS guard_business_question_update()"))
