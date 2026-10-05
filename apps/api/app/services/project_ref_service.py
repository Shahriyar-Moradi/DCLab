"""Project refs: the only mutable graph pointers (ADR 0006 §2).

P2.2-B ships the bootstrap rule only (``refs.bootstrap.v1``): the first run in
a project without a champion that reaches a locked ModelVersion *with* a project
source dataset and a SplitPlan initializes the missing refs from its own lineage
(refs a human already set are kept) with one accepted ``ref_initialized`` record,
in the same transaction. A run that cannot satisfy
``dataset``, ``split_plan`` and ``champion_model`` defers bootstrap to a later
run. ``problem_spec`` / ``feature_recipe`` may still be unsatisfiable; they are
listed in ``details.skipped_refs`` and P2.5-A ``move_ref`` may insert them later
(``from: null``) under an accepted record (ADR 0006 Revision 2). Later runs
never move refs automatically. A run started by a service token (an agent)
never initializes refs either: its bootstrap is recorded as that agent's proposal
(P3.4-A), which a human accepts with ``move_ref(proposal_id=...)``.

P2.5-A ``move_ref`` is the only other writer: one transaction (savepoint) that
INSERTs the accepted ``ref_moved``/``champion_promoted`` record first, then a
versioned UPDATE (or, for a missing kind, an INSERT) of every moved ref; any
0-row update raises ``RefVersionConflictError`` and rolls the record back, so
no accepted move record exists without its move. ``propose_ref_move`` records
a proposal (agents and humans) that nothing applies until a human ``move_ref``
with ``proposal_id``.

Champion evidence rule (P6.10-A; ADR 0008 §2b, ADR 0006 Q1): agents never see or cite
the final holdout. On an agent's ``champion_promoted`` proposal the service attaches
the promoted model's own single locked winner final-holdout evaluation itself
(``champion_final_evaluation``: same model version / winner candidate, same experiment
and so the same split plan, evidence locked; selected by identity, never by value),
refuses with the generic ``champion_evidence_unavailable`` when there is none, and
marks it ``details.service_attached_evidence`` for the audit; an agent-supplied
holdout ref is refused (``holdout_not_allowed``). Accepting stays human
(``move_ref``), which re-checks that the evaluation still belongs to the model;
``champion_final_evaluation_for_human`` gives the accepting human its values.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Iterable, Sequence
from uuid import UUID

from sqlalchemy import func, or_, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.db.models import (
    Dataset,
    EvaluationMetric,
    ExecutionRequest,
    Experiment,
    ExperimentCandidate,
    FeatureSetVersion,
    ModelEvaluation,
    ModelVersion,
    ProblemSpec,
    ProjectDecisionRecord,
    ProjectRef,
    SplitPlan,
    WorkflowRun,
)
from app.domain.decision_records import (
    ACTOR_AGENT,
    ACTOR_HUMAN,
    DECISION_CHAMPION_PROMOTED,
    DETAIL_SERVICE_ATTACHED_EVIDENCE,
    EVIDENCE_REFS_MAX,
    RULE_CHAMPION_FINAL_EVALUATION,
    DECISION_REF_INITIALIZED,
    DECISION_REF_MOVED,
    REF_INITIALIZED_SCHEMA_VERSION,
    REF_MOVE_SCHEMA_VERSION,
    REFS_BOOTSTRAP_RATIONALE,
    RULE_REFS_BOOTSTRAP,
    STATE_ACCEPTED,
    STATE_PROPOSED,
    DecisionActor,
    ref_initialized_idempotency_key,
)
from app.domain.errors import (
    ChampionSplitPlanMismatchError,
    DecisionActorNotPermittedError,
    InvalidDecisionRecordError,
    RefTargetNotFoundError,
    RefVersionConflictError,
)
from app.domain.state_graph import REF_KINDS, REF_TARGET_COLUMNS, REF_TARGET_NODE_KINDS
from app.services.decision_record_service import (
    FINAL_HOLDOUT_SCOPE,
    authorize_writer,
    build_record,
    clean_json_object,
    clean_rationale,
    evidence_ref,
    get_record,
    insert_record,
    load_project,
    node_digest,
    parse_evidence_refs,
    raise_for_record_race,
    refuse_agent_holdout,
    replay_record,
    require_open_proposal,
    rule_record,
    scoped_idempotency_key,
    unique_violation,
    validate_evidence_refs,
)
from app.services.split_plan_service import is_unique_race

logger = logging.getLogger(__name__)

REFS_BOOTSTRAP_PROPOSAL_RATIONALE = (
    "first locked model of a run started by a service token; refs initialize only when a human accepts"
)

# Bootstrap waits for a run whose lineage satisfies these kinds.
BOOTSTRAP_REQUIRED_REF_KINDS = ("dataset", "split_plan", "champion_model")
# A concurrent bootstrap of the same project collides on one of these.
BOOTSTRAP_RACE_CONSTRAINTS = frozenset(
    {"uq_pdr_workspace_idempotency_key", "uq_project_refs_project_ref_kind"}
)


def _bootstrap_targets(
    db: Session, experiment: Experiment, model_version: ModelVersion
) -> tuple[dict[str, UUID], list[dict[str, str]]]:
    """Ref kind -> target id from the run's own lineage; unmet kinds are reported."""

    ws, project = experiment.workspace_id, experiment.project_id
    targets: dict[str, UUID] = {}
    skipped: list[dict[str, str]] = []

    workflow_run = (
        db.get(WorkflowRun, experiment.workflow_run_id)
        if experiment.workflow_run_id is not None
        else None
    )
    spec = (
        db.get(ProblemSpec, workflow_run.problem_spec_id)
        if workflow_run is not None and workflow_run.problem_spec_id is not None
        else None
    )
    if spec is None:
        skipped.append({"ref_kind": "problem_spec", "reason": "run has no problem spec"})
    elif spec.workspace_id != ws or spec.project_id != project or spec.status != "locked":
        skipped.append({"ref_kind": "problem_spec", "reason": "problem spec is not locked"})
    else:
        targets["problem_spec"] = spec.id

    dataset = (
        db.get(Dataset, experiment.source_dataset_id)
        if experiment.source_dataset_id is not None
        else None
    )
    if dataset is None or dataset.workspace_id != ws or dataset.project_id != project:
        skipped.append({"ref_kind": "dataset", "reason": "run has no project source dataset"})
    else:
        targets["dataset"] = dataset.id

    plan = db.get(SplitPlan, experiment.split_plan_id) if experiment.split_plan_id else None
    if plan is None or plan.workspace_id != ws or plan.project_id != project:
        skipped.append({"ref_kind": "split_plan", "reason": "run has no split plan"})
    elif targets.get("dataset") != plan.dataset_id:
        skipped.append({"ref_kind": "split_plan", "reason": "split plan dataset is not the dataset ref"})
    else:
        targets["split_plan"] = plan.id

    recipe = (
        db.get(FeatureSetVersion, model_version.feature_set_version_id)
        if model_version.feature_set_version_id is not None
        else None
    )
    if recipe is None or recipe.workspace_id != ws or recipe.project_id != project:
        skipped.append({"ref_kind": "feature_recipe", "reason": "model has no feature recipe"})
    elif recipe.locked_at is None:
        skipped.append({"ref_kind": "feature_recipe", "reason": "feature recipe is not locked"})
    else:
        targets["feature_recipe"] = recipe.id

    targets["champion_model"] = model_version.id
    return targets, skipped


def initialize_refs_on_first_model(
    db: Session, *, experiment: Experiment, model_version: ModelVersion
) -> ProjectDecisionRecord | None:
    """``refs.bootstrap.v1``: initialize the missing refs while the project has no
    champion; never moves an existing ref.

    Called after the run's evidence lock, before its commit. Returns the
    ``ref_initialized`` record, or None when the project already has a champion,
    the run has no project, its lineage differs from an existing dataset /
    split_plan / feature_recipe ref, or the champion rule cannot be met (no final
    holdout). Refs a human already set (e.g. an accepted problem_spec) are kept
    and listed in ``details.skipped_refs``. A run a service token started (P3.4-A)
    writes no refs: it returns that agent's proposed ``champion_promoted`` record
    instead (one open proposal per project; a new one after a rejection).
    """

    project_id = experiment.project_id
    if project_id is None or model_version.project_id != project_id:
        return None
    if experiment.scientific_evidence_locked_at is None:
        return None
    if model_version.pipeline_run_id != experiment.id or model_version.workspace_id != experiment.workspace_id:
        return None
    current = current_refs(db, workspace_id=experiment.workspace_id, project_id=project_id)
    if "champion_model" in current:
        return None
    holdout = db.scalar(
        select(ModelEvaluation)
        .where(
            ModelEvaluation.workspace_id == experiment.workspace_id,
            ModelEvaluation.candidate_id == model_version.selected_candidate_id,
            ModelEvaluation.evaluation_scope == FINAL_HOLDOUT_SCOPE,
        )
        .limit(1)
    )
    if holdout is None:
        return None
    targets, skipped = _bootstrap_targets(db, experiment, model_version)
    if any(kind not in targets for kind in BOOTSTRAP_REQUIRED_REF_KINDS):
        # Defer: a one-shot bootstrap without dataset/split plan would leave
        # the project's refs permanently incomplete.
        return None
    if not _keep_existing_refs(targets, skipped, current):
        return None
    selection_metric = None
    if isinstance(experiment.result, dict):
        selection_metric = (experiment.result.get("selection") or {}).get("selection_metric")
    token_id = _initiating_service_token(db, experiment)
    if token_id is not None:
        # P3.4-A: a run an agent (service token) started never sets refs on its
        # own; the bootstrap becomes that agent's proposal, which only a human
        # accepting it (the ref-move path with this proposal) applies.
        return _propose_bootstrap(
            db, experiment=experiment, model_version=model_version, targets=targets, current=current,
            skipped=skipped, token_id=token_id, holdout_id=holdout.id, selection_metric=selection_metric,
        )
    if "problem_spec" in targets:
        spec = db.get(ProblemSpec, targets["problem_spec"])
        if spec is not None and spec.created_by_service_token_id is not None:
            # A rule never adopts an agent-authored spec; a human accepts its proposal.
            del targets["problem_spec"]
            skipped.append({"ref_kind": "problem_spec", "reason": "agent-authored spec; accept its proposal"})
    ref_moves = [
        {
            "ref_kind": kind,
            "from": None,
            "to": {"kind": REF_TARGET_NODE_KINDS[kind], "id": str(targets[kind])},
        }
        for kind in REF_KINDS
        if kind in targets
    ]
    record = rule_record(
        workspace_id=experiment.workspace_id,
        project_id=project_id,
        decision_type=DECISION_REF_INITIALIZED,
        subject_kind="model_version",
        subject_id=model_version.id,
        subject_digest=model_version.content_digest,
        actor_rule=RULE_REFS_BOOTSTRAP,
        rationale=REFS_BOOTSTRAP_RATIONALE,
        facts={
            "experiment_id": str(experiment.id),
            "model_version": model_version.version,
            "final_holdout_evaluation_id": str(holdout.id),
        },
        evidence_refs=[
            evidence_ref(
                "candidate",
                model_version.selected_candidate_id,
                metric=selection_metric,
                scope=FINAL_HOLDOUT_SCOPE,
            )
        ],
        details={"ref_moves": ref_moves, "skipped_refs": skipped},
        schema_version=REF_INITIALIZED_SCHEMA_VERSION,
        idempotency_key=ref_initialized_idempotency_key(project_id),
    )
    try:
        # A concurrent bootstrap collides on the idempotency key or on
        # UNIQUE(project_id, ref_kind); the loser keeps the winner's refs.
        with db.begin_nested():
            db.add(record)
            db.flush()
            for kind in REF_KINDS:
                if kind not in targets:
                    continue
                ref = ProjectRef(
                    workspace_id=experiment.workspace_id,
                    project_id=project_id,
                    ref_kind=kind,
                    version=1,
                    # moved_at = created_at = now() for a bootstrap (ADR §2).
                    decision_record_id=record.id,
                )
                setattr(ref, REF_TARGET_COLUMNS[kind], targets[kind])
                db.add(ref)
            db.flush()
    except IntegrityError as exc:
        if not is_unique_race(exc, BOOTSTRAP_RACE_CONSTRAINTS):
            raise
        logger.info("project %s refs were bootstrapped concurrently; keeping them", project_id)
        return None
    return record


def _keep_existing_refs(
    targets: dict[str, UUID], skipped: list[dict[str, str]], current: dict[str, ProjectRef]
) -> bool:
    """Drop kinds a ref already covers (never moved by a bootstrap). False when the run's
    dataset / split plan / feature recipe differs from an existing ref: the run is not
    on the project's current lineage, so it defers."""

    for kind, ref in current.items():
        if kind not in targets:
            continue
        if _ref_target_id(ref) == targets[kind]:
            reason = "ref already set"
        elif kind == "problem_spec":
            reason = "ref already points at another problem spec"
        else:
            return False
        del targets[kind]
        skipped.append({"ref_kind": kind, "reason": reason})
    return True


def _initiating_service_token(db: Session, experiment: Experiment) -> UUID | None:
    """The service token whose /v1 call queued this run (``execution_requests``), if any."""

    return db.scalar(
        select(ExecutionRequest.initiated_by_service_token_id)
        .where(
            ExecutionRequest.workspace_id == experiment.workspace_id,
            ExecutionRequest.pipeline_run_id == experiment.id,
            ExecutionRequest.initiated_by_service_token_id.is_not(None),
        )
        .limit(1)
    )


BOOTSTRAP_PROPOSAL_KEY_PREFIX = "ref_bootstrap_proposal"


def bootstrap_proposal_idempotency_key(project_id: UUID | str, generation: int) -> str:
    """One open agent bootstrap proposal per project; a rejected one is followed by
    the next generation (a concurrent duplicate collides on the key)."""

    return f"{BOOTSTRAP_PROPOSAL_KEY_PREFIX}:{project_id}:{generation}"


def _propose_bootstrap(
    db: Session,
    *,
    experiment: Experiment,
    model_version: ModelVersion,
    targets: dict[str, UUID],
    current: dict[str, ProjectRef],
    skipped: list[dict[str, str]],
    token_id: UUID,
    holdout_id: UUID,
    selection_metric: str | None,
) -> ProjectDecisionRecord | None:
    """The bootstrap's ref moves (missing kinds only, ``from: null``) as a proposed
    ``champion_promoted`` record of the initiating agent; no ref is written. Skipped
    while an earlier bootstrap proposal of the project is still open."""

    ws, project_id = experiment.workspace_id, experiment.project_id
    earlier = list(db.scalars(
        select(ProjectDecisionRecord).where(
            ProjectDecisionRecord.workspace_id == ws,
            ProjectDecisionRecord.project_id == project_id,
            ProjectDecisionRecord.idempotency_key.like(f"{BOOTSTRAP_PROPOSAL_KEY_PREFIX}:{project_id}:%"),
        )
    ))
    if earlier:
        resolved = set(db.scalars(
            select(ProjectDecisionRecord.supersedes_id).where(
                ProjectDecisionRecord.workspace_id == ws,
                ProjectDecisionRecord.supersedes_id.in_([row.id for row in earlier]),
            )
        ))
        if any(row.id not in resolved for row in earlier):
            return None  # still open: a human has not accepted or rejected it yet
    key = bootstrap_proposal_idempotency_key(project_id, len(earlier) + 1)
    moves = [RefMove(kind, targets[kind], None) for kind in REF_KINDS if kind in targets]
    evidence = [
        evidence_ref("candidate", model_version.selected_candidate_id, metric=selection_metric,
                     scope=FINAL_HOLDOUT_SCOPE)
    ]
    try:
        # The same single locked evaluation the accept re-check requires (never by value).
        holdout_id = champion_final_evaluation(db, workspace_id=ws, project_id=project_id,
                                               model_version_id=model_version.id).id
        rows = _load_targets(db, workspace_id=ws, project_id=project_id, moves=moves)
        _check_targets(db, moves=moves, targets=rows, current=current, evidence=evidence)
    except (InvalidDecisionRecordError, RefTargetNotFoundError, ChampionSplitPlanMismatchError) as exc:
        logger.info("project %s: no bootstrap proposal (%s)", project_id, getattr(exc, "code", exc))
        return None
    record = _ref_move_record(
        workspace_id=ws, project_id=project_id, actor=DecisionActor.agent(service_token_id=token_id),
        decision_type=DECISION_CHAMPION_PROMOTED, state=STATE_PROPOSED, moves=moves, targets=rows,
        current=current, rationale=REFS_BOOTSTRAP_PROPOSAL_RATIONALE,
        evidence=evidence,
        facts={"experiment_id": str(experiment.id), "model_version": model_version.version,
               "final_holdout_evaluation_id": str(holdout_id)},
        supersedes_id=None, key=key,
    )
    record.details = {**record.details, "skipped_refs": skipped,
                      DETAIL_SERVICE_ATTACHED_EVIDENCE: [_attached_marker(evidence[0], holdout_id)]}
    try:
        with db.begin_nested():
            db.add(record)
            db.flush()
    except IntegrityError as exc:
        if not is_unique_race(exc, BOOTSTRAP_RACE_CONSTRAINTS):
            raise
        return None
    return record


# --- P2.5-A: move_ref ----------------------------------------------------------------

_REF_TARGET_TABLES: dict[str, Any] = {
    "problem_spec": ProblemSpec,
    "dataset": Dataset,
    "split_plan": SplitPlan,
    "feature_recipe": FeatureSetVersion,
    "champion_model": ModelVersion,
}
_REF_KIND_UNIQUE = "uq_project_refs_project_ref_kind"


@dataclass(frozen=True)
class RefMove:
    """Move ``ref_kind`` to ``target_id``; ``expected_version`` is the optimistic token.

    ``expected_version=None`` asserts the ref kind does not exist yet: it is
    inserted (``from: null``) under the accepted record (ADR 0006 Revision 2).
    """

    ref_kind: str
    target_id: UUID
    expected_version: int | None


@dataclass
class RefMoveResult:
    record: ProjectDecisionRecord
    refs: list[ProjectRef] = field(default_factory=list)
    replayed: bool = False


CHAMPION_EVIDENCE_UNAVAILABLE = "champion_evidence_unavailable"


def champion_final_evaluation(
    db: Session, *, workspace_id: UUID, project_id: UUID, model_version_id: UUID
) -> ModelEvaluation:
    """The promoted model's own single locked winner final-holdout evaluation. Chosen
    by identity only (never by a metric value: no holdout oracle); anything else, or
    more than one, is the same generic refusal."""

    model = db.scalar(select(ModelVersion).where(
        ModelVersion.id == model_version_id, ModelVersion.workspace_id == workspace_id,
        ModelVersion.project_id == project_id))
    experiment = model and db.scalar(select(Experiment).where(
        Experiment.id == model.pipeline_run_id, Experiment.workspace_id == workspace_id,
        Experiment.project_id == project_id))
    rows = [] if experiment is None or experiment.scientific_evidence_locked_at is None else list(db.scalars(
        select(ModelEvaluation)
        .join(ExperimentCandidate, ExperimentCandidate.id == ModelEvaluation.candidate_id)
        .where(ModelEvaluation.workspace_id == workspace_id,
               ModelEvaluation.candidate_id == model.selected_candidate_id,
               ModelEvaluation.evaluation_scope == FINAL_HOLDOUT_SCOPE,
               or_(ModelEvaluation.model_version_id.is_(None), ModelEvaluation.model_version_id == model.id),
               ExperimentCandidate.workspace_id == workspace_id, ExperimentCandidate.experiment_id == experiment.id)
        .limit(2)
    ))
    if len(rows) != 1:
        raise InvalidDecisionRecordError(CHAMPION_EVIDENCE_UNAVAILABLE, "the promoted model is not eligible")
    return rows[0]


def _attached_marker(ref: dict[str, str], evaluation_id: UUID) -> dict[str, str]:
    return {**ref, "rule": RULE_CHAMPION_FINAL_EVALUATION, "evaluation_id": str(evaluation_id)}


def _champion_evidence(
    db: Session, *, workspace_id: UUID, project_id: UUID, moves: Sequence[RefMove]
) -> tuple[dict[str, str], dict[str, str]] | None:
    """(evidence ref, audit marker) the service attaches to a champion move, or None."""

    champion = next((move for move in moves if move.ref_kind == "champion_model"), None)
    if champion is None:
        return None
    evaluation = champion_final_evaluation(
        db, workspace_id=workspace_id, project_id=project_id, model_version_id=champion.target_id)
    ref = evidence_ref("candidate", evaluation.candidate_id, scope=FINAL_HOLDOUT_SCOPE)
    return ref, _attached_marker(ref, evaluation.id)


def check_proposed_ref_moves(
    db: Session, *, workspace_id: UUID, project_id: UUID, moves: dict[str, UUID]
) -> None:
    """Validator of an agent's ref-move tool call (harness, P6.10-A): every target is a
    node of this project and a champion has its attachable final evaluation."""

    items = _normalized_moves([RefMove(kind, target, None) for kind, target in moves.items()])
    _load_targets(db, workspace_id=workspace_id, project_id=project_id, moves=items)
    _champion_evidence(db, workspace_id=workspace_id, project_id=project_id, moves=items)


def champion_final_evaluation_for_human(
    db: Session, *, actor: DecisionActor, workspace_id: UUID, project_id: UUID, record_id: UUID
) -> dict[str, Any]:
    """The values of the final evaluation a champion proposal carries, for the human
    about to accept it (P6.6-A confirm card). Humans with ML-write only, never a token,
    an agent run or a rule; re-checked against the promoted model."""

    if actor.kind != ACTOR_HUMAN:
        raise DecisionActorNotPermittedError("human_only", "only a human sees the final evaluation")
    authorize_writer(db, actor, workspace_id=workspace_id, allow_rule=False)
    proposal = get_record(db, workspace_id=workspace_id, project_id=project_id, record_id=record_id)
    target = next((m for m in (proposal.details or {}).get("ref_moves") or []
                   if isinstance(m, dict) and m.get("ref_kind") == "champion_model"), None)
    if proposal.decision_type != DECISION_CHAMPION_PROMOTED or target is None:
        raise InvalidDecisionRecordError("not_a_champion_proposal", "this record does not promote a champion")
    evaluation = champion_final_evaluation(
        db, workspace_id=workspace_id, project_id=project_id, model_version_id=UUID(str(target["to"]["id"])))
    metrics = dict(db.execute(select(EvaluationMetric.metric_name, EvaluationMetric.metric_value).where(
        EvaluationMetric.model_evaluation_id == evaluation.id)).all())
    return {"evaluation_id": evaluation.id, "candidate_id": evaluation.candidate_id, "metrics": metrics}


def current_refs(
    db: Session, *, workspace_id: UUID, project_id: UUID, lock: bool = False
) -> dict[str, ProjectRef]:
    stmt = select(ProjectRef).where(
        ProjectRef.workspace_id == workspace_id, ProjectRef.project_id == project_id
    )
    if lock:
        # FOR UPDATE must refresh identity-map rows, or a stale version would
        # survive the lock and turn a valid move into a false 409.
        stmt = stmt.with_for_update().execution_options(populate_existing=True)
    return {ref.ref_kind: ref for ref in db.scalars(stmt)}


def _ref_target_id(ref: ProjectRef) -> UUID:
    return getattr(ref, REF_TARGET_COLUMNS[ref.ref_kind])


def _normalized_moves(moves: Iterable[RefMove]) -> list[RefMove]:
    items = list(moves or [])
    if not items:
        raise InvalidDecisionRecordError("ref_moves_required", "a ref move names at least one ref")
    seen: set[str] = set()
    for move in items:
        if not isinstance(move, RefMove) or move.ref_kind not in REF_KINDS:
            raise InvalidDecisionRecordError("unknown_ref_kind", "unknown ref kind")
        if move.ref_kind in seen:
            raise InvalidDecisionRecordError("duplicate_ref_kind", f"{move.ref_kind} is moved twice")
        version = move.expected_version
        if version is not None and (isinstance(version, bool) or not isinstance(version, int) or version < 1):
            raise InvalidDecisionRecordError("invalid_expected_version", "expected_version must be >= 1")
        seen.add(move.ref_kind)
    return sorted(items, key=lambda move: REF_KINDS.index(move.ref_kind))


def _load_targets(
    db: Session, *, workspace_id: UUID, project_id: UUID, moves: Sequence[RefMove]
) -> dict[str, Any]:
    targets: dict[str, Any] = {}
    for move in moves:
        model = _REF_TARGET_TABLES[move.ref_kind]
        row = db.scalar(
            select(model).where(
                model.id == move.target_id,
                model.workspace_id == workspace_id,
                model.project_id == project_id,
            )
        )
        if row is None:
            # Unknown, another project's or another tenant's: indistinguishable.
            raise RefTargetNotFoundError(
                "target_not_found", "ref target not found in this project", ref_kind=move.ref_kind
            )
        targets[move.ref_kind] = row
    return targets


def _cites_final_holdout(evidence: list[dict[str, str]], model: ModelVersion) -> bool:
    cited = {("model_version", str(model.id)), ("candidate", str(model.selected_candidate_id))}
    return any(
        ref.get("scope") == FINAL_HOLDOUT_SCOPE and (ref.get("kind"), ref.get("id")) in cited
        for ref in evidence
    )


def _model_split_plan(db: Session, model: ModelVersion) -> UUID | None:
    experiment = db.scalar(
        select(Experiment).where(
            Experiment.id == model.pipeline_run_id, Experiment.workspace_id == model.workspace_id
        )
    )
    return experiment.split_plan_id if experiment is not None else None


def _check_targets(
    db: Session,
    *,
    moves: Sequence[RefMove],
    targets: dict[str, Any],
    current: dict[str, ProjectRef],
    evidence: list[dict[str, str]],
) -> None:
    """ADR 0006 §2 semantic rules + the Rev 2 same-``split_plan_id`` champion rule."""

    by_kind = {move.ref_kind: move for move in moves}
    spec = targets.get("problem_spec")
    if spec is not None and spec.status != "locked":
        raise InvalidDecisionRecordError("problem_spec_not_locked", "only a locked problem spec can be referenced")
    recipe = targets.get("feature_recipe")
    if recipe is not None and recipe.locked_at is None:
        raise InvalidDecisionRecordError("feature_recipe_not_locked", "only a locked feature recipe can be referenced")
    dataset_id = (
        by_kind["dataset"].target_id
        if "dataset" in by_kind
        else (current["dataset"].dataset_id if "dataset" in current else None)
    )
    plan = targets.get("split_plan")
    if plan is not None and plan.dataset_id != dataset_id:
        raise InvalidDecisionRecordError(
            "split_plan_dataset_mismatch",
            "the split plan must partition the dataset ref target (or move both in one decision)",
        )
    model = targets.get("champion_model")
    if model is None:
        return
    experiment = db.scalar(
        select(Experiment).where(
            Experiment.id == model.pipeline_run_id,
            Experiment.workspace_id == model.workspace_id,
            Experiment.project_id == model.project_id,
        )
    )
    if experiment is None or experiment.scientific_evidence_locked_at is None:
        raise InvalidDecisionRecordError(
            "champion_not_locked", "a champion's experiment must have locked scientific evidence"
        )
    if not _cites_final_holdout(evidence, model):
        raise InvalidDecisionRecordError(
            "champion_requires_final_holdout_evidence",
            "a champion promotion cites the promoted model's final_holdout evaluation",
        )
    model_recipe = (
        db.get(FeatureSetVersion, model.feature_set_version_id)
        if model.feature_set_version_id is not None
        else None
    )
    if "feature_recipe" in by_kind:
        if by_kind["feature_recipe"].target_id != model.feature_set_version_id:
            raise InvalidDecisionRecordError(
                "champion_feature_recipe_mismatch", "feature_recipe moves to the promoted model's recipe"
            )
    elif (
        model_recipe is not None
        and model_recipe.locked_at is not None
        and model_recipe.project_id == model.project_id
    ):
        raise InvalidDecisionRecordError(
            "champion_requires_feature_recipe",
            "a champion promotion moves feature_recipe with champion_model",
        )
    # Rev 2 carry-forward: champions are only compared on the same SplitPlan. A
    # decision that also moves split_plan re-baselines on the new plan.
    if "split_plan" in by_kind:
        expected = by_kind["split_plan"].target_id
    elif "champion_model" in current:
        champion = db.get(ModelVersion, current["champion_model"].model_version_id)
        expected = _model_split_plan(db, champion) if champion is not None else None
    elif "split_plan" in current:
        expected = current["split_plan"].split_plan_id
    else:
        return
    if experiment.split_plan_id is None or experiment.split_plan_id != expected:
        raise ChampionSplitPlanMismatchError(
            "split_plan_mismatch",
            "the new champion was not evaluated on the current champion's split plan",
            expected_split_plan_id=str(expected) if expected else None,
            candidate_split_plan_id=str(experiment.split_plan_id) if experiment.split_plan_id else None,
        )


def _version_conflict(move: RefMove, ref: ProjectRef | None) -> RefVersionConflictError:
    return RefVersionConflictError(
        "stale_ref_version",
        f"{move.ref_kind} ref version is not the expected one",
        ref_kind=move.ref_kind,
        expected_version=move.expected_version,
        current_version=ref.version if ref is not None else None,
    )


def check_ref_versions(moves: Sequence[RefMove], current: dict[str, ProjectRef]) -> None:
    """Clean 409 before any write; the versioned UPDATE re-checks inside the transaction."""

    for move in moves:
        ref = current.get(move.ref_kind)
        if (ref.version if ref is not None else None) != move.expected_version:
            raise _version_conflict(move, ref)


def _decision_type(moves: Sequence[RefMove], requested: str | None) -> str:
    inferred = (
        DECISION_CHAMPION_PROMOTED
        if any(move.ref_kind == "champion_model" for move in moves)
        else DECISION_REF_MOVED
    )
    if requested is not None and requested != inferred:
        raise InvalidDecisionRecordError(
            "decision_type_mismatch", "champion_promoted moves champion_model; ref_moved never does"
        )
    return inferred


def _ref_move_record(
    *,
    workspace_id: UUID,
    project_id: UUID,
    actor: DecisionActor,
    decision_type: str,
    state: str,
    moves: Sequence[RefMove],
    targets: dict[str, Any],
    current: dict[str, ProjectRef],
    rationale: str,
    evidence: list[dict[str, str]],
    facts: dict[str, Any] | None,
    supersedes_id: UUID | None,
    key: str | None,
) -> ProjectDecisionRecord:
    ref_moves = []
    for move in moves:
        ref = current.get(move.ref_kind)
        node_kind = REF_TARGET_NODE_KINDS[move.ref_kind]
        ref_moves.append(
            {
                "ref_kind": move.ref_kind,
                "from": {"kind": node_kind, "id": str(_ref_target_id(ref))} if ref is not None else None,
                "to": {"kind": node_kind, "id": str(move.target_id)},
                "from_version": ref.version if ref is not None else None,
            }
        )
    primary = "champion_model" if "champion_model" in targets else moves[0].ref_kind
    return build_record(
        workspace_id=workspace_id,
        project_id=project_id,
        actor=actor,
        decision_type=decision_type,
        state=state,
        subject_kind=REF_TARGET_NODE_KINDS[primary],
        subject_id=targets[primary].id,
        subject_digest=node_digest(targets[primary]),
        rationale=rationale,
        facts=clean_json_object(facts, label="facts"),
        evidence_refs=evidence,
        details={"ref_moves": ref_moves},
        supersedes_id=supersedes_id,
        idempotency_key=key,
        schema_version=REF_MOVE_SCHEMA_VERSION,
    )


def _replay_request(
    moves: Sequence[RefMove], rationale: str, evidence_refs: Iterable[Any] | None
) -> dict[str, Any]:
    return {
        "rationale": clean_rationale(rationale),
        "evidence_refs": parse_evidence_refs(evidence_refs),
        "ref_moves": sorted((move.ref_kind, str(move.target_id)) for move in moves),
    }


def _prepare(
    db: Session,
    *,
    workspace_id: UUID,
    project_id: UUID,
    moves: Iterable[RefMove],
    rationale: str,
    evidence_refs: Iterable[Any] | None,
    lock: bool,
    attached: dict[str, str] | None = None,
) -> tuple[list[RefMove], str, list[dict[str, str]], dict[str, ProjectRef], dict[str, Any]]:
    items = _normalized_moves(moves)
    text = clean_rationale(rationale)
    evidence = validate_evidence_refs(
        db, workspace_id=workspace_id, project_id=project_id, refs=evidence_refs
    )
    if not evidence:
        raise InvalidDecisionRecordError("evidence_required", "a ref move cites at least one evidence ref")
    if attached is not None and attached not in evidence:  # the service-attached champion evidence
        if len(evidence) >= EVIDENCE_REFS_MAX:
            raise InvalidDecisionRecordError("too_many_evidence_refs", f"at most {EVIDENCE_REFS_MAX} evidence refs")
        evidence = [*evidence, attached]
    current = current_refs(db, workspace_id=workspace_id, project_id=project_id, lock=lock)
    targets = _load_targets(db, workspace_id=workspace_id, project_id=project_id, moves=items)
    _check_targets(db, moves=items, targets=targets, current=current, evidence=evidence)
    return items, text, evidence, current, targets


def propose_ref_move(
    db: Session,
    *,
    workspace_id: UUID,
    project_id: UUID,
    actor: DecisionActor,
    moves: Iterable[RefMove],
    rationale: str,
    evidence_refs: Iterable[Any] | None,
    facts: dict[str, Any] | None = None,
    decision_type: str | None = None,
    idempotency_key: str | None = None,
) -> ProjectDecisionRecord:
    """A proposed ``ref_moved``/``champion_promoted`` record; no ref changes."""

    authorize_writer(db, actor, workspace_id=workspace_id, allow_agent=True, allow_rule=False)
    refuse_agent_holdout(actor, evidence_refs, facts)
    load_project(db, workspace_id=workspace_id, project_id=project_id)
    items = _normalized_moves(moves)
    kind = _decision_type(items, decision_type)
    key = scoped_idempotency_key(actor, idempotency_key)
    service_attaches = actor.kind == ACTOR_AGENT and kind == DECISION_CHAMPION_PROMOTED
    request = _replay_request(items, rationale, evidence_refs)
    if service_attaches:
        request["evidence_refs_without_holdout"] = request.pop("evidence_refs")
    replayed = replay_record(
        db, workspace_id=workspace_id, project_id=project_id, idempotency_key=key,
        decision_type=kind, state=STATE_PROPOSED, supersedes_id=None, request=request,
    )
    if replayed is not None:
        return replayed
    champion = (_champion_evidence(db, workspace_id=workspace_id, project_id=project_id, moves=items)
                if service_attaches else None)
    items, text, evidence, current, targets = _prepare(
        db, workspace_id=workspace_id, project_id=project_id, moves=moves,
        rationale=rationale, evidence_refs=evidence_refs, lock=False, attached=champion and champion[0],
    )
    row = _ref_move_record(
        workspace_id=workspace_id, project_id=project_id, actor=actor, decision_type=kind,
        state=STATE_PROPOSED, moves=items, targets=targets, current=current, rationale=text,
        evidence=evidence, facts=facts, supersedes_id=None, key=key,
    )
    if champion is not None:
        row.details = {**row.details, DETAIL_SERVICE_ATTACHED_EVIDENCE: [champion[1]]}
    return insert_record(db, row)


def move_ref(
    db: Session,
    *,
    workspace_id: UUID,
    project_id: UUID,
    actor: DecisionActor,
    moves: Iterable[RefMove],
    rationale: str,
    evidence_refs: Iterable[Any] | None,
    facts: dict[str, Any] | None = None,
    decision_type: str | None = None,
    proposal_id: UUID | None = None,
    idempotency_key: str | None = None,
) -> RefMoveResult:
    """Move refs under one accepted decision record, atomically (ADR 0006 §2).

    The record is inserted first, then each ref is UPDATEd ``WHERE version =
    expected`` (or INSERTed when ``expected_version`` is None and the kind is
    missing). A 0-row update raises ``RefVersionConflictError`` and the savepoint
    rolls back the record too. With ``proposal_id`` the accepted record
    supersedes that open proposal, whose moves must match. The caller commits.
    """

    # Human only: rules never move refs (bootstrap has its own path), agents propose.
    authorize_writer(db, actor, workspace_id=workspace_id, allow_rule=False)
    load_project(db, workspace_id=workspace_id, project_id=project_id)
    items = _normalized_moves(moves)
    kind = _decision_type(items, decision_type)
    key = scoped_idempotency_key(actor, idempotency_key)
    proposal = (get_record(db, workspace_id=workspace_id, project_id=project_id, record_id=proposal_id)
                if proposal_id is not None else None)
    marked = list((proposal.details or {}).get(DETAIL_SERVICE_ATTACHED_EVIDENCE) or []) if proposal else []
    request = _replay_request(items, rationale, evidence_refs)
    if marked:  # the stored record also carries the service-attached ref
        request["evidence_refs_without_holdout"] = [
            ref for ref in request.pop("evidence_refs") if ref.get("scope") != FINAL_HOLDOUT_SCOPE]
    replayed = replay_record(
        db, workspace_id=workspace_id, project_id=project_id, idempotency_key=key,
        decision_type=kind, state=STATE_ACCEPTED, supersedes_id=proposal_id, request=request,
    )
    if replayed is not None:
        refs = current_refs(db, workspace_id=workspace_id, project_id=project_id)
        return RefMoveResult(record=replayed, refs=list(refs.values()), replayed=True)
    champion = None
    if marked:
        # Champion evidence the service attached: re-checked now (it must still be the
        # promoted model's own final evaluation) and carried for the human.
        proposed_moves = [RefMove(m["ref_kind"], UUID(str(m["to"]["id"])), None)
                          for m in (proposal.details or {}).get("ref_moves") or [] if isinstance(m, dict)]
        champion = _champion_evidence(db, workspace_id=workspace_id, project_id=project_id, moves=proposed_moves)
        if champion is None or [champion[1]["evaluation_id"]] != [m.get("evaluation_id") for m in marked]:
            raise InvalidDecisionRecordError(CHAMPION_EVIDENCE_UNAVAILABLE, "the promoted model is not eligible")
    items, text, evidence, current, targets = _prepare(
        db, workspace_id=workspace_id, project_id=project_id, moves=moves,
        rationale=rationale, evidence_refs=evidence_refs, lock=True, attached=champion and champion[0],
    )
    check_ref_versions(items, current)
    if proposal is not None:
        require_open_proposal(db, proposal)
        proposed = {
            (move.get("ref_kind"), (move.get("to") or {}).get("id"))
            for move in (proposal.details or {}).get("ref_moves") or []
            if isinstance(move, dict)
        }
        if proposal.decision_type != kind or proposed != {(m.ref_kind, str(m.target_id)) for m in items}:
            raise InvalidDecisionRecordError(
                "proposal_mismatch", "the moves differ from the proposal being accepted"
            )
    row = _ref_move_record(
        workspace_id=workspace_id, project_id=project_id, actor=actor, decision_type=kind,
        state=STATE_ACCEPTED, moves=items, targets=targets, current=current, rationale=text,
        evidence=evidence, facts=facts, supersedes_id=proposal_id, key=key,
    )
    if champion is not None:
        row.details = {**row.details, DETAIL_SERVICE_ATTACHED_EVIDENCE: [{**champion[1], "rechecked_at_accept": True}]}
    try:
        with db.begin_nested():
            db.add(row)
            db.flush()
            for move in items:
                column = REF_TARGET_COLUMNS[move.ref_kind]
                existing = current.get(move.ref_kind)
                if existing is None:
                    db.add(
                        ProjectRef(
                            workspace_id=workspace_id,
                            project_id=project_id,
                            ref_kind=move.ref_kind,
                            version=1,
                            decision_record_id=row.id,
                            **{column: move.target_id},
                        )
                    )
                    db.flush()
                    continue
                moved = db.execute(
                    update(ProjectRef)
                    .where(
                        ProjectRef.id == existing.id,
                        ProjectRef.workspace_id == workspace_id,
                        ProjectRef.version == move.expected_version,
                    )
                    .values(
                        {
                            column: move.target_id,
                            "version": ProjectRef.version + 1,
                            "moved_at": func.now(),
                            "decision_record_id": row.id,
                        }
                    )
                    .execution_options(synchronize_session=False)
                )
                if moved.rowcount != 1:
                    raise _version_conflict(move, existing)
    except IntegrityError as exc:
        if unique_violation(exc) == _REF_KIND_UNIQUE:
            raise RefVersionConflictError(
                "ref_created_concurrently", "the ref kind was created concurrently",
                ref_kind=move.ref_kind, current_version=1,
            ) from exc
        raise_for_record_race(exc)
        raise
    for ref in current.values():
        db.expire(ref)
    refs = current_refs(db, workspace_id=workspace_id, project_id=project_id)
    return RefMoveResult(record=row, refs=[refs[kind] for kind in REF_KINDS if kind in refs])
