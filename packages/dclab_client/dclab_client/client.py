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
    DecisionRecordPage,
    EventPage,
    ExecutionRequest,
    ExperimentCode,
    ModelBuild,
    NodeImpact,
    Principal,
    ProblemSpec,
    Project,
    ProjectGraph,
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
        self.visualizations = VisualizationsClient(self._transport)
        self.artifacts = ArtifactsClient(self._transport)

    def close(self) -> None:
        self._transport.close()

    def __enter__(self) -> DCLabClient:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()
