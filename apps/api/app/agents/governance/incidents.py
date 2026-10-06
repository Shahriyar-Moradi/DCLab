"""``ai_incidents``: open, auto-demote, resolve (ADR 0008 §4 demotion table; ADR 0009 §2.9).

A qualifying incident demotes the affected decision points in the incident's scope
(workspace row, or the platform row for a platform incident) through the same
append-only ``decision_point_policies`` chain a human uses, with
``actor_rule = governance.auto_demote.v1`` and the incident id as evidence; the row is
the audit. Safety incidents (data exposure) go to L0 and switch the purpose / agent
(a workspace subject: ``all_ai``; a provider subject: ``provider:<name>``) off until an
approver resolves the incident. Rules only ever lower a level (the chain trigger
``ck_dpp_rule_only_down`` enforces it); re-promotion is a human proposal accepted by an
approver (``policy.propose_level`` / ``accept_level``). The caller commits.
"""

from __future__ import annotations

import re
from datetime import UTC, datetime
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.agents.governance.decision_points import REGISTRY
from app.agents.governance.policy import (
    LOCK_NS_LEVELS,
    GovernanceNotFound,
    GovernanceNotPermitted,
    _level_head,
    advisory_lock,
    is_platform_admin,
    set_level,
)
from app.agents.governance.switches import ALL_AI, GLOBAL_AI, flip_off
from app.db.models import AiIncident, DecisionPointPolicy, User
from app.services.authorization_service import can_approve_ai_policy

AUTO_DEMOTE_RULE = "governance.auto_demote.v1"
AUTO_SWITCH_OFF_RULE = "incidents.auto_switch_off.v1"
# Per subject kind, the key form its switch row accepts (ADR 0009 §2.11 switch-key pattern).
SUBJECT_KEYS = {
    "decision_point": re.compile(r"^[a-z][a-z0-9_.]{0,55}$"),
    "purpose": re.compile(r"^[a-z][a-z0-9_.:-]{0,55}$"),
    "agent": re.compile(r"^[a-z][a-z0-9_]{0,57}$"),
    "provider": re.compile(r"^[a-z][a-z0-9_]{0,54}$"),
    "workspace": re.compile(r"^[a-z][a-z0-9_.-]{0,55}$"),
}
# kind -> how far the affected points fall: "l0" (safety), "one" (one level),
# "evidence" (the level the R3 demotion verdict names), None (no level change).
DEMOTION: dict[str, str | None] = {
    "data_exposure": "l0",
    "eval_failure": "evidence",
    "validator_rejections": "one",
    "drift": "one",
    "revert_rate": "one",
    "budget_exhausted": "one",
    "provider_failure": None,
    "replay_mismatch": None,
    "reconciliation": None,
    "manual": None,
}
SAFETY_KINDS = frozenset({"data_exposure"})


def affected_keys(subject_kind: str, subject_key: str) -> list[str]:
    """Registry keys an incident subject demotes (a purpose or agent reaches every point it
    backs; a workspace subject reaches every point; a provider subject none — it is switched off)."""

    if subject_kind == "decision_point":
        return [subject_key] if subject_key in REGISTRY else []
    if subject_kind == "workspace":
        return list(REGISTRY)
    if subject_kind in ("purpose", "agent"):
        prefix = "jev:" if subject_kind == "purpose" else "agent:"
        keys = [p.key for p in REGISTRY.values() if p.ai_kind == f"{prefix}{subject_key}"]
        if subject_kind == "purpose" and subject_key in REGISTRY and subject_key not in keys:
            keys.append(subject_key)
        return keys
    return []


def switch_key_for(key: str) -> str | None:
    point = REGISTRY.get(key)
    if point is None or point.ai_kind == "none":
        return None
    kind, _, name = point.ai_kind.partition(":")
    return f"{'purpose' if kind == 'jev' else 'agent'}:{name}"


def _safety_switches(subject_kind: str, subject_key: str, keys: list[str], workspace_id: UUID | None) -> set[str]:
    if subject_kind == "workspace":
        return {ALL_AI if workspace_id is not None else GLOBAL_AI}
    if subject_kind == "provider":
        return {f"provider:{subject_key}"}
    switches = {switch_key_for(k) for k in keys} - {None}
    if subject_kind in ("purpose", "agent"):
        switches.add(f"{subject_kind}:{subject_key}")
    return switches


def demotion_target(kind: str, current: int, evidence: dict[str, Any]) -> int | None:
    mode = DEMOTION.get(kind)
    if mode is None or current <= 0:
        return None
    if mode == "l0":
        return 0
    if mode == "evidence":
        target = evidence.get("demote_to")
        return int(target) if isinstance(target, int) and 0 <= target < current else None
    return current - 1


def open_incident(
    db: Session, *, workspace_id: UUID | None, kind: str, subject_kind: str, subject_key: str,
    evidence: dict[str, Any] | None = None, platform: bool = False,
) -> AiIncident:
    """Open the incident and apply its automatic action in one transaction (caller commits).
    ``evidence`` must hold ids, codes, counts and digests only — never an exposed value. A
    ``workspace`` subject names its workspace; the platform-wide form needs ``platform=True``
    (it switches ``global_ai`` off and demotes every platform row)."""

    pattern = SUBJECT_KEYS.get(subject_kind)
    if kind not in DEMOTION or pattern is None or not pattern.fullmatch(subject_key or ""):
        raise ValueError(f"illegal incident {kind!r} / subject {subject_kind}:{subject_key!r}")
    if subject_kind == "workspace" and workspace_id is None and not platform:
        raise ValueError("a workspace incident names its workspace (platform=True for the platform scope)")
    evidence = dict(evidence or {})
    keys = affected_keys(subject_kind, subject_key)
    plan: list[tuple[str, DecisionPointPolicy | None, int, int]] = []
    for key in keys:  # the same lock ``set_level`` takes, so the head cannot move in between
        advisory_lock(db, LOCK_NS_LEVELS, f"{workspace_id}:{key}")
        head = _level_head(db, workspace_id, key)
        current = head.level if head is not None else 0
        target = demotion_target(kind, current, evidence)
        if target is not None:
            plan.append((key, head, current, target))
    switches = _safety_switches(subject_kind, subject_key, keys, workspace_id) if kind in SAFETY_KINDS else set()
    # ``action`` is immutable after insert (only the link columns fill once), so it is decided first.
    action = "switch_off" if switches else "auto_demote" if plan else "none"
    incident = AiIncident(id=uuid4(), workspace_id=workspace_id, kind=kind, subject_kind=subject_kind,
                          subject_key=subject_key, evidence=evidence, action=action, status="open")
    db.add(incident)
    db.flush()
    rows = [set_level(
        db, workspace_id=workspace_id, key=key, level=target, actor_rule=AUTO_DEMOTE_RULE,
        rationale=f"auto-demotion L{current} -> L{target}: incident {kind} on {subject_kind} {subject_key}",
        prompt_release_id=head.prompt_release_id if target > 0 else None,
        model_id=head.model_id if target > 0 else None,
        evidence=[{"kind": "incident", "id": str(incident.id)}],
    ) for key, head, current, target in plan]
    switch = None
    for switch_key in sorted(switches):  # every switch row cites the incident; the link column holds one
        switch = flip_off(db, workspace_id=workspace_id, switch_key=switch_key,
                          reason=f"safety incident {incident.id}: {kind}", actor_rule=AUTO_SWITCH_OFF_RULE,
                          incident_id=incident.id)
    if switch is not None:
        incident.action_switch_id = switch.id
    elif rows:
        incident.action_level_policy_id = rows[0].id
    db.flush()
    return incident


def resolve_incident(db: Session, *, incident_id: UUID, actor: User, resolution: str) -> AiIncident:
    """Human only: a workspace approver (owner/admin) or, for platform incidents, a dclab_admin.
    Resolving never restores a level; that is a separate human promotion."""

    incident = db.get(AiIncident, incident_id)
    allowed = incident is not None and (
        is_platform_admin(db, actor) if incident.workspace_id is None
        else can_approve_ai_policy(db, actor, incident.workspace_id))
    if not allowed:  # another tenant's id reads as absent
        raise GovernanceNotFound(detail="incident")
    if incident.status != "open":
        raise GovernanceNotPermitted("incident_not_open", "the incident is already resolved")
    incident.status, incident.resolved_by_user_id = "resolved", actor.id
    incident.resolved_at, incident.resolution = datetime.now(UTC), resolution[:2000]
    db.flush()
    return incident


def open_incident_counts(db: Session, workspace_id: UUID | None, key: str) -> dict[str, int]:
    """Open incidents touching a point (its own key, its purpose / agent, the whole scope), by kind."""

    point = REGISTRY.get(key)
    subjects = [("decision_point", key), ("workspace", "*")]
    if point is not None and point.ai_kind != "none":
        kind, _, name = point.ai_kind.partition(":")
        subjects.append(("purpose" if kind == "jev" else "agent", name))
        subjects.append(("provider", "typesafe" if kind == "jev" else "openai"))  # provider-wide incidents too
    scope = AiIncident.workspace_id.is_(None) if workspace_id is None else AiIncident.workspace_id == workspace_id
    rows = db.execute(
        select(AiIncident.kind, func.count()).where(
            scope, AiIncident.status == "open",
            (func.concat(AiIncident.subject_kind, ":", AiIncident.subject_key).in_(
                [f"{a}:{b}" for a, b in subjects if b != "*"])) | (AiIncident.subject_kind == "workspace"),
        ).group_by(AiIncident.kind)
    ).all()
    return {kind: int(count) for kind, count in rows}


__all__ = ["AUTO_DEMOTE_RULE", "DEMOTION", "affected_keys", "demotion_target", "open_incident",
           "open_incident_counts", "resolve_incident", "switch_key_for"]
