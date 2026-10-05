"""The AI policy snapshot a job takes at claim (ADR 0008 §2c; P6.9-A).

One run decides with one set of levels: the effective policy digest, the kill-switch
state and every level of the run's decision points (per §1b answer kind, keyed by the
pinned Jev release and model) are read once and digested into ``digest`` — the run's
``policy_digest`` (run evidence, decision records, events). Switches stay enforced
live by the gateway; the snapshot only fixes the levels. ``fingerprint_digest`` is
the part of the policy that can change an applied value: the digest when some point
may apply an AI value (level >= 2), else ``None`` — at L0/L1 the rule value is used
by construction, so the candidate fingerprint (whose contract is "equivalent
configurations fingerprint identically") stays the rule run's. AI off (setting,
missing provider, branch run) reads nothing and has no digest.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from types import MappingProxyType
from typing import Any
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.agents.governance.decision_points import REGISTRY
from app.agents.governance.policy import PolicyUnavailable, effective_level, effective_policy
from app.agents.governance.switches import effective_switches
from app.agents.semantic.port import NO_KIND
from app.agents.semantic.releases import release_for
from app.db.models import PromptRelease

SNAPSHOT_SCHEMA = "dclab.ai_policy_snapshot.v1"


@dataclass(frozen=True)
class PolicySnapshot:
    ai: str  # off | on
    reason: str | None = None  # why AI is off for the run
    digest: str | None = None
    levels: MappingProxyType = field(default_factory=lambda: MappingProxyType({}))  # key -> {kind: level}
    releases: MappingProxyType = field(default_factory=lambda: MappingProxyType({}))  # key -> release facts
    port: Any = field(default=None, compare=False, repr=False)

    @classmethod
    def off(cls, reason: str, port: Any = None) -> "PolicySnapshot":
        return cls(ai="off", reason=reason, port=port)

    def levels_for(self, key: str) -> dict[str, int]:
        return dict(self.levels.get(key) or {})

    def max_level(self, key: str) -> int:
        return max(self.levels_for(key).values(), default=0)

    @property
    def fingerprint_digest(self) -> str | None:
        applies = any(level >= 2 for kinds in self.levels.values() for level in kinds.values())
        return self.digest if self.ai == "on" and applies else None


def _canonical(value: Any) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), default=str).encode()


def take_snapshot(db: Session, workspace_id: UUID, keys: tuple[str, ...], *, port: Any = None,
                  settings: Any = None) -> PolicySnapshot:
    """Levels of ``keys`` (Jev points) for one run. ``port`` defaults to ``semantic_port()``."""

    from app.agents.semantic.deterministic import DeterministicSemanticPort
    from app.agents.semantic.port import semantic_port

    port = port if port is not None else semantic_port(settings=settings)
    if isinstance(port, DeterministicSemanticPort):
        return PolicySnapshot.off("ai_disabled", port)
    try:
        policy_digest = effective_policy(db, workspace_id).digest
    except PolicyUnavailable:
        policy_digest = "unavailable"  # the gateway refuses each call; points record `unavailable`
    switches = effective_switches(db, workspace_id)
    levels: dict[str, dict[str, int]] = {}
    releases: dict[str, dict[str, Any]] = {}
    blocking: dict[str, str | None] = {}
    for key in keys:
        point, release = REGISTRY[key], release_for(key)
        if release is None:
            continue
        release_id = db.scalar(select(PromptRelease.id).where(
            PromptRelease.agent_key == release.agent_key, PromptRelease.version == release.version,
            PromptRelease.status == "released"))
        levels[key] = {
            kind or NO_KIND: min(release.max_level, effective_level(
                db, workspace_id, key, kind, prompt_release_id=release_id, model_id=release.model_id))
            for kind in (None, *sorted(point.answer_kinds))
        }
        releases[key] = {"prompt_release_id": str(release_id) if release_id else None,
                         "release": f"{key}@{release.version}", "model_id": release.model_id}
        blocking[key] = switches.blocking(ai_enabled=True, provider="typesafe", purpose=key)
    digest = hashlib.sha256(_canonical({
        "schema": SNAPSHOT_SCHEMA, "policy_digest": policy_digest, "levels": levels, "releases": releases,
        "switches": blocking,
    })).hexdigest()
    return PolicySnapshot(ai="on", digest=digest, levels=MappingProxyType(levels),
                          releases=MappingProxyType(releases), port=port)


__all__ = ["PolicySnapshot", "take_snapshot"]
