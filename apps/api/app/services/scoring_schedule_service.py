"""Weekly scheduled scoring: a person saves the schedule; the worker tick scores the week's file.

Config (API): ``save_schedule`` is a person with ML-write (never a service token or agent: the
route refuses them) creating or changing the project's one schedule; every save writes an
accepted ``scoring_schedule_changed`` History entry with the person's reason (a server-written
default is marked ``facts.rationale_default``). Runs act as ``updated_by_user_id``, the person
who last saved the schedule (an accepted deviation from "the creator": saving again is how
someone with the right fixes an owner warning). Reads are workspace-scoped; service-token
readers get no user ids.

Runs (worker): ``run_scoring_schedule_tick`` runs inside ``dclab worker run`` between jobs (no
new process or queue; a worker busy with one job for more than 24 hours records
``skipped_missed``). With ``ML_JOB_DISPATCHER=thread`` (an API-process debugging mode) no
worker runs, so no schedule runs. ``SCORING_SCHEDULE_ENABLED`` (the kill switch) is read by the
worker process: changing it needs a worker restart. The tick claims due schedules one at a time
``FOR UPDATE SKIP LOCKED``, handles only the most recent weekly slot (within 24 hours, else
``skipped_missed``; never a backlog), re-checks the person it acts as, the model in use and its
approval, picks the newest new scoring file and queues it through ``create_batch_prediction``
(the same path as "Score new data"); the run row, the prediction and the schedule's advance
commit together, and the scoring job re-checks the approval and the person before it loads
anything. ``UNIQUE (schedule_id, planned_for)`` backs the row lock, so a slot is handled once
across workers and restarts. One schedule's failure is recorded (``failed_to_queue``) and never
blocks the others.

Known limit (documented, accepted): uploads (``POST /v1/datasets``) record the acting person but
not whether a service token made them, so a token with ``datasets:write`` can upload a scoring
file that the next weekly run scores as the person who saved the schedule. The model in use and
its approval stay a person's choice; only the input file can come from an agent.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from app.db.models import (
    BatchPrediction,
    Dataset,
    Experiment,
    ModelVersion,
    ProjectDecisionRecord,
    ProjectRef,
    ScoringSchedule,
    ScoringScheduleRun,
    User,
)
from app.domain.data_plane import DATASET_PURPOSE_SCORING
from app.domain.decision_records import DECISION_SCORING_SCHEDULE_CHANGED, STATE_ACCEPTED, DecisionActor
from app.domain.errors import ExperimentRequestError, IdentityError, InvalidCursorError, RunQuotaExceededError
from app.domain.scoring_schedules import (
    CATCH_UP_HOURS,
    OUTCOME_FAILED_TO_QUEUE,
    OUTCOME_MESSAGES,
    OUTCOME_MISSED,
    OUTCOME_NO_APPROVAL,
    OUTCOME_NO_MODEL_IN_USE,
    OUTCOME_NO_NEW_FILE,
    OUTCOME_OWNER_NOT_ALLOWED,
    OUTCOME_QUEUED,
    NO_WORKER_MESSAGE,
    PLATFORM_PAUSED_MESSAGE,
    REASON_DATASET_UNAVAILABLE,
    REASON_INTERNAL_ERROR,
    REASON_MODEL_NOT_SCOREABLE,
    REASON_OWNER_MISSING,
    REASON_RELEASE_CHANGED,
    REASON_RUN_LIMIT,
    REASON_WORKER_WAS_DOWN,
    RECENT_RUNS,
    WEEKDAY_NAMES,
    ScoringScheduleRead,
    ScoringScheduleRunPage,
    ScoringScheduleRunRead,
    ScoringScheduleState,
    ScoringScheduleWrite,
)
from app.services import decision_record_service as drs
from app.services.authorization_service import can_perform_ml_write
from app.services.cursor_codec import open_cursor, sign_cursor
from app.services.model_release_service import current_release

logger = logging.getLogger("dclab.scoring_schedules")

WEEK = timedelta(days=7)
CATCH_UP = timedelta(hours=CATCH_UP_HOURS)
TICK_MAX_SCHEDULES = 200  # per tick; the rest wait for the next tick


def _now() -> datetime:
    return datetime.now(UTC)


def utc(value: datetime | None) -> datetime | None:
    """Slots are shown in UTC whatever the database session's time zone."""

    return value.astimezone(UTC) if value is not None else None


def slot_at_or_before(moment: datetime, weekday: int, hour_utc: int) -> datetime:
    """The latest weekly slot (``weekday`` at ``hour_utc``:00 UTC) not after ``moment``."""

    moment = moment.astimezone(UTC)
    slot = moment.replace(hour=hour_utc, minute=0, second=0, microsecond=0) - timedelta(
        days=(moment.weekday() - weekday) % 7)
    return slot if slot <= moment else slot - WEEK


def next_slot_after(moment: datetime, weekday: int, hour_utc: int) -> datetime:
    return slot_at_or_before(moment, weekday, hour_utc) + WEEK


def _platform_enabled() -> bool:
    from app.config import get_settings

    return bool(get_settings().scoring_schedule_enabled)


def _pause_message() -> str | None:
    """Why the platform holds every run, as this process sees its settings (None: runs go ahead)."""

    from app.config import get_settings

    if not _platform_enabled():
        return PLATFORM_PAUSED_MESSAGE
    if str(get_settings().ml_job_dispatcher or "").strip().lower() != "postgres":
        return NO_WORKER_MESSAGE  # thread / inline: no `dclab worker run`, so no tick
    return None


def _schedule(db: Session, workspace_id: UUID, project_id: UUID, *, lock: bool = False) -> ScoringSchedule | None:
    query = select(ScoringSchedule).where(
        ScoringSchedule.workspace_id == workspace_id, ScoringSchedule.project_id == project_id)
    return db.scalar(query.with_for_update() if lock else query)


def _champion_id(db: Session, workspace_id: UUID, project_id: UUID) -> UUID | None:
    return db.scalar(select(ProjectRef.model_version_id).where(
        ProjectRef.workspace_id == workspace_id, ProjectRef.project_id == project_id,
        ProjectRef.ref_kind == "champion_model"))


def _owner(db: Session, schedule: ScoringSchedule) -> User | None:
    """The person runs act as, while they can still score in the workspace (else None)."""

    user = db.get(User, schedule.updated_by_user_id) if schedule.updated_by_user_id else None
    if user is None or not user.is_active or not can_perform_ml_write(db, user, schedule.workspace_id):
        return None
    return user


# --- config (API) ----------------------------------------------------------------------------


def _when(weekday: int, hour_utc: int) -> str:
    return f"{WEEKDAY_NAMES[weekday]}s at {hour_utc:02d}:00 UTC"


def _default_reason(request: ScoringScheduleWrite, previous: dict[str, Any] | None) -> str:
    """Server-written text (marked ``rationale_default`` in the record's facts), never the person's words."""

    if request.enabled:
        return f"Weekly scoring turned on: {_when(request.weekday, request.hour_utc)}."
    was = previous or {"weekday": request.weekday, "hour_utc": request.hour_utc}
    return f"Weekly scoring turned off (was {_when(was['weekday'], was['hour_utc'])})."


def save_schedule(
    db: Session, *, actor: User, workspace_id: UUID, project_id: UUID, request: ScoringScheduleWrite,
    now: datetime | None = None,
) -> ProjectDecisionRecord:
    """Create or change the project's schedule as this person and write its History entry (does
    not commit). Runs then act as this person. Raises ``IdentityError`` / ``ProjectNotFoundError``
    / ``InvalidDecisionRecordError``."""

    human = DecisionActor.human(actor)
    drs.authorize_writer(db, human, workspace_id=workspace_id, allow_rule=False)
    drs.load_project(db, workspace_id=workspace_id, project_id=project_id)
    schedule = _schedule(db, workspace_id, project_id, lock=True)
    previous = None if schedule is None else {
        "weekday": schedule.weekday, "hour_utc": schedule.hour_utc, "enabled": schedule.enabled}
    written_by_server = not (request.rationale or "").strip()
    rationale = drs.clean_rationale(_default_reason(request, previous) if written_by_server else request.rationale)
    if schedule is None:  # a concurrent first save loses on uq_scoring_schedules_project (409, retry)
        schedule = ScoringSchedule(workspace_id=workspace_id, project_id=project_id, created_by_user_id=actor.id)
        db.add(schedule)
    else:
        schedule.updated_at = func.now()
    schedule.weekday, schedule.hour_utc, schedule.enabled = request.weekday, request.hour_utc, request.enabled
    schedule.updated_by_user_id = actor.id
    # Always the next slot after now: turning it on (again) never catches up on past weeks.
    schedule.next_planned_for = next_slot_after(now or _now(), request.weekday, request.hour_utc)
    db.flush()
    facts = {"weekday": request.weekday, "hour_utc": request.hour_utc, "timezone": "UTC", "enabled": request.enabled,
             "previous": previous, "rationale_default": written_by_server,
             "next_planned_for": utc(schedule.next_planned_for).isoformat() if request.enabled else None}
    row = drs.build_record(
        workspace_id=workspace_id, project_id=project_id, actor=human, decision_type=DECISION_SCORING_SCHEDULE_CHANGED,
        state=STATE_ACCEPTED, subject_kind="project", subject_id=None, rationale=rationale,
        facts=drs.clean_json_object(facts, label="facts"), evidence_refs=[],
        details=drs.clean_json_object({"scoring_schedule_id": str(schedule.id)}, label="details"),
    )
    return drs.insert_record(db, row)


def _run_read(row: ScoringScheduleRun) -> ScoringScheduleRunRead:
    return ScoringScheduleRunRead(
        id=row.id, planned_for=utc(row.planned_for), outcome=row.outcome, message=OUTCOME_MESSAGES[row.outcome],
        reason_code=row.reason_code, batch_prediction_id=row.batch_prediction_id,
        model_version_id=row.model_version_id, model_release_id=row.model_release_id,
        input_dataset_id=row.input_dataset_id, created_at=utc(row.created_at),
    )


def _runs_query(schedule: ScoringSchedule) -> Any:
    return select(ScoringScheduleRun).where(
        ScoringScheduleRun.workspace_id == schedule.workspace_id, ScoringScheduleRun.schedule_id == schedule.id
    ).order_by(ScoringScheduleRun.planned_for.desc())


def _warning(db: Session, schedule: ScoringSchedule, owner_ok: bool) -> str | None:
    if not schedule.enabled:
        return None
    if not owner_ok:
        return OUTCOME_MESSAGES[OUTCOME_OWNER_NOT_ALLOWED]
    if _champion_id(db, schedule.workspace_id, schedule.project_id) is None:
        return "Runs will score nothing until the project has a model in use."
    if current_release(db, workspace_id=schedule.workspace_id, project_id=schedule.project_id) is None:
        return "Runs will score nothing until a person approves the model in use for scoring."
    return None


def schedule_state(
    db: Session, *, actor: User, workspace_id: UUID, project_id: UUID, agent: bool
) -> ScoringScheduleState:
    """The project's schedule (or null) and its newest runs. ``agent=True`` (service tokens)
    drops the people's ids. Raises ``IdentityError`` / ``ProjectNotFoundError``."""

    from app.services.project_service import get_project

    get_project(db, actor=actor, workspace_id=workspace_id, project_id=project_id)
    schedule = _schedule(db, workspace_id, project_id)
    if schedule is None:
        return ScoringScheduleState(project_id=project_id, status="off")
    owner_ok = _owner(db, schedule) is not None
    paused = _pause_message()
    read = ScoringScheduleRead(
        id=schedule.id, weekday=schedule.weekday, weekday_name=WEEKDAY_NAMES[schedule.weekday],
        hour_utc=schedule.hour_utc, enabled=schedule.enabled,
        next_planned_for=utc(schedule.next_planned_for) if schedule.enabled else None,
        last_planned_for=utc(schedule.last_planned_for), created_at=utc(schedule.created_at),
        updated_at=utc(schedule.updated_at),
        created_by_user_id=None if agent else schedule.created_by_user_id,
        updated_by_user_id=None if agent else schedule.updated_by_user_id,
        owner_can_score=owner_ok, warning=_warning(db, schedule, owner_ok),
    )
    status = "off" if not schedule.enabled else ("paused_by_platform" if paused else "on")
    runs = db.scalars(_runs_query(schedule).limit(RECENT_RUNS)).all()
    return ScoringScheduleState(
        project_id=project_id, schedule=read, status=status,
        status_message=paused if status == "paused_by_platform" else None,
        recent_runs=[_run_read(row) for row in runs],
    )


def list_runs(
    db: Session, *, actor: User, workspace_id: UUID, project_id: UUID, cursor: str | None, limit: int
) -> ScoringScheduleRunPage:
    """Handled weeks, newest first (``planned_for`` is unique per schedule). Raises
    ``IdentityError`` / ``ProjectNotFoundError`` / ``InvalidCursorError``."""

    from app.services.project_service import get_project

    get_project(db, actor=actor, workspace_id=workspace_id, project_id=project_id)
    scope = f"scoring-schedule-runs:{workspace_id}:{project_id}"
    query = None
    schedule = _schedule(db, workspace_id, project_id)
    if schedule is not None:
        query = _runs_query(schedule)
        if cursor:
            message = "cursor is not a cursor of this project's scheduled runs"
            try:
                (position,) = open_cursor(cursor, scope, message=message)
                before = datetime.fromisoformat(position)
            except (ValueError, TypeError) as exc:
                raise InvalidCursorError(message) from exc
            query = query.where(ScoringScheduleRun.planned_for < before)
    rows = list(db.scalars(query.limit(limit + 1))) if query is not None else []
    more = len(rows) > limit
    rows = rows[:limit]
    return ScoringScheduleRunPage(
        project_id=project_id, items=[_run_read(row) for row in rows],
        next_cursor=sign_cursor(scope, [rows[-1].planned_for.isoformat()]) if more else None,
    )


# --- runs (worker) ---------------------------------------------------------------------------


def due_query(moment: datetime, skip: set[UUID]) -> Any:
    """The due scan. A bare ``enabled`` filter (not ``enabled IS true``) so Postgres can use the
    partial index ``ix_scoring_schedules_due`` (``WHERE enabled``)."""

    query = select(ScoringSchedule).where(ScoringSchedule.enabled, ScoringSchedule.next_planned_for <= moment)
    if skip:
        query = query.where(ScoringSchedule.id.not_in(skip))
    return query.order_by(ScoringSchedule.next_planned_for, ScoringSchedule.id).limit(1).with_for_update(
        skip_locked=True)


def _claim_due(db: Session, moment: datetime, skip: set[UUID]) -> ScoringSchedule | None:
    return db.scalar(due_query(moment, skip))


def _slot_taken(db: Session, schedule: ScoringSchedule, slot: datetime) -> bool:
    return db.scalar(select(ScoringScheduleRun.id).where(
        ScoringScheduleRun.workspace_id == schedule.workspace_id, ScoringScheduleRun.schedule_id == schedule.id,
        ScoringScheduleRun.planned_for == slot)) is not None


def _advance(schedule: ScoringSchedule, slot: datetime) -> None:
    schedule.last_planned_for = slot
    schedule.next_planned_for = slot + WEEK  # the slot is the latest one <= now: this one is > now


def _finish(db: Session, schedule: ScoringSchedule, slot: datetime, outcome: str, *, reason: str | None = None,
            **ids: UUID | None) -> ScoringScheduleRun:
    """Record why this slot scored nothing, advance and commit (the row lock is released)."""

    row = ScoringScheduleRun(workspace_id=schedule.workspace_id, project_id=schedule.project_id,
                             schedule_id=schedule.id, planned_for=slot, outcome=outcome, reason_code=reason, **ids)
    db.add(row)
    _advance(schedule, slot)
    db.commit()
    return row


def _newest_new_file(db: Session, schedule: ScoringSchedule, model: ModelVersion) -> Dataset | None:
    """The newest file uploaded for scoring (``purpose=scoring``, the "Score new data" upload)
    since the schedule was created and since its last queued run, that this model never scored
    and that is not the file the model was trained on (by id or identical content)."""

    ws = schedule.workspace_id
    experiment = db.scalar(select(Experiment).where(
        Experiment.id == model.pipeline_run_id, Experiment.workspace_id == ws))
    source = None
    if experiment is not None and experiment.source_dataset_id is not None:
        source = db.scalar(select(Dataset).where(Dataset.id == experiment.source_dataset_id, Dataset.workspace_id == ws))
    last_queued = db.scalar(select(func.max(ScoringScheduleRun.created_at)).where(
        ScoringScheduleRun.workspace_id == ws, ScoringScheduleRun.schedule_id == schedule.id,
        ScoringScheduleRun.outcome == OUTCOME_QUEUED))
    since = max(schedule.created_at, last_queued) if last_queued is not None else schedule.created_at
    scored = select(BatchPrediction.id).where(
        BatchPrediction.workspace_id == ws, BatchPrediction.input_dataset_id == Dataset.id,
        BatchPrediction.model_version_id == model.id).exists()
    in_runs = select(ScoringScheduleRun.id).where(
        ScoringScheduleRun.workspace_id == ws, ScoringScheduleRun.schedule_id == schedule.id,
        ScoringScheduleRun.input_dataset_id == Dataset.id, ScoringScheduleRun.outcome == OUTCOME_QUEUED).exists()
    query = select(Dataset).where(
        Dataset.workspace_id == ws, Dataset.project_id == schedule.project_id,
        Dataset.purpose == DATASET_PURPOSE_SCORING, Dataset.artifact_id.is_not(None), Dataset.created_at > since,
        ~scored, ~in_runs)
    if source is not None:
        query = query.where(Dataset.id != source.id)
        if source.content_digest:
            query = query.where(or_(Dataset.content_digest.is_(None), Dataset.content_digest != source.content_digest))
    return db.scalar(query.order_by(Dataset.created_at.desc(), Dataset.id.desc()).limit(1))


def _queue_failure_reason(exc: Exception) -> str:
    if isinstance(exc, RunQuotaExceededError):
        return REASON_RUN_LIMIT
    code = getattr(exc, "code", None)
    if code == "release_required":
        return REASON_RELEASE_CHANGED
    if code in {"not_found", "model_not_scoreable"}:
        return REASON_MODEL_NOT_SCOREABLE
    if isinstance(exc, ExperimentRequestError):
        return REASON_DATASET_UNAVAILABLE
    return REASON_INTERNAL_ERROR


def _handle(db: Session, schedule: ScoringSchedule, moment: datetime) -> ScoringScheduleRun | None:
    """One claimed (locked) due schedule: commits, or raises with nothing written."""

    from app.services.batch_prediction_service import create_batch_prediction

    slot = slot_at_or_before(moment, schedule.weekday, schedule.hour_utc)
    if _slot_taken(db, schedule, slot):  # handled already (restart, a re-saved config): only advance
        _advance(schedule, slot)
        db.commit()
        return None
    if moment - slot > CATCH_UP:  # the worker was down: never a backlog of old weeks
        return _finish(db, schedule, slot, OUTCOME_MISSED, reason=REASON_WORKER_WAS_DOWN)
    owner = _owner(db, schedule)
    if owner is None:
        missing = schedule.updated_by_user_id is None or db.get(User, schedule.updated_by_user_id) is None
        return _finish(db, schedule, slot, OUTCOME_OWNER_NOT_ALLOWED, reason=REASON_OWNER_MISSING if missing else None)
    if _champion_id(db, schedule.workspace_id, schedule.project_id) is None:
        return _finish(db, schedule, slot, OUTCOME_NO_MODEL_IN_USE)
    release = current_release(db, workspace_id=schedule.workspace_id, project_id=schedule.project_id)
    if release is None:  # never approved, or a bootstrapped first model: never scored on a schedule
        return _finish(db, schedule, slot, OUTCOME_NO_APPROVAL)
    model = db.scalar(select(ModelVersion).where(
        ModelVersion.id == release.model_version_id, ModelVersion.workspace_id == schedule.workspace_id))
    ids: dict[str, UUID | None] = {"model_version_id": release.model_version_id, "model_release_id": release.id}
    dataset = _newest_new_file(db, schedule, model) if model is not None else None
    if dataset is None:
        return _finish(db, schedule, slot, OUTCOME_NO_NEW_FILE, **ids)
    run_id, schedule_id, dataset_id = uuid4(), schedule.id, dataset.id

    def log_run(prediction: BatchPrediction) -> None:  # same transaction as the prediction, before its commit
        db.add(ScoringScheduleRun(
            id=run_id, workspace_id=schedule.workspace_id, project_id=schedule.project_id, schedule_id=schedule.id,
            planned_for=slot, outcome=OUTCOME_QUEUED, batch_prediction_id=prediction.id, input_dataset_id=dataset_id,
            **ids))
        _advance(schedule, slot)
        db.flush()

    try:
        create_batch_prediction(db, actor=owner, workspace_id=schedule.workspace_id, model_version_id=model.id,
                                dataset_id=dataset_id, before_commit=log_run, scheduled_run_id=run_id)
    except (RunQuotaExceededError, ExperimentRequestError, IdentityError) as exc:
        db.rollback()  # nothing of the prediction exists; record why under a fresh lock
        logger.info("scoring schedule %s: could not queue (%s)", schedule_id, exc)
        if isinstance(exc, IdentityError):  # the person lost the role between the check and the queueing
            return _record_after_rollback(db, schedule_id, moment, None, outcome=OUTCOME_OWNER_NOT_ALLOWED)
        return _record_after_rollback(db, schedule_id, moment, _queue_failure_reason(exc),
                                      input_dataset_id=dataset_id, **ids)
    return db.get(ScoringScheduleRun, run_id)


def _record_after_rollback(db: Session, schedule_id: UUID, moment: datetime, reason: str | None, *,
                           outcome: str = OUTCOME_FAILED_TO_QUEUE, **ids: UUID | None) -> ScoringScheduleRun | None:
    """``outcome`` (``failed_to_queue`` by default) for the schedule's due slot, re-claimed after a
    rollback without waiting (SKIP LOCKED: a row someone else holds is left to the next tick);
    nothing when another worker or a person changed it meanwhile (it is then no longer due)."""

    schedule = db.scalar(select(ScoringSchedule).where(ScoringSchedule.id == schedule_id)
                         .with_for_update(skip_locked=True))
    if schedule is None or not schedule.enabled or schedule.next_planned_for > moment:
        db.rollback()
        return None
    slot = slot_at_or_before(moment, schedule.weekday, schedule.hour_utc)
    if _slot_taken(db, schedule, slot):
        _advance(schedule, slot)
        db.commit()
        return None
    return _finish(db, schedule, slot, outcome, reason=reason, **ids)


def run_scoring_schedule_tick(db: Session, *, now: datetime | None = None) -> list[ScoringScheduleRun]:
    """Handle every due schedule once (worker only). Returns the run rows written this tick.
    With ``SCORING_SCHEDULE_ENABLED=false`` (platform kill switch) it does nothing."""

    if not _platform_enabled():
        return []
    moment = now or _now()
    handled: list[ScoringScheduleRun] = []
    seen: set[UUID] = set()
    for _ in range(TICK_MAX_SCHEDULES):
        schedule = _claim_due(db, moment, seen)
        if schedule is None:
            db.rollback()  # ends the scan's transaction
            break
        schedule_id = schedule.id
        seen.add(schedule_id)
        try:
            run = _handle(db, schedule, moment)
        except Exception:  # noqa: BLE001 - one schedule never blocks the others
            logger.exception("scoring schedule %s: run failed", schedule_id)
            db.rollback()
            try:
                run = _record_after_rollback(db, schedule_id, moment, REASON_INTERNAL_ERROR)
            except Exception:  # noqa: BLE001 - logged; the next tick retries this schedule
                logger.exception("scoring schedule %s: could not record the failure", schedule_id)
                db.rollback()
                run = None
        if run is not None:
            handled.append(run)
    return handled
