"""Operating points of a binary run (P5.2-A): read the stored out-of-fold curve; a person
chooses a point.

Reads of ``experiments.result`` use an allowlist (``task``, ``operating_curve``, the lock's
``value`` / ``source`` / ``status``, ``objective``, the selection metric): never
``test_metrics``, ``test_predictions``, ``final_test_evaluation`` or a ``holdout_*`` key,
so no final-evaluation figure exists at any point. A choice is one accepted
``operating_point_chosen`` ProjectDecisionRecord (human actor with ML-write; subject = the
experiment; the reason is its rationale) that supersedes the previous choice; the stored
curve, the locked threshold and the model version stay as they are, and batch scoring
keeps the locked threshold (``OperatingScoringRead``).
"""

from __future__ import annotations

from typing import Any
from uuid import UUID

from pydantic import ValidationError
from sqlalchemy import and_, select
from sqlalchemy.orm import Session, aliased

from app.db.models import Experiment, ProjectDecisionRecord, User
from app.domain.decision_records import (
    DECISION_OPERATING_POINT_CHOSEN,
    RATIONALE_READ_MAX_CHARS,
    STATE_ACCEPTED,
    DecisionActor,
)
from app.domain.errors import ExperimentNotFoundError, IdentityError, OperatingPointError
from app.domain.operating_points import (
    STATUS_MESSAGES,
    ChosenOperatingPointRead,
    LockedOperatingPointRead,
    OperatingCostMatrix,
    OperatingObjectiveInput,
    OperatingPointChoiceRead,
    OperatingPointChoiceRequest,
    OperatingPointDetailRead,
    OperatingPointRead,
    OperatingPointsRead,
    OperatingScoringRead,
    what_this_means,
)
from app.engine.modeling.objective import MetricConstraint, ObjectiveError, objective_from_dict
from app.engine.modeling.operating_points import (
    CURVE_VERSION,
    MIN_CLASS_ROWS,
    TIE_BREAK,
    Curve,
    PointObjective,
    curve_digest,
    load_curve,
    not_evaluated_reason,
    pareto_indices,
    point,
    solve,
    solve_lock,
)
from app.services import decision_record_service as drs
from app.services.audience_projection import public_diagnostic
from app.services.authorization_service import can_read_workspace

_LOCK_KEYS = ("value", "source", "status")  # the lock's out-of-fold fields; never holdout_*


def _load(db: Session, workspace_id: UUID, experiment_id: UUID, *, for_update: bool = False) -> Experiment:
    stmt = select(Experiment).where(Experiment.id == experiment_id, Experiment.workspace_id == workspace_id)
    row = db.scalar(stmt.with_for_update() if for_update else stmt)
    if row is None:
        raise ExperimentNotFoundError("experiment not found")
    return row


def _chosen_head(db: Session, workspace_id: UUID, experiment_id: UUID) -> ProjectDecisionRecord | None:
    """The current choice: the newest accepted record of the chain without a successor."""

    pdr, successor = ProjectDecisionRecord, aliased(ProjectDecisionRecord)
    return db.scalar(
        select(pdr)
        .outerjoin(successor, and_(successor.supersedes_id == pdr.id, successor.workspace_id == pdr.workspace_id))
        .where(pdr.workspace_id == workspace_id, pdr.experiment_id == experiment_id,
               pdr.decision_type == DECISION_OPERATING_POINT_CHOSEN, pdr.state == STATE_ACCEPTED,
               successor.id.is_(None))
        .order_by(pdr.recorded_at.desc(), pdr.id.desc())
        .limit(1)
    )


def _num(value: Any) -> float | None:
    return float(value) if isinstance(value, (int, float)) and not isinstance(value, bool) else None


class _State:
    """What the allowlisted result keys say about a run's operating points."""

    def __init__(self, result: dict[str, Any], completed: bool, evidence_locked: bool = True) -> None:
        task = result.get("task") if isinstance(result.get("task"), dict) else {}
        self.task_type = task.get("task_type") if isinstance(task.get("task_type"), str) else None
        stored = result.get("operating_curve")
        self.stored = stored if isinstance(stored, dict) else {}
        self.curve: Curve | None = (
            load_curve(stored) if completed and evidence_locked and self.task_type == "binary" else None)
        self.digest = curve_digest(stored)
        lock = result.get("decision_threshold") if isinstance(result.get("decision_threshold"), dict) else {}
        self.lock = {key: lock.get(key) for key in _LOCK_KEYS}
        for key in ("source", "status"):
            self.lock[key] = self.lock[key] if isinstance(self.lock[key], str) else None
        objective = result.get("objective")
        try:
            self.objective = objective_from_dict(objective, task_type="binary") if isinstance(objective, dict) else None
            self.objective_valid = True
        except ObjectiveError:
            self.objective, self.objective_valid = None, False
        selection = result.get("selection") if isinstance(result.get("selection"), dict) else {}
        self.primary_metric = selection.get("selection_metric")
        has_cost = self.objective is not None and self.objective.has_cost_matrix
        self.cost = (float(self.objective.cost_false_positive), float(self.objective.cost_false_negative)) \
            if has_cost else None
        if self.task_type in {"multiclass", "regression"}:
            self.status, self.reason = "not_applicable", f"task_{self.task_type}"
        elif not completed:
            self.status, self.reason = "not_available", "run_not_completed"
        elif not evidence_locked:
            self.status, self.reason = "not_available", "evidence_not_locked"
        elif self.task_type != "binary":
            self.status, self.reason = "not_available", "task_unknown"
        elif self.curve is None:
            self.status = "not_available"
            self.reason = str(self.stored.get("reason") or ("invalid_curve" if self.stored else "no_operating_curve"))
        else:
            self.reason = not_evaluated_reason(self.curve)
            self.status = "not_evaluated" if self.reason else "available"


def _rationale(value: str | None) -> str:
    """The same projection as decision-record reads (redacted, bounded)."""

    projected = public_diagnostic(value or "")
    return (projected if isinstance(projected, str) else "[REDACTED]")[:RATIONALE_READ_MAX_CHARS]


def _detail(curve: Curve, index: int, cost: tuple[float, float] | None) -> OperatingPointDetailRead:
    values = point(curve, index, cost=cost, detail=True)
    return OperatingPointDetailRead(**values, what_this_means=what_this_means(values))


def _chosen(curve: Curve | None, row: ProjectDecisionRecord, run_cost: tuple[float, float] | None,
            digest: str | None) -> ChosenOperatingPointRead | None:
    facts, details = dict(row.facts or {}), dict(row.details or {})
    threshold = _num(facts.get("threshold"))
    if threshold is None:
        return None
    raw_objective = details.get("objective")
    try:
        objective = OperatingObjectiveInput.model_validate(raw_objective) if isinstance(raw_objective, dict) else None
    except ValidationError:  # a record from a later vocabulary: show the point, not the objective
        objective = None
    method = "objective" if details.get("method") == "objective" else "threshold"
    cost = run_cost
    if objective is not None and objective.cost_false_positive is not None:
        cost = (objective.cost_false_positive, float(objective.cost_false_negative or 0.0))
    index = curve.index_of(threshold) if curve is not None else None
    return ChosenOperatingPointRead(
        decision_id=row.id, threshold=threshold, method=method,
        objective=objective, rationale=_rationale(row.rationale), chosen_by_user_id=row.actor_user_id,
        recorded_at=row.recorded_at, supersedes_id=row.supersedes_id,
        point=_detail(curve, index, cost) if curve is not None and index is not None else None,
        curve_changed=None if not isinstance(facts.get("curve_digest"), str) else facts["curve_digest"] != digest,
    )


def operating_points_from_result(
    experiment_id: UUID, result: dict[str, Any], *, completed: bool, evidence_locked: bool = True,
    chosen: ProjectDecisionRecord | None = None,
) -> OperatingPointsRead:
    """The read model from the allowlisted result keys (pure; holdout keys are never read).
    ``not_evaluated`` (too few rows of a class) reports the locked threshold with no confusion counts."""

    state = _State(dict(result or {}), completed, evidence_locked)
    curve = state.curve
    body: dict[str, Any] = {
        "experiment_id": experiment_id, "status": state.status, "reason": state.reason,
        "message": STATUS_MESSAGES[state.status], "task_type": state.task_type, "min_class_rows": MIN_CLASS_ROWS,
        "tie_break": TIE_BREAK,
    }
    if state.task_type != "binary" or not completed or not evidence_locked:
        return OperatingPointsRead(**body)
    locked_value = _num(state.lock["value"])
    body["scoring"] = OperatingScoringRead(threshold=locked_value)
    if state.cost is not None:
        body["cost_matrix"] = OperatingCostMatrix(false_positive=state.cost[0], false_negative=state.cost[1])
    locked = LockedOperatingPointRead(threshold=locked_value, source=state.lock["source"],
                                      constraint_status=state.lock["status"])
    if curve is not None:
        body.update(version=CURVE_VERSION, oof_folds=curve.oof_folds, rows=curve.rows,
                    positives=curve.positives, negatives=curve.negatives)
        index = curve.index_of(locked_value) if locked_value is not None else None
        reproduced = None
        if locked_value is not None and state.objective_valid:
            reproduced = solve_lock(curve, state.objective, state.primary_metric) == locked_value
        locked = locked.model_copy(update={
            # Small cells: no counts below the class-size gate (the threshold alone is shown).
            "point": _detail(curve, index, state.cost) if index is not None and state.status == "available" else None,
            "reproduced_from_curve": reproduced,
        })
        if state.status == "available":
            body["points"] = [OperatingPointRead(**point(curve, i, cost=state.cost)) for i in range(len(curve.thresholds))]
            body["pareto"] = [_detail(curve, i, state.cost) for i in pareto_indices(curve, state.cost)]
    body["locked"] = locked
    if chosen is not None:
        body["chosen"] = _chosen(curve if state.status == "available" else None, chosen, state.cost, state.digest)
    return OperatingPointsRead(**body)


def _completed(experiment: Experiment) -> bool:
    return str(experiment.status or "").upper() == "COMPLETED"


def operating_points_read(
    db: Session, *, actor: User, workspace_id: UUID, experiment_id: UUID
) -> OperatingPointsRead:
    if not can_read_workspace(db, actor, workspace_id):
        raise IdentityError("not authorized for this workspace", status_code=403)
    experiment = _load(db, workspace_id, experiment_id)
    return operating_points_from_result(
        experiment.id, dict(experiment.result or {}), completed=_completed(experiment),
        evidence_locked=experiment.scientific_evidence_locked_at is not None,
        chosen=_chosen_head(db, workspace_id, experiment.id),
    )


def _point_objective(request: OperatingObjectiveInput, run_cost: tuple[float, float] | None) -> PointObjective:
    cost = (request.cost_false_positive, request.cost_false_negative)
    if request.goal == "expected_cost" and cost[0] is None:
        if run_cost is None:
            raise OperatingPointError("cost_matrix_required",
                                      "goal expected_cost needs a cost matrix: in the objective or the run's own")
        cost = run_cost
    return PointObjective(
        goal=request.goal,
        constraints=tuple(MetricConstraint(metric=c.metric, op=c.op, value=float(c.value)) for c in request.constraints),
        cost_false_positive=None if cost[0] is None else float(cost[0]),
        cost_false_negative=None if cost[1] is None else float(cost[1]),
    )


def choose_operating_point(
    db: Session, *, actor: User, workspace_id: UUID, experiment_id: UUID, request: OperatingPointChoiceRequest
) -> ProjectDecisionRecord:
    """Record a person's operating point (does not commit). The experiment row is locked
    so concurrent choices form one linear chain."""

    human = DecisionActor.human(actor)
    drs.authorize_writer(db, human, workspace_id=workspace_id, allow_rule=False)
    experiment = _load(db, workspace_id, experiment_id, for_update=True)
    state = _State(dict(experiment.result or {}), _completed(experiment),
                   experiment.scientific_evidence_locked_at is not None)
    if state.status != "available" or state.curve is None:
        raise OperatingPointError(f"operating_points_{state.status}", STATUS_MESSAGES[state.status], status_code=409,
                                  details={"reason": state.reason})
    if experiment.project_id is None:
        raise OperatingPointError("operating_points_no_project", "the run belongs to no project", status_code=409)
    curve, cost = state.curve, state.cost
    objective_payload = None
    if request.threshold is not None:
        index = curve.index_of(request.threshold)
        if index is None:
            raise OperatingPointError("threshold_not_on_curve",
                                      "the threshold is not one of the run's candidate thresholds (GET operating-points)")
    else:
        assert request.objective is not None
        solved_for = _point_objective(request.objective, cost)
        if solved_for.cost_false_positive is not None:  # the requested cost matrix prices every point
            cost = (solved_for.cost_false_positive, float(solved_for.cost_false_negative or 0.0))
        outcome = solve(curve, solved_for)
        index = outcome["index"]
        if outcome["status"] != "optimal":
            closest = point(curve, index, cost=cost) if index is not None else None
            raise OperatingPointError(
                "objective_infeasible", "no candidate threshold meets every constraint on out-of-fold predictions",
                details={"closest": closest, "shortfall": outcome.get("shortfall"),
                         "constraints": outcome.get("constraints")})
        objective_payload = request.objective.model_dump(mode="json")
        if solved_for.cost_false_positive is not None:
            objective_payload.update(cost_false_positive=cost[0], cost_false_negative=cost[1])
    values = point(curve, index, cost=cost)
    prior = _chosen_head(db, workspace_id, experiment.id)
    drs.load_project(db, workspace_id=workspace_id, project_id=experiment.project_id)
    facts = {
        "threshold": values["threshold"], "basis": "out_of_fold_cv", "outcome_scope": "cv",
        **{key: values[key] for key in ("precision", "recall", "specificity", "f1", "flagged_share")},
        "expected_cost": values["expected_cost"], "locked_threshold": _num(state.lock["value"]),
        "curve_version": CURVE_VERSION, "curve_digest": state.digest, "candidate_id": state.stored.get("candidate_id"),
    }
    details: dict[str, Any] = {"method": "objective" if objective_payload is not None else "threshold",
                               "applies_to_scoring": False}
    if objective_payload is not None:
        details["objective"] = objective_payload
    row = drs.build_record(
        workspace_id=workspace_id, project_id=experiment.project_id, actor=human,
        decision_type=DECISION_OPERATING_POINT_CHOSEN, state=STATE_ACCEPTED, subject_kind="experiment",
        subject_id=experiment.id, subject_digest=drs.node_digest(experiment),
        rationale=drs.clean_rationale(request.reason),
        facts=drs.clean_json_object(facts, label="facts"),
        evidence_refs=drs.validate_evidence_refs(db, workspace_id=workspace_id, project_id=experiment.project_id,
                                                 refs=[drs.evidence_ref("experiment", experiment.id)]),
        details=drs.clean_json_object(details, label="details"),
        supersedes_id=prior.id if prior is not None else None,
    )
    return drs.insert_record(db, row)


def choice_read(db: Session, row: ProjectDecisionRecord) -> OperatingPointChoiceRead:
    """The response of a choice (also on an idempotent replay): built from that record."""

    experiment = _load(db, row.workspace_id, row.experiment_id)
    body = operating_points_from_result(experiment.id, dict(experiment.result or {}), completed=True, chosen=row)
    if body.chosen is None:  # the record always carries its threshold
        raise OperatingPointError("operating_point_unreadable", "the recorded operating point cannot be read",
                                  status_code=409)
    return OperatingPointChoiceRead(experiment_id=experiment.id, chosen=body.chosen,
                                    decision=drs.record_read(db, row),
                                    scoring=body.scoring or OperatingScoringRead(threshold=None))
