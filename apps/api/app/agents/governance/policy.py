"""Effective AI policy and decision-point levels (ADR 0009 §2.5, §2.6, §3; ADR 0008 §3).

``effective_policy`` = platform accepted head ⊕ workspace accepted head, narrowed by
CAPS (caps only narrow; a stored workspace document wider than the bound is
clamped at read, refused at write). It FAILS CLOSED: any load or validation
problem raises ``PolicyUnavailable``, which callers turn into a refusal / the
rule path. ``effective_level`` = min(workspace head, platform head, code cap,
§1b ceiling) and is L0 whenever a row is absent or covers another
(prompt release, model) pair.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import Any
from uuid import UUID

from pydantic import BaseModel, ValidationError
from sqlalchemy import exists, func, select, text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session, aliased

from app.agents.governance.decision_points import REGISTRY, answer_ceiling
from app.agents.governance.platform_default import (
    CAPS,
    DATA_CLASS_ORDER,
    ROLE_NAMES,
    AiPolicyV1,
    Budgets,
    DataPolicy,
    Incidents,
    Limits,
    Models,
    OpsAutonomy,
    Proposals,
    Retention,
    RoleModels,
    Roles,
)
from app.db.models import AiPolicy, DecisionPointPolicy, User, Workspace
from app.services.authorization_service import (
    can_approve_ai_policy,
    count_ai_policy_approvers,
    explicit_workspace_role,
    is_ml_write_role,
)


class GovernanceError(Exception):
    code = "governance_error"
    status_code = 400

    def __init__(self, code: str | None = None, detail: str = "") -> None:
        self.code = code or self.code
        self.detail = detail
        super().__init__(f"{self.code}: {detail}" if detail else self.code)


class PolicyUnavailable(GovernanceError):
    """The effective policy cannot be loaded or validated: callers refuse (fail closed)."""

    code = "policy_unavailable"
    status_code = 503


class PolicyInvalid(GovernanceError):
    code = "policy_invalid"
    status_code = 422


class PolicyCapViolation(GovernanceError):
    code = "policy_cap_violation"
    status_code = 422

    def __init__(self, violations: list[str]) -> None:
        self.violations = violations
        super().__init__(detail=", ".join(violations))


class PolicyConflict(GovernanceError):
    code = "policy_conflict"
    status_code = 409


class GovernanceNotFound(GovernanceError):
    code = "not_found"
    status_code = 404


class GovernanceNotPermitted(GovernanceError):
    code = "not_permitted"
    status_code = 403


# --- documents ----------------------------------------------------------------------


def _canonical(value: Any) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()


def document_digest(policy: AiPolicyV1 | dict) -> str:
    raw = policy.model_dump(mode="json") if isinstance(policy, BaseModel) else policy
    return hashlib.sha256(_canonical(raw)).hexdigest()


def _min_fields(candidate: BaseModel, bound: BaseModel) -> dict[str, Any]:
    return {name: min(getattr(candidate, name), getattr(bound, name)) for name in type(candidate).model_fields}


def _narrow_role(candidate: RoleModels, bound: RoleModels) -> RoleModels:
    allowed = tuple(m for m in candidate.allowed if m in bound.allowed) or bound.allowed
    default = candidate.default if candidate.default in allowed else (
        bound.default if bound.default in allowed else allowed[0]
    )
    return RoleModels(
        default=default,
        allowed=allowed,
        fallback=candidate.fallback,
        per_agent={k: v for k, v in candidate.per_agent.items() if v in allowed},
    )


def narrow(candidate: AiPolicyV1, bound: AiPolicyV1) -> AiPolicyV1:
    """The candidate clamped into the bound: every cap only narrows (ADR 0009 §3)."""

    data_index = min(
        DATA_CLASS_ORDER.index(candidate.data.max_class), DATA_CLASS_ORDER.index(bound.data.max_class)
    )
    ops, bound_ops = candidate.autonomy.ops, bound.autonomy.ops
    budgets = {**_min_fields(candidate.budgets, bound.budgets),
               "hard_stop": candidate.budgets.hard_stop or bound.budgets.hard_stop}
    limits = {
        name: type(getattr(candidate.limits, name))(
            **_min_fields(getattr(candidate.limits, name), getattr(bound.limits, name))
        )
        for name in Limits.model_fields
    }
    return candidate.model_copy(
        update={
            "models": Models(roles=Roles(**{
                role: _narrow_role(getattr(candidate.models.roles, role), getattr(bound.models.roles, role))
                for role in ROLE_NAMES
            })),
            "data": DataPolicy(
                max_class=DATA_CLASS_ORDER[data_index],
                sample_values_per_column=min(
                    candidate.data.sample_values_per_column, bound.data.sample_values_per_column
                ),
                user_text_to_jev=candidate.data.user_text_to_jev and bound.data.user_text_to_jev,
            ),
            "autonomy": candidate.autonomy.model_copy(update={"ops": OpsAutonomy(
                auto_retrain_per_week=min(ops.auto_retrain_per_week, bound_ops.auto_retrain_per_week),
                auto_release=ops.auto_release and bound_ops.auto_release,
                rollback_rule=ops.rollback_rule.model_copy(update={
                    "enabled": ops.rollback_rule.enabled and bound_ops.rollback_rule.enabled,
                }),
            )}),
            "budgets": Budgets(**budgets),
            "limits": Limits(**limits),
            "incidents": Incidents(**_min_fields(candidate.incidents, bound.incidents)),
            "proposals": Proposals(ttl_days=min(candidate.proposals.ttl_days, bound.proposals.ttl_days)),
            "retention": Retention(
                prompts_days=min(candidate.retention.prompts_days, bound.retention.prompts_days)
            ),
        }
    )


def _flatten(value: Any, prefix: str = "") -> dict[str, Any]:
    if isinstance(value, dict):
        out: dict[str, Any] = {}
        for key, item in value.items():
            out.update(_flatten(item, f"{prefix}.{key}" if prefix else str(key)))
        return out
    return {prefix: value}


def cap_violations(candidate: AiPolicyV1, bound: AiPolicyV1) -> list[str]:
    """Paths where the candidate is wider than the bound (refused at write)."""

    before = _flatten(candidate.model_dump(mode="json"))
    after = _flatten(narrow(candidate, bound).model_dump(mode="json"))
    return sorted(path for path in before.keys() | after.keys() if before.get(path) != after.get(path))


@dataclass(frozen=True)
class EffectivePolicy:
    policy: AiPolicyV1
    digest: str
    platform_version: int
    workspace_version: int | None


def _accepted_head(db: Session, workspace_id: UUID | None) -> AiPolicy | None:
    scope = AiPolicy.workspace_id.is_(None) if workspace_id is None else AiPolicy.workspace_id == workspace_id
    return db.scalar(
        select(AiPolicy).where(scope, AiPolicy.state == "accepted").order_by(AiPolicy.version.desc()).limit(1)
    )


def _load(row: AiPolicy) -> AiPolicyV1:
    if row.schema_version != 1 or document_digest(row.policy) != row.policy_digest.strip():
        raise PolicyUnavailable(detail=f"ai_policies {row.id} digest or schema mismatch")
    return AiPolicyV1.model_validate(row.policy)


def _platform_bound(db: Session) -> tuple[AiPolicyV1, int]:
    row = _accepted_head(db, None)
    if row is None:
        raise PolicyUnavailable(detail="no accepted platform policy")
    return narrow(_load(row), CAPS), row.version


def effective_policy(db: Session, workspace_id: UUID) -> EffectivePolicy:
    try:
        bound, platform_version = _platform_bound(db)
        row = _accepted_head(db, workspace_id)
        policy = narrow(_load(row), bound) if row is not None else bound
        workspace_version = row.version if row is not None else None
    except PolicyUnavailable:
        raise
    except (ValidationError, SQLAlchemyError, ValueError, TypeError, KeyError, AttributeError) as exc:
        raise PolicyUnavailable(detail=type(exc).__name__) from exc
    digest = hashlib.sha256(_canonical({
        "policy": policy.model_dump(mode="json"),
        "platform_version": platform_version,
        "workspace_version": workspace_version,
    })).hexdigest()
    return EffectivePolicy(policy, digest, platform_version, workspace_version)


def _lock_workspace(db: Session, workspace_id: UUID) -> None:
    if db.scalar(select(Workspace.id).where(Workspace.id == workspace_id).with_for_update()) is None:
        raise GovernanceNotFound(detail="workspace")


def _next_version(db: Session, workspace_id: UUID) -> int:
    return (db.scalar(select(func.max(AiPolicy.version)).where(AiPolicy.workspace_id == workspace_id)) or 0) + 1


def propose_policy(
    db: Session,
    *,
    actor: User,
    workspace_id: UUID,
    policy: AiPolicyV1 | dict,
    rationale: str,
    evidence: list[dict] | None = None,
    change_kind: str = "policy",
) -> AiPolicy:
    """A ``proposed`` row with ``base_version`` = the accepted head the proposer saw."""

    if not is_ml_write_role(explicit_workspace_role(db, actor, workspace_id)):
        raise GovernanceNotPermitted(detail="proposing a policy needs ML-write membership")
    try:
        candidate = policy if isinstance(policy, AiPolicyV1) else AiPolicyV1.model_validate(policy)
    except ValidationError as exc:
        raise PolicyInvalid(detail=str(exc.errors()[:3])) from exc
    violations = cap_violations(candidate, _platform_bound(db)[0])
    if violations:
        raise PolicyCapViolation(violations)
    _lock_workspace(db, workspace_id)
    head = _accepted_head(db, workspace_id)
    row = AiPolicy(
        workspace_id=workspace_id,
        version=_next_version(db, workspace_id),
        base_version=head.version if head is not None else None,
        state="proposed",
        policy=candidate.model_dump(mode="json"),
        policy_digest=document_digest(candidate),
        schema_version=1,
        change_kind=change_kind,
        rationale=rationale,
        evidence=list(evidence or []),
        proposed_by_user_id=actor.id,
    )
    db.add(row)
    db.flush()
    return row


def accept_policy(db: Session, *, approver: User, workspace_id: UUID, proposal_id: UUID) -> AiPolicy:
    """Accept a proposal if the head has not moved (else ``PolicyConflict``, 409)."""

    if not can_approve_ai_policy(db, approver, workspace_id):
        raise GovernanceNotPermitted(detail="approving needs workspace owner/admin")
    _lock_workspace(db, workspace_id)
    proposal = db.scalar(
        select(AiPolicy).where(
            AiPolicy.id == proposal_id, AiPolicy.workspace_id == workspace_id, AiPolicy.state == "proposed"
        )
    )
    if proposal is None:
        raise GovernanceNotFound(detail="proposal")
    if db.scalar(select(exists().where(AiPolicy.supersedes_id == proposal.id))):
        raise PolicyConflict("already_decided")
    head = _accepted_head(db, workspace_id)
    if (head.version if head is not None else None) != proposal.base_version:
        raise PolicyConflict(detail="the accepted head moved since the proposal")
    candidate = AiPolicyV1.model_validate(proposal.policy)
    violations = cap_violations(candidate, _platform_bound(db)[0])
    if violations:
        raise PolicyCapViolation(violations)
    self_approved = proposal.proposed_by_user_id == approver.id
    if self_approved and count_ai_policy_approvers(db, workspace_id) != 1:
        raise GovernanceNotPermitted("self_approval_not_allowed", "another approver must decide")
    row = AiPolicy(
        workspace_id=workspace_id,
        version=_next_version(db, workspace_id),
        base_version=proposal.base_version,
        state="accepted",
        policy=proposal.policy,
        policy_digest=proposal.policy_digest,
        schema_version=proposal.schema_version,
        change_kind=proposal.change_kind,
        rationale=proposal.rationale,
        evidence=proposal.evidence,
        proposed_by_user_id=proposal.proposed_by_user_id,
        decided_by_user_id=approver.id,
        self_approved=self_approved,
        supersedes_id=proposal.id,
    )
    db.add(row)
    db.flush()
    return row


# --- decision-point levels ----------------------------------------------------------


def _level_head(db: Session, workspace_id: UUID | None, key: str) -> DecisionPointPolicy | None:
    successor = aliased(DecisionPointPolicy)
    scope = (
        DecisionPointPolicy.workspace_id.is_(None)
        if workspace_id is None
        else DecisionPointPolicy.workspace_id == workspace_id
    )
    return db.scalar(
        select(DecisionPointPolicy).where(
            scope,
            DecisionPointPolicy.decision_point_key == key,
            DecisionPointPolicy.state == "accepted",
            ~exists().where(
                successor.supersedes_id == DecisionPointPolicy.id, successor.state == "accepted"
            ),
        )
    )


def effective_level(
    db: Session,
    workspace_id: UUID,
    key: str,
    answer_kind: str | None = None,
    prompt_release_id: UUID | None = None,
    model_id: str | None = None,
) -> int:
    point = REGISTRY.get(key)
    if point is None:
        return 0
    try:
        rows = (_level_head(db, workspace_id, key), _level_head(db, None, key))
    except SQLAlchemyError:
        return 0

    def level(row: DecisionPointPolicy | None) -> int:
        if row is None or row.level == 0:
            return 0
        if prompt_release_id is None or row.prompt_release_id != prompt_release_id or row.model_id != model_id:
            return 0
        return row.level

    return min(level(rows[0]), level(rows[1]), point.cap, answer_ceiling(key, answer_kind))


# Two-key advisory locks: (namespace, hashtext(scope:key)), so governance never
# collides with another subsystem's single-key locks.
LOCK_NS_LEVELS = 72061
LOCK_NS_SWITCHES = 72062


def advisory_lock(db: Session, namespace: int, key: str) -> None:
    db.execute(text("SELECT pg_advisory_xact_lock(:ns, hashtext(:k))"), {"ns": namespace, "k": key})


def _level_row(
    *, workspace_id: UUID | None, key: str, level: int, rationale: str, state: str,
    actor: User | None, actor_rule: str | None, decided_by: User | None,
    prompt_release_id: UUID | None, model_id: str | None, evidence: list | None,
    supersedes_id: UUID | None, self_approved: bool = False,
) -> DecisionPointPolicy:
    return DecisionPointPolicy(
        workspace_id=workspace_id,
        decision_point_key=key,
        level=level,
        cap_level=REGISTRY[key].cap,
        prompt_release_id=prompt_release_id,
        model_id=model_id,
        state=state,
        actor_kind="human" if actor is not None else "rule",
        actor_user_id=actor.id if actor is not None else None,
        actor_rule=actor_rule,
        decided_by_user_id=decided_by.id if decided_by is not None else None,
        rationale=rationale,
        evidence=list(evidence or []),
        supersedes_id=supersedes_id,
        self_approved=self_approved,
    )


def _require_point(key: str) -> None:
    if key not in REGISTRY:
        raise GovernanceNotFound(detail=f"decision point {key}")


def _require_workspace_levels(workspace_id: UUID | None) -> UUID:
    if workspace_id is None:
        raise GovernanceNotPermitted(
            "platform_levels_not_supported", "platform levels are written by P6.8-A (R3), not here"
        )
    return workspace_id


def set_level(
    db: Session,
    *,
    workspace_id: UUID | None,
    key: str,
    level: int,
    rationale: str,
    actor: User | None = None,
    actor_rule: str | None = None,
    prompt_release_id: UUID | None = None,
    model_id: str | None = None,
    evidence: list[dict] | None = None,
) -> DecisionPointPolicy:
    """Lower a level immediately (owner/admin, or a rule). Raising one goes through
    ``propose_level`` / ``accept_level``; a new evidence pair above L0 is a raise."""

    _require_point(key)
    if (actor is None) == (actor_rule is None):
        raise GovernanceNotPermitted(detail="exactly one of actor / actor_rule")
    if actor is not None and not can_approve_ai_policy(db, actor, _require_workspace_levels(workspace_id)):
        raise GovernanceNotPermitted(detail="levels need a workspace owner/admin")
    advisory_lock(db, LOCK_NS_LEVELS, f"{workspace_id}:{key}")
    head = _level_head(db, workspace_id, key)
    head_level = head.level if head is not None else 0
    head_pair = (head.prompt_release_id, head.model_id) if head is not None else (None, None)
    if actor is not None and (
        level > head_level or (level > 0 and (prompt_release_id, model_id) != head_pair)
    ):
        raise GovernanceNotPermitted("level_raise_needs_approval", "propose the level and have it accepted")
    row = _level_row(
        workspace_id=workspace_id, key=key, level=level, rationale=rationale, state="accepted",
        actor=actor, actor_rule=actor_rule, decided_by=actor, prompt_release_id=prompt_release_id,
        model_id=model_id, evidence=evidence, supersedes_id=head.id if head is not None else None,
    )
    db.add(row)
    db.flush()
    return row


def propose_level(
    db: Session,
    *,
    actor: User,
    workspace_id: UUID | None,
    key: str,
    level: int,
    rationale: str,
    prompt_release_id: UUID | None = None,
    model_id: str | None = None,
    evidence: list[dict] | None = None,
) -> DecisionPointPolicy:
    """A ``proposed`` level row naming the accepted head it would supersede (its base)."""

    _require_point(key)
    workspace_id = _require_workspace_levels(workspace_id)
    if not is_ml_write_role(explicit_workspace_role(db, actor, workspace_id)):
        raise GovernanceNotPermitted(detail="proposing a level needs ML-write membership")
    head = _level_head(db, workspace_id, key)
    row = _level_row(
        workspace_id=workspace_id, key=key, level=level, rationale=rationale, state="proposed",
        actor=actor, actor_rule=None, decided_by=None, prompt_release_id=prompt_release_id,
        model_id=model_id, evidence=evidence, supersedes_id=head.id if head is not None else None,
    )
    db.add(row)
    db.flush()
    return row


def accept_level(db: Session, *, approver: User, workspace_id: UUID | None, proposal_id: UUID) -> DecisionPointPolicy:
    """Accept a level proposal if its base is still the head; same rules as ``accept_policy``."""

    workspace_id = _require_workspace_levels(workspace_id)
    if not can_approve_ai_policy(db, approver, workspace_id):
        raise GovernanceNotPermitted(detail="approving needs workspace owner/admin")
    _lock_workspace(db, workspace_id)
    proposal = db.scalar(
        select(DecisionPointPolicy).where(
            DecisionPointPolicy.id == proposal_id,
            DecisionPointPolicy.workspace_id == workspace_id,
            DecisionPointPolicy.state == "proposed",
        )
    )
    if proposal is None:
        raise GovernanceNotFound(detail="level proposal")
    advisory_lock(db, LOCK_NS_LEVELS, f"{workspace_id}:{proposal.decision_point_key}")
    head = _level_head(db, workspace_id, proposal.decision_point_key)
    if (head.id if head is not None else None) != proposal.supersedes_id:
        raise PolicyConflict(detail="the level head moved since the proposal")
    self_approved = proposal.actor_user_id == approver.id
    if self_approved and count_ai_policy_approvers(db, workspace_id) != 1:
        raise GovernanceNotPermitted("self_approval_not_allowed", "another approver must decide")
    proposer = db.get(User, proposal.actor_user_id)
    row = _level_row(
        workspace_id=workspace_id, key=proposal.decision_point_key, level=proposal.level,
        rationale=proposal.rationale, state="accepted", actor=proposer, actor_rule=None,
        decided_by=approver, prompt_release_id=proposal.prompt_release_id, model_id=proposal.model_id,
        evidence=proposal.evidence, supersedes_id=proposal.supersedes_id, self_approved=self_approved,
    )
    db.add(row)
    db.flush()
    return row


class GovernancePolicyService:
    """Facade P6.2-B (gateway) and P6.11-A (governance API) read through."""

    effective_policy = staticmethod(effective_policy)
    effective_level = staticmethod(effective_level)
    propose_policy = staticmethod(propose_policy)
    accept_policy = staticmethod(accept_policy)
    set_level = staticmethod(set_level)
    propose_level = staticmethod(propose_level)
    accept_level = staticmethod(accept_level)
