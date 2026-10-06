"""Read model of the governance console, ``GET /v1/governance`` (P6.11-A; ADR 0009 §3, §4).

One workspace, one caller. It reads the effective policy (platform narrowed by the workspace
head), the workspace's switches, the decision-point levels with links to the stored R3 runs the
platform level cites, the spend counters, the workspace's open incidents and its recent policy,
level and switch changes. It exposes no tenant evidence beyond the caller's own workspace: no R3
report body, no ``workspace_evidence``, no other tenant's aggregates, no pseudonyms, no platform
policy document, no platform incidents and no operator identity (a platform user who changed
something in this workspace reads as ``platform_staff``). Authorization: workspace read plus an
ML-write membership, an approver membership or a platform role (viewers are refused).
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any
from uuid import UUID

from sqlalchemy import exists, select
from sqlalchemy.orm import Session, aliased

from app.agents.gateway.budget import period_start
from app.agents.governance.decision_points import REGISTRY
from app.agents.governance.incidents import open_incident_counts
from app.agents.governance.platform_default import DATA_CLASS_ORDER, ROLE_NAMES
from app.agents.governance.policy import (
    GovernanceNotFound,
    GovernanceNotPermitted,
    PolicyUnavailable,
    _accepted_head,
    _level_head,
    consent_changed,
    effective_level,
    effective_policy,
    policy_diff,
)
from app.agents.governance.switches import _heads, _held_keys, effective_switches
from app.config import get_settings
from app.db.models import (
    AiIncident,
    AiPolicy,
    AiSwitch,
    DecisionPointPolicy,
    R3Run,
    User,
    WorkspaceLlmBudget,
)
from app.domain.governance_console import (
    BudgetPeriodRead,
    DataClassesRead,
    DecisionPointLevelRead,
    EffectivePolicyRead,
    GovernanceRead,
    GovernanceViewer,
    IncidentRead,
    ModelRoleRead,
    PolicyChangeRead,
    PolicyFieldChange,
    R3EvidenceRead,
    RecentChangeRead,
    SpendRead,
    SwitchesRead,
    SwitchRead,
)
from app.services.authorization_service import (
    can_approve_ai_policy,
    can_read_workspace,
    PlatformRole,
    explicit_workspace_role,
    is_ml_write_role,
    platform_role_for,
)

CHANGES_LIMIT = 20
RECENT_LIMIT = 30
INCIDENT_LIMIT = 50
EVIDENCE_TEXT_CHARS = 200


def viewer_for(db: Session, actor: User, workspace_id: UUID) -> GovernanceViewer:
    approver = can_approve_ai_policy(db, actor, workspace_id)
    return GovernanceViewer(
        can_approve=approver,
        can_propose=is_ml_write_role(explicit_workspace_role(db, actor, workspace_id)),
        can_switch_off=approver or platform_role_for(db, actor) is PlatformRole.DCLAB_ADMIN,
    )


def authorize_console(db: Session, actor: User, workspace_id: UUID) -> GovernanceViewer:
    """Another tenant reads as absent (404); a viewer-only member is refused (403)."""

    if not can_read_workspace(db, actor, workspace_id):
        raise GovernanceNotFound(detail="workspace")
    viewer = viewer_for(db, actor, workspace_id)
    if not (viewer.can_approve or viewer.can_propose or platform_role_for(db, actor) is not None):
        raise GovernanceNotPermitted(detail="the governance console needs ML-write, owner/admin or platform access")
    return viewer


class _Actors:
    """``user:<id>`` for workspace people, ``platform_staff`` for any platform user, never a name."""

    def __init__(self, db: Session) -> None:
        self.db, self._cache = db, {}

    def ref(self, user_id: UUID | None, rule: str | None = None) -> str | None:
        if user_id is None:
            return f"rule:{rule}" if rule else None
        if user_id not in self._cache:
            user = self.db.get(User, user_id)
            staff = user is not None and platform_role_for(self.db, user) is not None
            self._cache[user_id] = "platform_staff" if staff else f"user:{user_id}"
        return self._cache[user_id]


def _scalars_only(evidence: dict[str, Any]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for key, value in list((evidence or {}).items())[:20]:
        if isinstance(value, str):
            out[str(key)[:64]] = value[:EVIDENCE_TEXT_CHARS]
        elif isinstance(value, (bool, int, float)) or value is None:
            out[str(key)[:64]] = value
        elif isinstance(value, list) and all(isinstance(v, (str, int, float, bool)) for v in value[:20]):
            out[str(key)[:64]] = [v[:EVIDENCE_TEXT_CHARS] if isinstance(v, str) else v for v in value[:20]]
    return out


# --- sections -------------------------------------------------------------------------------------


def _switches(db: Session, workspace_id: UUID, actors: _Actors, ai_enabled: bool) -> SwitchesRead:
    effective = effective_switches(db, workspace_id)
    # Platform part of the precedence only; a workspace's own off keys are listed below.
    platform_block = "setting:AI_ENABLED" if not ai_enabled else (
        "platform:global_ai" if effective.platform.get("global_ai", "off") != "on" else None)
    held = _held_keys(db, workspace_id)
    rows = sorted(_heads(db, workspace_id).values(), key=lambda row: row.switch_key)
    return SwitchesRead(platform_ai_blocking=platform_block, workspace=[
        SwitchRead(id=row.id, switch_key=row.switch_key, state="off" if row.switch_key in held else row.state,
                   held_by_incident=row.switch_key in held, reason=row.reason,
                   changed_by=actors.ref(row.changed_by_user_id, row.actor_rule) or "rule",
                   changed_at=row.created_at) for row in rows])


def _r3_links(db: Session, run_ids: set[UUID]) -> dict[UUID, R3Run]:
    if not run_ids:
        return {}
    return {row.id: row for row in db.scalars(select(R3Run).where(R3Run.id.in_(run_ids)))}


def _evidence_run_id(row: DecisionPointPolicy | None) -> UUID | None:
    for item in (row.evidence if row is not None else None) or []:
        if isinstance(item, dict) and item.get("kind") == "r3_run":
            try:
                return UUID(str(item.get("id")))
            except ValueError:
                return None
    return None


def _run_verified(row: R3Run) -> bool:
    """Non-raising ``load_run``: digests, run id, live flag and pair of the stored report match its row."""

    from app.services.r3_evaluation_service import report_digest, run_digest

    report = row.report or {}
    try:
        return bool(
            report.get("digest") == row.content_digest.strip() == report_digest(report)
            and report.get("run_digest") == row.run_digest.strip() == run_digest(report)
            and str(report.get("run_id")) == str(row.id) and bool(report.get("live")) == row.live
            and (report.get("pair") or {}).get("release") == row.pair_release
            and (report.get("pair") or {}).get("model_id") == row.model_id)
    except (KeyError, TypeError, AttributeError):
        return False


def _r3_read(db: Session, row: R3Run, key: str, *, head_pair_current: bool = True) -> tuple[R3EvidenceRead, bool]:
    """A link plus the verdicts as recorded. Verdicts show only when ``verify_platform_raise`` could still rely
    on the run: verified, live, partition ``both``, the run's own pair is the current release pair, recorded after
    the latest demotion, and no open platform incident touches the point. Otherwise no verdict at all."""

    from app.services.r3_evaluation_service import latest_demotion_at, release_pair

    report, verified = row.report or {}, _run_verified(row)
    pair = release_pair()
    run_pair_current = (row.pair_release, row.model_id) == (pair["release"], pair["model_id"])
    demoted_at = latest_demotion_at(db, key)
    current = (verified and head_pair_current and row.live and report.get("partition") in (None, "both") and run_pair_current
               and (demoted_at is None or row.recorded_at > demoted_at)
               and not open_incident_counts(db, None, key))
    point = (report.get("points") or {}).get(key) or {} if current else {}
    promotion, demotion = point.get("promotion") or {}, point.get("demotion") or {}
    return R3EvidenceRead(
        run_id=row.id, content_digest=row.content_digest.strip(), run_digest=row.run_digest.strip(),
        live=row.live, digest_verified=verified, verdict_current=bool(current and point),
        pair_release=row.pair_release, model_id=row.model_id, cases=row.cases, recorded_at=row.recorded_at,
        current_platform_level=point.get("current_platform_level"),
        promotion_allowed={str(level): bool((v or {}).get("allowed")) for level, v in promotion.items()},
        demotion_allowed=bool(demotion.get("allowed")) if demotion else None), run_pair_current


def _levels(db: Session, workspace_id: UUID) -> list[DecisionPointLevelRead]:
    from app.agents.semantic.releases import RELEASES
    from app.services.r3_evaluation_service import _max_level, _platform_level, current_pair

    heads = {key: (_level_head(db, workspace_id, key), _level_head(db, None, key)) for key in REGISTRY}
    runs = _r3_links(db, {rid for _, platform in heads.values() if (rid := _evidence_run_id(platform))})
    out = []
    for key, point in REGISTRY.items():
        workspace_head, platform_head = heads[key]
        platform_level = _platform_level(db, key)
        release_id, model_id = current_pair(db, key)
        # Jev points: the current released pair; agent points: the pair the platform head cites.
        unreleased = key in RELEASES and release_id is None  # code pins a Jev release the DB has not released
        expected = (release_id, model_id) if release_id is not None else (
            (platform_head.prompt_release_id, platform_head.model_id) if platform_head is not None else (None, None))
        run = runs.get(_evidence_run_id(platform_head) or UUID(int=0))
        head_pair_current = not unreleased and (
            platform_head is None or platform_head.level == 0 or release_id is None
            or (platform_head.prompt_release_id, platform_head.model_id) == expected)
        evidence, run_pair_current = (_r3_read(db, run, key, head_pair_current=head_pair_current)
                                      if run is not None else (None, True))
        pair_current = run_pair_current and head_pair_current
        effective = 0
        if platform_level > 0 and platform_head is not None:
            effective = min(effective_level(db, workspace_id, key, None, platform_head.prompt_release_id,
                                            platform_head.model_id), _max_level(point))
        ws_level = 0
        if workspace_head is not None and not unreleased and (workspace_head.level == 0 or (
                workspace_head.prompt_release_id, workspace_head.model_id) == expected):
            ws_level = workspace_head.level  # a head for another pair counts as L0
        out.append(DecisionPointLevelRead(
            key=key, stage=point.stage, pattern=point.pattern, ai_kind=point.ai_kind, cap=point.cap,
            workspace_level=ws_level, platform_level=platform_level, effective_level=effective,
            pair_current=pair_current,
            prompt_release_id=platform_head.prompt_release_id if platform_head is not None else None,
            model_id=platform_head.model_id if platform_head is not None else None,
            open_incidents=open_incident_counts(db, workspace_id, key), r3_evidence=evidence))
    return out


def _spend(db: Session, workspace_id: UUID, policy: Any) -> SpendRead:
    budgets, today = policy.policy.budgets, datetime.now(UTC).date()
    rows = list(db.scalars(select(WorkspaceLlmBudget).where(
        WorkspaceLlmBudget.workspace_id == workspace_id, WorkspaceLlmBudget.scope == "workspace")
        .order_by(WorkspaceLlmBudget.period)))
    periods = []
    for row in rows:
        stale = row.period in ("month", "day") and row.period_start < period_start(row.period, today)
        periods.append(BudgetPeriodRead(
            scope=row.scope, period=row.period,
            period_start=(period_start(row.period, today) if stale else row.period_start).isoformat(),
            limit_micros=min(row.limit_micros, budgets.workspace_month_micros),
            spent_micros=0 if stale else row.spent_micros, reserved_micros=row.reserved_micros,
            calls=0 if stale else row.calls, hard_stop=row.hard_stop or budgets.hard_stop,
            alert_fraction=float(row.alert_fraction), currency=row.currency.strip()))
    if not periods:  # no call yet: the counter row is created on the first reservation
        periods.append(BudgetPeriodRead(
            scope="workspace", period="month", period_start=period_start("month", today).isoformat(),
            limit_micros=budgets.workspace_month_micros, spent_micros=0, reserved_micros=0, calls=0,
            hard_stop=budgets.hard_stop, alert_fraction=budgets.alert_fraction, currency="USD"))
    return SpendRead(currency="USD", workspace=periods, per_run_limits_micros={
        "assistant_turn": budgets.assistant_turn_micros, "assistant_thread": budgets.assistant_thread_micros,
        "specialist": budgets.specialist_run_micros, "project_month": budgets.project_month_micros})


def _incidents(db: Session, workspace_id: UUID) -> list[IncidentRead]:
    rows = db.scalars(select(AiIncident).where(AiIncident.workspace_id == workspace_id, AiIncident.status == "open")
                      .order_by(AiIncident.opened_at.desc()).limit(INCIDENT_LIMIT))
    return [IncidentRead(id=r.id, kind=r.kind, subject_kind=r.subject_kind, subject_key=r.subject_key,
                         action=r.action, status=r.status, opened_at=r.opened_at,
                         evidence=_scalars_only(r.evidence)) for r in rows]


def _change(r: AiPolicy, decided: bool, actors: _Actors) -> PolicyChangeRead:
    return PolicyChangeRead(
        id=r.id, version=r.version, base_version=r.base_version, state=r.state, change_kind=r.change_kind,
        open=r.state == "proposed" and not decided, rationale=r.rationale, policy_digest=r.policy_digest.strip(),
        proposed_by=actors.ref(r.proposed_by_user_id), decided_by=actors.ref(r.decided_by_user_id),
        self_approved=r.self_approved, supersedes_id=r.supersedes_id, created_at=r.created_at)


def policy_change_read(db: Session, row: AiPolicy) -> PolicyChangeRead:
    decided = bool(db.scalar(select(exists().where(AiPolicy.supersedes_id == row.id))))
    return _change(row, decided, _Actors(db))


def switch_read(db: Session, row: AiSwitch) -> SwitchRead:
    held = row.switch_key in _held_keys(db, row.workspace_id)
    return SwitchRead(id=row.id, switch_key=row.switch_key, state="off" if held else row.state, held_by_incident=held,
                      reason=row.reason, changed_by=_Actors(db).ref(row.changed_by_user_id, row.actor_rule) or "rule",
                      changed_at=row.created_at)


def _policy_changes(db: Session, workspace_id: UUID, actors: _Actors, *, actor: User | None,
                    viewer: GovernanceViewer, effective: Any) -> list[PolicyChangeRead]:
    """Open proposals carry the proposed document and field-level diffs for owners/admins and the proposer."""

    successor = aliased(AiPolicy)
    rows = db.execute(
        select(AiPolicy, exists().where(successor.supersedes_id == AiPolicy.id))
        .where(AiPolicy.workspace_id == workspace_id).order_by(AiPolicy.version.desc()).limit(CHANGES_LIMIT)).all()
    head = _accepted_head(db, workspace_id)
    eff_doc = effective.policy.model_dump(mode="json") if effective is not None else None
    out = []
    for r, decided in rows:
        change = _change(r, decided, actors)
        if actor is not None and change.open and eff_doc is not None and (
                viewer.can_approve or r.proposed_by_user_id == actor.id):
            change = change.model_copy(update={
                "document": r.policy,
                "diff_vs_head": [PolicyFieldChange(**d) for d in policy_diff(r.policy, head.policy if head else eff_doc)],
                "diff_vs_effective": [PolicyFieldChange(**d) for d in policy_diff(r.policy, eff_doc)],
                "consent_change": consent_changed(r.policy, effective.policy)})
        out.append(change)
    return out


def _recent(db: Session, workspace_id: UUID, actors: _Actors) -> list[RecentChangeRead]:
    items: list[RecentChangeRead] = []
    for r in db.scalars(select(AiPolicy).where(AiPolicy.workspace_id == workspace_id)
                        .order_by(AiPolicy.created_at.desc()).limit(CHANGES_LIMIT)):
        items.append(RecentChangeRead(
            kind="policy", id=r.id, at=r.created_at, subject="ai_policy", state=r.state,
            detail=f"v{r.version} {r.change_kind}", rationale=r.rationale,
            actor=actors.ref(r.decided_by_user_id or r.proposed_by_user_id)))
    for r in db.scalars(select(DecisionPointPolicy).where(DecisionPointPolicy.workspace_id == workspace_id)
                        .order_by(DecisionPointPolicy.created_at.desc()).limit(CHANGES_LIMIT)):
        items.append(RecentChangeRead(
            kind="level", id=r.id, at=r.created_at, subject=r.decision_point_key, state=r.state,
            detail=f"L{r.level}", rationale=r.rationale,
            actor=actors.ref(r.decided_by_user_id or r.actor_user_id, r.actor_rule)))
    for r in db.scalars(select(AiSwitch).where(AiSwitch.workspace_id == workspace_id)
                        .order_by(AiSwitch.created_at.desc()).limit(CHANGES_LIMIT)):
        items.append(RecentChangeRead(
            kind="switch", id=r.id, at=r.created_at, subject=r.switch_key, state=r.state, detail=r.state,
            rationale=r.reason, actor=actors.ref(r.changed_by_user_id, r.actor_rule)))
    return sorted(items, key=lambda item: item.at, reverse=True)[:RECENT_LIMIT]


def agent_view(g: GovernanceRead) -> GovernanceRead:
    """The console as an agent (service token) may read it: states, ids, counts and digests; every free-text
    field (rationales, switch reasons, incident evidence) and every actor ref is dropped."""

    return g.model_copy(update={
        "switches": g.switches.model_copy(update={"workspace": [
            s.model_copy(update={"reason": "", "changed_by": "withheld"}) for s in g.switches.workspace]}),
        "open_incidents": [i.model_copy(update={"evidence": {}}) for i in g.open_incidents],
        "policy_changes": [c.model_copy(update={"rationale": "", "proposed_by": None, "decided_by": None,
                                                "document": None, "diff_vs_head": None, "diff_vs_effective": None})
                           for c in g.policy_changes],
        "recent_changes": [c.model_copy(update={"rationale": "", "actor": None}) for c in g.recent_changes]})


def console_read(db: Session, *, actor: User, workspace_id: UUID, agent: bool = False) -> GovernanceRead:
    """``agent=True`` (a service token / the MCP tool): no free text, no actor refs, no proposed documents."""

    viewer = authorize_console(db, actor, workspace_id)
    actors, ai_enabled = _Actors(db), bool(get_settings().ai_enabled)
    effective, unavailable = None, None
    try:
        effective = effective_policy(db, workspace_id)
    except PolicyUnavailable as exc:
        unavailable = exc.code
    doc = effective.policy if effective is not None else None
    view = GovernanceRead(
        workspace_id=workspace_id, viewer=viewer, ai_enabled_setting=ai_enabled, policy_unavailable=unavailable,
        policy=EffectivePolicyRead(digest=effective.digest, platform_version=effective.platform_version,
                                   workspace_version=effective.workspace_version,
                                   document=doc.model_dump(mode="json")) if effective is not None else None,
        model_allowlist=[ModelRoleRead(role=role, default=getattr(doc.models.roles, role).default,
                                       allowed=list(getattr(doc.models.roles, role).allowed),
                                       fallback=getattr(doc.models.roles, role).fallback)
                         for role in ROLE_NAMES] if doc is not None else [],
        data_classes=DataClassesRead(order=list(DATA_CLASS_ORDER), max_class=doc.data.max_class,
                                     sample_values_per_column=doc.data.sample_values_per_column,
                                     user_text_to_jev=doc.data.user_text_to_jev,
                                     share_r3_aggregates=doc.data.share_r3_aggregates) if doc is not None else None,
        switches=_switches(db, workspace_id, actors, ai_enabled), levels=_levels(db, workspace_id),
        spend=_spend(db, workspace_id, effective) if effective is not None else None,
        open_incidents=_incidents(db, workspace_id),
        policy_changes=_policy_changes(db, workspace_id, actors, actor=None if agent else actor, viewer=viewer,
                                       effective=effective),
        recent_changes=_recent(db, workspace_id, actors))
    return agent_view(view) if agent else view


__all__ = ["authorize_console", "console_read", "policy_change_read", "switch_read", "viewer_for"]
