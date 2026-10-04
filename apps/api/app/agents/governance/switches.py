"""Kill switches (ADR 0009 §2.11, §4): append-only chains per (scope, switch key).

Effective state = the chain head, except that an ``off`` row citing a still-open
incident keeps the key off whatever came later. Off always succeeds for workspace
owners/admins (platform keys: platform admins); on needs an approver and is refused
while a switch-off incident is open. The platform ``AI_ENABLED`` setting and the
platform ``global_ai`` row (absent = off) come first in the precedence.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from uuid import UUID

from sqlalchemy import exists, or_, select
from sqlalchemy.orm import Session, aliased

from app.agents.governance.policy import (
    LOCK_NS_SWITCHES,
    GovernanceNotPermitted,
    PolicyConflict,
    advisory_lock,
)
from app.db.models import AiIncident, AiSwitch, PlatformRole, User
from app.services.authorization_service import can_approve_ai_policy, platform_role_for

GLOBAL_AI = "global_ai"
ALL_AI = "all_ai"


class SwitchHeldByIncident(PolicyConflict):
    code = "switch_held_by_incident"


@dataclass(frozen=True)
class EffectiveSwitches:
    platform: dict[str, str] = field(default_factory=dict)
    workspace: dict[str, str] = field(default_factory=dict)

    def blocking(
        self,
        *,
        ai_enabled: bool,
        agent_key: str | None = None,
        provider: str | None = None,
        purpose: str | None = None,
    ) -> str | None:
        """The first key that keeps AI off for this call, or ``None`` (precedence of §4)."""

        if not ai_enabled:
            return "setting:AI_ENABLED"
        if self.platform.get(GLOBAL_AI, "off") != "on":
            return f"platform:{GLOBAL_AI}"
        ordered = [("workspace", ALL_AI)]
        for prefix, value in (("agent", agent_key), ("provider", provider), ("purpose", purpose)):
            if value:
                ordered += [("platform", f"{prefix}:{value}"), ("workspace", f"{prefix}:{value}")]
        for scope, key in ordered:
            if getattr(self, scope).get(key) == "off":
                return f"{scope}:{key}"
        return None


def _scope(workspace_id: UUID | None):
    return AiSwitch.workspace_id.is_(None) if workspace_id is None else AiSwitch.workspace_id == workspace_id


def _held_keys(db: Session, workspace_id: UUID | None) -> set[str]:
    rows = db.scalars(
        select(AiSwitch.switch_key)
        .join(AiIncident, AiIncident.id == AiSwitch.incident_id)
        .where(_scope(workspace_id), AiSwitch.state == "off", AiIncident.status == "open")
    )
    return set(rows)


def _heads(db: Session, workspace_id: UUID | None) -> dict[str, AiSwitch]:
    successor = aliased(AiSwitch)
    rows = db.scalars(
        select(AiSwitch).where(_scope(workspace_id), ~exists().where(successor.supersedes_id == AiSwitch.id))
    )
    return {row.switch_key: row for row in rows}


def _states(db: Session, workspace_id: UUID | None) -> dict[str, str]:
    states = {key: row.state for key, row in _heads(db, workspace_id).items()}
    states.update({key: "off" for key in _held_keys(db, workspace_id)})
    return states


def effective_switches(db: Session, workspace_id: UUID) -> EffectiveSwitches:
    return EffectiveSwitches(platform=_states(db, None), workspace=_states(db, workspace_id))


def _authorize(db: Session, actor: User, workspace_id: UUID | None, *, turning_off: bool) -> None:
    """Platform keys: platform admins. Workspace keys: owners/admins; platform admins may
    switch a workspace key off (ADR 0009 rev. 1), never back on."""

    platform_admin = platform_role_for(db, actor) is PlatformRole.DCLAB_ADMIN
    if workspace_id is None:
        allowed = platform_admin
    else:
        allowed = can_approve_ai_policy(db, actor, workspace_id) or (turning_off and platform_admin)
    if not allowed:
        raise GovernanceNotPermitted(detail="switch changes need workspace owner/admin (platform: admin)")


def _append(
    db: Session, *, workspace_id: UUID | None, switch_key: str, state: str, reason: str,
    actor: User | None, actor_rule: str | None, incident_id: UUID | None,
) -> AiSwitch:
    advisory_lock(db, LOCK_NS_SWITCHES, f"{workspace_id}:{switch_key}")
    head = _heads(db, workspace_id).get(switch_key)
    row = AiSwitch(
        workspace_id=workspace_id,
        switch_key=switch_key,
        state=state,
        changed_by_user_id=actor.id if actor is not None else None,
        actor_rule=actor_rule,
        reason=reason,
        incident_id=incident_id,
        supersedes_id=head.id if head is not None else None,
    )
    db.add(row)
    db.flush()
    return row


def flip_off(
    db: Session,
    *,
    workspace_id: UUID | None,
    switch_key: str,
    reason: str,
    actor: User | None = None,
    actor_rule: str | None = None,
    incident_id: UUID | None = None,
) -> AiSwitch:
    """Always succeeds for an authorized actor or a rule: no approval, no base version."""

    if actor is not None:
        _authorize(db, actor, workspace_id, turning_off=True)
    elif actor_rule is None:
        raise GovernanceNotPermitted(detail="flip_off needs an actor or a rule")
    return _append(db, workspace_id=workspace_id, switch_key=switch_key, state="off", reason=reason,
                   actor=actor, actor_rule=actor_rule, incident_id=incident_id)


def re_enable(db: Session, *, workspace_id: UUID | None, switch_key: str, reason: str, actor: User) -> AiSwitch:
    _authorize(db, actor, workspace_id, turning_off=False)
    # Lock before the incident check so a concurrent hold cannot slip in between.
    advisory_lock(db, LOCK_NS_SWITCHES, f"{workspace_id}:{switch_key}")
    chain = select(AiSwitch.id).where(_scope(workspace_id), AiSwitch.switch_key == switch_key)
    held = db.scalar(
        select(exists().where(
            AiIncident.status == "open",
            or_(
                AiIncident.id.in_(
                    select(AiSwitch.incident_id).where(
                        _scope(workspace_id), AiSwitch.switch_key == switch_key, AiSwitch.state == "off"
                    )
                ),
                (AiIncident.action == "switch_off") & AiIncident.action_switch_id.in_(chain),
            ),
        ))
    )
    if held:
        raise SwitchHeldByIncident(detail=f"{switch_key} stays off until its incident is resolved")
    return _append(db, workspace_id=workspace_id, switch_key=switch_key, state="on", reason=reason,
                   actor=actor, actor_rule=None, incident_id=None)
