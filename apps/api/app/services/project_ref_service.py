"""Project refs: the only mutable graph pointers (ADR 0006 §2).

P2.2-B ships the bootstrap rule only (``refs.bootstrap.v1``): the first run in
a project that reaches a locked ModelVersion *with* a project source dataset and
a SplitPlan initializes the refs from its own lineage with one accepted
``ref_initialized`` record, in the same transaction. A run that cannot satisfy
``dataset``, ``split_plan`` and ``champion_model`` defers bootstrap to a later
run. ``problem_spec`` / ``feature_recipe`` may still be unsatisfiable; they are
listed in ``details.skipped_refs`` and P2.5-A ``move_ref`` may insert them later
(``from: null``) under an accepted record (ADR 0006 Revision 2). Later runs
never move refs automatically.

P2.5-A ``move_ref`` is the only other writer: one transaction (savepoint) that
INSERTs the accepted ``ref_moved``/``champion_promoted`` record first, then a
versioned UPDATE (or, for a missing kind, an INSERT) of every moved ref; any
0-row update raises ``RefVersionConflictError`` and rolls the record back, so
no accepted move record exists without its move. ``propose_ref_move`` records
a proposal (agents and humans) that nothing applies until a human ``move_ref``
with ``proposal_id``.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Iterable, Sequence
from uuid import UUID

from sqlalchemy import func, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.db.models import (
    Dataset,
    Experiment,
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
    DECISION_CHAMPION_PROMOTED,
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
    replay_record,
    require_open_proposal,
    rule_record,
    scoped_idempotency_key,
    unique_violation,
    validate_evidence_refs,
)
from app.services.split_plan_service import is_unique_race

logger = logging.getLogger(__name__)

# Bootstrap waits for a run whose lineage satisfies these kinds.
BOOTSTRAP_REQUIRED_REF_KINDS = ("dataset", "split_plan", "champion_model")
# A concurrent bootstrap of the same project collides on one of these.
BOOTSTRAP_RACE_CONSTRAINTS = frozenset(
    {"uq_pdr_workspace_idempotency_key", "uq_project_refs_project_ref_kind"}
)


def project_has_refs(db: Session, *, workspace_id: UUID, project_id: UUID) -> bool:
    return (
        db.scalar(
            select(ProjectRef.id)
            .where(ProjectRef.workspace_id == workspace_id, ProjectRef.project_id == project_id)
            .limit(1)
        )
        is not None
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
    """``refs.bootstrap.v1``: initialize refs once per project; never moves existing refs.

    Called after the run's evidence lock, before its commit. Returns the
    ``ref_initialized`` record, or None when the project already has refs, the
    run has no project, or the champion rule cannot be met (no final holdout).
    """

    project_id = experiment.project_id
    if project_id is None or model_version.project_id != project_id:
        return None
    if experiment.scientific_evidence_locked_at is None:
        return None
    if model_version.pipeline_run_id != experiment.id or model_version.workspace_id != experiment.workspace_id:
        return None
    if project_has_refs(db, workspace_id=experiment.workspace_id, project_id=project_id):
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
    selection_metric = None
    if isinstance(experiment.result, dict):
        selection_metric = (experiment.result.get("selection") or {}).get("selection_metric")
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
) -> tuple[list[RefMove], str, list[dict[str, str]], dict[str, ProjectRef], dict[str, Any]]:
    items = _normalized_moves(moves)
    text = clean_rationale(rationale)
    evidence = validate_evidence_refs(
        db, workspace_id=workspace_id, project_id=project_id, refs=evidence_refs
    )
    if not evidence:
        raise InvalidDecisionRecordError("evidence_required", "a ref move cites at least one evidence ref")
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
    load_project(db, workspace_id=workspace_id, project_id=project_id)
    items = _normalized_moves(moves)
    kind = _decision_type(items, decision_type)
    key = scoped_idempotency_key(actor, idempotency_key)
    replayed = replay_record(
        db, workspace_id=workspace_id, project_id=project_id, idempotency_key=key,
        decision_type=kind, state=STATE_PROPOSED, supersedes_id=None,
        request=_replay_request(items, rationale, evidence_refs),
    )
    if replayed is not None:
        return replayed
    items, text, evidence, current, targets = _prepare(
        db, workspace_id=workspace_id, project_id=project_id, moves=moves,
        rationale=rationale, evidence_refs=evidence_refs, lock=False,
    )
    row = _ref_move_record(
        workspace_id=workspace_id, project_id=project_id, actor=actor, decision_type=kind,
        state=STATE_PROPOSED, moves=items, targets=targets, current=current, rationale=text,
        evidence=evidence, facts=facts, supersedes_id=None, key=key,
    )
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
    replayed = replay_record(
        db, workspace_id=workspace_id, project_id=project_id, idempotency_key=key,
        decision_type=kind, state=STATE_ACCEPTED, supersedes_id=proposal_id,
        request=_replay_request(items, rationale, evidence_refs),
    )
    if replayed is not None:
        refs = current_refs(db, workspace_id=workspace_id, project_id=project_id)
        return RefMoveResult(record=replayed, refs=list(refs.values()), replayed=True)
    items, text, evidence, current, targets = _prepare(
        db, workspace_id=workspace_id, project_id=project_id, moves=moves,
        rationale=rationale, evidence_refs=evidence_refs, lock=True,
    )
    check_ref_versions(items, current)
    if proposal_id is not None:
        proposal = get_record(db, workspace_id=workspace_id, project_id=project_id, record_id=proposal_id)
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
                "ref_created_concurrently", "the ref kind was created concurrently", current_version=1
            ) from exc
        raise_for_record_race(exc)
        raise
    for ref in current.values():
        db.expire(ref)
    refs = current_refs(db, workspace_id=workspace_id, project_id=project_id)
    return RefMoveResult(record=row, refs=[refs[kind] for kind in REF_KINDS if kind in refs])
