"""Comparison verdict and test-set look count of an experiment (P5.5-B-read; ADR 0007 option B).

``GET /v1/experiments/{id}/verdict?against=`` (default: the parent). Evidence is read from the
locked ``cv_fold`` ``model_evaluations`` / ``evaluation_metrics`` rows of each run's winning
candidate (``model_selection_decisions.selected_candidate_id``), keyed by
``summary.fold_number``; ``experiments.result`` stays writable after the lock, so it is never
read. The multiplicity T is the number of new trained non-baseline candidates (ADR 0007 §5:
T = Σ k_i), read from the locked ``experiment_candidates`` rows. The look count reads only whether
rows exist (selection decisions, final-evaluation rows, ``final_test_started`` events), never a
value. Every query filters by workspace.

P5.4-A1b adds ``locked_run_evidence`` (the locked rows as ``RunEvidence``: the pinned selection of a
final run and the per-try comparability check read it) and the goal verdict: the same paired rule on
the goal a person set with an operating-point choice, each run scored per fold at its own threshold on
its stored out-of-fold curve (curve folds pair by position; identical per-fold class counts are
asserted and both curve digests reported). Sealed runs (ADR 0007 §6.1) never count as a look.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.db.models import (
    CVFoldRun,
    DataQualityFinding,
    EvaluationMetric,
    Experiment,
    ExperimentCandidate,
    MlRunEvent,
    ModelEvaluation,
    ModelSelectionDecision,
    SplitPlan,
    User,
)
from app.domain.errors import ExperimentNotFoundError, IdentityError
from app.domain.experiment_verdict import (
    GOAL_SHORTFALL,
    ExperimentVerdictRead,
    GoalConstraintRead,
    GoalFoldRead,
    GoalVerdictRead,
    SplitPlanLooksRead,
    VerdictFoldRead,
    goal_message,
    verdict_message,
)
from app.domain.findings import INVESTIGATION_SOURCE
from app.domain.operating_points import OperatingObjectiveInput
from app.domain.pipeline_run_branch import EVIDENCE_MODE_SEALED
from app.engine.evaluation.comparability import RunEvidence, candidate_from_payload, is_time_skip
from app.engine.evaluation.paired_verdict import FoldScore, PairedVerdict, paired_verdict
from app.engine.investigate.checks import DUMMY_FAMILIES
from app.engine.modeling.objective import MetricConstraint
from app.engine.modeling.operating_points import (
    GoalScoreRefusal,
    PointObjective,
    curve_digest,
    goal_fold_scores,
    load_curve,
)
from app.engine.modeling.validation_planner import TIME_SERIES_SPLIT
from app.services.authorization_service import can_read_workspace
from app.services.operating_point_service import choice_before

CV_FOLD_SCOPE = "cv_fold"
FINAL_SCOPE = "final_holdout"  # existence only (look count); no value of it is ever selected
FINAL_TEST_EVENT = "final_test_started"
POSITIVE_RATE = "positive_rate"  # binary folds: share of positives among the fold's (training-partition) rows
SUSPICIOUS_CHECKS = ("target_leakage", "implausible_score")  # ADR 0007 §5 rejected_suspicious
_IN_FLIGHT_EXCLUDED = ("COMPLETED", "FAILED", "CANCELLED", "SKIPPED")


def _load(db: Session, workspace_id: UUID, experiment_id: UUID) -> Experiment:
    row = db.scalar(select(Experiment).where(Experiment.id == experiment_id, Experiment.workspace_id == workspace_id))
    if row is None:
        raise ExperimentNotFoundError("experiment not found")
    return row


def _selection(db: Session, workspace_id: UUID, experiment_id: UUID) -> ModelSelectionDecision | None:
    return db.scalar(
        select(ModelSelectionDecision)
        .where(ModelSelectionDecision.pipeline_run_id == experiment_id,
               ModelSelectionDecision.workspace_id == workspace_id)
        .order_by(ModelSelectionDecision.locked_at.desc())
        .limit(1)
    )


def _fold_number(summary: Any) -> int | None:
    raw = summary.get("fold_number") if isinstance(summary, dict) else None
    try:
        number = int(raw)
    except (TypeError, ValueError):
        return None
    return number if number > 0 and str(raw).strip() == str(number) else None


def locked_fold_scores(db: Session, workspace_id: UUID, candidate_id: UUID, metric: str) -> dict[int, FoldScore] | None:
    """The winner's locked per-fold scores of ``metric`` keyed by fold number, or None when a
    completed fold lacks exactly one locked score (or a score names an unknown fold). Binary folds
    also carry the smallest class count (from the fold's positive rate)."""

    expected = {
        number: rows for number, rows in db.execute(
            select(CVFoldRun.fold_number, CVFoldRun.validation_row_count).where(
                CVFoldRun.candidate_id == candidate_id, CVFoldRun.workspace_id == workspace_id,
                func.lower(CVFoldRun.status) == "completed")
        )
    }
    scored: dict[int, list[float]] = {}
    rates: dict[int, float] = {}
    for summary, name, value in db.execute(
        select(ModelEvaluation.summary, EvaluationMetric.metric_name, EvaluationMetric.metric_value)
        .join(EvaluationMetric, EvaluationMetric.model_evaluation_id == ModelEvaluation.id)
        .where(ModelEvaluation.candidate_id == candidate_id, ModelEvaluation.workspace_id == workspace_id,
               ModelEvaluation.evaluation_scope == CV_FOLD_SCOPE, func.lower(ModelEvaluation.status) == "completed",
               EvaluationMetric.metric_name.in_((metric, POSITIVE_RATE)))
    ):
        number = _fold_number(summary)
        if number is None:
            return None
        if name == metric:
            scored.setdefault(number, []).append(float(value))
        else:
            rates[number] = float(value)
    if not expected or set(scored) != set(expected) or any(len(values) != 1 for values in scored.values()):
        return None

    def smallest_class(number: int) -> int | None:
        rate, rows = rates.get(number), expected[number]
        if metric == POSITIVE_RATE or rate is None or not rows or not 0.0 <= rate <= 1.0:
            return None
        return int(round(min(rate, 1.0 - rate) * rows))

    return {number: FoldScore(values[0], expected[number], smallest_class(number))
            for number, values in scored.items()}


def new_candidate_count(db: Session, workspace_id: UUID, experiment_id: UUID, against_id: UUID) -> int:
    """k: trained non-baseline candidate keys of ``experiment_id`` that ``against_id`` did not train."""

    def trained(run_id: UUID, *, learned_only: bool) -> set[str]:
        rows = db.execute(
            select(ExperimentCandidate.candidate_key, ExperimentCandidate.model_family).where(
                ExperimentCandidate.experiment_id == run_id, ExperimentCandidate.workspace_id == workspace_id,
                func.lower(ExperimentCandidate.status) == "trained")
        )
        return {key for key, family in rows
                if not learned_only or (family not in DUMMY_FAMILIES and key not in DUMMY_FAMILIES)}

    return len(trained(experiment_id, learned_only=True) - trained(against_id, learned_only=False))


def locked_run_evidence(db: Session, workspace_id: UUID, experiment_id: UUID) -> RunEvidence | None:
    """The run's locked CV evidence (None unless its evidence is locked with a winner): per trained
    candidate the stored fingerprint, the scalar metrics of each completed ``cv_fold`` evaluation keyed
    by ``summary.fold_number`` (a fold with two completed rows makes the run unreadable: None), the
    tuning trials per fold and truncation from the locked candidate row; the candidates skipped for time."""

    experiment = db.scalar(select(Experiment).where(Experiment.id == experiment_id,
                                                    Experiment.workspace_id == workspace_id))
    selection = _selection(db, workspace_id, experiment_id) if experiment is not None else None
    if experiment is None or experiment.scientific_evidence_locked_at is None or selection is None:
        return None
    candidates = list(db.scalars(select(ExperimentCandidate).where(
        ExperimentCandidate.experiment_id == experiment_id, ExperimentCandidate.workspace_id == workspace_id)))
    trained_ids = {row.id: row for row in candidates if str(row.status or "").lower() == "trained"}
    folds: dict[UUID, dict[int, dict[str, float]]] = {candidate_id: {} for candidate_id in trained_ids}
    owner: dict[tuple[UUID, int], UUID] = {}
    for candidate_id, evaluation_id, summary, name, value in db.execute(
        select(ModelEvaluation.candidate_id, ModelEvaluation.id, ModelEvaluation.summary,
               EvaluationMetric.metric_name, EvaluationMetric.metric_value)
        .join(EvaluationMetric, EvaluationMetric.model_evaluation_id == ModelEvaluation.id)
        .where(ModelEvaluation.workspace_id == workspace_id, ModelEvaluation.candidate_id.in_(list(trained_ids)),
               ModelEvaluation.evaluation_scope == CV_FOLD_SCOPE, func.lower(ModelEvaluation.status) == "completed")
    ):
        number = _fold_number(summary)
        if number is None:
            return None
        if owner.setdefault((candidate_id, number), evaluation_id) != evaluation_id:
            return None  # two completed evaluations of one fold: not one locked score
        folds[candidate_id].setdefault(number, {})[str(name)] = float(value)
    winner = trained_ids.get(selection.selected_candidate_id)
    return RunEvidence(
        winner=winner.candidate_key if winner is not None else None,
        trained={row.candidate_key: candidate_from_payload(dict(row.payload or {}), folds[row.id],
                                                           fingerprint=row.fingerprint)
                 for row in trained_ids.values()},
        skipped=frozenset(row.candidate_key for row in candidates if is_time_skip({"status": row.status})),
    )


def _suspicious(db: Session, workspace_id: UUID, experiment_id: UUID) -> bool:
    """The run's own locked trust checks failed target leakage or implausible score."""

    evidence = DataQualityFinding.evidence
    return db.scalar(
        select(DataQualityFinding.id).where(
            DataQualityFinding.pipeline_run_id == experiment_id, DataQualityFinding.workspace_id == workspace_id,
            evidence["source"].astext == INVESTIGATION_SOURCE, evidence["check"].astext.in_(SUSPICIOUS_CHECKS),
            evidence["status"].astext == "fail",
        ).limit(1)
    ) is not None


def plan_looks(db: Session, workspace_id: UUID, split_plan_id: UUID | None) -> tuple[dict[UUID, int], int]:
    """({experiment id: look number} on the plan, comparisons on the plan).

    A look (ADR 0007 §6.4, conservative): a selection decision (the winner was locked, so the final
    test may have run), a final-evaluation row, or a ``final_test_started`` event; failed and
    cancelled runs included. Order: selection lock time, else the event time, else the evidence
    lock, else creation; ties by id. Three grouped queries over the plan's runs plus one event query
    limited to the runs without a selection row (no per-run subqueries). A sealed improve try's winner
    lock is not a look (it never scores the test set); a final row or event on one still counts."""

    if split_plan_id is None:
        return {}, 0
    runs = db.execute(
        select(Experiment.id, Experiment.status, Experiment.scientific_evidence_locked_at, Experiment.created_at,
               Experiment.evidence_mode)
        .where(Experiment.workspace_id == workspace_id, Experiment.split_plan_id == split_plan_id)
    ).all()
    if not runs:
        return {}, 0
    plan_runs = (select(Experiment.id)
                 .where(Experiment.workspace_id == workspace_id, Experiment.split_plan_id == split_plan_id))
    selected: dict[UUID, datetime] = dict(db.execute(
        select(ModelSelectionDecision.pipeline_run_id, func.min(ModelSelectionDecision.locked_at))
        .where(ModelSelectionDecision.workspace_id == workspace_id,
               ModelSelectionDecision.pipeline_run_id.in_(plan_runs))
        .group_by(ModelSelectionDecision.pipeline_run_id)
    ).all())
    scored = set(db.scalars(
        select(ExperimentCandidate.experiment_id).distinct()
        .join(ModelEvaluation, ModelEvaluation.candidate_id == ExperimentCandidate.id)
        .where(ExperimentCandidate.workspace_id == workspace_id, ModelEvaluation.workspace_id == workspace_id,
               ModelEvaluation.evaluation_scope == FINAL_SCOPE, ExperimentCandidate.experiment_id.in_(plan_runs))
    ))
    # ADR 0007 §6.4: a sealed try's winner lock is not a look; a stray final row or event still is.
    unselected = [run_id for run_id, *_rest, mode in runs if run_id not in selected or mode == EVIDENCE_MODE_SEALED]
    tested: dict[UUID, datetime] = dict(db.execute(
        select(MlRunEvent.experiment_id, func.min(MlRunEvent.timestamp))
        .where(MlRunEvent.workspace_id == workspace_id, MlRunEvent.event_type == FINAL_TEST_EVENT,
               MlRunEvent.experiment_id.in_(unselected))
        .group_by(MlRunEvent.experiment_id)
    ).all()) if unselected else {}
    looks: list[tuple[datetime, str, UUID]] = []
    comparisons = 0
    for run_id, status, locked, created, mode in runs:
        chosen = selected.get(run_id)
        if chosen is not None or str(status or "").upper() not in _IN_FLIGHT_EXCLUDED:
            comparisons += 1
        look_lock = chosen if mode != EVIDENCE_MODE_SEALED else None
        if look_lock is not None or run_id in tested or run_id in scored:
            looks.append((look_lock or tested.get(run_id) or chosen or locked or created, str(run_id), run_id))
    looks.sort(key=lambda item: (item[0], item[1]))
    return {item[2]: number for number, item in enumerate(looks, start=1)}, comparisons


def experiment_verdict(
    db: Session, *, actor: User, workspace_id: UUID, experiment_id: UUID, against_id: UUID | None = None
) -> ExperimentVerdictRead:
    if not can_read_workspace(db, actor, workspace_id):
        raise IdentityError("not authorized for this workspace", status_code=403)
    experiment = _load(db, workspace_id, experiment_id)
    against = _load(db, workspace_id, against_id) if against_id is not None else (
        _load(db, workspace_id, experiment.parent_pipeline_run_id)
        if experiment.parent_pipeline_run_id is not None else None)
    looks, comparisons_on_plan = plan_looks(db, workspace_id, experiment.split_plan_id)
    body: dict[str, Any] = {
        "experiment_id": experiment.id,
        "against_experiment_id": against.id if against is not None else None,
        "against_is_parent": against is not None and against.id == experiment.parent_pipeline_run_id,
        "split_plan_id": experiment.split_plan_id,
        "comparisons_on_plan": comparisons_on_plan,
        "test_set_looks": SplitPlanLooksRead(
            split_plan_id=experiment.split_plan_id, looks_on_plan=len(looks),
            experiment_look=looks.get(experiment.id), against_look=looks.get(against.id) if against else None,
        ),
    }

    # The plan caveat: this try sealed (never scored the final test), or only the other one sealed.
    caveat_kind = ("sealed" if experiment.evidence_mode == EVIDENCE_MODE_SEALED else
                   "mixed" if against is not None and against.evidence_mode == EVIDENCE_MODE_SEALED else "scored")

    def refuse(reason: str, **extra: Any) -> ExperimentVerdictRead:
        message = verdict_message("not_comparable", reason, metric=None, direction=None, folds=None,
                                  mean_difference=None, margin=None, comparisons_on_plan=comparisons_on_plan,
                                  caveat_kind=caveat_kind)
        return ExperimentVerdictRead(**{**body, **extra}, verdict="not_comparable", reason=reason, message=message)

    if against is None:
        return refuse("no_parent")
    if against.id == experiment.id:
        return refuse("same_experiment")
    if experiment.split_plan_id is None or experiment.split_plan_id != against.split_plan_id:
        return refuse("different_split_plan")
    strategy = db.scalar(select(SplitPlan.validation_strategy).where(
        SplitPlan.id == experiment.split_plan_id, SplitPlan.workspace_id == workspace_id))
    if strategy == TIME_SERIES_SPLIT:
        return refuse("time_ordered_folds")  # Nadeau-Bengio assumes exchangeable K-fold folds
    if any(str(run.status or "").upper() != "COMPLETED" for run in (experiment, against)):
        return refuse("not_finished")
    if any(run.scientific_evidence_locked_at is None for run in (experiment, against)):
        return refuse("evidence_not_locked")
    mine, theirs = _selection(db, workspace_id, experiment.id), _selection(db, workspace_id, against.id)
    if mine is None or theirs is None:
        return refuse("no_winner")
    if mine.selection_metric != theirs.selection_metric:
        return refuse("different_metric")
    metric = mine.selection_metric
    new_candidates = new_candidate_count(db, workspace_id, experiment.id, against.id)
    comparisons = max(1, new_candidates)  # ADR 0007 §5: T = Σ k_i; the winner is the best of k new ones
    body.update(comparisons=comparisons, new_candidates=new_candidates)
    scores = (locked_fold_scores(db, workspace_id, mine.selected_candidate_id, metric),
              locked_fold_scores(db, workspace_id, theirs.selected_candidate_id, metric))
    if scores[0] is None or scores[1] is None:
        return refuse("missing_locked_folds", metric=metric)
    outcome = paired_verdict(metric, scores[0], scores[1], comparisons=comparisons)
    suspicious = _suspicious(db, workspace_id, experiment.id)
    verdict, reason = _guarded(outcome, suspicious)
    message = verdict_message(verdict, reason, metric=metric, direction=outcome.direction, folds=outcome.folds,
                              mean_difference=outcome.mean_difference, margin=outcome.margin,
                              new_candidates=new_candidates, comparisons_on_plan=comparisons_on_plan,
                              caveat_kind=caveat_kind)
    texts = {"new_candidates": new_candidates, "comparisons_on_plan": comparisons_on_plan, "caveat_kind": caveat_kind}
    goal = _goal_verdict(db, workspace_id, (experiment, mine), (against, theirs), metric, outcome, comparisons,
                         suspicious, texts)
    return ExperimentVerdictRead(
        **body, verdict=verdict, reason=reason, message=message, metric=metric,
        direction=outcome.direction, folds=outcome.folds or None, mean_difference=outcome.mean_difference,
        margin=outcome.margin, minimum_effect=outcome.minimum_effect,
        minimum_effect_basis=outcome.minimum_effect_basis, standard_error=outcome.standard_error,
        t_quantile=outcome.t_quantile,
        fold_scores=[VerdictFoldRead(fold_number=f.fold_number, experiment=f.experiment, against=f.against,
                                     difference=f.difference) for f in outcome.paired],
        goal=goal,
    )


def _guarded(outcome: PairedVerdict, suspicious: bool) -> tuple[str, str | None]:
    """ADR 0007 §5 ``rejected_suspicious``: a run failing its own leakage / implausible-score check never beats."""

    if outcome.verdict == "beats_the_noise" and suspicious:
        return "not_comparable", "suspicious_result"
    return outcome.verdict, outcome.reason


def _numbers(outcome: PairedVerdict) -> dict[str, Any]:
    return {"direction": outcome.direction, "folds": outcome.folds or None, "mean_difference": outcome.mean_difference,
            "margin": outcome.margin, "minimum_effect": outcome.minimum_effect,
            "minimum_effect_basis": outcome.minimum_effect_basis, "standard_error": outcome.standard_error,
            "t_quantile": outcome.t_quantile,
            "fold_scores": [GoalFoldRead(fold=f.fold_number, experiment=f.experiment, against=f.against,
                                         difference=f.difference) for f in outcome.paired]}


def _goal_objective(db: Session, workspace_id: UUID, against: Experiment,
                    experiment: Experiment) -> tuple[Any, OperatingObjectiveInput] | None:
    """The pre-registered goal: the operating-point choice that was current on the compared-against run
    (the model in use) when this run was created, if it was made by objective. A choice recorded after the
    try (or on the try itself) could be fitted to the try's own curve, so it is never used; a choice of an
    exact threshold sets no goal."""

    choice = choice_before(db, workspace_id, against.id, experiment.created_at)
    details = dict(choice.details or {}) if choice is not None else {}
    if choice is None or details.get("method") != "objective" or not isinstance(details.get("objective"), dict):
        return None
    try:
        return choice, OperatingObjectiveInput.model_validate(details["objective"])
    except ValueError:
        return None


def _winner_key(db: Session, workspace_id: UUID, candidate_id: UUID) -> str | None:
    return db.scalar(select(ExperimentCandidate.candidate_key).where(
        ExperimentCandidate.id == candidate_id, ExperimentCandidate.workspace_id == workspace_id))


def _goal_verdict(db: Session, workspace_id: UUID, mine: tuple[Experiment, ModelSelectionDecision],
                  theirs: tuple[Experiment, ModelSelectionDecision], metric: str, outcome: PairedVerdict,
                  comparisons: int, suspicious: bool, texts: dict[str, Any]) -> GoalVerdictRead:
    """The §5 rule on the goal (exp U3). Plain goal = the ranking metric (the same paired numbers);
    a constrained goal scores each run per fold at its own solved threshold (``goal_fold_scores``)."""

    (experiment, experiment_selection), (against, against_selection) = mine, theirs
    found = _goal_objective(db, workspace_id, against, experiment)
    if found is None:
        verdict, reason = _guarded(outcome, suspicious)
        numbers = _numbers(outcome)
        message = goal_message(verdict, reason, kind="plain", metric=metric, constraints=[], scored_by="goal_metric",
                               direction=outcome.direction, folds=outcome.folds, mean_difference=outcome.mean_difference,
                               margin=outcome.margin, **texts)
        return GoalVerdictRead(kind="plain", source="ranking_metric", metric=metric, verdict=verdict, reason=reason,
                               message=message, comparisons=comparisons, **numbers)
    choice, objective = found
    constraints = [GoalConstraintRead(metric=item.metric, op=item.op, value=item.value) for item in objective.constraints]
    stored = [(run.result or {}).get("operating_curve") for run in (experiment, against)]
    base: dict[str, Any] = {
        "kind": "objective", "source": "operating_point_choice", "choice_decision_id": choice.id,
        "metric": objective.goal, "constraints": constraints, "comparisons": comparisons,
        "basis": "out_of_fold_operating_curves",
        "experiment_curve_digest": curve_digest(stored[0]), "against_curve_digest": curve_digest(stored[1]),
    }

    def refuse(reason: str) -> GoalVerdictRead:
        message = goal_message("not_comparable", reason, kind="objective", metric=objective.goal,
                               constraints=constraints, scored_by="goal_metric", direction=None, folds=None,
                               mean_difference=None, margin=None, **texts)
        return GoalVerdictRead(**base, verdict="not_comparable", reason=reason, message=message)

    winners = (_winner_key(db, workspace_id, experiment_selection.selected_candidate_id),
               _winner_key(db, workspace_id, against_selection.selected_candidate_id))
    # experiments.result stays writable: a curve counts only when it names the locked winner.
    curves = [load_curve(item) if isinstance(item, dict) and winner and item.get("candidate_id") == winner else None
              for item, winner in zip(stored, winners)]
    if curves[0] is None or curves[1] is None:
        return refuse("no_goal_curve")
    point_objective = PointObjective(goal=objective.goal, constraints=tuple(
        MetricConstraint(metric=item.metric, op=item.op, value=float(item.value)) for item in objective.constraints))
    try:
        own = goal_fold_scores(curves[0], point_objective)
        other = goal_fold_scores(curves[1], point_objective, fold_counts=own.fold_counts)
    except GoalScoreRefusal as exc:
        return refuse(exc.reason)
    base.update(experiment_threshold=own.threshold, against_threshold=other.threshold,
                experiment_meets_rule=own.feasible, against_meets_rule=other.feasible)
    by_shortfall = not (own.feasible and other.feasible)

    def folds(scores: Any) -> dict[int, FoldScore]:
        return {n: FoldScore(value=scores.shortfalls[n - 1] if by_shortfall else scores.values[n - 1],
                             validation_rows=sum(scores.fold_counts[n - 1]))
                for n in range(1, len(scores.values) + 1)}

    scored = GOAL_SHORTFALL if by_shortfall else objective.goal
    # δ_min from the counts behind each fold's score: the goal metric's denominator, or the rule's.
    behind = [n for n in ((*own.rule_denominators, *other.rule_denominators) if by_shortfall
                          else (*own.denominators, *other.denominators)) if n is not None]
    result = paired_verdict(scored, folds(own), folds(other), comparisons=comparisons,
                            lower_is_better=True if by_shortfall else None,
                            count_denominator=min(behind) if behind else None)
    verdict, reason = _guarded(result, suspicious)
    if verdict == "beats_the_noise" and not own.feasible and other.feasible:
        # §5: a smaller mean shortfall never makes a try that misses the rule beat one that meets it.
        verdict, reason = "within_the_noise", "try_misses_rule"
    scored_by = GOAL_SHORTFALL if by_shortfall else "goal_metric"
    message = goal_message(verdict, reason, kind="objective", metric=objective.goal, constraints=constraints,
                           scored_by=scored_by, direction=result.direction, folds=result.folds,
                           mean_difference=result.mean_difference, margin=result.margin, **texts)
    return GoalVerdictRead(**base, scored_by=scored_by, verdict=verdict, reason=reason, message=message,
                           **_numbers(result))
