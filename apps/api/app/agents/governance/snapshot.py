"""The AI policy snapshot a job takes at claim (ADR 0008 §2c; P6.9-A).

One run decides with one set of levels: the effective policy digest, the kill-switch
state and every level of the run's decision points (per §1b answer kind, keyed by the
pinned Jev release and model) are read once and digested into ``digest`` — the run's
``policy_digest`` (run evidence, decision records, events). Switches stay enforced
live by the gateway; the snapshot only fixes the levels. ``fingerprint_digest`` is
the part of the policy that can change an applied value: the digest when some point
may apply an AI value (level >= 2), else ``None`` — at L0/L1 the rule value is used
by construction, so the candidate fingerprint (whose contract is "equivalent
configurations fingerprint identically") stays the rule run's. At L2 the digest
enters every candidate fingerprint even when no AI value ends up applied (all answers
agree, abstain or are refused): conservative on purpose (ADR 0008 §2c — a run under a
policy that could have changed values is not the rule run's configuration); the run's
applied values are hashed in on top (``RunDecisionPoints.fingerprint_digest``). AI off
(setting, missing provider, branch run) reads nothing and has no digest.
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

from app.agents.governance.decision_points import LEGACY_PURPOSE_KEYS, REGISTRY
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
    # The legacy decision agent's points (interim AI source), keyed by its prompt release.
    legacy_levels: MappingProxyType = field(default_factory=lambda: MappingProxyType({}))
    legacy_releases: MappingProxyType = field(default_factory=lambda: MappingProxyType({}))
    port: Any = field(default=None, compare=False, repr=False)

    @classmethod
    def off(cls, reason: str, port: Any = None) -> "PolicySnapshot":
        return cls(ai="off", reason=reason, port=port)

    def levels_for(self, key: str, *, legacy: bool = False) -> dict[str, int]:
        return dict((self.legacy_levels if legacy else self.levels).get(key) or {})

    def max_level(self, key: str) -> int:
        return max(self.levels_for(key).values(), default=0)

    @property
    def fingerprint_digest(self) -> str | None:
        applies = any(level >= 2 for table in (self.levels, self.legacy_levels)
                      for kinds in table.values() for level in kinds.values())
        return self.digest if self.ai == "on" and applies else None


def _canonical(value: Any) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), default=str).encode()


def take_snapshot(db: Session, workspace_id: UUID, keys: tuple[str, ...], *, port: Any = None,
                  settings: Any = None, agent_keys: tuple[str, ...] = (), agent_release: Any = None,
                  plan: dict[str, Any] | None = None, legacy: dict[str, tuple[str, int]] | None = None,
                  ) -> PolicySnapshot:
    """Levels of ``keys`` (Jev points) and of ``agent_keys`` (the run plan's points, keyed by
    the plan's agent run (prompt release id, model id), ``agent_release``) for one run.
    ``port`` defaults to ``semantic_port()``; ``plan`` (id, status) enters the digest."""

    from app.agents.semantic.deterministic import DeterministicSemanticPort
    from app.agents.semantic.port import semantic_port

    port = port if port is not None else semantic_port(settings=settings)
    if isinstance(port, DeterministicSemanticPort) and not legacy:
        return PolicySnapshot.off("ai_disabled", port)
    if isinstance(port, DeterministicSemanticPort):
        keys = ()  # Jev is not available; only the legacy decision agent answers
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
    release_id, model_id = agent_release or (None, None)
    for key in agent_keys:
        point = REGISTRY[key]
        levels[key] = {kind or NO_KIND: effective_level(db, workspace_id, key, kind, prompt_release_id=release_id,
                                                        model_id=model_id)
                       for kind in (None, *sorted(point.answer_kinds))}
        releases[key] = {"prompt_release_id": str(release_id) if release_id else None,
                         "release": point.ai_kind, "model_id": model_id}
        blocking[key] = switches.blocking(ai_enabled=True, agent_key=point.ai_kind.split(":", 1)[-1])
    legacy_levels: dict[str, dict[str, int]] = {}
    legacy_releases: dict[str, dict[str, Any]] = {}
    if legacy:
        try:
            model_id = effective_policy(db, workspace_id).policy.models.roles.legacy_decision.default
        except PolicyUnavailable:
            model_id = None
        for key, (agent_key, version) in legacy.items():
            point = REGISTRY[key]
            release_id = db.scalar(select(PromptRelease.id).where(
                PromptRelease.agent_key == agent_key, PromptRelease.version == version,
                PromptRelease.status == "released"))
            # The legacy purpose and its registry point: either switch off keeps the source at L0.
            off = next((b for p in (*[p for p, k in LEGACY_PURPOSE_KEYS.items() if k == key], key)
                        if (b := switches.blocking(ai_enabled=True, agent_key=agent_key, purpose=p))), None)
            blocking[f"legacy:{key}"] = off
            legacy_levels[key] = {kind or NO_KIND: 0 if off else effective_level(
                db, workspace_id, key, kind, prompt_release_id=release_id, model_id=model_id)
                for kind in (None, *sorted(point.answer_kinds))}
            legacy_releases[key] = {"prompt_release_id": str(release_id) if release_id else None,
                                    "release": f"legacy:{agent_key}@v{version}", "model_id": model_id}
    digest = hashlib.sha256(_canonical({
        "schema": SNAPSHOT_SCHEMA, "policy_digest": policy_digest, "levels": levels, "releases": releases,
        "switches": blocking, **({"plan": plan} if plan else {}),
        **({"legacy_levels": legacy_levels, "legacy_releases": legacy_releases} if legacy else {}),
    })).hexdigest()
    return PolicySnapshot(ai="on", digest=digest, levels=MappingProxyType(levels),
                          releases=MappingProxyType(releases), legacy_levels=MappingProxyType(legacy_levels),
                          legacy_releases=MappingProxyType(legacy_releases), port=port)


__all__ = ["PolicySnapshot", "take_snapshot"]
