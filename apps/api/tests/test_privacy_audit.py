"""Column policy metadata and append-only data_access_events. Not a classifier."""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID, uuid4

import pytest
from sqlalchemy import select, text
from sqlalchemy.exc import DBAPIError, IntegrityError

from app.db.models import (
    ClientLabUpload,
    DataAccessEvent,
    DatasetPolicyRevision,
    DatasetColumn,
    IngestionRun,
    UserRole,
    Workspace,
    WorkspaceMembership,
)
from app.domain.errors import DataAccessEventSpecError, IdentityError
from app.domain.privacy_audit import (
    ACTOR_USER,
    EVENT_COMPLETED,
    OPERATION_COPY,
    PURPOSE_INGEST,
    SUMMARY_MAX_BYTES,
)
from app.services.auth_service import create_user
from app.services.data_access_event_service import (
    append_data_access_event,
    bound_column_summary,
    bound_resource_summary,
)
from app.services.data_access_service import create_data_access
from app.services.data_source_service import create_data_source
from app.services.dataset_column_service import (
    publish_dataset_policy_defaults,
    resolve_dataset_policy,
    set_dataset_column_policy,
)
from app.services.ingestion_run_service import start_ingestion_run
from app.services.project_service import create_project
from app.translation.banned_terms import find_banned_terms


def _user(db, *, workspace_id, prefix: str = "audit"):
    return create_user(
        db,
        email=f"{prefix}-{uuid4().hex}@test.invalid",
        password="test-password",
        role=UserRole.WORKSPACE_OWNER,
        full_name=prefix,
        workspace_id=workspace_id,
    )


def _workspace(db, name: str) -> Workspace:
    row = Workspace(slug=f"{name}-{uuid4().hex[:10]}", name=name)
    db.add(row)
    db.flush()
    return row


def _access_path(db, workspace, actor, prefix: str):
    project = create_project(
        db,
        actor=actor,
        workspace_id=workspace.id,
        name=f"{prefix} case",
        slug=f"{prefix}-case",
    )
    source = create_data_source(
        db,
        workspace_id=workspace.id,
        project_id=project.id,
        name=f"{prefix} files",
        source_type="upload",
        provider="local",
        created_by=actor.id,
    )
    access = create_data_access(
        db,
        workspace_id=workspace.id,
        project_id=project.id,
        data_source_id=source.id,
        name=f"{prefix} access",
        access_type="upload",
        provider="local",
        execution_mode="copy",
        created_by=actor.id,
    )
    run = start_ingestion_run(
        db,
        workspace_id=workspace.id,
        project_id=project.id,
        data_source_id=source.id,
        data_access_id=access.id,
    )
    return project, source, access, run


def test_bound_summaries_reject_raw_rows_and_credentials():
    with pytest.raises(DataAccessEventSpecError, match="rows"):
        bound_column_summary({"rows": [{"a": 1}]})
    with pytest.raises(DataAccessEventSpecError, match="password"):
        bound_resource_summary({"password": "n"})
    with pytest.raises(DataAccessEventSpecError, match="unsupported"):
        bound_resource_summary({"connection_string": "postgres://x"})


def test_labs_upload_leaves_columns_unclassified_and_records_copy_event(
    auth_client, db_session, monkeypatch
):
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
    assert "data_access_event_id" not in body
    assert find_banned_terms(response.text) == []
    upload = db_session.get(ClientLabUpload, UUID(body["id"]))
    columns = (
        db_session.query(DatasetColumn)
        .filter(DatasetColumn.dataset_id == upload.dataset_id)
        .order_by(DatasetColumn.ordinal_position)
        .all()
    )
    assert [column.name for column in columns] == ["tenure", "churn"]
    # ADR 0005: the upload carries the conservative internal_training labels,
    # declared as policy defaults rather than a classification.
    assert all(column.sensitivity_class == "restricted" for column in columns)
    assert all(column.classification_source == "policy" for column in columns)
    assert all(column.model_use_policy == "allow" for column in columns)
    assert all(column.llm_exposure_policy == "deny" for column in columns)

    tenure = columns[0]
    labeled = set_dataset_column_policy(
        db_session,
        workspace_id=upload.workspace_id,
        column_id=tenure.id,
        sensitivity_class="identifier",
        classification_source="manual",
        model_use_policy="deny",
        llm_exposure_policy="deny",
    )
    db_session.commit()
    db_session.refresh(labeled)
    assert labeled.sensitivity_class == "identifier"
    assert labeled.classification_source == "manual"

    ingestion = db_session.get(IngestionRun, upload.ingestion_run_id)
    event = db_session.scalar(
        select(DataAccessEvent).where(DataAccessEvent.ingestion_run_id == ingestion.id)
    )
    assert event is not None
    assert event.actor_type == ACTOR_USER
    assert event.purpose == PURPOSE_INGEST
    assert event.operation == OPERATION_COPY
    assert event.status == EVENT_COMPLETED
    assert event.data_access_id == ingestion.data_access_id
    assert event.execution_request_id == ingestion.execution_request_id
    assert "rows" not in event.resource_summary
    assert "password" not in event.resource_summary
    assert event.column_summary["column_names"] == ["tenure", "churn"]
    assert event.rows_read == 2

    with pytest.raises(IntegrityError):
        db_session.execute(
            text(
                "UPDATE dataset_columns SET sensitivity_class = 'secret' WHERE id = :id"
            ),
            {"id": tenure.id},
        )
        db_session.commit()
    db_session.rollback()


def test_dataset_policy_defaults_are_versioned_and_null_columns_stay_denied(
    auth_client, db_session, client_user, monkeypatch
):
    monkeypatch.setattr(
        "app.services.client_lab_upload_service.enqueue_auto_train", lambda _id: None
    )
    # Resolver semantics on an unclassified dataset: skip ADR 0005 auto-publication.
    monkeypatch.setattr(
        "app.services.client_lab_upload_service.publish_upload_for_internal_training",
        lambda *_args, **_kwargs: None,
    )
    response = auth_client.post(
        "/app/labs/uploads",
        data={"category": "Revenue"},
        files={"file": ("policy.csv", b"tenure,churn\n1,Yes\n2,No\n", "text/csv")},
    )
    assert response.status_code == 200, response.text
    upload = db_session.get(ClientLabUpload, UUID(response.json()["id"]))
    denied = resolve_dataset_policy(
        db_session, actor=client_user, workspace_id=upload.workspace_id,
        dataset_id=upload.dataset_id,
    )
    assert denied.dataset_policy_revision is None
    assert denied.llm_exposure_policy == "deny"
    assert not denied.complete

    first = publish_dataset_policy_defaults(
        db_session, actor=client_user, workspace_id=upload.workspace_id,
        dataset_id=upload.dataset_id, expected_revision=0,
        sensitivity_class="internal", llm_exposure_policy="allow",
        retention_class="standard", residency_class="home_cloud_only",
        classification_source="manual", classification_confidence=1.0,
    )
    assert first.revision == 1
    still_denied = resolve_dataset_policy(
        db_session, actor=client_user, workspace_id=upload.workspace_id,
        dataset_id=upload.dataset_id,
    )
    assert still_denied.llm_exposure_policy == "deny"
    assert not still_denied.complete

    columns = db_session.scalars(
        select(DatasetColumn).where(DatasetColumn.dataset_id == upload.dataset_id)
        .order_by(DatasetColumn.ordinal_position)
    ).all()
    for column, sensitivity, exposure, residency in (
        (columns[0], "public", "allow", "home_cloud_only"),
        (columns[1], "pii", "metadata_only", "home_region_only"),
    ):
        set_dataset_column_policy(
            db_session, workspace_id=upload.workspace_id, column_id=column.id,
            sensitivity_class=sensitivity, classification_source="manual",
            model_use_policy="deny", llm_exposure_policy=exposure,
            retention_class="standard", residency_class=residency,
            classification_confidence=1.0,
        )
    effective = resolve_dataset_policy(
        db_session, actor=client_user, workspace_id=upload.workspace_id,
        dataset_id=upload.dataset_id,
    )
    assert effective.complete
    assert effective.sensitivity_class == "pii"
    assert effective.llm_exposure_policy == "metadata_only"
    assert effective.residency_class == "home_region_only"
    assert [item.sensitivity_class for item in effective.columns] == ["internal", "pii"]

    set_dataset_column_policy(
        db_session, workspace_id=upload.workspace_id, column_id=columns[1].id,
        sensitivity_class="pii", classification_source="manual",
        model_use_policy="deny", llm_exposure_policy="metadata_only",
        retention_class="short", residency_class="home_region_only",
        classification_confidence=1.0,
    )
    retention_conflict = resolve_dataset_policy(
        db_session, actor=client_user, workspace_id=upload.workspace_id,
        dataset_id=upload.dataset_id,
    )
    assert not retention_conflict.complete
    assert retention_conflict.llm_exposure_policy == "deny"
    set_dataset_column_policy(
        db_session, workspace_id=upload.workspace_id, column_id=columns[1].id,
        sensitivity_class="pii", classification_source="manual",
        model_use_policy="deny", llm_exposure_policy="metadata_only",
        retention_class="standard", residency_class="home_region_only",
        classification_confidence=1.0,
    )

    with pytest.raises(IdentityError) as conflict:
        publish_dataset_policy_defaults(
            db_session, actor=client_user, workspace_id=upload.workspace_id,
            dataset_id=upload.dataset_id, expected_revision=0,
            sensitivity_class="public", llm_exposure_policy="allow",
            retention_class="standard", residency_class="home_cloud_only",
            classification_source="manual", classification_confidence=1.0,
        )
    assert conflict.value.status_code == 409
    second = publish_dataset_policy_defaults(
        db_session, actor=client_user, workspace_id=upload.workspace_id,
        dataset_id=upload.dataset_id, expected_revision=1,
        sensitivity_class="restricted", llm_exposure_policy="deny",
        retention_class="standard", residency_class="home_region_only",
        classification_source="policy", classification_confidence=1.0,
    )
    assert second.revision == 2
    assert resolve_dataset_policy(
        db_session, actor=client_user, workspace_id=upload.workspace_id,
        dataset_id=upload.dataset_id,
    ).llm_exposure_policy == "deny"
    db_session.commit()
    with pytest.raises(DBAPIError):
        db_session.execute(
            text("UPDATE dataset_policy_revisions SET llm_exposure_policy = 'allow' WHERE id = :id"),
            {"id": second.id},
        )
        db_session.commit()
    db_session.rollback()
    membership = db_session.scalar(
        select(WorkspaceMembership).where(
            WorkspaceMembership.workspace_id == upload.workspace_id,
            WorkspaceMembership.user_id == client_user.id,
        )
    )
    assert membership is not None
    membership.suspended_at = datetime.now(UTC)
    db_session.flush()
    with pytest.raises(IdentityError) as suspended:
        resolve_dataset_policy(
            db_session, actor=client_user, workspace_id=upload.workspace_id,
            dataset_id=upload.dataset_id,
        )
    assert suspended.value.status_code == 403
    with pytest.raises(IdentityError) as suspended_write:
        publish_dataset_policy_defaults(
            db_session, actor=client_user, workspace_id=upload.workspace_id,
            dataset_id=upload.dataset_id, expected_revision=2,
            sensitivity_class="public", llm_exposure_policy="allow",
            retention_class="standard", residency_class="home_region_only",
            classification_source="manual", classification_confidence=1.0,
        )
    assert suspended_write.value.status_code == 403


def test_dataset_policy_rejects_foreign_lineage_and_bad_labels(
    auth_client, db_session, client_user, admin_user, monkeypatch
):
    monkeypatch.setattr(
        "app.services.client_lab_upload_service.enqueue_auto_train", lambda _id: None
    )
    response = auth_client.post(
        "/app/labs/uploads", data={"category": "Revenue"},
        files={"file": ("foreign.csv", b"x,y\n1,2\n", "text/csv")},
    )
    assert response.status_code == 200, response.text
    upload = db_session.get(ClientLabUpload, UUID(response.json()["id"]))
    foreign = _workspace(db_session, "foreign-policy")
    with pytest.raises(IdentityError) as denied:
        resolve_dataset_policy(
            db_session, actor=client_user, workspace_id=foreign.id,
            dataset_id=upload.dataset_id,
        )
    assert denied.value.status_code == 403
    with pytest.raises(IdentityError) as foreign_missing:
        resolve_dataset_policy(
            db_session, actor=admin_user, workspace_id=foreign.id,
            dataset_id=upload.dataset_id,
        )
    assert foreign_missing.value.status_code == 404
    with pytest.raises(IdentityError) as missing:
        resolve_dataset_policy(
            db_session, actor=client_user, workspace_id=upload.workspace_id,
            dataset_id=uuid4(),
        )
    assert missing.value.status_code == 404
    with pytest.raises(IdentityError) as invalid:
        publish_dataset_policy_defaults(
            db_session, actor=client_user, workspace_id=upload.workspace_id,
            dataset_id=upload.dataset_id, expected_revision=0,
            sensitivity_class="secret", llm_exposure_policy="allow",
            retention_class="standard", residency_class="home_cloud_only",
            classification_source="manual", classification_confidence=1.0,
        )
    assert invalid.value.status_code == 400
    with pytest.raises(IntegrityError):
        db_session.add(DatasetPolicyRevision(
            workspace_id=foreign.id, dataset_id=upload.dataset_id, revision=1,
            policy_schema_version=1, sensitivity_class="public",
            llm_exposure_policy="deny", retention_class="standard",
            residency_class="home_region_only", classification_source="manual",
            classification_confidence=1.0, created_by_user_id=client_user.id,
        ))
        db_session.flush()
    db_session.rollback()


def test_postgres_rejects_raw_rows_and_oversized_event_summaries(db_session):
    workspace = _workspace(db_session, "ck")
    actor = _user(db_session, workspace_id=workspace.id)
    _project, _source, access, run = _access_path(db_session, workspace, actor, "ck")
    db_session.commit()
    with pytest.raises(IntegrityError):
        db_session.execute(
            text(
                """
                INSERT INTO data_access_events (
                    id, workspace_id, data_access_id, actor_type, purpose, operation,
                    resource_summary, status, started_at, completed_at
                ) VALUES (
                    gen_random_uuid(), :workspace, :access, 'user', 'ingest', 'copy',
                    '{"rows":[]}'::jsonb, 'completed', now(), now()
                )
                """
            ),
            {"workspace": workspace.id, "access": access.id},
        )
        db_session.commit()
    db_session.rollback()
    with pytest.raises(IntegrityError):
        db_session.execute(
            text(
                """
                INSERT INTO data_access_events (
                    id, workspace_id, data_access_id, ingestion_run_id, actor_type,
                    purpose, operation, column_summary, status, started_at, completed_at
                ) VALUES (
                    gen_random_uuid(), :workspace, :access, :run, 'user', 'ingest',
                    'copy', CAST(:summary AS jsonb), 'completed', now(), now()
                )
                """
            ),
            {
                "workspace": workspace.id,
                "access": access.id,
                "run": run.id,
                "summary": '{"column_names":["%s"]}' % ("x" * (SUMMARY_MAX_BYTES + 8)),
            },
        )
        db_session.commit()
    db_session.rollback()


def test_append_only_blocks_update_and_delete(db_session):
    workspace = _workspace(db_session, "append")
    actor = _user(db_session, workspace_id=workspace.id)
    _project, _source, access, run = _access_path(db_session, workspace, actor, "append")
    event = append_data_access_event(
        db_session,
        workspace_id=workspace.id,
        data_access_id=access.id,
        ingestion_run_id=run.id,
        actor_type=ACTOR_USER,
        actor_user_id=actor.id,
        purpose=PURPOSE_INGEST,
        operation=OPERATION_COPY,
        resource_summary={"filename": "a.csv"},
        column_summary={"column_names": ["a"], "column_count": 1},
        rows_read=1,
        bytes_read=4,
    )
    db_session.commit()
    with pytest.raises(Exception, match="append-only"):
        event.status = "failed"
        db_session.commit()
    db_session.rollback()
    with pytest.raises(Exception, match="append-only"):
        db_session.execute(
            text("UPDATE data_access_events SET status = 'failed' WHERE id = :id"),
            {"id": event.id},
        )
        db_session.commit()
    db_session.rollback()
    with pytest.raises(Exception, match="append-only"):
        db_session.execute(
            text("DELETE FROM data_access_events WHERE id = :id"),
            {"id": event.id},
        )
        db_session.commit()
    db_session.rollback()


def test_event_rejects_cross_tenant_data_access(db_session):
    alpha = _workspace(db_session, "alpha")
    beta = _workspace(db_session, "beta")
    alpha_actor = _user(db_session, workspace_id=alpha.id, prefix="alpha")
    beta_actor = _user(db_session, workspace_id=beta.id, prefix="beta")
    _ap, _as, _alpha_access, alpha_run = _access_path(
        db_session, alpha, alpha_actor, "alpha"
    )
    _bp, _bs, beta_access, _br = _access_path(db_session, beta, beta_actor, "beta")
    db_session.commit()
    with pytest.raises(IdentityError, match="workspace"):
        append_data_access_event(
            db_session,
            workspace_id=alpha.id,
            data_access_id=beta_access.id,
            ingestion_run_id=alpha_run.id,
            actor_type=ACTOR_USER,
            purpose=PURPOSE_INGEST,
            operation=OPERATION_COPY,
        )
    with pytest.raises(IntegrityError):
        db_session.execute(
            text(
                """
                INSERT INTO data_access_events (
                    id, workspace_id, data_access_id, actor_type, purpose, operation,
                    status, started_at, completed_at
                ) VALUES (
                    gen_random_uuid(), :workspace, :access, 'user', 'ingest', 'copy',
                    'completed', now(), now()
                )
                """
            ),
            {"workspace": alpha.id, "access": beta_access.id},
        )
        db_session.commit()
    db_session.rollback()
