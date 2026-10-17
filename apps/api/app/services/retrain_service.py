"""Retrain on newest data (D5a, ADR 0012): policy, start, the worker tick and the status read.

``start_retrain`` queues a ROOT run (``start_root_experiment``: usage quota and run capacity
included) on a newer labelled file of the project with what a root run accepts from the model in
use: its target and its goal (ProblemSpec: metric and constraints). Branch settings of the model
in use (families, class weighting, column treatments) are not carried: the new run re-decides them
on its own training rows; ``not_carried`` lists them. One unfinished retrain per project
(``retrain_in_progress``), serialized by a transaction-scoped advisory lock; the /v1 route binds its
Idempotency-Key to the run.

``run_retrain_tick`` (``dclab worker run``): (1) for each finished retrain without a comparison,
stores the comparison once (``retrain_comparison``) and opens the challenger proposal: a
``champion_promoted`` proposal by the code-owned rule ``retrain.challenger.v1`` moving dataset,
split plan, feature recipe and champion together (the re-baseline, ADR 0006 Rev 2); a person
switches by accepting it (``move_ref`` with ``proposal_id``) or keeps the model in use by rejecting
it. (2) For each project whose policy is ``ask`` or ``auto``: when the newest week of the model in
use is an alert, a person uploaded a newer labelled file and no retrain used it yet, at most once
per 7 days and once per alert week: ``ask`` writes the Inbox question (no run), ``auto`` starts the
retrain as the person who set the policy (re-checked). Automatic never switches. No AI anywhere.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID

from sqlalchemy import exists, func, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, aliased

from app.db.models import (
    Dataset,
    DatasetAsset,
    Experiment,
    FeatureSetVersion,
    ModelVersion,
    MonitoringWindow,
    ProjectDecisionRecord,
    RetrainComparison,
    RetrainPolicy,
    RetrainRequest,
    User,
    WorkflowRun,
)
from app.domain import retrain as dr
from app.domain.data_plane import DATASET_PURPOSE_TRAINING
from app.domain.decision_records import (
    DECISION_RETRAIN_POLICY_CHANGED,
    RULE_RETRAIN_CHALLENGER,
    STATE_ACCEPTED,
    DecisionActor,
)
from app.domain.errors import (
    ChampionSplitPlanMismatchError,
    DecisionActorNotPermittedError,
    ExperimentRequestError,
    IdentityError,
    InvalidDecisionRecordError,
    RefTargetNotFoundError,
    RetrainError,
    RunQuotaExceededError,
)
from app.domain.monitoring import outcome_rates
from app.services import decision_record_service as drs
from app.services.authorization_service import (
    can_manage_workspace_members,
    can_perform_ml_write,
    platform_role_for,
)
from app.services.experiment_service import STATUS_SQL, start_root_experiment
from app.services.graph_service import uploaded_file
from app.services.monitoring_service import model_in_use
from app.services.project_service import get_project

logger = logging.getLogger("dclab.retrain")

UNFINISHED = ("queued", "running", "cancelling", "needs_input")
WEEK = timedelta(days=7)
QUOTA_BACKOFF = timedelta(hours=dr.QUOTA_BACKOFF_HOURS)
_RATIONALE = ("Retrained on the newest data. The retrained model is ready to compare with the model in use "
              "(same kind of test, latest months). Accept to switch (the data, test design, feature recipe and "
              "model move together); reject to keep the model in use.")


def _now() -> datetime:
    return datetime.now(UTC)


def _scoped(db: Session, model: Any, workspace_id: UUID, row_id: UUID | None) -> Any:
    if row_id is None:
        return None
    return db.scalar(select(model).where(model.id == row_id, model.workspace_id == workspace_id))


def _policy_row(db: Session, workspace_id: UUID, project_id: UUID, *, lock: bool = False) -> RetrainPolicy | None:
    query = select(RetrainPolicy).where(RetrainPolicy.workspace_id == workspace_id,
                                        RetrainPolicy.project_id == project_id)
    return db.scalar(query.with_for_update() if lock else query)


def can_set_policy(db: Session, user: User, workspace_id: UUID) -> bool:
    """A workspace owner or administrator of this workspace; never platform staff (whose admin role
    spans every workspace): automatic starts spend the customer's compute as this person."""

    return platform_role_for(db, user) is None and can_manage_workspace_members(db, user, workspace_id)


def _policy_owner(db: Session, row: RetrainPolicy | None) -> User | None:
    """The person automatic starts act as, while they still may set the policy and start runs here."""

    user = db.get(User, row.updated_by_user_id) if row is not None and row.updated_by_user_id else None
    if (user is None or not user.is_active or not can_set_policy(db, user, row.workspace_id)
            or not can_perform_ml_write(db, user, row.workspace_id)):
        return None
    return user


# --- policy (API) -----------------------------------------------------------------------------


def policy_read(db: Session, *, actor: User, workspace_id: UUID, project_id: UUID, agent: bool) -> dr.RetrainPolicyRead:
    get_project(db, actor=actor, workspace_id=workspace_id, project_id=project_id)
    row = _policy_row(db, workspace_id, project_id)
    version = row.version if row is not None else 0
    policy = row.policy if row is not None else dr.POLICY_OFF
    return dr.RetrainPolicyRead(
        project_id=project_id, policy=policy, version=version, etag=f'"{version}"',
        updated_at=row.updated_at if row is not None else None,
        updated_by_user_id=None if agent or row is None else row.updated_by_user_id,
        owner_can_start=(_policy_owner(db, row) is not None) if policy == dr.POLICY_AUTO and not agent else None,
        auto_paused_until=row.auto_paused_until if row is not None and policy == dr.POLICY_AUTO else None)


def set_policy(
    db: Session, *, actor: User, workspace_id: UUID, project_id: UUID, request: dr.RetrainPolicyWrite,
    precondition: Callable[[int], None],
) -> ProjectDecisionRecord:
    """A workspace owner or administrator sets the policy with a reason (``precondition(version)``
    checks If-Match under the row lock) and writes the accepted ``retrain_policy_changed`` record.
    Automatic starts then act as this person. Does not commit."""

    human = DecisionActor.human(actor)
    drs.authorize_writer(db, human, workspace_id=workspace_id, allow_rule=False)
    if not can_set_policy(db, actor, workspace_id):
        raise IdentityError("only a workspace owner or administrator can change the retrain policy", status_code=403)
    drs.load_project(db, workspace_id=workspace_id, project_id=project_id)
    row = _policy_row(db, workspace_id, project_id, lock=True)
    precondition(row.version if row is not None else 0)
    previous = row.policy if row is not None else dr.POLICY_OFF
    if row is None:  # a concurrent first save loses on uq_retrain_policies_project (409, retry)
        row = RetrainPolicy(workspace_id=workspace_id, project_id=project_id, version=1)
        db.add(row)
    else:
        row.version, row.updated_at = row.version + 1, func.now()
    row.policy, row.updated_by_user_id, row.auto_paused_until = request.policy, actor.id, None
    db.flush()
    facts = {"policy": request.policy, "previous": previous, "version": row.version}
    return drs.insert_record(db, drs.build_record(
        workspace_id=workspace_id, project_id=project_id, actor=human, decision_type=DECISION_RETRAIN_POLICY_CHANGED,
        state=STATE_ACCEPTED, subject_kind="project", subject_id=None, rationale=drs.clean_rationale(request.rationale),
        facts=drs.clean_json_object(facts, label="facts"), evidence_refs=[],
        details=drs.clean_json_object({"retrain_policy_id": str(row.id)}, label="details")))


# --- start (API and worker) -------------------------------------------------------------------


@dataclass(frozen=True)
class RetrainResult:
    request: RetrainRequest
    experiment: Experiment


def _incumbent(db: Session, workspace_id: UUID, project_id: UUID) -> tuple[ModelVersion, Experiment] | None:
    version = _scoped(db, ModelVersion, workspace_id, model_in_use(db, workspace_id, project_id))
    experiment = _scoped(db, Experiment, workspace_id, version.pipeline_run_id) if version is not None else None
    if experiment is None or experiment.project_id != project_id or experiment.source_dataset_id is None:
        return None
    return version, experiment


def _open_retrain(db: Session, workspace_id: UUID, project_id: UUID) -> RetrainRequest | None:
    return db.scalar(
        select(RetrainRequest)
        .join(Experiment, (Experiment.id == RetrainRequest.experiment_id)
              & (Experiment.workspace_id == RetrainRequest.workspace_id))
        .where(RetrainRequest.workspace_id == workspace_id, RetrainRequest.project_id == project_id,
               STATUS_SQL.in_(UNFINISHED)).limit(1))


def _newer_files(source: Dataset) -> tuple[Any, ...]:
    """Labelled files uploaded into the project after ``source`` (the training file of the model in
    use) whose content differs from it."""

    digest = () if source.content_digest is None else (
        or_(Dataset.content_digest.is_(None), Dataset.content_digest != source.content_digest),)
    return (Dataset.workspace_id == source.workspace_id, Dataset.project_id == source.project_id,
            Dataset.purpose == DATASET_PURPOSE_TRAINING, Dataset.artifact_id.is_not(None), uploaded_file(),
            Dataset.created_at > source.created_at, Dataset.id != source.id, *digest)


def newest_labelled_file(db: Session, source: Dataset) -> Dataset | None:
    """The newest labelled file a person uploaded after ``source`` (never one a service token uploaded)."""

    return db.scalar(select(Dataset).where(*_newer_files(source), Dataset.uploaded_by_service_token_id.is_(None))
                     .order_by(Dataset.created_at.desc(), Dataset.id.desc()).limit(1))


def _uploader_may_train(db: Session, dataset: Dataset) -> bool:
    """The rule trains only on a file uploaded by someone who can start runs here (never an external
    reader's upload into the Labs project)."""

    creator = db.scalar(select(DatasetAsset.created_by).where(DatasetAsset.id == dataset.dataset_asset_id,
                                                             DatasetAsset.workspace_id == dataset.workspace_id))
    user = db.get(User, creator) if creator is not None else None
    return user is not None and user.is_active and can_perform_ml_write(db, user, dataset.workspace_id)


def start_retrain(
    db: Session, *, actor: User, workspace_id: UUID, project_id: UUID, dataset_id: UUID,
    origin: str = dr.ORIGIN_PERSON, window: MonitoringWindow | None = None,
    before_commit: Callable[[Experiment], None] | None = None,
) -> RetrainResult:
    """Queue the retrain (a root run) and its ``retrain_requests`` row in one transaction. Commits.
    Raises ``RetrainError`` (409: ``no_model_in_use``, ``dataset_not_newer``, ``retrain_in_progress``),
    ``IdentityError`` and everything ``start_root_experiment`` raises (quota 429 included)."""

    project = get_project(db, actor=actor, workspace_id=workspace_id, project_id=project_id)
    if not can_perform_ml_write(db, actor, workspace_id):
        raise IdentityError("starting a retrain requires an ML-write workspace role", status_code=403)
    db.execute(select(func.pg_advisory_xact_lock(func.hashtextextended(f"retrain:{project.id}", 0))))
    current = _incumbent(db, workspace_id, project.id)
    if current is None:
        raise RetrainError("no_model_in_use", "the project has no model in use to retrain", status_code=409)
    version, incumbent = current
    source = _scoped(db, Dataset, workspace_id, incumbent.source_dataset_id)
    if source is None:
        raise RetrainError("no_model_in_use", "the training file of the model in use is not available", status_code=409)
    newer = db.scalar(select(Dataset.id).where(*_newer_files(source), Dataset.id == dataset_id))
    if newer is None:
        chosen = _scoped(db, Dataset, workspace_id, dataset_id)
        if chosen is None:
            raise RetrainError("dataset_not_found", "dataset not found", status_code=404)
        if chosen.project_id != project.id:
            raise RetrainError("dataset_not_in_project", "the dataset belongs to another project", status_code=409)
        raise RetrainError("dataset_not_newer", "the file must be a labelled file uploaded after the training file "
                           "of the model in use", status_code=409)
    if _open_retrain(db, workspace_id, project.id) is not None:
        raise RetrainError("retrain_in_progress", "a retrain of this project is still running", status_code=409)
    if db.scalar(select(RetrainRequest.id).where(
            RetrainRequest.workspace_id == workspace_id, RetrainRequest.project_id == project.id,
            RetrainRequest.incumbent_model_version_id == version.id, RetrainRequest.dataset_id == dataset_id,
            RetrainRequest.experiment_id.is_not(None)).limit(1)) is not None:
        # One final test per model in use and file: retraining again on the same file would judge
        # another challenger on the same final-test rows (retry until one wins).
        raise RetrainError("dataset_already_retrained", "the model in use was already retrained on this file",
                           status_code=409)
    run = _scoped(db, WorkflowRun, workspace_id, incumbent.workflow_run_id)
    target = (run.resolved_target if run is not None else None) or ((incumbent.result or {}).get("task") or {}).get(
        "target")
    if not target:
        raise RetrainError("no_model_in_use", "the model in use has no resolved target", status_code=409)

    def bind(shell: Experiment) -> None:
        request = RetrainRequest(
            workspace_id=workspace_id, project_id=project.id, origin=origin, incumbent_model_version_id=version.id,
            dataset_id=dataset_id, experiment_id=shell.id, requested_by_user_id=actor.id,
            monitoring_window_id=window.id if window is not None else None,
            window_start=window.window_start if window is not None else None)
        db.add(request)
        db.flush()
        holder.append(request)
        if before_commit is not None:
            before_commit(shell)

    holder: list[RetrainRequest] = []
    result = start_root_experiment(
        db, actor=actor, workspace_id=workspace_id, project_id=project.id, dataset_id=dataset_id,
        problem_spec_id=run.problem_spec_id if run is not None else None, target_column=str(target),
        intent=f"Retrain on newest data: same target and goal as the model in use (version {version.version})",
        before_commit=bind)
    return RetrainResult(holder[0], result.experiment)


# --- worker tick ------------------------------------------------------------------------------


def _propose(db: Session, request: RetrainRequest, challenger: Experiment) -> tuple[UUID | None, str | None]:
    from app.services.project_ref_service import RefMove, propose_ref_move

    ws = request.workspace_id
    if model_in_use(db, ws, request.project_id) != request.incumbent_model_version_id:
        return None, "model_in_use_changed"
    model = db.scalar(select(ModelVersion).where(ModelVersion.workspace_id == ws,
                                                 ModelVersion.pipeline_run_id == challenger.id))
    if model is None or challenger.split_plan_id is None or challenger.source_dataset_id is None:
        return None, "no_model_version"
    moves = [RefMove("dataset", challenger.source_dataset_id, None),
             RefMove("split_plan", challenger.split_plan_id, None), RefMove("champion_model", model.id, None)]
    recipe = _scoped(db, FeatureSetVersion, ws, model.feature_set_version_id)
    if recipe is not None and recipe.locked_at is not None and recipe.project_id == request.project_id:
        moves.append(RefMove("feature_recipe", recipe.id, None))
    evidence = [drs.evidence_ref("candidate", model.selected_candidate_id, scope=drs.FINAL_HOLDOUT_SCOPE),
                drs.evidence_ref("experiment", challenger.id),
                drs.evidence_ref("model_version", request.incumbent_model_version_id)]
    try:
        with db.begin_nested():
            record = propose_ref_move(
                db, workspace_id=ws, project_id=request.project_id,
                actor=DecisionActor.rule_actor(RULE_RETRAIN_CHALLENGER, triggered_by=request.requested_by_user_id),
                moves=moves, rationale=_RATIONALE, evidence_refs=evidence,
                facts={"retrain_request_id": str(request.id), "origin": request.origin,
                       "incumbent_model_version_id": str(request.incumbent_model_version_id)},
                idempotency_key=f"retrain_challenger:{request.id}")
    except (InvalidDecisionRecordError, ChampionSplitPlanMismatchError, RefTargetNotFoundError,
            DecisionActorNotPermittedError) as exc:
        return None, str(getattr(exc, "reason", None) or getattr(exc, "code", None) or "proposal_refused")[:64]
    return record.id, None


def compare_retrain(db: Session, request: RetrainRequest, challenger: Experiment) -> RetrainComparison:
    """Store the comparison of one finished retrain and open its proposal (one transaction). Commits."""

    from app.services.retrain_comparison import compare

    result = compare(db, request, challenger)
    proposal_id, refusal = _propose(db, request, challenger)
    if refusal is not None:
        result["proposal_refusal"] = refusal
    row = RetrainComparison(
        workspace_id=request.workspace_id, project_id=request.project_id, retrain_request_id=request.id,
        experiment_id=challenger.id, status=result["status"], reason_code=result.get("reason_code"),
        result=drs.clean_json_object(result, label="result"), proposal_record_id=proposal_id)
    db.add(row)
    db.commit()
    return row


def _claim_finished(db: Session, seen: set[UUID]) -> tuple[RetrainRequest, Experiment] | None:
    """One finished retrain (evidence locked) without a comparison, locked ``SKIP LOCKED``."""

    done = exists().where(RetrainComparison.retrain_request_id == RetrainRequest.id)
    query = (select(RetrainRequest, Experiment)
             .join(Experiment, (Experiment.id == RetrainRequest.experiment_id)
                   & (Experiment.workspace_id == RetrainRequest.workspace_id))
             .where(~done, func.upper(Experiment.status) == "COMPLETED",
                    Experiment.scientific_evidence_locked_at.is_not(None)))
    if seen:
        query = query.where(RetrainRequest.id.not_in(seen))
    row = db.execute(query.order_by(RetrainRequest.created_at).limit(1)
                     .with_for_update(of=RetrainRequest, skip_locked=True)).first()
    return (row[0], row[1]) if row is not None else None


def _alert_window(db: Session, workspace_id: UUID, project_id: UUID, version_id: UUID) -> MonitoringWindow | None:
    """The newest week of the model in use (its highest revision), when that week is an alert: drift,
    or (D5b, summary code ``below_floor``) real outcomes below a precision / recall floor."""

    row = db.scalar(select(MonitoringWindow).where(
        MonitoringWindow.workspace_id == workspace_id, MonitoringWindow.project_id == project_id,
        MonitoringWindow.model_version_id == version_id)
        .order_by(MonitoringWindow.window_start.desc(), MonitoringWindow.revision.desc()).limit(1))
    return row if row is not None and row.status == "alert" else None


def _apply_policy(db: Session, policy: RetrainPolicy, now: datetime) -> str | None:
    """One project's rule step (the policy row is locked). Returns what it did, or None."""

    ws, project_id = policy.workspace_id, policy.project_id
    if policy.policy == dr.POLICY_AUTO and policy.auto_paused_until is not None and now < policy.auto_paused_until:
        return None  # a plan limit refused the last automatic start: wait (QUOTA_BACKOFF)
    db.execute(select(func.pg_advisory_xact_lock(func.hashtextextended(f"retrain:{project_id}", 0))))
    current = _incumbent(db, ws, project_id)
    if current is None:
        return None
    version, incumbent = current
    window = _alert_window(db, ws, project_id, version.id)
    source = _scoped(db, Dataset, ws, incumbent.source_dataset_id)
    dataset = newest_labelled_file(db, source) if window is not None and source is not None else None
    if dataset is None or not _uploader_may_train(db, dataset):
        return None
    rule_rows = (RetrainRequest.workspace_id == ws, RetrainRequest.project_id == project_id)
    recent = db.scalar(select(func.count()).select_from(RetrainRequest).where(
        *rule_rows, RetrainRequest.origin != dr.ORIGIN_PERSON, RetrainRequest.created_at > now - WEEK))
    used = db.scalar(select(func.count()).select_from(RetrainRequest).where(
        *rule_rows, RetrainRequest.incumbent_model_version_id == version.id, or_(
            RetrainRequest.dataset_id == dataset.id, RetrainRequest.window_start == window.window_start)))
    if int(recent or 0) >= dr.RULE_TRIGGERS_PER_WEEK or used or _open_retrain(db, ws, project_id) is not None:
        return None
    if policy.policy == dr.POLICY_ASK:
        db.add(RetrainRequest(workspace_id=ws, project_id=project_id, origin=dr.ORIGIN_ASK,
                              incumbent_model_version_id=version.id, dataset_id=dataset.id,
                              monitoring_window_id=window.id, window_start=window.window_start))
        db.commit()
        return dr.ORIGIN_ASK
    owner = _policy_owner(db, policy)
    if owner is None:
        logger.info("retrain policy of project %s: the person who set it can no longer start runs", project_id)
        return None
    start_retrain(db, actor=owner, workspace_id=ws, project_id=project_id, dataset_id=dataset.id,
                  origin=dr.ORIGIN_AUTO, window=window)
    return dr.ORIGIN_AUTO


def _pause_after_quota(db: Session, policy_id: UUID, moment: datetime) -> None:
    """A plan limit refused the automatic start: no new attempt for this project before
    ``moment + QUOTA_BACKOFF`` (the policy read shows it; a person saving the policy clears it)."""

    try:
        row = db.scalar(select(RetrainPolicy).where(RetrainPolicy.id == policy_id).with_for_update(skip_locked=True))
        if row is not None and row.policy == dr.POLICY_AUTO:
            row.auto_paused_until = moment + QUOTA_BACKOFF
            db.commit()
            logger.info("retrain policy %s: plan limit reached; automatic starts paused until %s", policy_id,
                        row.auto_paused_until.isoformat())
    except Exception:  # noqa: BLE001 - the next tick tries again
        logger.exception("retrain policy %s: pause not recorded", policy_id)
        db.rollback()


def run_retrain_tick(db: Session, *, now: datetime | None = None) -> dict[str, int]:
    """Worker only: compare finished retrains, then apply each active policy once. Every unit commits
    or rolls back on its own; one project's failure never blocks the others. Kill switch:
    ``RETRAIN_ENABLED=false``."""

    from app.config import get_settings

    counts = {"compared": 0, "asked": 0, "started": 0}
    if not get_settings().retrain_enabled:
        return counts
    moment = now or _now()
    seen: set[UUID] = set()
    for _ in range(dr.TICK_MAX_COMPARISONS):
        claimed = _claim_finished(db, seen)
        if claimed is None:
            break
        request_id = claimed[0].id
        seen.add(request_id)
        try:
            compare_retrain(db, *claimed)
            counts["compared"] += 1
        except Exception:  # noqa: BLE001 - logged; retried next tick
            logger.exception("retrain %s: comparison not stored", request_id)
            db.rollback()
    db.rollback()  # ends the scan's transaction (and its row locks)
    policy_ids = list(db.scalars(select(RetrainPolicy.id).where(RetrainPolicy.policy != dr.POLICY_OFF)
                                 .order_by(RetrainPolicy.project_id).limit(dr.TICK_MAX_PROJECTS)))
    db.rollback()
    for policy_id in policy_ids:
        try:
            policy = db.scalar(select(RetrainPolicy).where(RetrainPolicy.id == policy_id)
                               .with_for_update(skip_locked=True))
            done = _apply_policy(db, policy, moment) if policy is not None and policy.policy != dr.POLICY_OFF else None
            if done is not None:
                counts["asked" if done == dr.ORIGIN_ASK else "started"] += 1
        except RunQuotaExceededError:  # includes a P8.4-A plan limit
            db.rollback()
            _pause_after_quota(db, policy_id, moment)
        except (RetrainError, ExperimentRequestError, IdentityError, IntegrityError) as exc:
            logger.info("retrain policy %s: nothing started (%s)", policy_id, type(exc).__name__)
        except Exception:  # noqa: BLE001 - one project never blocks the others
            logger.exception("retrain policy %s: step failed", policy_id)
        finally:
            db.rollback()
    return counts


# --- status read (API) ------------------------------------------------------------------------


def _not_carried(incumbent: Experiment | None) -> list[str]:
    overrides = dict(((incumbent.config if incumbent is not None else None) or {}).get("branch_overrides") or {})
    kinds = {str(item.get("kind")) for item in ((incumbent.change_set or {}) if incumbent else {}).get("changes") or []
             if isinstance(item, dict)}
    out = []
    if overrides.get("families_include") or overrides.get("families_exclude"):
        out.append("model_families")
    if (overrides.get("class_weighting") or {}).get("mode") not in (None, "none"):
        out.append("class_weighting")
    if overrides.get("columns"):
        out.append("column_treatments")
    if overrides.get("hyperparameters"):
        out.append("hyperparameters")
    if kinds & {"metric_override", "threshold_objective"}:
        out.append("branch_objective")
    return out


def _comparison_read(row: RetrainComparison) -> dr.RetrainComparisonRead:
    result = dict(row.result or {})
    holdout = dict(result.get("challenger_holdout") or {})
    metric = result.get("challenger_metric")
    incumbent = result.get("incumbent") if isinstance(result.get("incumbent"), dict) else None
    keys = list((incumbent or {}).get("metrics") or {}) or sorted(holdout)[:12]
    return dr.RetrainComparisonRead(
        status=row.status, reason_code=row.reason_code,
        message=dr.COMPARED_MESSAGE if row.reason_code is None else dr.NOT_COMPARABLE_MESSAGES.get(
            row.reason_code, dr.NOT_COMPARABLE_MESSAGES["comparison_failed"]),
        time_column=result.get("time_column"), incumbent_training_end=result.get("incumbent_training_end"),
        final_test_start=result.get("final_test_start"), final_test_rows=result.get("final_test_rows"),
        challenger=dr.RetrainSideRead(metric=metric, metric_value=holdout.get(str(metric)),
                                      metrics={key: holdout.get(key) for key in keys},
                                      decision_threshold=result.get("challenger_threshold"),
                                      n_rows=result.get("final_test_rows")) if holdout else None,
        incumbent=dr.RetrainSideRead(metric=incumbent.get("metric"), metric_value=incumbent.get("metric_value"),
                                     metrics=dict(incumbent.get("metrics") or {}),
                                     decision_threshold=incumbent.get("decision_threshold"),
                                     n_rows=incumbent.get("n_labelled")) if incumbent else None,
        proposal_refusal=result.get("proposal_refusal"), computed_at=row.created_at)


def retrain_status(db: Session, *, actor: User, workspace_id: UUID, project_id: UUID, agent: bool
                   ) -> dr.RetrainStatusRead:
    """The Monitoring card read: policy, state (drift found > retraining > ready to compare > waiting
    for you > switched / kept), the newest retrain, its comparison (people only) and the real
    outcomes of the model in use beside it (not the same data)."""

    get_project(db, actor=actor, workspace_id=workspace_id, project_id=project_id)
    policy = _policy_row(db, workspace_id, project_id)
    current = _incumbent(db, workspace_id, project_id)
    version_id = current[0].id if current is not None else model_in_use(db, workspace_id, project_id)
    newest = db.scalar(select(MonitoringWindow).where(
        MonitoringWindow.workspace_id == workspace_id, MonitoringWindow.project_id == project_id,
        MonitoringWindow.model_version_id == version_id)
        .order_by(MonitoringWindow.window_start.desc(), MonitoringWindow.revision.desc()).limit(1)) \
        if version_id is not None else None
    alert = newest is not None and newest.status == "alert"
    real = None
    if version_id is not None:
        for window in db.scalars(select(MonitoringWindow).where(
                MonitoringWindow.workspace_id == workspace_id, MonitoringWindow.project_id == project_id,
                MonitoringWindow.model_version_id == version_id)
                .order_by(MonitoringWindow.window_start.desc(), MonitoringWindow.revision.desc()).limit(24)):
            precision, recall = outcome_rates(window.outcomes or {})
            if precision is not None or recall is not None:
                real = dr.RetrainRealOutcomesRead(window_start=window.window_start, precision=precision, recall=recall,
                                                  n_labelled=(window.outcomes or {}).get("n_labelled"))
                break
    source = _scoped(db, Dataset, workspace_id, current[1].source_dataset_id) if current is not None else None
    newer = newest_labelled_file(db, source) if source is not None else None
    found = db.execute(select(RetrainRequest, STATUS_SQL).outerjoin(
        Experiment, (Experiment.id == RetrainRequest.experiment_id) & (Experiment.workspace_id == workspace_id))
        .where(RetrainRequest.workspace_id == workspace_id, RetrainRequest.project_id == project_id)
        .order_by(RetrainRequest.created_at.desc(), RetrainRequest.id.desc()).limit(1)).first()
    request, run_status = (found[0], str(found[1]) if found[0].experiment_id else None) if found else (None, None)
    comparison = db.scalar(select(RetrainComparison).where(
        RetrainComparison.workspace_id == workspace_id, RetrainComparison.retrain_request_id == request.id)) \
        if request is not None else None
    proposal = _scoped(db, ProjectDecisionRecord, workspace_id, comparison.proposal_record_id) if comparison else None
    proposal_state = None
    if proposal is not None:
        successor = aliased(ProjectDecisionRecord)
        resolved = db.scalar(select(successor.state).where(successor.workspace_id == workspace_id,
                                                           successor.supersedes_id == proposal.id))
        proposal_state = resolved or proposal.state
    found_state = "below_floor" if alert and newest.summary_code == "below_floor" else "drift_found"
    state = found_state if alert else "idle"
    if request is not None and (request.incumbent_model_version_id == version_id or proposal_state == STATE_ACCEPTED):
        if proposal_state is not None:
            state = {"accepted": "switched", "rejected": "kept"}.get(proposal_state, "waiting_for_you")
            state = found_state if state == "switched" and alert else state
        elif run_status in UNFINISHED:
            state = "retraining"
        elif run_status == "completed":
            state = "ready_to_compare"
    incumbent = _scoped(db, Experiment, workspace_id, db.scalar(select(ModelVersion.pipeline_run_id).where(
        ModelVersion.workspace_id == workspace_id, ModelVersion.id == request.incumbent_model_version_id))) \
        if request is not None else None
    return dr.RetrainStatusRead(
        project_id=project_id, policy=policy.policy if policy is not None else dr.POLICY_OFF, state=state,
        model_in_use_id=version_id, latest_window_start=newest.window_start if newest is not None else None,
        latest_window_status=newest.status if newest is not None else None,
        newer_dataset_id=newer.id if newer is not None else None,
        request=dr.RetrainRequestRead(
            id=request.id, origin=request.origin, created_at=request.created_at,
            incumbent_model_version_id=request.incumbent_model_version_id, dataset_id=request.dataset_id,
            experiment_id=request.experiment_id, experiment_status=run_status, window_start=request.window_start,
            carried=["target_column", "problem_spec"], not_carried=_not_carried(incumbent),
        ) if request is not None else None,
        comparison=None if agent or comparison is None else _comparison_read(comparison),
        comparison_withheld=agent and comparison is not None, real_outcomes=real,
        proposal_id=proposal.id if proposal is not None else None, proposal_state=proposal_state)
