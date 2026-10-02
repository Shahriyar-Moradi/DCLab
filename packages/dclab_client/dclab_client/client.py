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
    Artifact,
    Dataset,
    DatasetUpload,
    DecisionRecord,
    DecisionRecordPage,
    EventPage,
    ExecutionRequest,
    Experiment,
    ExperimentCode,
    ExperimentComparison,
    ExperimentPage,
    ModelBuild,
    ModelVersion,
    NodeImpact,
    Principal,
    ProblemSpec,
    Project,
    ProjectGraph,
    ProjectRef,
    ProjectRefList,
    RefMoveResult,
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
        idempotency_key: str | None = None,
        request_id: str | None = None,
    ) -> ProblemSpec:
        """Append the next ProblemSpec version (``draft`` or ``locked``)."""

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


class ModelVersionsClient:
    def __init__(self, transport: V1Transport) -> None:
        self._transport = transport

    def get(self, model_version_id: UUID | str, *, request_id: str | None = None) -> ModelVersion:
        """Detail: locked metrics, lineage, champion flag, artifacts by id + digest."""

        payload, headers = self._transport.request_with_headers(
            "GET", f"/v1/model-versions/{_id(model_version_id)}", request_id=request_id
        )
        return _versioned(ModelVersion, payload, headers)


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

    def get(self, dataset_id: UUID | str, *, request_id: str | None = None) -> Dataset:
        payload = self._transport.request(
            "GET", f"/v1/datasets/{_id(dataset_id)}", request_id=request_id
        )
        return Dataset.model_validate(payload)

    def upload(
        self,
        project_id: UUID | str,
        file: str | Path | BinaryIO,
        *,
        filename: str | None = None,
        content_type: str | None = None,
        idempotency_key: str | None = None,
        request_id: str | None = None,
    ) -> DatasetUpload:
        """Upload a file into a project; it is ingested and published (no training).

        ``file`` is a path or a binary stream (``filename`` required for streams).
        Pass a stable ``idempotency_key`` to make a resend of the same bytes safe."""

        if isinstance(file, (str, Path)):
            path = Path(file)
            with path.open("rb") as handle:
                return self._upload(project_id, handle, filename or path.name, content_type,
                                    idempotency_key, request_id)
        if not filename:
            raise DCLabClientError("filename is required when uploading a stream")
        return self._upload(project_id, file, filename, content_type, idempotency_key, request_id)

    def _upload(
        self,
        project_id: UUID | str,
        stream: BinaryIO,
        filename: str,
        content_type: str | None,
        idempotency_key: str | None,
        request_id: str | None,
    ) -> DatasetUpload:
        part = (filename, stream, content_type) if content_type else (filename, stream)
        payload, headers = self._transport.request_with_headers(
            "POST",
            "/v1/datasets",
            data={"project_id": _id(project_id)},
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
        idempotency_key: str | None = None,
        request_id: str | None = None,
    ) -> Experiment:
        """Queue a root run on a published dataset (202; the worker trains).
        Resend with the same ``idempotency_key`` to retry safely."""

        body: dict[str, Any] = {"project_id": _id(project_id), "dataset_id": _id(dataset_id)}
        if problem_spec_id is not None:
            body["problem_spec_id"] = _id(problem_spec_id)
        if target_column is not None:
            body["target_column"] = target_column
        if intent is not None:
            body["intent"] = intent
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
        self.model_versions = ModelVersionsClient(self._transport)
        self.visualizations = VisualizationsClient(self._transport)
        self.artifacts = ArtifactsClient(self._transport)

    def close(self) -> None:
        self._transport.close()

    def __enter__(self) -> DCLabClient:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()
