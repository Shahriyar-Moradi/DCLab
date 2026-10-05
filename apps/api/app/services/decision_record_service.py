"""The decision record service: append-only project memory (ADR 0006 §5).

Rule-actor builders (P2.2-B: ``winner_locked``, ``split_plan_created``,
``ref_initialized``) and the P2.5-A state machine over a chain:

    proposed --accept--> accepted   (new row, supersedes_id = proposal)
    proposed --reject--> rejected   (new row, supersedes_id = proposal)
    accepted --correct-> accepted'  (``supersede``; the old row becomes superseded)
    rejected, superseded: terminal

Rows are never updated (DB trigger); ``superseded`` is derived from a successor
row and DB-enforced linear chains (``uq_pdr_supersedes_id``) make double
acceptance impossible. Ref moves are written by ``project_ref_service.move_ref``
together with their accepted record. Actors are typed (``DecisionActor``):
humans need ML-write access, agents may only propose (their rationale is stored
``rationale_untrusted``), rules are engine code. Idempotency keys make every
write replay-safe.
"""

from __future__ import annotations

import json
import math
import re
from collections import defaultdict
from datetime import UTC, datetime
from typing import Any, Callable, Iterable
from uuid import UUID

from sqlalchemy import and_, or_, select, tuple_
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, aliased

from app.db.models import (
    Dataset,
    EvaluationMetric,
    Experiment,
    ExperimentCandidate,
    FeatureSetVersion,
    ModelEvaluation,
    ModelSelectionDecision,
    ModelVersion,
    ProblemSpec,
    Project,
    ProjectDecisionRecord,
    SplitPlan,
    User,
)
from app.domain.decision_records import (
    ACTOR_AGENT,
    ACTOR_HUMAN,
    ACTOR_KINDS,
    ACTOR_RULE,
    CALLER_IDEMPOTENCY_KEY_MAX_CHARS,
    DECISION_PAGE_DEFAULT,
    DECISION_PAGE_MAX,
    DECISION_POLICY_VERSION,
    DECISION_RECORD_SCHEMA_VERSION,
    DECISION_TYPES,
    DECISION_WINNER_LOCKED,
    DETAIL_CARRIED_FROM_AGENT,
    DETAILS_READ_MAX_BYTES,
    EFFECTIVE_STATE_SUPERSEDED,
    EVIDENCE_METRIC_KINDS,
    EVIDENCE_METRIC_PATTERN,
    EVIDENCE_REF_KINDS,
    EVIDENCE_REFS_MAX,
    EVIDENCE_SCOPES,
    IDEMPOTENCY_KEY_MAX_CHARS,
    RATIONALE_MAX_CHARS,
    RATIONALE_READ_MAX_CHARS,
    REF_HISTORY_DECISION_TYPES,
    REF_MOVE_DECISION_TYPES,
    RESERVED_DECISION_TYPES,
    RESERVED_DETAIL_KEYS,
    RULE_ONLY_DECISION_TYPES,
    RULE_WINNER_LOCKED,
    RULE_WINNER_LOCKED_BACKFILL,
    STATE_ACCEPTED,
    STATE_PROPOSED,
    STATE_REJECTED,
    SERVICE_ONLY_DECISION_TYPES,
    SUBJECT_COLUMNS,
    SUBJECT_KINDS,
    UNTRUSTED_RATIONALE_LABEL,
    WINNER_LOCKED_SCHEMA_VERSION,
    DecisionActor,
    DecisionActorRead,
    DecisionRecordPage,
    DecisionRecordRead,
    DecisionSubjectRead,
    EvidenceRefRead,
    is_code_owned_rule,
    winner_locked_idempotency_key,
)
from app.domain.errors import (
    DecisionActorNotPermittedError,
    DecisionRecordNotFoundError,
    IdempotencyKeyConflictError,
    IdentityError,
    InvalidCursorError,
    InvalidDecisionCursorError,
    InvalidDecisionQueryError,
    InvalidDecisionRecordError,
    InvalidDecisionTransitionError,
    ProjectNotFoundError,
)
from app.domain.execution_requests import FORBIDDEN_REQUEST_PAYLOAD_KEYS
from app.domain.state_graph import STATE_GRAPH_JSON_MAX_BYTES, node_key
from app.services.audience_projection import BLOCKED_KEY_PARTS, SECRET_TEXT, public_diagnostic
from app.services.authorization_service import can_perform_ml_write, can_read_workspace
from app.services.cursor_codec import open_cursor, scope_digest, sign_cursor
from app.services.project_service import get_project
from app.services.service_token_service import verify_agent_binding

_DIGEST = re.compile(r"^[0-9a-f]{1,64}$")
CV_AGGREGATE_SCOPE = "cv_aggregate"
FINAL_HOLDOUT_SCOPE = "final_holdout"
_HOLDOUT_TEXT = re.compile(r"holdout|final_test", re.IGNORECASE)


def _names_holdout(value: Any) -> bool:
    if isinstance(value, dict):
        return any(_names_holdout(str(key)) or _names_holdout(item) for key, item in value.items())
    if isinstance(value, (list, tuple)):
        return any(_names_holdout(item) for item in value)
    return isinstance(value, str) and bool(_HOLDOUT_TEXT.search(value))


def refuse_agent_holdout(actor: DecisionActor, *values: Any) -> None:
    """Agents never cite the final holdout (ADR 0008 §2b; P6.10-A): an agent's evidence
    ref, fact or detail naming it is refused, never stripped. A champion proposal's
    final-evaluation ref is attached by the service (``project_ref_service``)."""

    if actor.kind == ACTOR_AGENT and any(_names_holdout(value) for value in values):
        raise InvalidDecisionRecordError(
            "holdout_not_allowed",
            "agents never cite the final holdout; a champion's final evaluation is attached by DCLab",
        )


def evidence_ref(kind: str, node_id: Any, *, metric: str | None = None, scope: str | None = None) -> dict[str, str]:
    ref = {"kind": kind, "id": str(node_id)}
    if metric:
        ref["metric"] = str(metric)
    if scope:
        ref["scope"] = scope
    return ref


def existing_record(db: Session, *, workspace_id: UUID, idempotency_key: str) -> ProjectDecisionRecord | None:
    return db.scalar(
        select(ProjectDecisionRecord).where(
            ProjectDecisionRecord.workspace_id == workspace_id,
            ProjectDecisionRecord.idempotency_key == idempotency_key,
        )
    )


def rule_record(
    *,
    workspace_id: UUID,
    project_id: UUID,
    decision_type: str,
    subject_kind: str,
    subject_id: UUID,
    actor_rule: str,
    rationale: str,
    schema_version: int,
    idempotency_key: str,
    facts: dict[str, Any] | None = None,
    evidence_refs: list[dict[str, Any]] | None = None,
    details: dict[str, Any] | None = None,
    subject_digest: str | None = None,
    event_at: datetime | None = None,
) -> ProjectDecisionRecord:
    """Build one accepted, rule-actor record (not added to the session)."""

    if decision_type not in DECISION_TYPES:
        raise ValueError(f"unknown decision_type {decision_type!r}")
    column = SUBJECT_COLUMNS.get(subject_kind)
    if column is None:
        raise ValueError(f"subject_kind {subject_kind!r} needs a typed subject")
    text = str(rationale or "").strip()[:RATIONALE_MAX_CHARS] or decision_type
    refs = list(evidence_refs or [])
    if len(refs) > EVIDENCE_REFS_MAX:
        raise ValueError("too many evidence refs")
    row = ProjectDecisionRecord(
        workspace_id=workspace_id,
        project_id=project_id,
        decision_type=decision_type,
        state=STATE_ACCEPTED,
        subject_kind=subject_kind,
        subject_digest=subject_digest if _DIGEST.match(subject_digest or "") else None,
        actor_kind=ACTOR_RULE,
        actor_rule=actor_rule,
        rationale=text,
        rationale_untrusted=False,
        facts=dict(facts or {}),
        evidence_refs=refs,
        details=dict(details or {}),
        schema_version=schema_version,
        policy_version=DECISION_POLICY_VERSION,
        idempotency_key=idempotency_key,
    )
    setattr(row, column, subject_id)
    if event_at is not None:
        row.event_at = event_at
    return row


def winner_locked_record(
    selection: ModelSelectionDecision, fingerprint: str | None, *, backfilled: bool
) -> ProjectDecisionRecord:
    """The ``winner_locked`` fact for one ModelSelection; same shape live and backfilled."""

    evidence = [
        evidence_ref(
            "candidate",
            selection.selected_candidate_id,
            metric=selection.selection_metric,
            scope=CV_AGGREGATE_SCOPE,
        )
    ]
    if selection.runner_up_candidate_id is not None:
        evidence.append(
            evidence_ref(
                "candidate",
                selection.runner_up_candidate_id,
                metric=selection.selection_metric,
                scope=CV_AGGREGATE_SCOPE,
            )
        )
    score = selection.selected_score
    finite = score is not None and math.isfinite(score)
    details: dict[str, object] = {"model_selection_decision_id": str(selection.id)}
    if backfilled:
        details["backfilled"] = True
    if not finite:
        details["selected_score_non_finite"] = True
    rationale = (selection.reason or "").strip() or (
        f"CV winner locked by selection policy {selection.selection_policy}"
    )
    return rule_record(
        workspace_id=selection.workspace_id,
        project_id=selection.project_id,
        decision_type=DECISION_WINNER_LOCKED,
        subject_kind="candidate",
        subject_id=selection.selected_candidate_id,
        subject_digest=fingerprint,
        actor_rule=RULE_WINNER_LOCKED_BACKFILL if backfilled else RULE_WINNER_LOCKED,
        rationale=rationale,
        facts={
            "selected_score": score if finite else None,
            "selection_metric": selection.selection_metric,
            "selection_policy": selection.selection_policy,
        },
        evidence_refs=evidence,
        details=details,
        schema_version=WINNER_LOCKED_SCHEMA_VERSION,
        idempotency_key=winner_locked_idempotency_key(selection.id),
        event_at=selection.locked_at,
    )


def record_winner_locked(
    db: Session, selection: ModelSelectionDecision, winner: ExperimentCandidate
) -> ProjectDecisionRecord | None:
    """Live path: one record in the selection's transaction (skips project-less runs)."""

    if selection.project_id is None or winner.project_id != selection.project_id:
        return None
    if selection.id is None:
        db.flush()
    key = winner_locked_idempotency_key(selection.id)
    found = existing_record(db, workspace_id=selection.workspace_id, idempotency_key=key)
    if found is not None:
        return found
    row = winner_locked_record(selection, winner.fingerprint, backfilled=False)
    db.add(row)
    return row


# --- P2.5-A: validation helpers --------------------------------------------------

_SUBJECT_TABLES: dict[str, Any] = {
    "problem_spec": ProblemSpec,
    "dataset_version": Dataset,
    "split_plan": SplitPlan,
    "feature_recipe": FeatureSetVersion,
    "experiment": Experiment,
    "candidate": ExperimentCandidate,
    "model_version": ModelVersion,
}
_EVIDENCE_TABLES: dict[str, Any] = {
    **_SUBJECT_TABLES,
    "model_selection": ModelSelectionDecision,
    "decision_record": ProjectDecisionRecord,
}
_CALLER_KEY = re.compile(r"^[A-Za-z0-9._:-]{1,%d}$" % CALLER_IDEMPOTENCY_KEY_MAX_CHARS)
_METRIC = re.compile(EVIDENCE_METRIC_PATTERN)
_EVIDENCE_KEYS = frozenset({"kind", "id", "metric", "scope"})
_SUPERSEDES_UNIQUE = "uq_pdr_supersedes_id"
_IDEMPOTENCY_UNIQUE = "uq_pdr_workspace_idempotency_key"


def load_project(db: Session, *, workspace_id: UUID, project_id: UUID) -> Project:
    """Workspace-filtered project lookup; another tenant's project is simply not found."""

    project = db.scalar(
        select(Project).where(Project.id == project_id, Project.workspace_id == workspace_id)
    )
    if project is None:
        raise ProjectNotFoundError("project not found")
    return project


# Agent binding hook: confirms an agent run / service token belongs to the
# workspace. P3.2-A installs the service-token verifier (an active token of this
# workspace with ``decisions:propose``); agent runs stay unbound until Phase 6
# adds ``agent_runs``. None fails closed: no agent write without a binding.
AgentBindingVerifier = Callable[[Session, DecisionActor, UUID], bool]
agent_binding_verifier: AgentBindingVerifier | None = verify_agent_binding


def _verify_agent(db: Session, actor: DecisionActor, workspace_id: UUID) -> None:
    if actor.agent_run_id is None and actor.service_token_id is None:
        raise DecisionActorNotPermittedError(
            "agent_actor_invalid", "an agent actor needs an agent run or a service token"
        )
    if agent_binding_verifier is None:
        raise DecisionActorNotPermittedError(
            "agent_actor_unbound", "agent actors are refused until a workspace binding exists"
        )
    if not agent_binding_verifier(db, actor, workspace_id):
        raise DecisionActorNotPermittedError(
            "agent_actor_foreign", "the agent run / service token is not bound to this workspace"
        )


def authorize_writer(
    db: Session,
    actor: DecisionActor,
    *,
    workspace_id: UUID,
    allow_agent: bool = False,
    allow_rule: bool = True,
) -> None:
    """Humans need ML-write access; agents may only propose (bound to the workspace);
    rules are code-owned ids and never resolve proposals or move refs."""

    if not isinstance(actor, DecisionActor) or actor.kind not in ACTOR_KINDS:
        raise DecisionActorNotPermittedError("unknown_actor", "decision writes need a typed actor")
    if actor.kind == ACTOR_HUMAN:
        user = actor.user
        if not isinstance(user, User) or user.id != actor.user_id:
            raise DecisionActorNotPermittedError("unknown_actor", "a human actor needs its user")
        if not can_read_workspace(db, user, workspace_id):
            raise IdentityError("not authorized for this workspace", status_code=403)
        if not can_perform_ml_write(db, user, workspace_id):
            raise IdentityError("workspace ML write access is required", status_code=403)
    elif actor.kind == ACTOR_RULE:
        if not is_code_owned_rule(actor.rule):
            raise DecisionActorNotPermittedError("unknown_rule", "rule actors are code-owned rule ids")
        if not allow_rule:
            raise DecisionActorNotPermittedError(
                "rule_not_permitted", "rules never accept, reject or move refs; a human does"
            )
    else:
        if not allow_agent:
            raise DecisionActorNotPermittedError(
                "agent_advisory_only", "agents may only propose; a human accepts or rejects"
            )
        _verify_agent(db, actor, workspace_id)


_CONTROL_CHARS = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")


def _check_text(value: str, *, label: str) -> None:
    if _CONTROL_CHARS.search(value):
        raise InvalidDecisionRecordError("control_characters", f"{label} contains control characters")
    if SECRET_TEXT.search(value):
        raise InvalidDecisionRecordError("secret_like_text", f"{label} looks like it contains a credential")


def check_agent_text(value: str, *, label: str) -> None:
    """The record-text gate (control characters, secret-like text incl. ``dclab_st_``)
    for other agent-authored text, e.g. problem specs written with a service token."""

    _check_text(value, label=label)


def clean_rationale(text: Any) -> str:
    value = str(text or "").strip()
    if not value:
        raise InvalidDecisionRecordError("rationale_required", "rationale must not be empty")
    if len(value) > RATIONALE_MAX_CHARS:
        raise InvalidDecisionRecordError(
            "rationale_too_long", f"rationale is limited to {RATIONALE_MAX_CHARS} characters"
        )
    _check_text(value, label="rationale")
    return value


def _blocked_key(key: str) -> bool:
    lowered = key.lower()
    return lowered in FORBIDDEN_REQUEST_PAYLOAD_KEYS or any(part in lowered for part in BLOCKED_KEY_PARTS)


def _scan_json(value: Any, *, label: str, path: str = "$") -> None:
    """Reject secret/raw-body keys (substring), credential-like or control-char strings."""

    if isinstance(value, dict):
        for key, item in value.items():
            key_text = str(key)
            _check_text(key_text, label=f"{label} key")
            if _blocked_key(key_text):
                raise InvalidDecisionRecordError("forbidden_key", f"{label} may not contain {path}.{key_text}")
            _scan_json(item, label=label, path=f"{path}.{key_text}")
    elif isinstance(value, list):
        for index, item in enumerate(value):
            _scan_json(item, label=label, path=f"{path}[{index}]")
    elif isinstance(value, str):
        _check_text(value, label=label)


def clean_json_object(value: Any, *, label: str, caller_details: bool = False) -> dict[str, Any]:
    """Bounded JSON object without secrets (the DB CHECKs are only a backstop).

    ``caller_details``: caller-supplied ``details`` may not set service-owned keys.
    """

    if value is None:
        return {}
    if not isinstance(value, dict):
        raise InvalidDecisionRecordError(f"invalid_{label}", f"{label} must be a JSON object")
    if caller_details and RESERVED_DETAIL_KEYS & set(map(str, value)):
        raise InvalidDecisionRecordError("reserved_detail_key", f"{label} keys {sorted(RESERVED_DETAIL_KEYS)} are service-owned")
    _scan_json(value, label=label)
    try:
        encoded = json.dumps(value, allow_nan=False, default=str)
    except ValueError as exc:
        raise InvalidDecisionRecordError(f"invalid_{label}", f"{label} must be finite JSON") from exc
    if len(encoded.encode("utf-8")) > STATE_GRAPH_JSON_MAX_BYTES:
        raise InvalidDecisionRecordError(
            f"{label}_too_large", f"{label} is limited to {STATE_GRAPH_JSON_MAX_BYTES} bytes"
        )
    return json.loads(encoded)


def _evidence_invalid(index: int, message: str) -> InvalidDecisionRecordError:
    return InvalidDecisionRecordError("evidence_ref_invalid", f"evidence_refs[{index}]: {message}")


def parse_evidence_refs(refs: Iterable[Any] | None) -> list[dict[str, str]]:
    """Syntactic part of ``validate_evidence_refs`` (no database access)."""

    if refs is None:
        return []
    if isinstance(refs, (str, bytes, dict)):
        raise InvalidDecisionRecordError("evidence_ref_invalid", "evidence_refs must be a list")
    items = list(refs)
    if len(items) > EVIDENCE_REFS_MAX:
        raise InvalidDecisionRecordError(
            "too_many_evidence_refs", f"at most {EVIDENCE_REFS_MAX} evidence refs"
        )
    cleaned: list[dict[str, str]] = []
    for index, raw in enumerate(items):
        if not isinstance(raw, dict) or not set(raw) <= _EVIDENCE_KEYS:
            raise _evidence_invalid(index, "must be {kind, id, metric?, scope?}")
        kind = raw.get("kind")
        if kind not in EVIDENCE_REF_KINDS:
            raise _evidence_invalid(index, "unknown kind")
        try:
            node_id = UUID(str(raw.get("id")))
        except ValueError as exc:
            raise _evidence_invalid(index, "id must be a UUID") from exc
        metric, scope = raw.get("metric"), raw.get("scope")
        if (metric is not None or scope is not None) and kind not in EVIDENCE_METRIC_KINDS:
            raise _evidence_invalid(index, "metric/scope apply to candidates and model versions")
        if metric is not None and (not isinstance(metric, str) or not _METRIC.match(metric)):
            raise _evidence_invalid(index, "metric must be a metric name")
        if scope is not None and scope not in EVIDENCE_SCOPES:
            raise _evidence_invalid(index, "unknown evaluation scope")
        cleaned.append(evidence_ref(kind, node_id, metric=metric, scope=scope))
    return cleaned


def validate_evidence_refs(
    db: Session, *, workspace_id: UUID, project_id: UUID, refs: Iterable[Any] | None
) -> list[dict[str, str]]:
    """``{kind, id, metric?, scope?}`` refs to nodes of *this* project (one query per kind).

    Unknown ids and nodes of another project or tenant are rejected alike
    (``evidence_ref_not_found``); ``metric``/``scope`` must name an existing
    evaluation (``evaluation_metrics``) of the cited candidate/model version.
    """

    cleaned = parse_evidence_refs(refs)
    wanted: dict[str, set[UUID]] = defaultdict(set)
    for ref in cleaned:
        wanted[ref["kind"]].add(UUID(ref["id"]))
    for kind, ids in wanted.items():
        model = _EVIDENCE_TABLES[kind]
        found = set(
            db.scalars(
                select(model.id).where(
                    model.workspace_id == workspace_id,
                    model.project_id == project_id,
                    model.id.in_(ids),
                )
            )
        )
        if found != ids:
            raise InvalidDecisionRecordError(
                "evidence_ref_not_found", f"an evidence ref of kind {kind} is not a node of this project"
            )
    _check_evaluation_coordinates(db, workspace_id=workspace_id, refs=cleaned)
    return cleaned


def _check_evaluation_coordinates(
    db: Session, *, workspace_id: UUID, refs: list[dict[str, str]]
) -> None:
    coords = [ref for ref in refs if "metric" in ref or "scope" in ref]
    if not coords:
        return
    mv_ids = {UUID(ref["id"]) for ref in coords if ref["kind"] == "model_version"}
    mv_candidates = (
        dict(
            db.execute(
                select(ModelVersion.id, ModelVersion.selected_candidate_id).where(
                    ModelVersion.workspace_id == workspace_id, ModelVersion.id.in_(mv_ids)
                )
            ).all()
        )
        if mv_ids
        else {}
    )
    candidate_ids = {UUID(ref["id"]) for ref in coords if ref["kind"] == "candidate"}
    candidate_ids |= {cid for cid in mv_candidates.values() if cid is not None}
    rows = db.execute(
        select(
            ModelEvaluation.candidate_id,
            ModelEvaluation.model_version_id,
            ModelEvaluation.evaluation_scope,
            EvaluationMetric.metric_name,
        )
        .outerjoin(EvaluationMetric, EvaluationMetric.model_evaluation_id == ModelEvaluation.id)
        .where(
            ModelEvaluation.workspace_id == workspace_id,
            or_(
                ModelEvaluation.candidate_id.in_(candidate_ids),
                ModelEvaluation.model_version_id.in_(mv_ids),
            ),
        )
    ).all()
    available: set[tuple[str, str, str, str | None]] = set()
    for candidate_id, model_version_id, scope, metric in rows:
        subjects = []
        if candidate_id is not None:
            subjects.append(("candidate", str(candidate_id)))
            subjects += [
                ("model_version", str(mv)) for mv, cid in mv_candidates.items() if cid == candidate_id
            ]
        if model_version_id is not None:
            subjects.append(("model_version", str(model_version_id)))
        for kind, node in subjects:
            available.add((kind, node, scope, metric))
    for ref in coords:
        if not any(
            kind == ref["kind"]
            and node == ref["id"]
            and ref.get("scope") in (None, scope)
            and ref.get("metric") in (None, metric)
            for kind, node, scope, metric in available
        ):
            raise InvalidDecisionRecordError(
                "evidence_metric_not_found",
                "an evidence ref names a metric/scope the cited node has no evaluation for",
            )


def node_digest(row: Any) -> str | None:
    for attr in ("content_digest", "plan_digest", "fingerprint"):
        value = getattr(row, attr, None)
        if isinstance(value, str) and _DIGEST.match(value):
            return value
    return None


def load_subject(
    db: Session, *, workspace_id: UUID, project_id: UUID, subject_kind: str, subject_id: UUID | None
) -> Any:
    """The subject node in this project (None for ``project``); 422 when it is not one."""

    if subject_kind not in SUBJECT_KINDS:
        raise InvalidDecisionRecordError("invalid_subject_kind", "unknown subject kind")
    if subject_kind == "project":
        if subject_id is not None and subject_id != project_id:
            raise InvalidDecisionRecordError("subject_not_found", "the subject is not this project")
        return None
    model = _SUBJECT_TABLES.get(subject_kind)
    if model is None:
        raise InvalidDecisionRecordError("invalid_subject_kind", "unknown subject kind")
    if subject_id is None:
        raise InvalidDecisionRecordError("subject_required", f"{subject_kind} needs a subject id")
    row = db.scalar(
        select(model).where(
            model.id == subject_id,
            model.workspace_id == workspace_id,
            model.project_id == project_id,
        )
    )
    if row is None:
        raise InvalidDecisionRecordError("subject_not_found", "the subject is not a node of this project")
    return row


def scoped_idempotency_key(actor: DecisionActor, key: str | None) -> str | None:
    """Caller keys live in the actor's namespace; rule callers own their full keys."""

    if key is None:
        return None
    if actor.kind == ACTOR_RULE:
        if not key or len(key) > IDEMPOTENCY_KEY_MAX_CHARS or key.startswith(("human:", "agent:")):
            raise InvalidDecisionRecordError("invalid_idempotency_key", "rule key out of bounds")
        return key
    if not isinstance(key, str) or not _CALLER_KEY.match(key):
        raise InvalidDecisionRecordError(
            "invalid_idempotency_key",
            f"idempotency keys are 1-{CALLER_IDEMPOTENCY_KEY_MAX_CHARS} chars of [A-Za-z0-9._:-]",
        )
    return f"{actor.kind}:{actor.principal_id}:{key}"


def replay_record(
    db: Session,
    *,
    workspace_id: UUID,
    project_id: UUID,
    idempotency_key: str | None,
    decision_type: str,
    state: str,
    supersedes_id: UUID | None,
    request: dict[str, Any] | None = None,
) -> ProjectDecisionRecord | None:
    """The record an earlier identical call wrote, or None; a different reuse is a 409.

    ``request`` holds the stored-form fields of this call (e.g. ``rationale``,
    ``evidence_refs``, ``subject_id``, ``ref_moves``); any difference is a 409.
    """

    if idempotency_key is None:
        return None
    found = existing_record(db, workspace_id=workspace_id, idempotency_key=idempotency_key)
    if found is None:
        return None
    if (found.project_id, found.decision_type, found.state, found.supersedes_id) != (
        project_id,
        decision_type,
        state,
        supersedes_id,
    ) or any(_stored_field(found, name) != value for name, value in (request or {}).items()):
        raise IdempotencyKeyConflictError(
            "idempotency_key_reused", "this idempotency key was used for a different decision"
        )
    return found


def _stored_field(row: ProjectDecisionRecord, name: str) -> Any:
    if name == "subject_id":
        return _subject_id(row)
    if name == "evidence_refs_without_holdout":  # an agent's champion proposal (service-attached ref)
        return [ref for ref in row.evidence_refs or [] if ref.get("scope") != FINAL_HOLDOUT_SCOPE]
    if name == "ref_moves":
        return sorted(
            (move.get("ref_kind"), (move.get("to") or {}).get("id"))
            for move in (row.details or {}).get("ref_moves") or []
        )
    return getattr(row, name)


def _actor_columns(actor: DecisionActor) -> dict[str, Any]:
    columns: dict[str, Any] = {
        "actor_kind": actor.kind,
        "rationale_untrusted": actor.kind == ACTOR_AGENT,
    }
    if actor.kind == ACTOR_HUMAN:
        columns["actor_user_id"] = actor.user_id
    elif actor.kind == ACTOR_RULE:
        columns["actor_rule"] = actor.rule
        columns["actor_user_id"] = actor.user_id
    else:
        columns["actor_agent_run_id"] = actor.agent_run_id
        columns["actor_service_token_id"] = actor.service_token_id
    return columns


def build_record(
    *,
    workspace_id: UUID,
    project_id: UUID,
    actor: DecisionActor,
    decision_type: str,
    state: str,
    subject_kind: str,
    subject_id: UUID | None,
    rationale: str,
    facts: dict[str, Any],
    evidence_refs: list[dict[str, str]],
    details: dict[str, Any],
    subject_digest: str | None = None,
    supersedes_id: UUID | None = None,
    idempotency_key: str | None = None,
    schema_version: int = DECISION_RECORD_SCHEMA_VERSION,
) -> ProjectDecisionRecord:
    """An unsaved row from already-validated parts (callers validate first)."""

    row = ProjectDecisionRecord(
        workspace_id=workspace_id,
        project_id=project_id,
        decision_type=decision_type,
        state=state,
        subject_kind=subject_kind,
        subject_digest=subject_digest if _DIGEST.match(subject_digest or "") else None,
        rationale=rationale,
        facts=facts,
        evidence_refs=evidence_refs,
        details=details,
        schema_version=schema_version,
        policy_version=DECISION_POLICY_VERSION,
        supersedes_id=supersedes_id,
        idempotency_key=idempotency_key,
        **_actor_columns(actor),
    )
    column = SUBJECT_COLUMNS.get(subject_kind)
    if column is not None:
        setattr(row, column, subject_id)
    return row


def unique_violation(exc: IntegrityError) -> str | None:
    """Name of the violated unique constraint (SQLSTATE 23505), else None."""

    orig = getattr(exc, "orig", None)
    # psycopg 3 exposes ``sqlstate``; psycopg2 (test driver) ``pgcode``.
    if (getattr(orig, "sqlstate", None) or getattr(orig, "pgcode", None)) != "23505":
        return None
    return getattr(getattr(orig, "diag", None), "constraint_name", None)


def raise_for_record_race(exc: IntegrityError) -> None:
    """Map a lost race on the chain/idempotency uniques to a typed error."""

    constraint = unique_violation(exc)
    if constraint == _SUPERSEDES_UNIQUE:
        raise InvalidDecisionTransitionError(
            "already_resolved", "the record was resolved or corrected concurrently"
        ) from exc
    if constraint == _IDEMPOTENCY_UNIQUE:
        raise IdempotencyKeyConflictError(
            "idempotency_key_reused", "this idempotency key was used concurrently"
        ) from exc


def insert_record(db: Session, row: ProjectDecisionRecord) -> ProjectDecisionRecord:
    try:
        with db.begin_nested():
            db.add(row)
            db.flush()
    except IntegrityError as exc:
        raise_for_record_race(exc)
        raise
    return row


def get_record(
    db: Session, *, workspace_id: UUID, project_id: UUID, record_id: UUID
) -> ProjectDecisionRecord:
    row = db.scalar(
        select(ProjectDecisionRecord).where(
            ProjectDecisionRecord.id == record_id,
            ProjectDecisionRecord.workspace_id == workspace_id,
            ProjectDecisionRecord.project_id == project_id,
        )
    )
    if row is None:
        raise DecisionRecordNotFoundError("decision record not found")
    return row


def find_record(db: Session, *, workspace_id: UUID, record_id: UUID) -> ProjectDecisionRecord:
    """A record of this workspace by id alone (``/v1/decisions/{id}``); foreign is not found."""

    row = db.scalar(
        select(ProjectDecisionRecord).where(
            ProjectDecisionRecord.id == record_id, ProjectDecisionRecord.workspace_id == workspace_id
        )
    )
    if row is None:
        raise DecisionRecordNotFoundError("decision record not found")
    return row


def record_read(db: Session, row: ProjectDecisionRecord) -> DecisionRecordRead:
    """The bounded /v1 projection of one record with its derived ``effective_state``."""

    return decision_read(row, successor_id(db, row))


def successor_id(db: Session, row: ProjectDecisionRecord) -> UUID | None:
    return db.scalar(
        select(ProjectDecisionRecord.id).where(
            ProjectDecisionRecord.workspace_id == row.workspace_id,
            ProjectDecisionRecord.supersedes_id == row.id,
        )
    )


def effective_state(db: Session, row: ProjectDecisionRecord) -> str:
    return EFFECTIVE_STATE_SUPERSEDED if successor_id(db, row) is not None else row.state


def require_open_proposal(db: Session, row: ProjectDecisionRecord) -> None:
    if row.state != STATE_PROPOSED:
        raise InvalidDecisionTransitionError(
            "not_a_proposal", f"an {row.state} record cannot be accepted or rejected"
        )
    if successor_id(db, row) is not None:
        raise InvalidDecisionTransitionError(
            "already_resolved", "this proposal was already accepted or rejected"
        )


def _subject_id(row: ProjectDecisionRecord) -> UUID | None:
    column = SUBJECT_COLUMNS.get(row.subject_kind)
    return getattr(row, column) if column else None


# --- P2.5-A: state machine ----------------------------------------------------------


def record(
    db: Session,
    *,
    workspace_id: UUID,
    project_id: UUID,
    actor: DecisionActor,
    decision_type: str,
    subject_kind: str,
    subject_id: UUID | None = None,
    rationale: str,
    state: str = STATE_PROPOSED,
    facts: dict[str, Any] | None = None,
    evidence_refs: Iterable[Any] | None = None,
    details: dict[str, Any] | None = None,
    idempotency_key: str | None = None,
) -> ProjectDecisionRecord:
    """Start a new chain: a proposal, or (humans, rules) a directly accepted record.

    Ref-moving types go through ``project_ref_service`` (``propose_ref_move`` /
    ``move_ref``) so an accepted move never exists without its ref update.
    """

    authorize_writer(db, actor, workspace_id=workspace_id, allow_agent=state == STATE_PROPOSED)
    refuse_agent_holdout(actor, evidence_refs, facts, details)
    load_project(db, workspace_id=workspace_id, project_id=project_id)
    if decision_type not in DECISION_TYPES:
        raise InvalidDecisionRecordError("unknown_decision_type", "unknown decision type")
    if decision_type in RESERVED_DECISION_TYPES:
        raise InvalidDecisionRecordError("reserved_decision_type", "reserved for Phase 6 proposals")
    if decision_type in RULE_ONLY_DECISION_TYPES and actor.kind != ACTOR_RULE:
        raise DecisionActorNotPermittedError("rule_only_decision_type", "only engine rules write this type")
    if decision_type in SERVICE_ONLY_DECISION_TYPES - RULE_ONLY_DECISION_TYPES:
        raise DecisionActorNotPermittedError(
            "service_only_decision_type", "this type is written only by its owning service"
        )
    if actor.kind == ACTOR_RULE and (
        decision_type not in RULE_ONLY_DECISION_TYPES or state != STATE_ACCEPTED
    ):
        raise DecisionActorNotPermittedError(
            "rule_not_permitted", "rules only write accepted records of rule-owned types"
        )
    if decision_type in REF_MOVE_DECISION_TYPES:
        raise InvalidDecisionRecordError(
            "use_move_ref", "ref moves are proposed with propose_ref_move and accepted with move_ref"
        )
    if state == STATE_REJECTED:
        raise InvalidDecisionTransitionError(
            "rejection_needs_proposal", "a rejection always supersedes a proposal"
        )
    if state not in (STATE_PROPOSED, STATE_ACCEPTED):
        raise InvalidDecisionRecordError("invalid_state", "unknown decision state")
    key = scoped_idempotency_key(actor, idempotency_key)
    replayed = replay_record(
        db, workspace_id=workspace_id, project_id=project_id, idempotency_key=key,
        decision_type=decision_type, state=state, supersedes_id=None,
        request={
            "subject_kind": subject_kind,
            "subject_id": None if subject_kind == "project" else subject_id,
            "rationale": clean_rationale(rationale),
            "evidence_refs": parse_evidence_refs(evidence_refs),
        },
    )
    if replayed is not None:
        return replayed
    subject = load_subject(
        db, workspace_id=workspace_id, project_id=project_id,
        subject_kind=subject_kind, subject_id=subject_id,
    )
    row = build_record(
        workspace_id=workspace_id,
        project_id=project_id,
        actor=actor,
        decision_type=decision_type,
        state=state,
        subject_kind=subject_kind,
        subject_id=subject.id if subject is not None else None,
        subject_digest=node_digest(subject),
        rationale=clean_rationale(rationale),
        facts=clean_json_object(facts, label="facts"),
        evidence_refs=validate_evidence_refs(
            db, workspace_id=workspace_id, project_id=project_id, refs=evidence_refs
        ),
        details=clean_json_object(details, label="details", caller_details=True),
        idempotency_key=key,
    )
    return insert_record(db, row)


def propose(db: Session, **kwargs: Any) -> ProjectDecisionRecord:
    """``record(state='proposed')`` — the only write an agent actor may perform."""

    return record(db, state=STATE_PROPOSED, **kwargs)


def _successor(
    db: Session,
    *,
    workspace_id: UUID,
    project_id: UUID,
    prior: ProjectDecisionRecord,
    actor: DecisionActor,
    state: str,
    rationale: str,
    facts: dict[str, Any] | None,
    evidence_refs: Iterable[Any] | None,
    key: str | None,
) -> ProjectDecisionRecord:
    details = dict(prior.details or {})
    if prior.actor_kind == ACTOR_AGENT:
        # Provenance: facts/details/evidence carried from an agent proposal stay labelled.
        details[DETAIL_CARRIED_FROM_AGENT] = True
    row = build_record(
        workspace_id=workspace_id,
        project_id=project_id,
        actor=actor,
        decision_type=prior.decision_type,
        state=state,
        subject_kind=prior.subject_kind,
        subject_id=_subject_id(prior),
        subject_digest=prior.subject_digest,
        rationale=clean_rationale(rationale),
        facts=clean_json_object(facts, label="facts") if facts is not None else dict(prior.facts or {}),
        evidence_refs=(
            validate_evidence_refs(
                db, workspace_id=workspace_id, project_id=project_id, refs=evidence_refs
            )
            if evidence_refs is not None
            else list(prior.evidence_refs or [])
        ),
        details=details,
        supersedes_id=prior.id,
        idempotency_key=key,
        schema_version=prior.schema_version,
    )
    return insert_record(db, row)


def _resolve(
    db: Session,
    *,
    workspace_id: UUID,
    project_id: UUID,
    record_id: UUID,
    actor: DecisionActor,
    state: str,
    rationale: str,
    evidence_refs: Iterable[Any] | None,
    idempotency_key: str | None,
) -> ProjectDecisionRecord:
    authorize_writer(db, actor, workspace_id=workspace_id, allow_rule=False)
    load_project(db, workspace_id=workspace_id, project_id=project_id)
    proposal = get_record(db, workspace_id=workspace_id, project_id=project_id, record_id=record_id)
    key = scoped_idempotency_key(actor, idempotency_key)
    request: dict[str, Any] = {"rationale": clean_rationale(rationale)}
    if evidence_refs is not None:
        request["evidence_refs"] = parse_evidence_refs(evidence_refs)
    replayed = replay_record(
        db, workspace_id=workspace_id, project_id=project_id, idempotency_key=key,
        decision_type=proposal.decision_type, state=state, supersedes_id=proposal.id,
        request=request,
    )
    if replayed is not None:
        return replayed
    require_open_proposal(db, proposal)
    if proposal.decision_type in SERVICE_ONLY_DECISION_TYPES:
        raise DecisionActorNotPermittedError("rule_only_decision_type", "only engine rules write this type")
    if state == STATE_ACCEPTED and proposal.decision_type in REF_MOVE_DECISION_TYPES:
        raise InvalidDecisionTransitionError(
            "ref_move_requires_move_ref",
            "accept a ref-move proposal with move_ref so the record and the move commit together",
        )
    return _successor(
        db, workspace_id=workspace_id, project_id=project_id, prior=proposal, actor=actor,
        state=state, rationale=rationale, facts=None, evidence_refs=evidence_refs, key=key,
    )


def accept(
    db: Session,
    *,
    workspace_id: UUID,
    project_id: UUID,
    record_id: UUID,
    actor: DecisionActor,
    rationale: str,
    evidence_refs: Iterable[Any] | None = None,
    idempotency_key: str | None = None,
) -> ProjectDecisionRecord:
    """proposed --accept--> accepted (a new row superseding the proposal)."""

    return _resolve(
        db, workspace_id=workspace_id, project_id=project_id, record_id=record_id, actor=actor,
        state=STATE_ACCEPTED, rationale=rationale, evidence_refs=evidence_refs,
        idempotency_key=idempotency_key,
    )


def reject(
    db: Session,
    *,
    workspace_id: UUID,
    project_id: UUID,
    record_id: UUID,
    actor: DecisionActor,
    rationale: str,
    evidence_refs: Iterable[Any] | None = None,
    idempotency_key: str | None = None,
) -> ProjectDecisionRecord:
    """proposed --reject--> rejected (terminal; a new proposal starts a new chain)."""

    return _resolve(
        db, workspace_id=workspace_id, project_id=project_id, record_id=record_id, actor=actor,
        state=STATE_REJECTED, rationale=rationale, evidence_refs=evidence_refs,
        idempotency_key=idempotency_key,
    )


def supersede(
    db: Session,
    *,
    workspace_id: UUID,
    project_id: UUID,
    record_id: UUID,
    actor: DecisionActor,
    rationale: str,
    facts: dict[str, Any] | None = None,
    evidence_refs: Iterable[Any] | None = None,
    idempotency_key: str | None = None,
) -> ProjectDecisionRecord:
    """accepted --correct--> accepted' (same type and subject; the old row becomes superseded).

    Ref history (``ref_initialized``/``ref_moved``/``champion_promoted``) is
    corrected by a new ``move_ref``, never in place; rule records only by rules.
    """

    authorize_writer(db, actor, workspace_id=workspace_id)
    load_project(db, workspace_id=workspace_id, project_id=project_id)
    prior = get_record(db, workspace_id=workspace_id, project_id=project_id, record_id=record_id)
    if actor.kind == ACTOR_RULE and prior.decision_type not in RULE_ONLY_DECISION_TYPES:
        raise DecisionActorNotPermittedError(
            "rule_not_permitted", "rules only correct records of rule-owned types"
        )
    key = scoped_idempotency_key(actor, idempotency_key)
    request: dict[str, Any] = {"rationale": clean_rationale(rationale)}
    if evidence_refs is not None:
        request["evidence_refs"] = parse_evidence_refs(evidence_refs)
    if facts is not None:
        request["facts"] = clean_json_object(facts, label="facts")
    replayed = replay_record(
        db, workspace_id=workspace_id, project_id=project_id, idempotency_key=key,
        decision_type=prior.decision_type, state=STATE_ACCEPTED, supersedes_id=prior.id,
        request=request,
    )
    if replayed is not None:
        return replayed
    if prior.state != STATE_ACCEPTED:
        raise InvalidDecisionTransitionError(
            "not_accepted",
            "only an accepted record can be corrected; a proposal is accepted or rejected",
        )
    if successor_id(db, prior) is not None:
        raise InvalidDecisionTransitionError("already_superseded", "this record was already superseded")
    if prior.decision_type in REF_HISTORY_DECISION_TYPES:
        raise InvalidDecisionTransitionError(
            "ref_history_not_correctable", "a ref move is corrected by a new move_ref, never in place"
        )
    if prior.decision_type in RULE_ONLY_DECISION_TYPES and actor.kind != ACTOR_RULE:
        raise DecisionActorNotPermittedError("rule_only_decision_type", "only engine rules write this type")
    if prior.decision_type in SERVICE_ONLY_DECISION_TYPES and actor.kind != ACTOR_RULE:
        # Service-owned records (e.g. problem_spec_locked) are corrected only by
        # their owning service, never through the generic supersede path.
        raise DecisionActorNotPermittedError(
            "service_only_decision_type", "only the owning service writes this type"
        )
    return _successor(
        db, workspace_id=workspace_id, project_id=project_id, prior=prior, actor=actor,
        state=STATE_ACCEPTED, rationale=rationale, facts=facts, evidence_refs=evidence_refs, key=key,
    )


# --- P2.5-A: read (GET /v1/projects/{id}/decisions) ----------------------------------


def decision_cursor_scope(workspace_id: UUID, project_id: UUID, filters: dict[str, Any]) -> str:
    """Cursors are bound to the project and the exact filter set they page (P3.1-A)."""

    return f"decisions:{workspace_id}:{project_id}:{scope_digest(filters)}"


def encode_decision_cursor(scope: str, recorded_at: datetime, record_id: UUID) -> str:
    return sign_cursor(scope, [recorded_at.isoformat(), str(record_id)])


def decode_decision_cursor(scope: str, cursor: str) -> tuple[datetime, UUID]:
    message = "cursor is not a decision list cursor for this project and filter set"
    try:
        stamp, record_id = open_cursor(cursor, scope, message=message)
        recorded_at = datetime.fromisoformat(stamp)
        if recorded_at.tzinfo is None:
            raise ValueError("cursor timestamp must be timezone-aware")
        return recorded_at, UUID(record_id)
    except (InvalidCursorError, ValueError, TypeError) as exc:
        raise InvalidDecisionCursorError(message) from exc


def _aware(value: datetime | None) -> datetime | None:
    if value is None or value.tzinfo is not None:
        return value
    return value.replace(tzinfo=UTC)


def _redacted_text(value: str) -> tuple[str, bool]:
    projected = public_diagnostic(value)
    text = projected if isinstance(projected, str) else "[REDACTED]"
    return text[:RATIONALE_READ_MAX_CHARS], len(value) > RATIONALE_READ_MAX_CHARS and text != "[REDACTED]"


def decision_read(row: ProjectDecisionRecord, superseded_by_id: UUID | None) -> DecisionRecordRead:
    """Bounded, redacted projection; agent rationale is labelled untrusted."""

    subject_id = _subject_id(row) or row.project_id
    rationale, truncated = _redacted_text(row.rationale or "")
    evidence: list[EvidenceRefRead] = []
    for ref in list(row.evidence_refs or [])[:EVIDENCE_REFS_MAX]:
        if not isinstance(ref, dict):
            continue
        safe = public_diagnostic(ref)
        kind, node = str(safe.get("kind") or ""), str(safe.get("id") or "")
        evidence.append(
            EvidenceRefRead(
                kind=kind,
                id=node,
                key=node_key(kind, node) if kind and node else None,
                metric=safe.get("metric") if isinstance(safe.get("metric"), str) else None,
                scope=safe.get("scope") if isinstance(safe.get("scope"), str) else None,
            )
        )
    details = public_diagnostic(dict(row.details or {}))
    details_truncated = len(json.dumps(details, default=str).encode("utf-8")) > DETAILS_READ_MAX_BYTES
    if details_truncated:
        details = {key: value for key, value in details.items() if key in RESERVED_DETAIL_KEYS}
        if len(json.dumps(details, default=str).encode("utf-8")) > DETAILS_READ_MAX_BYTES:
            details = {}
    carried = bool((row.details or {}).get(DETAIL_CARRIED_FROM_AGENT))
    return DecisionRecordRead(
        id=row.id,
        project_id=row.project_id,
        decision_type=row.decision_type,
        state=row.state,
        effective_state=EFFECTIVE_STATE_SUPERSEDED if superseded_by_id is not None else row.state,
        supersedes_id=row.supersedes_id,
        superseded_by_id=superseded_by_id,
        subject=DecisionSubjectRead(
            kind=row.subject_kind, id=subject_id, key=node_key(row.subject_kind, subject_id)
        ),
        subject_digest=row.subject_digest,
        actor=DecisionActorRead(
            kind=row.actor_kind,
            user_id=row.actor_user_id,
            rule=row.actor_rule,
            agent_run_id=row.actor_agent_run_id,
            service_token_id=row.actor_service_token_id,
        ),
        rationale=rationale,
        rationale_untrusted=row.rationale_untrusted,
        rationale_label=UNTRUSTED_RATIONALE_LABEL if row.rationale_untrusted else None,
        rationale_truncated=truncated,
        content_origin=ACTOR_AGENT if row.rationale_untrusted or carried else row.actor_kind,
        facts=public_diagnostic(dict(row.facts or {})),
        evidence_refs=evidence,
        details=details,
        details_truncated=details_truncated,
        schema_version=row.schema_version,
        policy_version=row.policy_version,
        event_at=row.event_at,
        recorded_at=row.recorded_at,
    )


def list_decisions(
    db: Session,
    *,
    actor: User,
    workspace_id: UUID,
    project_id: UUID,
    state: str | None = None,
    effective_state: str | None = None,
    decision_type: str | None = None,
    subject_kind: str | None = None,
    subject_id: UUID | None = None,
    actor_kind: str | None = None,
    recorded_after: datetime | None = None,
    recorded_before: datetime | None = None,
    cursor: str | None = None,
    limit: int = DECISION_PAGE_DEFAULT,
) -> DecisionRecordPage:
    """One project's records, newest first, with ``effective_state`` (one query, ADR §5)."""

    project = get_project(db, actor=actor, workspace_id=workspace_id, project_id=project_id)
    limit = max(1, min(int(limit), DECISION_PAGE_MAX))
    pdr = ProjectDecisionRecord
    successor = aliased(ProjectDecisionRecord)
    stmt = (
        select(pdr, successor.id)
        .outerjoin(
            successor,
            and_(successor.supersedes_id == pdr.id, successor.workspace_id == pdr.workspace_id),
        )
        .where(pdr.workspace_id == workspace_id, pdr.project_id == project.id)
    )
    if state is not None:
        stmt = stmt.where(pdr.state == state)
    if effective_state == EFFECTIVE_STATE_SUPERSEDED:
        stmt = stmt.where(successor.id.is_not(None))
    elif effective_state is not None:
        stmt = stmt.where(successor.id.is_(None), pdr.state == effective_state)
    if decision_type is not None:
        stmt = stmt.where(pdr.decision_type == decision_type)
    if subject_id is not None and subject_kind is None:
        raise InvalidDecisionQueryError("subject_id requires subject_kind")
    if subject_kind is not None:
        stmt = stmt.where(pdr.subject_kind == subject_kind)
        column = SUBJECT_COLUMNS.get(subject_kind)
        if subject_id is not None:
            if column is None:
                raise InvalidDecisionQueryError("subject_kind=project takes no subject_id")
            stmt = stmt.where(getattr(pdr, column) == subject_id)
    if actor_kind is not None:
        stmt = stmt.where(pdr.actor_kind == actor_kind)
    after, before = _aware(recorded_after), _aware(recorded_before)
    if after is not None and before is not None and after > before:
        raise InvalidDecisionQueryError("recorded_after must not be later than recorded_before")
    if after is not None:
        stmt = stmt.where(pdr.recorded_at >= after)
    if before is not None:
        stmt = stmt.where(pdr.recorded_at < before)
    scope = decision_cursor_scope(
        workspace_id,
        project.id,
        {
            "state": state,
            "effective_state": effective_state,
            "decision_type": decision_type,
            "subject_kind": subject_kind,
            "subject_id": subject_id,
            "actor_kind": actor_kind,
            "recorded_after": after.isoformat() if after is not None else None,
            "recorded_before": before.isoformat() if before is not None else None,
        },
    )
    if cursor is not None and cursor.strip():
        recorded_at, record_id = decode_decision_cursor(scope, cursor)
        stmt = stmt.where(tuple_(pdr.recorded_at, pdr.id) < tuple_(recorded_at, record_id))
    rows = db.execute(
        stmt.order_by(pdr.recorded_at.desc(), pdr.id.desc()).limit(limit + 1)
    ).all()
    page = rows[:limit]
    next_cursor = (
        encode_decision_cursor(scope, page[-1][0].recorded_at, page[-1][0].id) if len(rows) > limit else None
    )
    return DecisionRecordPage(
        items=[decision_read(row, superseded_by) for row, superseded_by in page],
        next_cursor=next_cursor,
        limit=limit,
    )
