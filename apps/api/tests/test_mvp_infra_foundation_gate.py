"""Greenfield MVP infrastructure E2E. No MCP, agents, or notebooks.

Proves the canonical path is ready for later transports:
User → Workspace → Project → DataSource → DataAccess → ExecutionRequest →
IngestionRun → Dataset → MlJob → WorkflowRun → PipelineRun → scientific
evidence → Visualization metadata → Artifact.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import uuid4

import pytest
from sqlalchemy import func, select, text
from sqlalchemy.exc import DBAPIError, IntegrityError

from dclab_client import DCLabAPIError, DCLabClient
from app.db.models import (
    ClientLabUpload,
    Dataset,
    DatasetAsset,
    Experiment,
    ExecutionRequest,
    IngestionRun,
    MlJob,
    MlRunEvent,
    PipelineScientificPlan,
    UserRole,
    Visualization,
)
from app.domain.errors import (
    DataAccessConfigurationError,
    DataSourceConfigurationError,
    MlJobSpecError,
)
from app.domain.execution_requests import REQUEST_ACCEPTED, SOURCE_API
from app.domain.ml_jobs import JOB_QUEUED, JOB_RUNNING
from app.engine.types import TaskSpec
from app.services.artifact_service import store_artifact
from app.services.auth_service import create_access_token, create_user
from app.services.data_access_service import create_data_access
from app.services.data_source_service import create_data_source
from app.services.dataset_materialization import materialize_dataset
from app.services.execution_request_service import (
    ExecutionRequestSpecError,
    bound_request_spec,
)
from app.services.ingestion_run_service import complete_ingestion_run, start_ingestion_run
from app.services.lab_service import ingest_dataset, seed_dogfood, upsert_task
from app.services.lineage_service import (
    create_dataset_asset,
    create_pipeline_run,
    create_workflow,
    create_workflow_run,
    enable_workspace_domain,
    seed_business_domains,
)
from app.services.ml_job_service import (
    bound_job_payload,
    claim_next_queued_job,
    create_ml_job,
    recover_abandoned_jobs,
)
from app.services.observability_service import append_ml_run_event
from app.services.project_service import create_project
from app.services.visualization_service import persist_visualization
from app.services.workspace_service import create_business_workspace
from app.storage.base import ObjectMetadata, ObjectPutResult
from app.storage.local import LocalStorage


class RemoteLikeStorage:
    """Filesystem-backed store that pretends to be remote (no local_path)."""

    provider = "s3"
    bucket = "greenfield-bucket"

    def __init__(self, root: str | Path) -> None:
        self._inner = LocalStorage(root)

    def put(self, key, data, *, content_type=None, metadata=None):
        result = self._inner.put(
            key, data, content_type=content_type, metadata=metadata
        )
        return ObjectPutResult(
            key=result.key,
            size_bytes=result.size_bytes,
            content_digest=result.content_digest,
            content_type=result.content_type,
            provider=self.provider,
            bucket=self.bucket,
        )

    def get(self, key: str) -> bytes:
        raise AssertionError("materialize must stream via open(), not get()")

    def open(self, key: str):
        return self._inner.open(key)

    def exists(self, key: str) -> bool:
        return self._inner.exists(key)

    def delete(self, key: str) -> None:
        self._inner.delete(key)

    def metadata(self, key: str) -> ObjectMetadata:
        meta = self._inner.metadata(key)
        return ObjectMetadata(
            key=meta.key,
            size_bytes=meta.size_bytes,
            content_digest=meta.content_digest,
            content_type=meta.content_type,
            provider=self.provider,
            bucket=self.bucket,
        )

    def signed_url(self, key: str, *, expires_in: int = 3600) -> str:
        return self._inner.signed_url(key, expires_in=expires_in)

    def checksum(self, key: str) -> str:
        return self._inner.checksum(key)

    def local_path(self, key: str) -> str | None:
        del key
        return None


def _reject(db_session, sql: str, **params) -> None:
    with pytest.raises((DBAPIError, IntegrityError), match="foreign key constraint"):
        db_session.execute(text(sql), params)
        db_session.commit()
    db_session.rollback()


def _api(http, token: str, workspace_id) -> DCLabClient:
    return DCLabClient(
        base_url=str(http.base_url),
        token=token,
        workspace_id=workspace_id,
        http=http,
    )


def _stub_foreign_pipeline(db_session, workspace_id) -> Experiment:
    environment = seed_dogfood(db_session)
    slug = f"foreign-{uuid4().hex[:10]}"
    asset = DatasetAsset(workspace_id=workspace_id, name=slug, slug=slug)
    db_session.add(asset)
    db_session.flush()
    dataset = Dataset(
        workspace_id=workspace_id,
        dataset_asset_id=asset.id,
        environment_id=environment.id,
        name=slug,
        source_type="csv",
        location="/private/unused.csv",
        version="v1",
        content_digest="c" * 64,
        row_count=2,
        column_count=2,
    )
    db_session.add(dataset)
    db_session.flush()
    experiment = Experiment(
        workspace_id=workspace_id,
        environment_id=environment.id,
        dataset_id=dataset.id,
        status="COMPLETED",
        config={},
        result={},
    )
    db_session.add(experiment)
    db_session.flush()
    return experiment


def _assert_no_leaked_payload(payload: dict | None) -> None:
    blocked = {
        "password",
        "secret",
        "token",
        "api_key",
        "access_key",
        "credentials",
        "authorization",
        "rows",
        "records",
        "dataset_rows",
        "csv",
        "file_bytes",
        "contents",
    }
    blob = payload or {}
    assert blocked.isdisjoint(blob)
    encoded = str(blob).lower()
    assert "postgres://" not in encoded
    assert "sk-proj-" not in encoded
    assert "private-row" not in encoded


def test_greenfield_mvp_infrastructure_e2e(client, db_session, tmp_path):
    owner = create_user(
        db_session,
        email=f"greenfield-owner-{uuid4().hex}@test.invalid",
        password="test-password",
        role=UserRole.WORKSPACE_OWNER,
        full_name="Greenfield Owner",
    )
    foreign_owner = create_user(
        db_session,
        email=f"greenfield-foreign-{uuid4().hex}@test.invalid",
        password="test-password",
        role=UserRole.WORKSPACE_OWNER,
        full_name="Foreign Owner",
    )
    workspace = create_business_workspace(
        db_session, owner=owner, name="Greenfield Co", slug=f"gf-{uuid4().hex[:8]}"
    )
    foreign_workspace = create_business_workspace(
        db_session,
        owner=foreign_owner,
        name="Foreign Co",
        slug=f"fx-{uuid4().hex[:8]}",
    )
    project = create_project(
        db_session,
        actor=owner,
        workspace_id=workspace.id,
        name="Greenfield project",
        slug="greenfield-project",
    )
    foreign_project = create_project(
        db_session,
        actor=foreign_owner,
        workspace_id=foreign_workspace.id,
        name="Foreign project",
        slug="foreign-project",
    )
    db_session.commit()

    with pytest.raises(DataSourceConfigurationError, match="secret"):
        create_data_source(
            db_session,
            workspace_id=workspace.id,
            project_id=project.id,
            name="blocked",
            source_type="upload",
            provider="local",
            created_by=owner.id,
            configuration={"password": "hunter2"},
        )
    source = create_data_source(
        db_session,
        workspace_id=workspace.id,
        project_id=project.id,
        name="Canonical upload source",
        source_type="upload",
        provider="s3",
        created_by=owner.id,
        configuration={"bucket": "greenfield-bucket", "prefix": "datasets"},
        credential_reference="vault:greenfield/ingest",
    )
    with pytest.raises(DataAccessConfigurationError, match="opaque"):
        create_data_access(
            db_session,
            workspace_id=workspace.id,
            project_id=project.id,
            data_source_id=source.id,
            name="blocked access",
            access_type="upload",
            provider="local",
            execution_mode="copy",
            created_by=owner.id,
            credential_reference="postgres://user:pass@db/app",
        )
    access = create_data_access(
        db_session,
        workspace_id=workspace.id,
        project_id=project.id,
        data_source_id=source.id,
        name="Canonical upload access",
        access_type="upload",
        provider="s3",
        execution_mode="copy",
        created_by=owner.id,
        resource_locator={"bucket": "greenfield-bucket", "prefix": "datasets"},
        credential_reference="vault:greenfield/ingest",
    )
    db_session.commit()

    token = create_access_token(owner)
    api = _api(client, token, workspace.id)
    me = api.identity.me()
    assert me.id == owner.id
    assert workspace.id in {row.id for row in api.workspaces.list()}
    assert project.id in {row.id for row in api.projects.list()}

    with pytest.raises(ExecutionRequestSpecError, match="rows"):
        bound_request_spec({"rows": [{"customer": "PRIVATE-ROW"}]})
    with pytest.raises(MlJobSpecError, match="rows"):
        bound_job_payload({"rows": [{"customer": "PRIVATE-ROW"}]})

    created = api.execution_requests.create(
        project_id=project.id,
        request_spec={"filename": "leads.csv", "record_count": 2},
        idempotency_key="greenfield-once",
        request_id="greenfield-trace",
    )
    assert created.status == REQUEST_ACCEPTED
    assert created.source_surface == SOURCE_API
    assert created.pipeline_run_id is None
    replay = api.execution_requests.create(
        project_id=project.id,
        request_spec={"filename": "other.csv"},
        idempotency_key="greenfield-once",
    )
    assert replay.id == created.id
    request_row = db_session.get(ExecutionRequest, created.id)
    assert request_row is not None
    _assert_no_leaked_payload(request_row.request_spec)

    ingest = start_ingestion_run(
        db_session,
        workspace_id=workspace.id,
        project_id=project.id,
        data_source_id=source.id,
        data_access_id=access.id,
        execution_request_id=created.id,
    )
    csv_path = tmp_path / "leads.csv"
    csv_bytes = b"feature,target\n1,0\n2,1\n"
    csv_path.write_bytes(csv_bytes)
    storage = RemoteLikeStorage(tmp_path / "object-a")
    worker_tmp = tmp_path / "worker-b"
    worker_tmp.mkdir()
    dataset_artifact = store_artifact(
        db_session,
        workspace_id=workspace.id,
        project_id=project.id,
        artifact_type="dataset",
        filename="leads.csv",
        data=csv_bytes,
        storage=storage,
        created_by=owner.id,
    )
    asset = create_dataset_asset(
        db_session,
        workspace_id=workspace.id,
        project_id=project.id,
        name="Leads",
        slug="leads",
        actor=owner,
    )
    environment = seed_dogfood(db_session)
    dataset = ingest_dataset(
        db_session,
        environment=environment,
        name="Leads",
        location=str(csv_path),
        workspace_id=workspace.id,
        dataset_asset=asset,
        created_by=owner.id,
        project_id=project.id,
        ingestion_run_id=ingest.id,
        artifact_id=dataset_artifact.id,
    )
    ingest = db_session.get(IngestionRun, ingest.id)
    complete_ingestion_run(
        db_session,
        ingest,
        rows_read=2,
        rows_written=2,
        bytes_read=len(csv_bytes),
        content_digest=dataset.content_digest,
        schema_digest=dataset.schema_digest,
    )
    db_session.commit()

    materialized = None
    with materialize_dataset(
        dataset,
        db=db_session,
        storage=storage,
        work_dir=worker_tmp,
    ) as path:
        materialized = path
        assert path.is_file()
        assert path.is_relative_to(worker_tmp)
        assert path.read_bytes() == csv_bytes
    assert materialized is not None
    assert not materialized.exists()
    assert dataset_artifact.provider == "s3"

    seed_business_domains(db_session)
    domain = enable_workspace_domain(
        db_session, workspace_id=workspace.id, domain_slug="sales", actor=owner
    )
    workflow = create_workflow(
        db_session,
        workspace_id=workspace.id,
        workspace_domain=domain,
        project_id=project.id,
        name="Lead conversion",
        slug="lead-conversion",
        actor=owner,
    )
    workflow_run = create_workflow_run(
        db_session,
        workspace_id=workspace.id,
        workflow=workflow,
        requester=owner,
        trigger_type="manual",
        source_type="dataset",
    )
    task = upsert_task(
        db_session,
        environment,
        TaskSpec(
            id=f"greenfield_{uuid4().hex[:8]}",
            name="Greenfield task",
            task_type="binary",
            target="target",
            feature_groups={"base": ["feature"]},
            validation_strategy="stratified",
        ),
    )
    pipeline = create_pipeline_run(
        db_session,
        workflow_run=workflow_run,
        environment=environment,
        dataset=dataset,
        task=task,
        commit=False,
    )
    request_row = db_session.get(ExecutionRequest, created.id)
    request_row.workflow_run_id = workflow_run.id
    request_row.pipeline_run_id = pipeline.id
    scientific = PipelineScientificPlan(
        workspace_id=workspace.id,
        project_id=project.id,
        pipeline_run_id=pipeline.id,
        task_type="binary",
        holdout_strategy="random",
        holdout_test_size=0.2,
        validation_strategy="stratified_kfold",
        requested_folds=5,
        primary_metric="pr_auc",
        holdout_plan_digest="e" * 64,
        model_development_plan_digest="f" * 64,
        full_plan={"holdout": "declared"},
        locked_at=datetime.now(UTC),
    )
    db_session.add(scientific)
    db_session.flush()
    job = create_ml_job(
        db_session,
        workspace_id=workspace.id,
        project_id=project.id,
        execution_request_id=created.id,
        workflow_run_id=workflow_run.id,
        pipeline_run_id=pipeline.id,
        job_type="inspect",
        handler_key="future.inspect",
        target_id=dataset.id,
        payload={"dataset_id": str(dataset.id), "filename": "leads.csv"},
        max_attempts=2,
    )
    db_session.commit()

    for index in range(3):
        append_ml_run_event(
            db_session,
            workspace_id=workspace.id,
            workflow_run_id=workflow_run.id,
            experiment_id=pipeline.id,
            stage="training",
            event_type=f"step_{index}",
            status="completed",
            payload={"sample_rows": [{"customer": "PRIVATE-ROW"}]},
            commit=False,
        )
    plot = store_artifact(
        db_session,
        workspace_id=workspace.id,
        project_id=project.id,
        pipeline_run_id=pipeline.id,
        artifact_type="plot",
        filename="roc.png",
        data=b"\x89PNG\r\n",
        storage=storage,
    )
    chart = persist_visualization(
        db_session,
        workspace_id=workspace.id,
        project_id=project.id,
        pipeline_run_id=pipeline.id,
        visualization_type="roc_curve",
        spec={"title": "declared only"},
        image_artifact_id=plot.id,
    )
    parent_status = pipeline.status
    parent_lock = pipeline.scientific_evidence_locked_at
    child = create_pipeline_run(
        db_session,
        workflow_run=workflow_run,
        environment=environment,
        dataset=dataset,
        task=task,
        pipeline_index=1,
        commit=False,
        input_role=None,
        parent_pipeline_run_id=pipeline.id,
        branch_key="agent_followup",
        branch_reason="later agent retry",
    )
    db_session.commit()

    uploads_on_ingest = db_session.scalar(
        select(func.count(ClientLabUpload.id)).where(
            ClientLabUpload.ingestion_run_id == ingest.id
        )
    )
    assert uploads_on_ingest == 0
    assert job.upload_id is None
    assert workflow_run.source_upload_id is None
    assert dataset.ingestion_run_id == ingest.id
    assert ingest.execution_request_id == created.id
    assert ingest.data_access_id == access.id
    assert access.credential_reference == "vault:greenfield/ingest"
    assert "://" not in (access.credential_reference or "")
    _assert_no_leaked_payload(job.payload)
    events = list(
        db_session.scalars(
            select(MlRunEvent)
            .where(MlRunEvent.experiment_id == pipeline.id)
            .order_by(MlRunEvent.sequence)
        )
    )
    assert [row.sequence for row in events] == [1, 2, 3]
    for row in events:
        assert row.payload.get("sample_rows") == "[REDACTED]"
        _assert_no_leaked_payload(
            {k: v for k, v in row.payload.items() if k != "sample_rows"}
        )
    db_session.refresh(pipeline)
    assert pipeline.status == parent_status
    assert pipeline.scientific_evidence_locked_at == parent_lock
    assert child.parent_pipeline_run_id == pipeline.id
    assert child.branch_key == "agent_followup"
    assert child.id != pipeline.id
    assert chart.image_artifact_id == plot.id
    assert chart.workspace_id == workspace.id

    claimed = claim_next_queued_job(db_session, job_id=job.id)
    assert claimed is not None
    assert claimed.status == JOB_RUNNING
    now = datetime.now(UTC)
    claimed.heartbeat_at = now - timedelta(minutes=20)
    claimed.started_at = now - timedelta(minutes=20)
    claimed.lease_expires_at = now - timedelta(minutes=5)
    db_session.commit()
    recovered = recover_abandoned_jobs(
        db_session, now=now, heartbeat_timeout_seconds=60
    )
    db_session.commit()
    assert [row.id for row in recovered] == [job.id]
    db_session.refresh(job)
    assert job.status == JOB_QUEUED
    assert "abandoned" in (job.failure_reason or "")

    fetched = api.execution_requests.get(created.id)
    assert fetched.id == created.id
    assert fetched.pipeline_run_id == pipeline.id
    assert fetched.status == REQUEST_ACCEPTED
    listed_datasets = api.datasets.list()
    assert dataset.id in {row.id for row in listed_datasets}
    assert api.datasets.get(dataset.id).id == dataset.id
    build = api.model_builds.get(pipeline.id)
    assert build.pipeline_run_id == pipeline.id
    assert build.workspace_id == workspace.id
    page = api.model_builds.events(pipeline.id, limit=2)
    assert [row.sequence for row in page.items] == [1, 2]
    assert page.next_cursor == "2"
    rest = api.model_builds.events(pipeline.id, cursor=page.next_cursor)
    assert [row.sequence for row in rest.items] == [3]
    charts = api.visualizations.list(pipeline.id)
    assert {row.id for row in charts} == {chart.id}
    blobs = api.artifacts.list(pipeline.id)
    assert plot.id in {row.id for row in blobs}
    foreign_api = _api(client, create_access_token(foreign_owner), foreign_workspace.id)
    with pytest.raises(DCLabAPIError) as hidden:
        foreign_api.model_builds.get(pipeline.id)
    assert hidden.value.status_code == 404
    with pytest.raises(DCLabAPIError) as hidden_dataset:
        foreign_api.datasets.get(dataset.id)
    assert hidden_dataset.value.status_code == 404

    foreign_run = _stub_foreign_pipeline(db_session, foreign_workspace.id)
    foreign_artifact = store_artifact(
        db_session,
        workspace_id=foreign_workspace.id,
        project_id=foreign_project.id,
        pipeline_run_id=foreign_run.id,
        artifact_type="plot",
        filename="other.png",
        data=b"\x89PNG\r\n",
        storage=LocalStorage(tmp_path / "foreign-objects"),
    )
    db_session.commit()
    _reject(
        db_session,
        """
        INSERT INTO visualizations (
            id, workspace_id, pipeline_run_id, visualization_type, spec_version,
            spec, content_digest
        ) VALUES (
            gen_random_uuid(), :workspace, :pipeline_run, 'roc_curve', '1',
            '{}'::jsonb, :digest
        )
        """,
        workspace=workspace.id,
        pipeline_run=foreign_run.id,
        digest="a" * 64,
    )
    _reject(
        db_session,
        """
        INSERT INTO visualizations (
            id, workspace_id, pipeline_run_id, image_artifact_id,
            visualization_type, spec_version, spec, content_digest
        ) VALUES (
            gen_random_uuid(), :workspace, :pipeline_run, :artifact,
            'roc_curve', '1', '{}'::jsonb, :digest
        )
        """,
        workspace=workspace.id,
        pipeline_run=pipeline.id,
        artifact=foreign_artifact.id,
        digest="b" * 64,
    )
    _reject(
        db_session,
        "UPDATE experiments SET parent_pipeline_run_id = :parent WHERE id = :id",
        parent=foreign_run.id,
        id=pipeline.id,
    )
    foreign_source = create_data_source(
        db_session,
        workspace_id=foreign_workspace.id,
        project_id=foreign_project.id,
        name="Foreign source",
        source_type="upload",
        provider="local",
        created_by=foreign_owner.id,
    )
    db_session.commit()
    _reject(
        db_session,
        """
        INSERT INTO data_accesses (
            id, workspace_id, data_source_id, name, access_type, provider,
            execution_mode, created_by
        ) VALUES (
            gen_random_uuid(), :workspace, :source, 'cross', 'upload', 'local',
            'copy', :actor
        )
        """,
        workspace=workspace.id,
        source=foreign_source.id,
        actor=owner.id,
    )
    _reject(
        db_session,
        """
        INSERT INTO execution_requests (
            id, workspace_id, project_id, operation, source_surface, status,
            request_spec
        ) VALUES (
            gen_random_uuid(), :workspace, :project, 'model_build', 'api',
            'accepted', '{}'::jsonb
        )
        """,
        workspace=workspace.id,
        project=foreign_project.id,
    )
    with pytest.raises((DBAPIError, IntegrityError)):
        db_session.execute(
            text(
                """
                INSERT INTO data_accesses (
                    id, workspace_id, data_source_id, name, access_type, provider,
                    execution_mode, created_by, credential_reference
                ) VALUES (
                    gen_random_uuid(), :workspace, :source, 'raw', 'upload', 's3',
                    'copy', :actor, :credential
                )
                """
            ),
            {
                "workspace": workspace.id,
                "source": source.id,
                "actor": owner.id,
                "credential": "postgres://user:pass@db/app",
            },
        )
        db_session.commit()
    db_session.rollback()
    with pytest.raises((DBAPIError, IntegrityError)):
        db_session.execute(
            text(
                """
                INSERT INTO execution_requests (
                    id, workspace_id, project_id, operation, source_surface,
                    status, request_spec
                ) VALUES (
                    gen_random_uuid(), :workspace, :project, 'model_build', 'api',
                    'accepted', CAST(:spec AS jsonb)
                )
                """
            ),
            {
                "workspace": workspace.id,
                "project": project.id,
                "spec": '{"rows":[{"a":1}]}',
            },
        )
        db_session.commit()
    db_session.rollback()

    assert db_session.get(Visualization, chart.id) is not None
    assert db_session.get(MlJob, job.id).upload_id is None


def test_labs_remains_a_compatibility_adapter(auth_client, db_session, monkeypatch):
    monkeypatch.setattr(
        "app.services.client_lab_upload_service.enqueue_auto_train", lambda _id: None
    )
    response = auth_client.post(
        "/app/labs/uploads",
        data={"category": "Revenue"},
        files={"file": ("customers.csv", b"tenure,churn\n1,Yes\n2,No\n", "text/csv")},
    )
    assert response.status_code == 200, response.text
    body = response.json()
    upload = db_session.get(ClientLabUpload, body["id"])
    assert upload is not None
    assert upload.data_source_id is not None
    assert upload.ingestion_run_id is not None
    assert body["status"] == "queued"
    assert "execution_request_id" not in body
    assert auth_client.get("/app/labs/problems").status_code == 200
