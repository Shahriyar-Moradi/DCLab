"""Idempotent platform governance seed: the code default policy and the ``global_ai`` switch.

Called by ``dclab user seed`` (compose ``init``) and ``dclab governance seed``; never at
import. Outside production ``global_ai`` is seeded ``on`` (the ``AI_ENABLED`` setting,
default false, stays the gate). In production nothing is seeded for it, so it is off
(absent = off) until a platform admin runs ``dclab governance switch on global_ai``.
"""

from __future__ import annotations

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.agents.governance.platform_default import PLATFORM_DEFAULT, SEED_ACTOR_RULE, is_development_env
from app.agents.governance.policy import _accepted_head, document_digest
from app.agents.governance.switches import GLOBAL_AI
from app.db.models import AiPolicy, AiSwitch


def seed_platform_governance(db: Session, *, environment: str | None = None) -> dict[str, bool]:
    if environment is None:
        from app.config import get_settings

        environment = get_settings().dclab_env
    digest = document_digest(PLATFORM_DEFAULT)
    head = _accepted_head(db, None)
    policy_created = head is None or head.policy_digest.strip() != digest
    if policy_created:
        version = (db.scalar(select(func.max(AiPolicy.version)).where(AiPolicy.workspace_id.is_(None))) or 0) + 1
        db.add(AiPolicy(
            workspace_id=None,
            version=version,
            state="accepted",
            policy=PLATFORM_DEFAULT.model_dump(mode="json"),
            policy_digest=digest,
            schema_version=1,
            change_kind="seed",
            rationale="code-owned platform default (ADR 0009 §3)",
            evidence=[],
        ))
    switch_created = is_development_env(environment) and db.scalar(
        select(AiSwitch.id).where(AiSwitch.workspace_id.is_(None), AiSwitch.switch_key == GLOBAL_AI).limit(1)
    ) is None
    if switch_created:
        db.add(AiSwitch(
            workspace_id=None,
            switch_key=GLOBAL_AI,
            state="on",
            actor_rule=SEED_ACTOR_RULE,
            reason="platform seed; AI_ENABLED remains the deployment gate",
        ))
    db.flush()
    return {"policy_created": policy_created, "switch_created": switch_created}
