"""SplitPlan lookup-or-create, assignment artifact, reuse and run checks (ADR 0006 §3).

Single owner of ``split_plans`` writes. A plan fixes the final holdout and the
outer CV folds of one source DatasetVersion; the row map is a canonical CSV in
object storage (``split_assignment``) and PostgreSQL keeps counts and digests.

Reuse is partition-by-map. At the holdout lock, a stored plan whose holdout
identity matches (same source dataset, target, task, holdout plan, planner
version, structural cleaning) is found, its map is downloaded and verified
against ``assignment_digest`` and the frame is partitioned by it; nothing is
re-split. After the train-only validation plan, the full ``plan_digest`` selects
the plan; a reused plan supplies the outer folds too and its stored
HoldoutPlan/ValidationPlan (reuse copy rule). A frame whose rows differ from the
map, or tampered bytes, fail closed with ``split_assignment_mismatch``.
"""

from __future__ import annotations

import hashlib
import json
import logging
from collections.abc import Callable, Collection
from dataclasses import dataclass
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.db.models import Artifact, Dataset, Experiment, PipelineScientificPlan, SplitPlan
from app.domain.data_plane import SPLIT_ASSIGNMENT_TYPE
from app.domain.decision_records import (
    DECISION_SPLIT_PLAN_CREATED,
    SPLIT_PLAN_CREATED_SCHEMA_VERSION,
    holdout_planner_rule,
    split_plan_created_idempotency_key,
)
from app.domain.errors import SplitPlanLineageError
from app.domain.state_graph import SPLIT_PLAN_EVIDENCE_MAX_BYTES, SPLIT_PLAN_HOLDOUT_STRATEGIES
from app.engine.modeling.holdout_planner import HoldoutPlan
from app.engine.modeling.validation_planner import TIME_SERIES_SPLIT, ValidationPlan
from app.engine.validation.split_assignment import (
    SplitAssignment,
    SplitAssignmentMismatchError,
    assignment_digest,
    parse_split_assignment,
)
from app.services.artifact_service import record_artifact
from app.services.decision_record_service import evidence_ref, rule_record
from app.services.scientific_lineage_service import holdout_plan_digest
from app.storage import factory as storage_factory
from app.storage.base import ObjectStorage
from app.storage.exceptions import ObjectNotFoundError
from app.storage.factory import storage_for_artifact

logger = logging.getLogger(__name__)

SPLIT_PLAN_IDENTITY_VERSION = 1
_CREATE_ATTEMPTS = 3
_UNIQUE_VIOLATION = "23505"
# Only these unique constraints mean "a concurrent run created the plan first".
SPLIT_PLAN_RACE_CONSTRAINTS = frozenset(
    {
        "uq_split_plans_project_plan_digest",
        "uq_split_plans_project_version",
        "uq_pdr_workspace_idempotency_key",
    }
)
_HOLDOUT_IDENTITY_KEYS = (
    "identity_version",
    "source_dataset_id",
    "source_dataset_content_digest",
    "target_column",
    "task_type",
    "holdout",
    "holdout_planner_version",
    "structural_cleaning_digest",
)


class SplitPlanUnavailable(Exception):
    """The run's lineage cannot carry a SplitPlan (no project-scoped source dataset)."""


@dataclass(frozen=True)
class StoredHoldout:
    """A verified stored plan whose holdout this run must use (partition-by-map)."""

    split_plan: SplitPlan
    assignment: SplitAssignment
    holdout_plan: HoldoutPlan


@dataclass(frozen=True)
class ResolvedSplitPlan:
    split_plan: SplitPlan
    assignment: SplitAssignment
    reused: bool
    holdout_plan: HoldoutPlan
    validation_plan: ValidationPlan


def is_unique_race(exc: IntegrityError, constraints: Collection[str]) -> bool:
    """True only for SQLSTATE 23505 on one of ``constraints``; anything else is a bug."""

    orig = getattr(exc, "orig", None)
    if getattr(orig, "sqlstate", None) != _UNIQUE_VIOLATION:
        return False
    diag = getattr(orig, "diag", None)
    return getattr(diag, "constraint_name", None) in constraints


def _canonical_json(payload: Any) -> bytes:
    return json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str).encode("utf-8")


def _json_ready(payload: Any) -> Any:
    return json.loads(json.dumps(payload, default=str))


def structural_cleaning_digest(cleaning_log: dict[str, Any]) -> str:
    """Digest of the pre-split row-removal rules (the only steps changing the row set)."""

    removals = [
        {"step": str(item.get("step")), "rows_removed": int(item.get("rows_removed") or 0)}
        for item in list((cleaning_log or {}).get("transformations") or [])
        if isinstance(item, dict) and "rows_removed" in item
    ]
    payload = {
        "row_removal_rules": removals,
        "rows_in": (cleaning_log or {}).get("rows_in"),
        "rows_out": (cleaning_log or {}).get("rows_out"),
    }
    return hashlib.sha256(_canonical_json(payload)).hexdigest()


def holdout_identity(
    *,
    source_dataset: Dataset,
    target_column: str,
    task_type: str,
    holdout_plan: HoldoutPlan,
    cleaning_log: dict[str, Any],
) -> dict[str, Any]:
    """The part of the plan identity known at the holdout lock."""

    return {
        "identity_version": SPLIT_PLAN_IDENTITY_VERSION,
        # Each upload is its own Dataset row; a plan partitions exactly one
        # (``split_plans.dataset_id == experiments.source_dataset_id``).
        "source_dataset_id": str(source_dataset.id),
        "source_dataset_content_digest": source_dataset.content_digest,
        "target_column": target_column,
        "task_type": task_type,
        "holdout": {
            "strategy": holdout_plan.strategy,
            "test_size": float(holdout_plan.test_size),
            "seed": int(holdout_plan.random_state),
            "stratified": bool(holdout_plan.stratified),
            "group_column": holdout_plan.group_column,
            "time_column": holdout_plan.time_column,
        },
        "holdout_planner_version": holdout_plan.plan_version,
        "structural_cleaning_digest": structural_cleaning_digest(cleaning_log),
    }


def plan_identity(
    *,
    source_dataset: Dataset,
    target_column: str,
    task_type: str,
    holdout_plan: HoldoutPlan,
    validation_plan: ValidationPlan,
    cleaning_log: dict[str, Any],
) -> dict[str, Any]:
    """ADR 0006 §3 plan identity (Revision 2: plus the source dataset id)."""

    return {
        **holdout_identity(
            source_dataset=source_dataset,
            target_column=target_column,
            task_type=task_type,
            holdout_plan=holdout_plan,
            cleaning_log=cleaning_log,
        ),
        "validation": {
            "strategy": validation_plan.strategy,
            "requested_folds": int(validation_plan.requested_folds),
            "shuffle": bool(validation_plan.shuffle),
            "seed": int(validation_plan.random_state),
            "group_column": validation_plan.group_column,
            "time_column": validation_plan.time_column,
        },
        "validation_planner_version": validation_plan.version,
    }


def plan_digest(identity: dict[str, Any]) -> str:
    return hashlib.sha256(_canonical_json(identity)).hexdigest()


def assignment_object_key(workspace_id: UUID, project_id: UUID, split_plan_id: UUID) -> str:
    return f"ws/{workspace_id}/projects/{project_id}/split_plans/{split_plan_id}/assignment.csv"


def require_partition_source(
    db: Session, *, workspace_id: UUID, project_id: UUID | None, source_dataset_id: UUID | None
) -> Dataset:
    """The published source dataset a plan may partition, or ``SplitPlanUnavailable``."""

    if project_id is None:
        raise SplitPlanUnavailable("run has no project")
    if source_dataset_id is None:
        raise SplitPlanUnavailable("run has no source dataset")
    dataset = db.get(Dataset, source_dataset_id)
    if dataset is None or dataset.workspace_id != workspace_id:
        raise SplitPlanUnavailable("source dataset not found in this workspace")
    if dataset.project_id != project_id:
        raise SplitPlanUnavailable("source dataset belongs to another project")
    if not dataset.content_digest:
        raise SplitPlanUnavailable("source dataset has no content digest")
    return dataset


def _find(db: Session, *, workspace_id: UUID, project_id: UUID, digest: str) -> SplitPlan | None:
    return db.scalar(
        select(SplitPlan).where(
            SplitPlan.workspace_id == workspace_id,
            SplitPlan.project_id == project_id,
            SplitPlan.plan_digest == digest,
        )
    )


def load_assignment(
    db: Session, split_plan: SplitPlan, *, storage: ObjectStorage | None = None
) -> SplitAssignment:
    """Download the stored map and prove its bytes are the plan's ``assignment_digest``."""

    artifact = db.get(Artifact, split_plan.assignment_artifact_id)
    if (
        artifact is None
        or artifact.workspace_id != split_plan.workspace_id
        or artifact.artifact_type != SPLIT_ASSIGNMENT_TYPE
    ):
        raise SplitAssignmentMismatchError("stored assignment artifact is missing")
    if artifact.content_digest != split_plan.assignment_digest:
        raise SplitAssignmentMismatchError("artifact digest differs from the split plan")
    try:
        payload = storage_for_artifact(artifact, storage=storage).get(artifact.object_key)
    except ObjectNotFoundError as exc:
        raise SplitAssignmentMismatchError("stored assignment object is missing") from exc
    if assignment_digest(payload) != split_plan.assignment_digest:
        raise SplitAssignmentMismatchError("stored assignment bytes do not match their digest")
    assignment = parse_split_assignment(payload)
    if (
        len(assignment.train_folds) != split_plan.train_row_count
        or len(assignment.holdout_rows) != split_plan.holdout_row_count
    ):
        raise SplitAssignmentMismatchError("stored assignment counts differ from the split plan")
    return assignment


def find_stored_holdout(
    db: Session,
    *,
    source_dataset: Dataset,
    target_column: str,
    task_type: str,
    holdout_plan: HoldoutPlan,
    cleaning_log: dict[str, Any],
    storage: ObjectStorage | None = None,
) -> StoredHoldout | None:
    """At the holdout lock: the oldest plan with this holdout identity, verified."""

    wanted = holdout_identity(
        source_dataset=source_dataset,
        target_column=target_column,
        task_type=task_type,
        holdout_plan=holdout_plan,
        cleaning_log=cleaning_log,
    )
    candidates = db.scalars(
        select(SplitPlan)
        .where(
            SplitPlan.workspace_id == source_dataset.workspace_id,
            SplitPlan.project_id == source_dataset.project_id,
            SplitPlan.dataset_id == source_dataset.id,
            SplitPlan.target_column == target_column,
            SplitPlan.task_type == task_type,
            SplitPlan.holdout_strategy == holdout_plan.strategy,
            SplitPlan.holdout_planner_version == holdout_plan.plan_version,
        )
        .order_by(SplitPlan.version)
    )
    for plan in candidates:
        stored_identity = dict((plan.plan_evidence or {}).get("identity") or {})
        if {key: stored_identity.get(key) for key in _HOLDOUT_IDENTITY_KEYS} != _json_ready(wanted):
            continue
        evidence = dict(plan.plan_evidence or {})
        return StoredHoldout(
            split_plan=plan,
            assignment=load_assignment(db, plan, storage=storage),
            holdout_plan=HoldoutPlan.from_dict(evidence.get("holdout_plan")),
        )
    return None


def branch_stored_holdout(
    db: Session,
    split_plan: SplitPlan,
    *,
    source_dataset: Dataset,
    target_column: str,
    task_type: str,
    holdout_plan: HoldoutPlan,
    cleaning_log: dict[str, Any],
    storage: ObjectStorage | None = None,
) -> StoredHoldout:
    """A branch's holdout is its parent's plan, never a lookup (ADR 0006 §3).

    The run's own holdout identity must equal the stored one; a different
    dataset, target, task, cleaning or planner version fails closed.
    """

    if split_plan.dataset_id != source_dataset.id or split_plan.project_id != source_dataset.project_id:
        raise SplitPlanLineageError("branch split plan partitions another source dataset")
    wanted = _json_ready(
        holdout_identity(
            source_dataset=source_dataset,
            target_column=target_column,
            task_type=task_type,
            holdout_plan=holdout_plan,
            cleaning_log=cleaning_log,
        )
    )
    stored_identity = dict((split_plan.plan_evidence or {}).get("identity") or {})
    if {key: stored_identity.get(key) for key in _HOLDOUT_IDENTITY_KEYS} != wanted:
        raise SplitPlanLineageError(
            "the branch's holdout identity differs from its parent's split plan; start a new root",
            code="branch_split_plan_mismatch",
        )
    evidence = dict(split_plan.plan_evidence or {})
    return StoredHoldout(
        split_plan=split_plan,
        assignment=load_assignment(db, split_plan, storage=storage),
        holdout_plan=HoldoutPlan.from_dict(evidence.get("holdout_plan")),
    )


def _require_partition(
    assignment: SplitAssignment, holdout_rows: Collection[int], train_rows: Collection[int]
) -> None:
    if set(assignment.holdout_rows) != {int(r) for r in holdout_rows} or {
        int(r) for r in assignment.train_folds
    } != {int(r) for r in train_rows}:
        raise SplitAssignmentMismatchError("run holdout/train rows differ from the split plan")


def _split_plan_created_record(plan: SplitPlan, holdout_plan: HoldoutPlan):
    return rule_record(
        workspace_id=plan.workspace_id,
        project_id=plan.project_id,
        decision_type=DECISION_SPLIT_PLAN_CREATED,
        subject_kind="split_plan",
        subject_id=plan.id,
        subject_digest=plan.plan_digest,
        actor_rule=holdout_planner_rule(plan.holdout_planner_version),
        rationale=holdout_plan.reason or f"{plan.holdout_strategy} holdout",
        facts={
            "version": plan.version,
            "row_count": plan.row_count,
            "train_row_count": plan.train_row_count,
            "holdout_row_count": plan.holdout_row_count,
            "holdout_strategy": plan.holdout_strategy,
            "holdout_test_size": plan.holdout_test_size,
            "validation_strategy": plan.validation_strategy,
            "validation_folds": plan.validation_folds,
        },
        evidence_refs=[evidence_ref("dataset_version", plan.dataset_id)],
        details={
            "plan_digest": plan.plan_digest,
            "assignment_digest": plan.assignment_digest,
            "holdout_plan_digest": plan.holdout_plan_digest,
        },
        schema_version=SPLIT_PLAN_CREATED_SCHEMA_VERSION,
        idempotency_key=split_plan_created_idempotency_key(plan.id),
    )


def _create(
    db: Session,
    *,
    source_dataset: Dataset,
    identity: dict[str, Any],
    target_column: str,
    task_type: str,
    holdout_plan: HoldoutPlan,
    validation_plan: ValidationPlan,
    assignment: SplitAssignment,
    created_by: UUID | None,
    storage: ObjectStorage | None,
) -> SplitPlan | None:
    """Upload the map, insert plan + record in a savepoint; None if a racer won."""

    workspace_id, project_id = source_dataset.workspace_id, source_dataset.project_id
    if holdout_plan.strategy not in SPLIT_PLAN_HOLDOUT_STRATEGIES:
        raise SplitPlanUnavailable(f"holdout strategy {holdout_plan.strategy!r} is not persistable")
    digest = plan_digest(identity)
    plan_evidence = _json_ready(
        {
            "holdout_plan": holdout_plan.to_dict(),
            "validation_plan": validation_plan.to_dict(),
            "identity": identity,
        }
    )
    if len(_canonical_json(plan_evidence)) > SPLIT_PLAN_EVIDENCE_MAX_BYTES:
        raise SplitPlanUnavailable("plan evidence exceeds the split-plan bound")
    split_plan_id = uuid4()
    payload = assignment.to_csv_bytes()
    backend = storage or storage_factory.get_object_storage()
    key = assignment_object_key(workspace_id, project_id, split_plan_id)
    put = backend.put(key, payload, content_type="text/csv")
    if put.content_digest != assignment_digest(payload):
        backend.delete(key)
        raise SplitAssignmentMismatchError("object storage changed the assignment bytes")
    try:
        with db.begin_nested():
            artifact = record_artifact(
                db,
                artifact_id=uuid4(),
                workspace_id=workspace_id,
                project_id=project_id,
                artifact_type=SPLIT_ASSIGNMENT_TYPE,
                put=put,
                mime_type="text/csv",
                created_by=created_by,
                extra_metadata={"split_plan_id": str(split_plan_id), "plan_digest": digest},
            )
            version = int(
                db.scalar(
                    select(func.coalesce(func.max(SplitPlan.version), 0)).where(
                        SplitPlan.workspace_id == workspace_id,
                        SplitPlan.project_id == project_id,
                    )
                )
                or 0
            ) + 1
            plan = SplitPlan(
                id=split_plan_id,
                workspace_id=workspace_id,
                project_id=project_id,
                dataset_id=source_dataset.id,
                version=version,
                task_type=task_type,
                target_column=target_column,
                holdout_strategy=holdout_plan.strategy,
                holdout_test_size=float(holdout_plan.test_size),
                holdout_seed=int(holdout_plan.random_state),
                stratified=bool(holdout_plan.stratified),
                group_column=holdout_plan.group_column,
                time_column=holdout_plan.time_column,
                validation_strategy=validation_plan.strategy,
                validation_folds=int(validation_plan.actual_folds or 0),
                validation_seed=int(validation_plan.random_state),
                row_count=assignment.row_count,
                train_row_count=len(assignment.train_folds),
                holdout_row_count=len(assignment.holdout_rows),
                plan_digest=digest,
                assignment_artifact_id=artifact.id,
                assignment_digest=artifact.content_digest,
                holdout_plan_digest=holdout_plan_digest(holdout_plan),
                holdout_planner_version=holdout_plan.plan_version,
                validation_planner_version=validation_plan.version,
                plan_evidence=plan_evidence,
                reason=(holdout_plan.reason or holdout_plan.strategy)[:2048],
                created_by=created_by,
            )
            db.add(plan)
            db.flush()
            db.add(_split_plan_created_record(plan, holdout_plan))
            db.flush()
    except IntegrityError as exc:
        # Compensating step: the bytes were uploaded but no row references them.
        backend.delete(key)
        if is_unique_race(exc, SPLIT_PLAN_RACE_CONSTRAINTS):
            logger.info("split plan %s lost a creation race; re-reading", digest[:12])
            return None
        raise
    except Exception:
        backend.delete(key)
        raise
    return plan


def resolve_split_plan(
    db: Session,
    *,
    source_dataset: Dataset,
    target_column: str,
    task_type: str,
    holdout_plan: HoldoutPlan,
    validation_plan: ValidationPlan,
    cleaning_log: dict[str, Any],
    holdout_rows: Collection[int],
    train_rows: Collection[int],
    derive_assignment: Callable[[], SplitAssignment],
    stored: StoredHoldout | None = None,
    created_by: UUID | None = None,
    storage: ObjectStorage | None = None,
    required_plan: SplitPlan | None = None,
) -> ResolvedSplitPlan:
    """Lookup by ``(project, plan_digest)``; reuse (rows verified) or create. Caller commits.

    ``derive_assignment`` (outer folds on the locked train partition) is only
    called when a new plan is created; a reused plan supplies its stored folds.
    ``required_plan`` (a branch's parent plan) must be the one selected; a
    branch never creates a plan.
    """

    identity = plan_identity(
        source_dataset=source_dataset,
        target_column=target_column,
        task_type=task_type,
        holdout_plan=holdout_plan,
        validation_plan=validation_plan,
        cleaning_log=cleaning_log,
    )
    digest = plan_digest(identity)
    if required_plan is not None and required_plan.plan_digest != digest:
        raise SplitPlanLineageError(
            "the branch's validation plan differs from its parent's split plan; start a new root",
            code="branch_split_plan_mismatch",
        )
    for _attempt in range(_CREATE_ATTEMPTS):
        existing = _find(
            db,
            workspace_id=source_dataset.workspace_id,
            project_id=source_dataset.project_id,
            digest=digest,
        )
        if required_plan is not None and (existing is None or existing.id != required_plan.id):
            raise SplitPlanLineageError(
                "the branch did not resolve to its parent's split plan", code="branch_split_plan_mismatch"
            )
        if existing is not None:
            if existing.dataset_id != source_dataset.id:
                raise SplitPlanLineageError("split plan partitions another source dataset")
            assignment = (
                stored.assignment
                if stored is not None and stored.split_plan.id == existing.id
                else load_assignment(db, existing, storage=storage)
            )
            _require_partition(assignment, holdout_rows, train_rows)
            evidence = dict(existing.plan_evidence or {})
            return ResolvedSplitPlan(
                split_plan=existing,
                assignment=assignment,
                reused=True,
                holdout_plan=HoldoutPlan.from_dict(evidence.get("holdout_plan")),
                validation_plan=ValidationPlan.from_dict(evidence.get("validation_plan")),
            )
        assignment = derive_assignment()
        _require_partition(assignment, holdout_rows, train_rows)
        created = _create(
            db,
            source_dataset=source_dataset,
            identity=identity,
            target_column=target_column,
            task_type=task_type,
            holdout_plan=holdout_plan,
            validation_plan=validation_plan,
            assignment=assignment,
            created_by=created_by,
            storage=storage,
        )
        if created is not None:
            return ResolvedSplitPlan(
                split_plan=created,
                assignment=assignment,
                reused=False,
                holdout_plan=holdout_plan,
                validation_plan=validation_plan,
            )
    raise RuntimeError("split plan could not be created after concurrent conflicts")


def assert_plan_partitions_run_source(split_plan: SplitPlan, experiment: Experiment) -> None:
    """DB-review rule: a run's plan partitions exactly the run's source dataset."""

    if split_plan.workspace_id != experiment.workspace_id:
        raise SplitPlanLineageError("split plan belongs to another workspace", code="split_plan_not_found")
    if split_plan.project_id != experiment.project_id:
        raise SplitPlanLineageError(
            "split plan belongs to another project", code="split_plan_project_mismatch"
        )
    if experiment.source_dataset_id is None or split_plan.dataset_id != experiment.source_dataset_id:
        raise SplitPlanLineageError(
            f"split plan {split_plan.id} partitions dataset {split_plan.dataset_id}, "
            f"run source dataset is {experiment.source_dataset_id}"
        )


def _expected_folds(
    plan: SplitPlan, assignment: SplitAssignment
) -> dict[int, tuple[set[int], set[int]]]:
    """Fold k -> (train rows, validation rows) the plan prescribes (cf. ``folds_from_assignment``)."""

    expanding = plan.validation_strategy == TIME_SERIES_SPLIT
    folds: dict[int, tuple[set[int], set[int]]] = {}
    for number in range(1, plan.validation_folds + 1):
        validation = {row for row, fold in assignment.train_folds.items() if fold == number}
        train = {
            row
            for row, fold in assignment.train_folds.items()
            if (fold < number if expanding else fold != number)
        }
        folds[number] = (train, validation)
    return folds


def verify_run_against_plan(
    db: Session,
    experiment: Experiment,
    assignment: SplitAssignment,
    result: dict[str, Any],
) -> SplitPlan:
    """Before the evidence lock: the run used exactly the plan's holdout and folds."""

    if experiment.split_plan_id is None:
        raise SplitPlanLineageError("run has no split plan", code="split_plan_missing")
    plan = db.get(SplitPlan, experiment.split_plan_id)
    if plan is None:
        raise SplitPlanLineageError("split plan not found", code="split_plan_not_found")
    assert_plan_partitions_run_source(plan, experiment)
    if assignment.digest() != plan.assignment_digest:
        raise SplitAssignmentMismatchError("run assignment differs from the split plan")
    scientific = db.scalar(
        select(PipelineScientificPlan).where(PipelineScientificPlan.pipeline_run_id == experiment.id)
    )
    if scientific is None:
        raise SplitAssignmentMismatchError("run has no locked scientific plan to compare")
    if scientific.holdout_plan_digest != plan.holdout_plan_digest:
        raise SplitAssignmentMismatchError("run holdout plan differs from the split plan")
    split = result.get("split") if isinstance(result.get("split"), dict) else {}
    train_rows = {int(row) for row in split.get("train_source_rows") or []}
    test_rows = {int(row) for row in split.get("test_source_rows") or []}
    if (
        test_rows != set(assignment.holdout_rows)
        or train_rows != {int(row) for row in assignment.train_folds}
        or split.get("n_train") != plan.train_row_count
        or split.get("n_test") != plan.holdout_row_count
    ):
        raise SplitAssignmentMismatchError("run holdout rows differ from the split plan")
    expected = _expected_folds(plan, assignment)
    for candidate in result.get("candidates") or []:
        if not isinstance(candidate, dict) or candidate.get("status") != "trained":
            continue
        observed = {
            int(fold.get("fold_number") or 0): (
                {int(row) for row in fold.get("train_provenance") or []},
                {int(row) for row in fold.get("validation_provenance") or []},
            )
            for fold in candidate.get("folds") or []
        }
        if observed != expected:
            raise SplitAssignmentMismatchError(
                f"candidate {candidate.get('candidate_id')} outer folds differ from the split plan"
            )
    return plan
