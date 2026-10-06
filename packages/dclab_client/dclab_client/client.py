"""Typed /v1 resource clients. MCP and CLI wrappers are not implemented here."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Any, BinaryIO, TypeVar
from uuid import UUID

import httpx

from dclab_client._http import DEFAULT_TIMEOUT_SECONDS, V1Transport
from dclab_client._version import __version__
from dclab_client.errors import DCLabClientError
from dclab_client.types import (
    AgentRun,
    AgentRunPage,
    Artifact,
    BatchPrediction,
    Dataset,
    DatasetProfile,
    DatasetUpload,
    DatasetVersion,
    DecisionRecord,
    DecisionRecordPage,
    EventPage,
    ExecutionRequest,
    Experiment,
    ExperimentCode,
    ExperimentComparison,
    ExperimentFindings,
    ExperimentPage,
    Governance,
    GovernanceSwitch,
    ModelBuild,
    ModelCard,
    ModelVersion,
    NodeImpact,
    PolicyChange,
    Principal,
    ProblemSpec,
    Project,
    ProjectGraph,
    ProjectRef,
    ProjectRefList,
    Proposal,
    ProposalPage,
    RefMoveResult,
    Replay,
    Visualization,
    Workspace,
    _Versioned,
)

_V = TypeVar("_V", bound=_Versioned)


# Kinds accepted by GET /v1/nodes/{kind}/{id}/impact (ADR 0006 §1).
GRAPH_NODE_KINDS = frozenset(
    {"problem_spec", "dataset_version", "split_plan", "feature_recipe", "experiment", "model_version"}
)


def _id(value: UUID | str) -> str:
    """Path ids are always UUIDs; anything else never reaches a URL."""

    try:
        return str(UUID(str(value)))
    except (TypeError, ValueError) as exc:
        raise DCLabClientError("resource ids must be UUIDs") from exc


def _versioned(model: type[_V], payload: Any, headers: httpx.Headers) -> _V:
    """Validate a mutable resource and keep its ETag (for If-Match) and replay flag."""

    row = model.model_validate(payload)
    row._etag = headers.get("etag")
    row._replayed = (headers.get("idempotent-replayed") or "").lower() == "true"
    return row


# Output formats of POST /v1/model-versions/{id}/predictions.
PREDICTION_OUTPUT_FORMATS = frozenset({"csv", "parquet"})
# ``purpose`` of POST /v1/datasets: ``scoring`` uploads carry rows to score (no target).
DATASET_PURPOSES = frozenset({"training", "scoring"})


# Kinds of project refs (ADR 0006 §2).
REF_KINDS = frozenset({"problem_spec", "dataset", "split_plan", "feature_recipe", "champion_model"})


def _ref_kind(kind: str) -> str:
    if kind not in REF_KINDS:
        raise DCLabClientError("ref_kind must be one of: " + ", ".join(sorted(REF_KINDS)))
    return kind


def _evidence(refs: list[dict[str, Any]] | None) -> list[dict[str, Any]]:
    return [{**ref, "id": _id(ref["id"])} if "id" in ref else dict(ref) for ref in (refs or [])]


def _node_kind(kind: str) -> str:
    if kind not in GRAPH_NODE_KINDS:
        raise DCLabClientError(
            "kind must be one of: " + ", ".join(sorted(GRAPH_NODE_KINDS))
        )
    return kind


class IdentityClient:
    def __init__(self, transport: V1Transport) -> None:
        self._transport = transport

    def me(self, *, request_id: str | None = None) -> Principal:
        payload = self._transport.request("GET", "/v1/me", request_id=request_id)
        return Principal.model_validate(payload)


class WorkspacesClient:
    def __init__(self, transport: V1Transport) -> None:
        self._transport = transport

    def list(self, *, request_id: str | None = None) -> list[Workspace]:
        payload = self._transport.request("GET", "/v1/workspaces", request_id=request_id)
        return [Workspace.model_validate(row) for row in payload]


class ProjectsClient:
    def __init__(self, transport: V1Transport) -> None:
        self._transport = transport

    def list(self, *, request_id: str | None = None) -> list[Project]:
        payload = self._transport.request("GET", "/v1/projects", request_id=request_id)
        return [Project.model_validate(row) for row in payload]

    def get(self, project_id: UUID | str, *, request_id: str | None = None) -> Project:
        payload, headers = self._transport.request_with_headers(
            "GET", f"/v1/projects/{_id(project_id)}", request_id=request_id
        )
        return _versioned(Project, payload, headers)

    def create(
        self,
        *,
        name: str,
        slug: str | None = None,
        description: str = "",
        idempotency_key: str | None = None,
        request_id: str | None = None,
    ) -> Project:
        """Create a project (ML-write role). Resend with the same ``idempotency_key``
        to retry safely; a generated key is exposed on errors."""

        body: dict[str, Any] = {"name": name, "description": description}
        if slug is not None:
            body["slug"] = slug
        payload, headers = self._transport.request_with_headers(
            "POST", "/v1/projects", json=body, request_id=request_id, idempotency_key=idempotency_key
        )
        return _versioned(Project, payload, headers)

    def create_problem_spec(
        self,
        project_id: UUID | str,
        *,
        task_type: str,
        business_objective: str,
        target_column: str | None = None,
        prediction_unit: str | None = None,
        prediction_time_column: str | None = None,
        prediction_horizon: str | None = None,
        primary_metric: str | None = None,
        constraints: dict[str, Any] | None = None,
        success_criteria: dict[str, Any] | None = None,
        status: str = "draft",
        plan: UUID | str | None = None,
        idempotency_key: str | None = None,
        request_id: str | None = None,
    ) -> ProblemSpec:
        """Append the next ProblemSpec version (``draft`` or ``locked``). ``plan``: an
        ExperimentPlanProposal whose target and metric fill the spec (``422 plan_refused``)."""

        body = {
            "task_type": task_type,
            "business_objective": business_objective,
            "target_column": target_column,
            "prediction_unit": prediction_unit,
            "prediction_time_column": prediction_time_column,
            "prediction_horizon": prediction_horizon,
            "primary_metric": primary_metric,
            "constraints": dict(constraints or {}),
            "success_criteria": dict(success_criteria or {}),
            "status": status,
        }
        if plan is not None:  # only when given: plan-less request digests stay the same
            body["plan"] = _id(plan)
        payload, headers = self._transport.request_with_headers(
            "POST",
            f"/v1/projects/{_id(project_id)}/problem-specs",
            json=body,
            request_id=request_id,
            idempotency_key=idempotency_key,
        )
        return _versioned(ProblemSpec, payload, headers)

    def graph(
        self,
        project_id: UUID | str,
        *,
        cursor: str | None = None,
        limit: int | None = None,
        request_id: str | None = None,
    ) -> ProjectGraph:
        """ML state graph: nodes, edges, refs and computed staleness (newest experiments first)."""

        payload = self._transport.request(
            "GET",
            f"/v1/projects/{_id(project_id)}/graph",
            params={"cursor": cursor, "limit": limit},
            request_id=request_id,
        )
        return ProjectGraph.model_validate(payload)

    def decisions(
        self,
        project_id: UUID | str,
        *,
        state: str | None = None,
        effective_state: str | None = None,
        decision_type: str | None = None,
        subject_kind: str | None = None,
        subject_id: UUID | str | None = None,
        actor_kind: str | None = None,
        recorded_after: datetime | None = None,
        recorded_before: datetime | None = None,
        cursor: str | None = None,
        limit: int | None = None,
        request_id: str | None = None,
    ) -> DecisionRecordPage:
        """Append-only decision records, newest first (rationale is untrusted data)."""

        payload = self._transport.request(
            "GET",
            f"/v1/projects/{_id(project_id)}/decisions",
            params={
                "state": state,
                "effective_state": effective_state,
                "decision_type": decision_type,
                "subject_kind": subject_kind,
                "subject_id": _id(subject_id) if subject_id is not None else None,
                "actor_kind": actor_kind,
                "recorded_after": recorded_after.isoformat() if recorded_after else None,
                "recorded_before": recorded_before.isoformat() if recorded_before else None,
                "cursor": cursor,
                "limit": limit,
            },
            request_id=request_id,
        )
        return DecisionRecordPage.model_validate(payload)

    def _start_decision(
        self, project_id: UUID | str, body: dict[str, Any], idempotency_key: str | None, request_id: str | None
    ) -> DecisionRecord:
        payload, headers = self._transport.request_with_headers(
            "POST",
            f"/v1/projects/{_id(project_id)}/decisions",
            json=body,
            request_id=request_id,
            idempotency_key=idempotency_key,
        )
        return _versioned(DecisionRecord, payload, headers)

    def create_decision(
        self,
        project_id: UUID | str,
        *,
        decision_type: str,
        subject_kind: str,
        rationale: str,
        subject_id: UUID | str | None = None,
        accepted: bool = False,
        evidence_refs: list[dict[str, Any]] | None = None,
        facts: dict[str, Any] | None = None,
        details: dict[str, Any] | None = None,
        idempotency_key: str | None = None,
        request_id: str | None = None,
    ) -> DecisionRecord:
        """Propose a decision (default) or, with ``accepted=True``, record one made now.
        The actor is always the authenticated principal."""

        body: dict[str, Any] = {
            "action": "record" if accepted else "propose",
            "decision_type": decision_type,
            "subject": {"kind": subject_kind, "id": _id(subject_id) if subject_id is not None else None},
            "rationale": rationale,
            "facts": dict(facts or {}),
            "evidence_refs": _evidence(evidence_refs),
            "details": dict(details or {}),
        }
        return self._start_decision(project_id, body, idempotency_key, request_id)

    def propose_ref_move(
        self,
        project_id: UUID | str,
        *,
        moves: dict[str, UUID | str],
        rationale: str,
        evidence_refs: list[dict[str, Any]],
        facts: dict[str, Any] | None = None,
        idempotency_key: str | None = None,
        request_id: str | None = None,
    ) -> DecisionRecord:
        """Propose moving refs (``{ref_kind: target_id}``); nothing moves until ``move_ref``
        with ``proposal_id``."""

        body = {
            "action": "propose_ref_move",
            "ref_moves": [{"ref_kind": _ref_kind(kind), "target_id": _id(target)} for kind, target in moves.items()],
            "rationale": rationale,
            "facts": dict(facts or {}),
            "evidence_refs": _evidence(evidence_refs),
        }
        return self._start_decision(project_id, body, idempotency_key, request_id)

    def refs(self, project_id: UUID | str, *, request_id: str | None = None) -> ProjectRefList:
        """Current refs; each item's ``etag`` is the ``if_match`` for ``move_ref``."""

        payload = self._transport.request("GET", f"/v1/projects/{_id(project_id)}/refs", request_id=request_id)
        return ProjectRefList.model_validate(payload)

    def ref(self, project_id: UUID | str, ref_kind: str, *, request_id: str | None = None) -> ProjectRef:
        """One ref; its ``etag`` is the ``if_match`` for ``move_ref`` (``NotFoundError`` when missing)."""

        payload = self._transport.request(
            "GET", f"/v1/projects/{_id(project_id)}/refs/{_ref_kind(ref_kind)}", request_id=request_id
        )
        return ProjectRef.model_validate(payload)

    def move_ref(
        self,
        project_id: UUID | str,
        ref_kind: str,
        *,
        target_id: UUID | str,
        rationale: str,
        evidence_refs: list[dict[str, Any]],
        if_match: str | None = None,
        create: bool = False,
        companion_moves: list[dict[str, Any]] | None = None,
        proposal_id: UUID | str | None = None,
        facts: dict[str, Any] | None = None,
        idempotency_key: str | None = None,
        request_id: str | None = None,
    ) -> RefMoveResult:
        """Move a ref under one accepted decision. ``if_match``: the ref's ``etag``
        (required; ``PreconditionFailedError`` when stale); ``create=True`` instead
        creates a missing kind (``If-None-Match: *``). ``companion_moves`` items are
        ``{ref_kind, target_id, expected_version}``."""

        body: dict[str, Any] = {
            "target_id": _id(target_id),
            "rationale": rationale,
            "evidence_refs": _evidence(evidence_refs),
            "facts": dict(facts or {}),
            "companion_moves": [
                {
                    "ref_kind": _ref_kind(item["ref_kind"]),
                    "target_id": _id(item["target_id"]),
                    "expected_version": item.get("expected_version"),
                }
                for item in (companion_moves or [])
            ],
        }
        if proposal_id is not None:
            body["proposal_id"] = _id(proposal_id)
        payload, headers = self._transport.request_with_headers(
            "POST",
            f"/v1/projects/{_id(project_id)}/refs/{_ref_kind(ref_kind)}",
            json=body,
            request_id=request_id,
            idempotency_key=idempotency_key,
            if_match=if_match,
            if_none_match="*" if create else None,
        )
        return _versioned(RefMoveResult, payload, headers)


class DecisionsClient:
    """Transitions of an existing decision record; each writes a new record."""

    def __init__(self, transport: V1Transport) -> None:
        self._transport = transport

    def get(self, decision_id: UUID | str, *, request_id: str | None = None) -> DecisionRecord:
        payload, headers = self._transport.request_with_headers(
            "GET", f"/v1/decisions/{_id(decision_id)}", request_id=request_id
        )
        return _versioned(DecisionRecord, payload, headers)

    def _transition(
        self, path: str, body: dict[str, Any], idempotency_key: str | None, request_id: str | None
    ) -> DecisionRecord:
        payload, headers = self._transport.request_with_headers(
            "POST", path, json=body, request_id=request_id, idempotency_key=idempotency_key
        )
        return _versioned(DecisionRecord, payload, headers)

    def accept(
        self,
        decision_id: UUID | str,
        *,
        rationale: str,
        evidence_refs: list[dict[str, Any]] | None = None,
        idempotency_key: str | None = None,
        request_id: str | None = None,
    ) -> DecisionRecord:
        """proposed -> accepted. Ref-move proposals are accepted with ``projects.move_ref``."""

        body: dict[str, Any] = {"rationale": rationale}
        if evidence_refs is not None:
            body["evidence_refs"] = _evidence(evidence_refs)
        return self._transition(f"/v1/decisions/{_id(decision_id)}/accept", body, idempotency_key, request_id)

    def reject(
        self,
        decision_id: UUID | str,
        *,
        rationale: str,
        evidence_refs: list[dict[str, Any]] | None = None,
        idempotency_key: str | None = None,
        request_id: str | None = None,
    ) -> DecisionRecord:
        body: dict[str, Any] = {"rationale": rationale}
        if evidence_refs is not None:
            body["evidence_refs"] = _evidence(evidence_refs)
        return self._transition(f"/v1/decisions/{_id(decision_id)}/reject", body, idempotency_key, request_id)

    def supersede(
        self,
        decision_id: UUID | str,
        *,
        rationale: str,
        facts: dict[str, Any] | None = None,
        evidence_refs: list[dict[str, Any]] | None = None,
        idempotency_key: str | None = None,
        request_id: str | None = None,
    ) -> DecisionRecord:
        """Correct an accepted record (same type and subject)."""

        body: dict[str, Any] = {"rationale": rationale}
        if facts is not None:
            body["facts"] = dict(facts)
        if evidence_refs is not None:
            body["evidence_refs"] = _evidence(evidence_refs)
        return self._transition(f"/v1/decisions/{_id(decision_id)}/supersede", body, idempotency_key, request_id)


class ProposalsClient:
    """The review flow of AI proposals. Deciding is a person's act: ``accept`` / ``reject`` /
    ``revert`` need a signed-in user's credential; a service token is refused (403)."""

    def __init__(self, transport: V1Transport) -> None:
        self._transport = transport

    def list(
        self,
        *,
        project_id: UUID | str | None = None,
        run_id: UUID | str | None = None,
        level: int | None = None,
        decision_point_key: str | None = None,
        status: str | None = None,
        proposal_type: str | None = None,
        cursor: str | None = None,
        limit: int | None = None,
        request_id: str | None = None,
    ) -> ProposalPage:
        payload = self._transport.request(
            "GET",
            "/v1/proposals",
            params={
                "project_id": _id(project_id) if project_id is not None else None,
                "run_id": _id(run_id) if run_id is not None else None,
                "level": level,
                "decision_point_key": decision_point_key,
                "status": status,
                "proposal_type": proposal_type,
                "cursor": cursor,
                "limit": limit,
            },
            request_id=request_id,
        )
        return ProposalPage.model_validate(payload)

    def get(self, proposal_id: UUID | str, *, request_id: str | None = None) -> Proposal:
        payload, headers = self._transport.request_with_headers(
            "GET", f"/v1/proposals/{_id(proposal_id)}", request_id=request_id
        )
        return _versioned(Proposal, payload, headers)

    def _decide(
        self,
        path: str,
        proposal_id: UUID | str,
        rationale: str | None,
        ref_versions: dict[str, int | None] | None,
        idempotency_key: str | None,
        request_id: str | None,
    ) -> Proposal:
        body: dict[str, Any] = {}
        if rationale is not None:
            body["rationale"] = rationale
        if ref_versions is not None:
            body["ref_versions"] = dict(ref_versions)
        payload, headers = self._transport.request_with_headers(
            "POST",
            path,
            json=body,
            request_id=request_id,
            idempotency_key=idempotency_key,
        )
        return _versioned(Proposal, payload, headers)

    def accept(
        self,
        proposal_id: UUID | str,
        *,
        rationale: str | None = None,
        ref_versions: dict[str, int | None] | None = None,
        idempotency_key: str | None = None,
        request_id: str | None = None,
    ) -> Proposal:
        """Run the proposal's normal command as you and record the decision (``ref_versions``: the
        version you saw of each ref a ref move changes; ``None`` = the kind does not exist yet)."""

        return self._decide(f"/v1/proposals/{_id(proposal_id)}/accept", proposal_id, rationale, ref_versions, idempotency_key, request_id)

    def reject(
        self,
        proposal_id: UUID | str,
        *,
        rationale: str | None = None,
        idempotency_key: str | None = None,
        request_id: str | None = None,
    ) -> Proposal:
        return self._decide(f"/v1/proposals/{_id(proposal_id)}/reject", proposal_id, rationale, None, idempotency_key, request_id)

    def revert(
        self,
        proposal_id: UUID | str,
        *,
        rationale: str | None = None,
        idempotency_key: str | None = None,
        request_id: str | None = None,
    ) -> Proposal:
        """Restore the rule value of an applied L2 plan or Jev item through a branch."""

        return self._decide(f"/v1/proposals/{_id(proposal_id)}/revert", proposal_id, rationale, None, idempotency_key, request_id)


class AgentRunsClient:
    def __init__(self, transport: V1Transport) -> None:
        self._transport = transport

    def list(
        self,
        *,
        project_id: UUID | str | None = None,
        agent_key: str | None = None,
        status: str | None = None,
        cursor: str | None = None,
        limit: int | None = None,
        request_id: str | None = None,
    ) -> AgentRunPage:
        payload = self._transport.request(
            "GET",
            "/v1/agent-runs",
            params={
                "project_id": _id(project_id) if project_id is not None else None,
                "agent_key": agent_key,
                "status": status,
                "cursor": cursor,
                "limit": limit,
            },
            request_id=request_id,
        )
        return AgentRunPage.model_validate(payload)

    def get(self, run_id: UUID | str, *, request_id: str | None = None) -> AgentRun:
        payload, headers = self._transport.request_with_headers(
            "GET", f"/v1/agent-runs/{_id(run_id)}", request_id=request_id
        )
        return _versioned(AgentRun, payload, headers)

    def replay(self, run_id: UUID | str, *, idempotency_key: str | None = None,
               request_id: str | None = None) -> Replay:
        """Replay a recorded specialist / ops run against its record (a person's act: POST, CSRF for cookies,
        ``Idempotency-Key``). Lead and assistant runs are refused (409 ``not_replayable``). A mismatch opens
        one ``replay_mismatch`` incident; a run that failed the same way replays as ``same_failure``."""

        payload = self._transport.request("POST", f"/v1/agent-runs/{_id(run_id)}/replay", json={},
                                          request_id=request_id, idempotency_key=idempotency_key)
        return Replay.model_validate(payload)


class GovernanceClient:
    """The governance console. Reading needs ML-write, owner/admin or platform access; changing is a person's
    act (a service token is refused): propose a policy, an owner/admin accepts it, switches flip at once."""

    def __init__(self, transport: V1Transport) -> None:
        self._transport = transport

    def get(self, *, request_id: str | None = None) -> Governance:
        return Governance.model_validate(self._transport.request("GET", "/v1/governance", request_id=request_id))

    def propose_policy(self, policy: dict[str, Any], *, rationale: str, idempotency_key: str | None = None,
                       request_id: str | None = None) -> PolicyChange:
        """Propose a full ``AiPolicyV1`` document (it may only narrow the platform policy: 422 otherwise)."""

        payload, headers = self._transport.request_with_headers(
            "POST", "/v1/governance/policy", json={"policy": policy, "rationale": rationale}, request_id=request_id,
            idempotency_key=idempotency_key)
        return _versioned(PolicyChange, payload, headers)

    def accept_policy(self, proposal_id: UUID | str, *, policy_digest: str, acknowledge_consent_change: bool = False,
                      idempotency_key: str | None = None, request_id: str | None = None) -> PolicyChange:
        """Accept an open proposal (workspace owner/admin only; platform staff never). ``policy_digest`` is the
        digest of the proposal you reviewed (409 ``policy_digest_mismatch`` otherwise); a proposal that changes
        R3 sharing (``consent_change``) needs ``acknowledge_consent_change=True``."""

        payload, headers = self._transport.request_with_headers(
            "POST", f"/v1/governance/policy/{_id(proposal_id)}/accept",
            json={"policy_digest": policy_digest, "acknowledge_consent_change": acknowledge_consent_change},
            request_id=request_id, idempotency_key=idempotency_key)
        return _versioned(PolicyChange, payload, headers)

    def set_switch(self, switch_key: str, state: str, *, reason: str, idempotency_key: str | None = None,
                   request_id: str | None = None) -> GovernanceSwitch:
        """Flip a workspace kill switch (``all_ai``, ``agent:<key>``, ``provider:<name>``, ``purpose:<key>``)."""

        if state not in ("on", "off"):
            raise DCLabClientError("state must be 'on' or 'off'")
        payload, headers = self._transport.request_with_headers(
            "POST", "/v1/governance/switches", json={"switch_key": switch_key, "state": state, "reason": reason},
            request_id=request_id, idempotency_key=idempotency_key)
        return _versioned(GovernanceSwitch, payload, headers)


REVIEW_AGENTS = frozenset({"experiment_critic", "dataset_investigator", "experiment_planner"})


class AgentReviewsClient:
    def __init__(self, transport: V1Transport) -> None:
        self._transport = transport

    def request(
        self,
        *,
        project_id: UUID | str,
        agent: str,
        experiment_id: UUID | str | None = None,
        dataset_id: UUID | str | None = None,
        idempotency_key: str | None = None,
        request_id: str | None = None,
    ) -> AgentRun:
        """Queue a Critic (``experiment_id``) / Investigator / Planner (``dataset_id``) run; it only proposes."""

        if agent not in REVIEW_AGENTS:
            raise DCLabClientError("agent must be one of: " + ", ".join(sorted(REVIEW_AGENTS)))
        body = {
            "project_id": _id(project_id),
            "agent": agent,
            "experiment_id": _id(experiment_id) if experiment_id is not None else None,
            "dataset_id": _id(dataset_id) if dataset_id is not None else None,
        }
        payload, headers = self._transport.request_with_headers(
            "POST", "/v1/agent-reviews", json=body, request_id=request_id, idempotency_key=idempotency_key
        )
        return _versioned(AgentRun, payload, headers)


class ModelVersionsClient:
    def __init__(self, transport: V1Transport) -> None:
        self._transport = transport

    def get(self, model_version_id: UUID | str, *, request_id: str | None = None) -> ModelVersion:
        """Detail: locked metrics, lineage, champion flag, artifacts by id + digest."""

        payload, headers = self._transport.request_with_headers(
            "GET", f"/v1/model-versions/{_id(model_version_id)}", request_id=request_id
        )
        return _versioned(ModelVersion, payload, headers)

    def card(self, model_version_id: UUID | str, *, request_id: str | None = None) -> ModelCard:
        """One-page model card: metric in plain words, baseline, drivers, risks, data and
        split, LLM use, and the single final evaluation (withheld for service tokens)."""

        payload = self._transport.request(
            "GET", f"/v1/model-versions/{_id(model_version_id)}/card", request_id=request_id
        )
        return ModelCard.model_validate(payload)


class NodesClient:
    def __init__(self, transport: V1Transport) -> None:
        self._transport = transport

    def impact(
        self, kind: str, node_id: UUID | str, *, request_id: str | None = None
    ) -> NodeImpact:
        """Downstream closure of one graph node (``kind`` is a graph node kind)."""

        payload = self._transport.request(
            "GET",
            f"/v1/nodes/{_node_kind(kind)}/{_id(node_id)}/impact",
            request_id=request_id,
        )
        return NodeImpact.model_validate(payload)


class DatasetsClient:
    def __init__(self, transport: V1Transport) -> None:
        self._transport = transport

    def list(
        self, *, limit: int | None = None, request_id: str | None = None
    ) -> list[Dataset]:
        payload = self._transport.request(
            "GET",
            "/v1/datasets",
            params={"limit": limit},
            request_id=request_id,
        )
        return [Dataset.model_validate(row) for row in payload]

    def get(self, dataset_id: UUID | str, *, request_id: str | None = None) -> DatasetVersion:
        payload = self._transport.request(
            "GET", f"/v1/datasets/{_id(dataset_id)}", request_id=request_id
        )
        return DatasetVersion.model_validate(payload)

    def profile(self, dataset_id: UUID | str, *, request_id: str | None = None) -> DatasetProfile:
        """Column profile: type, rule role vs role used, missing / unique counts over the
        training rows of the current split plan only (never holdout rows), transforms, CV importance."""

        payload = self._transport.request(
            "GET", f"/v1/datasets/{_id(dataset_id)}/profile", request_id=request_id
        )
        return DatasetProfile.model_validate(payload)

    def upload(
        self,
        project_id: UUID | str,
        file: str | Path | BinaryIO,
        *,
        filename: str | None = None,
        content_type: str | None = None,
        purpose: str | None = None,
        idempotency_key: str | None = None,
        request_id: str | None = None,
    ) -> DatasetUpload:
        """Upload a file into a project; it is ingested and published (no training).

        ``file`` is a path or a binary stream (``filename`` required for streams).
        ``purpose="scoring"`` marks rows to score with ``predictions.create`` (no target;
        never a run source); the default is ``training``.
        Pass a stable ``idempotency_key`` to make a resend of the same bytes safe."""

        if purpose is not None and purpose not in DATASET_PURPOSES:
            raise DCLabClientError("purpose must be one of: " + ", ".join(sorted(DATASET_PURPOSES)))
        if isinstance(file, (str, Path)):
            path = Path(file)
            with path.open("rb") as handle:
                return self._upload(project_id, handle, filename or path.name, content_type, purpose,
                                    idempotency_key, request_id)
        if not filename:
            raise DCLabClientError("filename is required when uploading a stream")
        return self._upload(project_id, file, filename, content_type, purpose, idempotency_key, request_id)

    def _upload(
        self,
        project_id: UUID | str,
        stream: BinaryIO,
        filename: str,
        content_type: str | None,
        purpose: str | None,
        idempotency_key: str | None,
        request_id: str | None,
    ) -> DatasetUpload:
        part = (filename, stream, content_type) if content_type else (filename, stream)
        form = {"project_id": _id(project_id)}
        if purpose is not None:
            form["purpose"] = purpose
        payload, headers = self._transport.request_with_headers(
            "POST",
            "/v1/datasets",
            data=form,
            files={"file": part},
            request_id=request_id,
            idempotency_key=idempotency_key,
        )
        return _versioned(DatasetUpload, payload, headers)


class ExecutionRequestsClient:
    def __init__(self, transport: V1Transport) -> None:
        self._transport = transport

    def create(
        self,
        *,
        operation: str = "model_build",
        request_spec: dict[str, Any] | None = None,
        project_id: UUID | str | None = None,
        parent_request_id: UUID | str | None = None,
        idempotency_key: str | None = None,
        external_request_id: str | None = None,
        request_id: str | None = None,
    ) -> ExecutionRequest:
        """Record intent. Without ``idempotency_key`` a fresh key is generated and
        sent as the ``Idempotency-Key`` header (exposed on errors for safe resend);
        a reused key with a different request raises ``IdempotencyConflictError``."""

        body: dict[str, Any] = {
            "operation": operation,
            "request_spec": dict(request_spec or {}),
        }
        if project_id is not None:
            body["project_id"] = _id(project_id)
        if parent_request_id is not None:
            body["parent_request_id"] = _id(parent_request_id)
        if idempotency_key:
            body["idempotency_key"] = idempotency_key
        if external_request_id:
            body["external_request_id"] = external_request_id
        payload, headers = self._transport.request_with_headers(
            "POST",
            "/v1/execution-requests",
            json=body,
            request_id=request_id,
            idempotency_key=idempotency_key,
        )
        return _versioned(ExecutionRequest, payload, headers)

    def confirm_target(
        self,
        execution_request_id: UUID | str,
        *,
        target_column: str,
        request_id: str | None = None,
        if_match: str | None = None,
        idempotency_key: str | None = None,
    ) -> ExecutionRequest:
        """``if_match``: the ``etag`` of a previous read; raises ``PreconditionFailedError``
        when the request changed since (unless this exact confirmation already applied)."""

        payload, headers = self._transport.request_with_headers(
            "POST",
            f"/v1/execution-requests/{_id(execution_request_id)}/target-confirmation",
            json={"target_column": target_column},
            request_id=request_id,
            idempotency_key=idempotency_key,
            if_match=if_match,
        )
        return _versioned(ExecutionRequest, payload, headers)

    def keep_rule_split(
        self,
        execution_request_id: UUID | str,
        *,
        request_id: str | None = None,
        if_match: str | None = None,
        idempotency_key: str | None = None,
    ) -> ExecutionRequest:
        """Answer ``split_confirmation_required``: keep the rule's split and resume the run
        (the run plan's split is refused). A person's answer: service tokens are refused."""

        payload, headers = self._transport.request_with_headers(
            "POST",
            f"/v1/execution-requests/{_id(execution_request_id)}/split-confirmation",
            json={"answer": "keep_rule_split"},
            request_id=request_id,
            idempotency_key=idempotency_key,
            if_match=if_match,
        )
        return _versioned(ExecutionRequest, payload, headers)

    def get(
        self, execution_request_id: UUID | str, *, request_id: str | None = None
    ) -> ExecutionRequest:
        payload, headers = self._transport.request_with_headers(
            "GET",
            f"/v1/execution-requests/{_id(execution_request_id)}",
            request_id=request_id,
        )
        return _versioned(ExecutionRequest, payload, headers)


class ModelBuildsClient:
    def __init__(self, transport: V1Transport) -> None:
        self._transport = transport

    def get(
        self, pipeline_run_id: UUID | str, *, request_id: str | None = None
    ) -> ModelBuild:
        payload, headers = self._transport.request_with_headers(
            "GET",
            f"/v1/model-builds/{_id(pipeline_run_id)}",
            request_id=request_id,
        )
        return _versioned(ModelBuild, payload, headers)

    def events(
        self,
        pipeline_run_id: UUID | str,
        *,
        cursor: str | None = None,
        limit: int | None = None,
        request_id: str | None = None,
    ) -> EventPage:
        payload = self._transport.request(
            "GET",
            f"/v1/model-builds/{_id(pipeline_run_id)}/events",
            params={"cursor": cursor, "limit": limit},
            request_id=request_id,
        )
        return EventPage.model_validate(payload)


class ExperimentsClient:
    def __init__(self, transport: V1Transport) -> None:
        self._transport = transport

    def list(
        self,
        *,
        project_id: UUID | str | None = None,
        status: str | None = None,
        parent_id: UUID | str | None = None,
        split_plan_id: UUID | str | None = None,
        created_after: datetime | None = None,
        created_before: datetime | None = None,
        has_change_set: bool | None = None,
        cursor: str | None = None,
        limit: int | None = None,
        request_id: str | None = None,
    ) -> ExperimentPage:
        """Experiments of the workspace, newest first (``next_cursor`` pages)."""

        payload = self._transport.request(
            "GET",
            "/v1/experiments",
            params={
                "project_id": _id(project_id) if project_id is not None else None,
                "status": status,
                "parent_id": _id(parent_id) if parent_id is not None else None,
                "split_plan_id": _id(split_plan_id) if split_plan_id is not None else None,
                "created_after": created_after.isoformat() if created_after else None,
                "created_before": created_before.isoformat() if created_before else None,
                "has_change_set": None if has_change_set is None else str(has_change_set).lower(),
                "cursor": cursor,
                "limit": limit,
            },
            request_id=request_id,
        )
        return ExperimentPage.model_validate(payload)

    def get(self, experiment_id: UUID | str, *, request_id: str | None = None) -> Experiment:
        payload, headers = self._transport.request_with_headers(
            "GET", f"/v1/experiments/{_id(experiment_id)}", request_id=request_id
        )
        return _versioned(Experiment, payload, headers)

    def create(
        self,
        *,
        project_id: UUID | str,
        dataset_id: UUID | str,
        problem_spec_id: UUID | str | None = None,
        target_column: str | None = None,
        intent: str | None = None,
        plan: UUID | str | None = None,
        idempotency_key: str | None = None,
        request_id: str | None = None,
    ) -> Experiment:
        """Queue a root run on a published dataset (202; the worker trains).
        ``plan``: an accepted/applied ExperimentPlanProposal id (single use).
        Resend with the same ``idempotency_key`` to retry safely."""

        body: dict[str, Any] = {"project_id": _id(project_id), "dataset_id": _id(dataset_id)}
        if problem_spec_id is not None:
            body["problem_spec_id"] = _id(problem_spec_id)
        if target_column is not None:
            body["target_column"] = target_column
        if intent is not None:
            body["intent"] = intent
        if plan is not None:
            body["plan"] = _id(plan)
        payload, headers = self._transport.request_with_headers(
            "POST", "/v1/experiments", json=body, request_id=request_id, idempotency_key=idempotency_key
        )
        return _versioned(Experiment, payload, headers)

    def branch(
        self,
        experiment_id: UUID | str,
        *,
        changes: list[dict[str, Any]],
        intent: str,
        idempotency_key: str | None = None,
        request_id: str | None = None,
    ) -> Experiment:
        """Branch a completed experiment with a typed change set (same split plan)."""

        payload, headers = self._transport.request_with_headers(
            "POST",
            f"/v1/experiments/{_id(experiment_id)}/branches",
            json={"intent": intent, "changes": list(changes)},
            request_id=request_id,
            idempotency_key=idempotency_key,
        )
        return _versioned(Experiment, payload, headers)

    def compare(
        self, experiment_ids: list[UUID | str], *, request_id: str | None = None
    ) -> ExperimentComparison:
        """Side-by-side metrics of 2-10 experiments; ``ConflictError`` (``split_plan_mismatch``)
        unless they share one split plan."""

        payload = self._transport.request(
            "GET",
            "/v1/experiments/compare",
            params={"ids": ",".join(_id(item) for item in experiment_ids)},
            request_id=request_id,
        )
        return ExperimentComparison.model_validate(payload)

    def cancel(
        self,
        experiment_id: UUID | str,
        *,
        if_match: str | None = None,
        idempotency_key: str | None = None,
        request_id: str | None = None,
    ) -> Experiment:
        """Cancel a queued run (``cancelled``) or ask a running one to stop
        (``cancelling``). Repeating it returns the same result."""

        payload, headers = self._transport.request_with_headers(
            "POST",
            f"/v1/experiments/{_id(experiment_id)}/cancel",
            request_id=request_id,
            idempotency_key=idempotency_key,
            if_match=if_match,
        )
        return _versioned(Experiment, payload, headers)

    def code(
        self, experiment_id: UUID | str, *, request_id: str | None = None
    ) -> ExperimentCode:
        """Standalone reproduction script/notebook (stored split map, branch changes)."""

        payload = self._transport.request(
            "GET",
            f"/v1/experiments/{_id(experiment_id)}/code",
            request_id=request_id,
        )
        return ExperimentCode.model_validate(payload)

    def findings(
        self, experiment_id: UUID | str, *, request_id: str | None = None
    ) -> ExperimentFindings:
        """The five trust checks of a run (leakage, overfit gap, duplicates, class
        imbalance, too-good-to-be-true score) with plain-language messages."""

        payload = self._transport.request(
            "GET",
            f"/v1/experiments/{_id(experiment_id)}/findings",
            request_id=request_id,
        )
        return ExperimentFindings.model_validate(payload)


class PredictionsClient:
    """Batch predictions (P4.9-A): score a dataset with a model version (the worker scores)."""

    def __init__(self, transport: V1Transport) -> None:
        self._transport = transport

    def create(
        self,
        *,
        model_version_id: UUID | str,
        dataset_id: UUID | str,
        output_format: str = "csv",
        idempotency_key: str | None = None,
        request_id: str | None = None,
    ) -> BatchPrediction:
        """Queue scoring of a dataset of the model's project (202; poll ``get``).
        Resend with the same ``idempotency_key`` to retry safely (one is generated
        when omitted and exposed on errors)."""

        if output_format not in PREDICTION_OUTPUT_FORMATS:
            raise DCLabClientError("output_format must be one of: " + ", ".join(sorted(PREDICTION_OUTPUT_FORMATS)))
        payload, headers = self._transport.request_with_headers(
            "POST",
            f"/v1/model-versions/{_id(model_version_id)}/predictions",
            json={"dataset_id": _id(dataset_id), "output_format": output_format},
            request_id=request_id,
            idempotency_key=idempotency_key,
        )
        return _versioned(BatchPrediction, payload, headers)

    def get(self, prediction_id: UUID | str, *, request_id: str | None = None) -> BatchPrediction:
        """Status, row counts, the feature-contract result and the output file (by digest)."""

        payload, headers = self._transport.request_with_headers(
            "GET", f"/v1/predictions/{_id(prediction_id)}", request_id=request_id
        )
        return _versioned(BatchPrediction, payload, headers)

    def download(
        self, prediction_id: UUID | str, *, to: str | Path | None = None, request_id: str | None = None
    ) -> bytes:
        """The completed predictions file (CSV or Parquet bytes); also written to ``to``
        when given. Not completed: ``ConflictError`` (``prediction_not_ready``)."""

        content, _headers = self._transport.request_bytes(
            f"/v1/predictions/{_id(prediction_id)}/download", request_id=request_id
        )
        if to is not None:
            Path(to).write_bytes(content)
        return content


class VisualizationsClient:
    def __init__(self, transport: V1Transport) -> None:
        self._transport = transport

    def list(
        self, pipeline_run_id: UUID | str, *, request_id: str | None = None
    ) -> list[Visualization]:
        payload = self._transport.request(
            "GET",
            f"/v1/model-builds/{_id(pipeline_run_id)}/visualizations",
            request_id=request_id,
        )
        return [Visualization.model_validate(row) for row in payload]


class ArtifactsClient:
    def __init__(self, transport: V1Transport) -> None:
        self._transport = transport

    def list(
        self, pipeline_run_id: UUID | str, *, request_id: str | None = None
    ) -> list[Artifact]:
        payload = self._transport.request(
            "GET",
            f"/v1/model-builds/{_id(pipeline_run_id)}/artifacts",
            request_id=request_id,
        )
        return [Artifact.model_validate(row) for row in payload]


class DCLabClient:
    """Synchronous HTTP client for the stable /v1 application boundary.

    ``token`` is a bearer credential: a service token (``dclab_st_...``, created in
    Studio settings; acts only in its workspace and scopes, so ``workspace_id`` may
    be omitted) or a user access token (``workspace_id`` required).

    Future MCP and CLI entrypoints should wrap this type. They are not
    implemented in this package.
    """

    version = __version__

    def __init__(
        self,
        base_url: str,
        *,
        token: str | None = None,
        workspace_id: UUID | str | None = None,
        timeout: float = DEFAULT_TIMEOUT_SECONDS,
        request_id: str | None = None,
        idempotency_key: str | None = None,
        http: httpx.Client | None = None,
    ) -> None:
        self._transport = V1Transport(
            base_url=base_url,
            token=token,
            workspace_id=workspace_id,
            timeout=timeout,
            request_id=request_id,
            idempotency_key=idempotency_key,
            http=http,
        )
        self.identity = IdentityClient(self._transport)
        self.workspaces = WorkspacesClient(self._transport)
        self.projects = ProjectsClient(self._transport)
        self.nodes = NodesClient(self._transport)
        self.datasets = DatasetsClient(self._transport)
        self.execution_requests = ExecutionRequestsClient(self._transport)
        self.model_builds = ModelBuildsClient(self._transport)
        self.experiments = ExperimentsClient(self._transport)
        self.decisions = DecisionsClient(self._transport)
        self.proposals = ProposalsClient(self._transport)
        self.agent_runs = AgentRunsClient(self._transport)
        self.agent_reviews = AgentReviewsClient(self._transport)
        self.governance = GovernanceClient(self._transport)
        self.model_versions = ModelVersionsClient(self._transport)
        self.predictions = PredictionsClient(self._transport)
        self.visualizations = VisualizationsClient(self._transport)
        self.artifacts = ArtifactsClient(self._transport)

    def close(self) -> None:
        self._transport.close()

    def __enter__(self) -> DCLabClient:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()
