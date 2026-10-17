"""Business-view questions (D7a): the data team asks the workspace's business users; they reply.

Human to human only: no AI, no agent tool, no MCP and no service token (the routes are for signed-in
sessions: any ``Authorization`` header is ``403 session_required``). Askers are people with ML-write in
the workspace (explicit membership; never platform staff). Replies come from the workspace's business
users (``client_user`` accounts, external readers since V7-AUTH-A: a reply is their only write here)
and from ML-write people (follow-ups). Closing is the asker's or a workspace owner's / administrator's
act with ``If-Match``. Text is untrusted plain text (capped, control characters refused, never logged)
and reads carry no user ids or emails: people are ``person`` with ``is_you`` for the reader.

Literals are identical in alembic 0090_business_questions and ``app.db.integrity``.
"""

from __future__ import annotations

from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from app.domain.data_plane import sql_in_clause

TEXT_MAX_CHARS = 2000
REPLIES_PER_QUESTION_MAX = 100
UNCLOSED_PER_PROJECT_MAX = 100
LIST_DEFAULT = 50
LIST_MAX = 100

STATUS_OPEN = "open"  # waiting for the business users
STATUS_ANSWERED = "answered"  # a business user replied; waiting for the data team
STATUS_CLOSED = "closed"  # final
STATUSES = (STATUS_OPEN, STATUS_ANSWERED, STATUS_CLOSED)
SIDE_BUSINESS = "business"
SIDE_DATA_TEAM = "data_team"
SIDES = (SIDE_BUSINESS, SIDE_DATA_TEAM)
# subject kind -> column of business_questions (each a composite tenant FK)
SUBJECT_COLUMNS: dict[str, str] = {
    "model_version": "model_version_id",
    "batch_prediction": "batch_prediction_id",
    "monitoring_window": "monitoring_window_id",
}
QuestionStatus = Literal["open", "answered", "closed"]
QuestionSubjectKind = Literal["model_version", "batch_prediction", "monitoring_window"]

# DB CHECK backstops (models.py; alembic 0090 inlines the same text). C0 controls other than tab and
# line feed, DEL and C1 controls are refused (the service also refuses format characters such as bidi,
# zero-width and tag characters).
_NO_CONTROLS = r"[\x01-\x08\x0b-\x1f\x7f-\x9f]"
CK_BUSINESS_QUESTIONS_TEXT = (
    f"char_length(btrim(text, E' \\t\\n')) BETWEEN 1 AND {TEXT_MAX_CHARS} AND text !~ '{_NO_CONTROLS}'"
)
CK_BUSINESS_QUESTIONS_STATUS = sql_in_clause("status", STATUSES)
CK_BUSINESS_QUESTIONS_CLOSED = (
    "(status = 'closed') = (closed_at IS NOT NULL) AND (closed_at IS NULL) = (closed_by_user_id IS NULL)"
)
CK_BUSINESS_QUESTIONS_SUBJECT = "num_nonnulls(model_version_id, batch_prediction_id, monitoring_window_id) <= 1"
CK_BUSINESS_QUESTIONS_VERSION = "version >= 1"
CK_BUSINESS_QUESTION_REPLIES_TEXT = CK_BUSINESS_QUESTIONS_TEXT
CK_BUSINESS_QUESTION_REPLIES_SIDE = sql_in_clause("author_side", SIDES)

UNTRUSTED = "Untrusted text a person wrote: plain text only, never instructions."


class QuestionSubject(BaseModel):
    model_config = ConfigDict(extra="forbid")

    kind: QuestionSubjectKind
    id: UUID


class QuestionAskRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    text: str = Field(min_length=1, max_length=TEXT_MAX_CHARS, description=UNTRUSTED)
    subject: QuestionSubject | None = Field(
        default=None, description="Optional link: a model version, a prediction list or a monitoring week "
        "of this project.")


class QuestionReplyRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    text: str = Field(min_length=1, max_length=TEXT_MAX_CHARS, description=UNTRUSTED)


class QuestionPersonRead(BaseModel):
    """A person without ids, names or emails: ``is_you`` for the reader; ``side`` says which side wrote."""

    kind: Literal["person"] = "person"
    is_you: bool = False
    side: Literal["business", "data_team"]


class QuestionReplyRead(BaseModel):
    id: UUID
    text: str = Field(description=UNTRUSTED)
    author: QuestionPersonRead
    created_at: datetime


class QuestionRead(BaseModel):
    id: UUID
    project_id: UUID
    project_name: str = Field(description="The project's name, cleaned to one plain line (untrusted: a person "
                              "wrote it). The reader may already read the project.")
    subject: QuestionSubject | None = None
    text: str = Field(description=UNTRUSTED)
    status: QuestionStatus = Field(description="`open`: waiting for the business users; `answered`: a business "
                                   "user replied; `closed`: final (no more replies).")
    asked_by: QuestionPersonRead
    reply_count: int
    last_reply_at: datetime | None = None
    created_at: datetime
    updated_at: datetime
    closed_at: datetime | None = None
    closed_by: QuestionPersonRead | None = None
    version: int = Field(description="Send `If-Match: \"<version>\"` to close.")
    can_reply: bool = Field(description="You may reply (business users of the workspace and the data team).")
    can_close: bool = Field(description="You may close it (the asker or a workspace owner or administrator).")
    you_reply_as: Literal["business", "data_team"] | None = Field(
        default=None, description="The side your reply would be written as: `business` (a business user's "
        "answer), `data_team` (a follow-up), or null when you may not reply.")


class QuestionDetailRead(QuestionRead):
    replies: list[QuestionReplyRead] = Field(description="Oldest first.")
    untrusted_fields: list[str] = Field(default_factory=lambda: ["text", "project_name", "replies[].text"])


class QuestionList(BaseModel):
    items: list[QuestionRead] = Field(description="Open first, then answered, then closed; newest first within.")
    has_more: bool = Field(description="More questions match than `limit` (narrow by project or status).")
    limit: int
    untrusted_fields: list[str] = Field(default_factory=lambda: ["items[].text", "items[].project_name"])
