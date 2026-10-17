"""Business-view questions (D7a): the only writer of ``business_questions`` / ``business_question_replies``.

Authority (re-checked on every call; the routes are signed-in sessions only, so no token or agent gets
here): asking needs an explicit ML-write membership (never platform staff, never a business user);
replying needs to be a business user of the workspace (a ``client_user`` account: its one write here) or
an ML-write person; closing is the asker (still ML-write) or a workspace owner / administrator, with
``If-Match``. Everything is workspace-scoped (another tenant's id is 404). Question and reply text is
untrusted: control characters are refused and it is never logged or put in a notification.
"""

from __future__ import annotations

import unicodedata
from collections.abc import Callable
from typing import Any
from uuid import UUID

from sqlalchemy import case, exists, func, or_, select
from sqlalchemy.orm import Session

from app.db.models import (
    BatchPrediction,
    BusinessQuestion,
    BusinessQuestionReply,
    ModelVersion,
    MonitoringWindow,
    Project,
    User,
    WorkspaceRole,
)
from app.domain import business_questions as bq
from app.domain.errors import IdentityError
from app.domain.outcomes import clean as clean_name
from app.services.authorization_service import (
    can_read_workspace,
    explicit_workspace_role,
    is_compatibility_client,
    is_ml_write_role,
    platform_role_for,
    workspace_role_for,
)
from app.services.notification_service import notify_question
from app.services.project_service import get_project

_SUBJECT_MODELS = {"model_version": ModelVersion, "batch_prediction": BatchPrediction,
                   "monitoring_window": MonitoringWindow}
_STATUS_ORDER = case((BusinessQuestion.status == bq.STATUS_OPEN, 0),
                     (BusinessQuestion.status == bq.STATUS_ANSWERED, 1), else_=2)


class QuestionError(Exception):
    def __init__(self, status: int, code: str, message: str) -> None:
        super().__init__(message)
        self.status_code, self.code, self.message = status, code, message


# --- authority ------------------------------------------------------------------------------------


def is_data_team(db: Session, user: User, workspace_id: UUID) -> bool:
    """An explicit, unsuspended ML-write membership (a business user resolves to viewer; no platform staff)."""

    return platform_role_for(db, user) is None and is_ml_write_role(explicit_workspace_role(db, user, workspace_id))


def is_business_user(db: Session, user: User, workspace_id: UUID) -> bool:
    return (is_compatibility_client(user) and platform_role_for(db, user) is None
            and workspace_role_for(db, user, workspace_id) is not None)


def _is_admin(db: Session, user: User, workspace_id: UUID) -> bool:
    return platform_role_for(db, user) is None and explicit_workspace_role(db, user, workspace_id) in (
        WorkspaceRole.WORKSPACE_OWNER, WorkspaceRole.WORKSPACE_ADMIN)


class _Viewer:
    def __init__(self, db: Session, user: User, workspace_id: UUID) -> None:
        if not can_read_workspace(db, user, workspace_id):
            raise IdentityError("not authorized for this workspace", status_code=403)
        self.id = user.id
        self.data_team = is_data_team(db, user, workspace_id)
        self.business = is_business_user(db, user, workspace_id)
        self.admin = _is_admin(db, user, workspace_id)

    def can_reply(self, row: BusinessQuestion) -> bool:
        return row.status != bq.STATUS_CLOSED and (self.business or self.data_team)

    def reply_side(self, row: BusinessQuestion) -> str | None:
        """The side a reply from this reader is written as (same rule as ``reply``), or None."""

        if not self.can_reply(row):
            return None
        return bq.SIDE_BUSINESS if self.business else bq.SIDE_DATA_TEAM

    def can_close(self, row: BusinessQuestion) -> bool:
        return row.status != bq.STATUS_CLOSED and ((row.asked_by_user_id == self.id and self.data_team) or self.admin)


# --- text -----------------------------------------------------------------------------------------


def clean_text(raw: str) -> str:
    """Line endings to LF and outer whitespace trimmed; refuses empty text, control characters (other
    than tab and line feed), format characters (bidi, zero-width, BOM, tag characters; only the emoji
    joiner U+200D is kept) and surrogates. Never echoes the text."""

    text = raw.replace("\r\n", "\n").replace("\r", "\n").strip()
    if not text or len(text) > bq.TEXT_MAX_CHARS:
        raise QuestionError(422, "question_text_invalid", f"write 1 to {bq.TEXT_MAX_CHARS} characters")
    for ch in text:
        category = unicodedata.category(ch)
        if (category == "Cc" and ch not in "\n\t") or category == "Cs" or (category == "Cf" and ch != "\u200d"):
            raise QuestionError(422, "question_text_invalid", "the text contains control characters")
    return text


# --- reads ----------------------------------------------------------------------------------------


def _person(viewer: _Viewer, user_id: UUID, side: str) -> bq.QuestionPersonRead:
    return bq.QuestionPersonRead(is_you=user_id == viewer.id, side=side)


def _subject(row: BusinessQuestion) -> bq.QuestionSubject | None:
    for kind, column in bq.SUBJECT_COLUMNS.items():
        if getattr(row, column) is not None:
            return bq.QuestionSubject(kind=kind, id=getattr(row, column))
    return None


def _project_names(db: Session, workspace_id: UUID, project_ids: list[UUID]) -> dict[UUID, str]:
    if not project_ids:
        return {}
    return {pid: clean_name(name) or "Project" for pid, name in db.execute(select(Project.id, Project.name).where(
        Project.workspace_id == workspace_id, Project.id.in_(set(project_ids)))).all()}


def _read(viewer: _Viewer, row: BusinessQuestion, count: int, last: Any, project_name: str) -> dict[str, Any]:
    return dict(
        id=row.id, project_id=row.project_id, project_name=project_name, subject=_subject(row), text=row.text, status=row.status,
        asked_by=_person(viewer, row.asked_by_user_id, bq.SIDE_DATA_TEAM), reply_count=count, last_reply_at=last,
        created_at=row.created_at, updated_at=row.updated_at, closed_at=row.closed_at,
        closed_by=_person(viewer, row.closed_by_user_id, bq.SIDE_DATA_TEAM) if row.closed_by_user_id else None,
        version=row.version, can_reply=viewer.can_reply(row), can_close=viewer.can_close(row),
        you_reply_as=viewer.reply_side(row),
    )


def _load(db: Session, workspace_id: UUID, question_id: UUID, *, lock: bool = False) -> BusinessQuestion:
    stmt = select(BusinessQuestion).where(BusinessQuestion.workspace_id == workspace_id,
                                          BusinessQuestion.id == question_id)
    row = db.scalar(stmt.with_for_update() if lock else stmt)
    if row is None:
        raise QuestionError(404, "not_found", "question not found")
    return row


def find_question(db: Session, workspace_id: UUID, question_id: Any, *, reply_id: Any = None) -> UUID | None:
    """Idempotency replays: the question (or the question of the reply) in this workspace, else None."""

    if reply_id is not None:
        return db.scalar(select(BusinessQuestionReply.question_id).where(
            BusinessQuestionReply.workspace_id == workspace_id, BusinessQuestionReply.id == reply_id,
            BusinessQuestionReply.question_id == question_id))
    return db.scalar(select(BusinessQuestion.id).where(BusinessQuestion.workspace_id == workspace_id,
                                                       BusinessQuestion.id == question_id))


def question_detail(db: Session, *, actor: User, workspace_id: UUID, question_id: UUID) -> bq.QuestionDetailRead:
    viewer = _Viewer(db, actor, workspace_id)
    row = _load(db, workspace_id, question_id)
    replies = db.scalars(select(BusinessQuestionReply).where(
        BusinessQuestionReply.workspace_id == workspace_id, BusinessQuestionReply.question_id == row.id)
        .order_by(BusinessQuestionReply.created_at, BusinessQuestionReply.id)).all()
    items = [bq.QuestionReplyRead(id=r.id, text=r.text, author=_person(viewer, r.author_user_id, r.author_side),
                                  created_at=r.created_at) for r in replies]
    name = _project_names(db, workspace_id, [row.project_id]).get(row.project_id, "Project")
    return bq.QuestionDetailRead(**_read(viewer, row, len(items), items[-1].created_at if items else None, name),
                                 replies=items)


def list_questions(db: Session, *, actor: User, workspace_id: UUID, project_id: UUID | None = None,
                   status: str | None = None, mine: bool = False, limit: int = bq.LIST_DEFAULT) -> bq.QuestionList:
    """Open first, then answered, then closed; newest change first within. ``mine``: questions you asked
    or replied to. ``ProjectNotFoundError`` for a project of another workspace."""

    viewer = _Viewer(db, actor, workspace_id)
    if project_id is not None:
        get_project(db, actor=actor, workspace_id=workspace_id, project_id=project_id)
    limit = max(1, min(int(limit), bq.LIST_MAX))
    q, r = BusinessQuestion, BusinessQuestionReply
    stmt = select(q).where(q.workspace_id == workspace_id)
    if project_id is not None:
        stmt = stmt.where(q.project_id == project_id)
    if status is not None:
        stmt = stmt.where(q.status == status)
    if mine:
        stmt = stmt.where(or_(q.asked_by_user_id == actor.id, exists().where(
            r.workspace_id == q.workspace_id, r.question_id == q.id, r.author_user_id == actor.id)))
    rows = db.scalars(stmt.order_by(_STATUS_ORDER, q.updated_at.desc(), q.id.desc()).limit(limit + 1)).all()
    page = rows[:limit]
    stats = {qid: (int(count), last) for qid, count, last in db.execute(
        select(r.question_id, func.count(), func.max(r.created_at))
        .where(r.workspace_id == workspace_id, r.question_id.in_([row.id for row in page]))
        .group_by(r.question_id)).all()} if page else {}
    names = _project_names(db, workspace_id, [row.project_id for row in page])
    return bq.QuestionList(items=[bq.QuestionRead(**_read(viewer, row, *stats.get(row.id, (0, None)),
                                                        names.get(row.project_id, "Project")))
                                  for row in page], has_more=len(rows) > limit, limit=limit)


# --- writes ---------------------------------------------------------------------------------------


def ask(db: Session, *, actor: User, workspace_id: UUID, project_id: UUID, request: bq.QuestionAskRequest,
        before_commit: Callable[[BusinessQuestion], None]) -> BusinessQuestion:
    get_project(db, actor=actor, workspace_id=workspace_id, project_id=project_id)
    if not is_data_team(db, actor, workspace_id):
        raise QuestionError(403, "question_ask_not_permitted",
                            "asking the business is for people with ML-write in this workspace")
    text = clean_text(request.text)
    links: dict[str, UUID] = {}
    if request.subject is not None:
        model = _SUBJECT_MODELS[request.subject.kind]
        found = db.scalar(select(model.id).where(model.workspace_id == workspace_id, model.id == request.subject.id,
                                                 model.project_id == project_id))
        if found is None:
            raise QuestionError(422, "question_subject_not_found", f"no such {request.subject.kind} in this project")
        links[bq.SUBJECT_COLUMNS[request.subject.kind]] = found
    unclosed = db.scalar(select(func.count()).select_from(BusinessQuestion).where(
        BusinessQuestion.workspace_id == workspace_id, BusinessQuestion.project_id == project_id,
        BusinessQuestion.status != bq.STATUS_CLOSED))
    if unclosed >= bq.UNCLOSED_PER_PROJECT_MAX:
        raise QuestionError(409, "too_many_open_questions",
                            f"this project has {bq.UNCLOSED_PER_PROJECT_MAX} questions not closed; close some first")
    row = BusinessQuestion(workspace_id=workspace_id, project_id=project_id, asked_by_user_id=actor.id, text=text,
                           status=bq.STATUS_OPEN, version=1, **links)
    db.add(row)
    db.flush()
    notify_question(db, question=row, reply_id=None, business_reply=False)
    before_commit(row)
    db.commit()
    db.refresh(row)
    return row


def reply(db: Session, *, actor: User, workspace_id: UUID, question_id: UUID, request: bq.QuestionReplyRequest,
          before_commit: Callable[[BusinessQuestionReply], None]) -> BusinessQuestion:
    """A business user's reply marks the question ``answered``; a data-team follow-up re-opens it."""

    viewer = _Viewer(db, actor, workspace_id)
    row = _load(db, workspace_id, question_id, lock=True)
    if not (viewer.business or viewer.data_team):
        raise QuestionError(403, "question_reply_not_permitted",
                            "replies come from this workspace's business users and its data team")
    if row.status == bq.STATUS_CLOSED:
        raise QuestionError(409, "question_closed", "this question is closed")
    text = clean_text(request.text)
    count = db.scalar(select(func.count()).select_from(BusinessQuestionReply).where(
        BusinessQuestionReply.workspace_id == workspace_id, BusinessQuestionReply.question_id == row.id))
    if count >= bq.REPLIES_PER_QUESTION_MAX:
        raise QuestionError(409, "question_reply_limit", f"a question takes at most {bq.REPLIES_PER_QUESTION_MAX} "
                            "replies; ask a new question")
    side = bq.SIDE_BUSINESS if viewer.business else bq.SIDE_DATA_TEAM
    item = BusinessQuestionReply(workspace_id=workspace_id, question_id=row.id, author_user_id=actor.id,
                                 author_side=side, text=text)
    db.add(item)
    row.status = bq.STATUS_ANSWERED if viewer.business else bq.STATUS_OPEN
    row.version += 1
    row.updated_at = func.now()
    db.flush()
    notify_question(db, question=row, reply_id=item.id, business_reply=viewer.business)
    before_commit(item)
    db.commit()
    db.refresh(row)
    return row


def close(db: Session, *, actor: User, workspace_id: UUID, question_id: UUID,
          precondition: Callable[[int], None], before_commit: Callable[[BusinessQuestion], None]) -> BusinessQuestion:
    viewer = _Viewer(db, actor, workspace_id)
    row = _load(db, workspace_id, question_id, lock=True)
    if not ((row.asked_by_user_id == viewer.id and viewer.data_team) or viewer.admin):
        raise QuestionError(403, "question_close_not_permitted",
                            "the person who asked or a workspace owner or administrator closes a question")
    precondition(row.version)
    if row.status == bq.STATUS_CLOSED:
        raise QuestionError(409, "question_closed", "this question is already closed")
    row.status, row.closed_by_user_id, row.closed_at = bq.STATUS_CLOSED, actor.id, func.now()
    row.version += 1
    row.updated_at = func.now()
    db.flush()
    before_commit(row)
    db.commit()
    db.refresh(row)
    return row
