"""Typed /v1 resource clients. MCP and CLI wrappers are not implemented here."""

from __future__ import annotations

from typing import Any
from uuid import UUID

import httpx

from dclab_client._http import DEFAULT_TIMEOUT_SECONDS, V1Transport
from dclab_client.types import (
    Artifact,
    Dataset,
    EventPage,
    ExecutionRequest,
    ModelBuild,
    Principal,
    Project,
    Visualization,
    Workspace,
)


def _id(value: UUID | str) -> str:
    return str(value)


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
        payload = self._transport.request(
            "GET", f"/v1/projects/{_id(project_id)}", request_id=request_id
        )
        return Project.model_validate(payload)


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
        payload = self._transport.request(
            "POST",
            "/v1/execution-requests",
            json=body,
            request_id=request_id,
            idempotency_key=idempotency_key,
        )
        return ExecutionRequest.model_validate(payload)

    def get(
        self, execution_request_id: UUID | str, *, request_id: str | None = None
    ) -> ExecutionRequest:
        payload = self._transport.request(
            "GET",
            f"/v1/execution-requests/{_id(execution_request_id)}",
            request_id=request_id,
        )
        return ExecutionRequest.model_validate(payload)


class ModelBuildsClient:
    def __init__(self, transport: V1Transport) -> None:
        self._transport = transport

    def get(
        self, pipeline_run_id: UUID | str, *, request_id: str | None = None
    ) -> ModelBuild:
        payload = self._transport.request(
            "GET",
            f"/v1/model-builds/{_id(pipeline_run_id)}",
            request_id=request_id,
        )
        return ModelBuild.model_validate(payload)

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
        self.datasets = DatasetsClient(self._transport)
        self.execution_requests = ExecutionRequestsClient(self._transport)
        self.model_builds = ModelBuildsClient(self._transport)
        self.visualizations = VisualizationsClient(self._transport)
        self.artifacts = ArtifactsClient(self._transport)

    def close(self) -> None:
        self._transport.close()

    def __enter__(self) -> DCLabClient:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()
