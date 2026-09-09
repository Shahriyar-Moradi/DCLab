"""Canonical visualization metadata. Specs only — no chart generation."""

from __future__ import annotations

from uuid import uuid4

import pytest
from sqlalchemy import select, text
from sqlalchemy.exc import DBAPIError, IntegrityError

from app.db.models import Visualization
from app.domain.errors import IdentityError, VisualizationSpecError
from app.domain.visualizations import SPEC_MAX_ARRAY_LENGTH, SPEC_MAX_BYTES
from app.services.artifact_service import store_artifact
from app.services.lineage_service import create_pipeline_run, create_workflow_run
from app.services.visualization_service import (
    bound_visualization_spec,
    persist_visualization,
)
from app.storage.local import LocalStorage
from test_data_model_lineage import make_lineage_setup


def _pipeline(db_session, setup):
    workflow_run = create_workflow_run(
        db_session,
        workspace_id=setup["alpha"].id,
        workflow=setup["alpha_workflow"],
        requester=setup["alpha_admin"],
        trigger_type="manual",
        source_type="dataset",
    )
    return create_pipeline_run(
        db_session,
        workflow_run=workflow_run,
        environment=setup["env"],
        dataset=setup["alpha_dataset"],
        task=setup["task"],
        commit=False,
    )


def test_bound_spec_rejects_bulk_series_and_secrets():
    with pytest.raises(VisualizationSpecError, match="points"):
        bound_visualization_spec({"points": [[0, 0], [1, 1]]})
    with pytest.raises(VisualizationSpecError, match="password"):
        bound_visualization_spec({"password": "secret"})
    with pytest.raises(VisualizationSpecError, match="array exceeds"):
        bound_visualization_spec(
            {"labels": [str(i) for i in range(SPEC_MAX_ARRAY_LENGTH + 1)]}
        )
    with pytest.raises(VisualizationSpecError, match="exceeds"):
        bound_visualization_spec({"title": "x" * (SPEC_MAX_BYTES + 1)})
    assert bound_visualization_spec({"title": "ROC", "x_axis": "fpr"}) == {
        "title": "ROC",
        "x_axis": "fpr",
    }


def test_persist_visualization_stores_spec_and_artifact_pointers(db_session, tmp_path):
    setup = make_lineage_setup(db_session, tmp_path)
    pipeline = _pipeline(db_session, setup)
    storage = LocalStorage(root=tmp_path / "objects")
    data = store_artifact(
        db_session,
        workspace_id=setup["alpha"].id,
        project_id=setup["alpha_project"].id,
        pipeline_run_id=pipeline.id,
        artifact_type="result_json",
        filename="roc.json",
        data=b'{"fpr":[0,1],"tpr":[0,1]}',
        storage=storage,
    )
    image = store_artifact(
        db_session,
        workspace_id=setup["alpha"].id,
        project_id=setup["alpha_project"].id,
        pipeline_run_id=pipeline.id,
        artifact_type="plot",
        filename="roc.png",
        data=b"\x89PNG\r\n",
        storage=storage,
    )
    row = persist_visualization(
        db_session,
        workspace_id=setup["alpha"].id,
        project_id=setup["alpha_project"].id,
        pipeline_run_id=pipeline.id,
        visualization_type="roc_curve",
        renderer_hint="plotly",
        spec={"title": "Holdout ROC", "x_axis": "fpr", "y_axis": "tpr"},
        data_artifact_id=data.id,
        image_artifact_id=image.id,
    )
    db_session.commit()
    stored = db_session.get(Visualization, row.id)
    assert stored is not None
    assert stored.visualization_type == "roc_curve"
    assert stored.spec_version == "1"
    assert stored.renderer_hint == "plotly"
    assert stored.spec["title"] == "Holdout ROC"
    assert "points" not in stored.spec
    assert stored.data_artifact_id == data.id
    assert stored.image_artifact_id == image.id
    assert len(stored.content_digest) == 64
    assert stored.pipeline_run_id == pipeline.id


def test_persist_does_not_generate_other_chart_rows(db_session, tmp_path):
    setup = make_lineage_setup(db_session, tmp_path)
    pipeline = _pipeline(db_session, setup)
    persist_visualization(
        db_session,
        workspace_id=setup["alpha"].id,
        pipeline_run_id=pipeline.id,
        visualization_type="roc_curve",
        spec={"title": "declared only"},
    )
    db_session.commit()
    rows = list(db_session.scalars(select(Visualization)))
    assert len(rows) == 1
    assert rows[0].visualization_type == "roc_curve"


def test_postgres_rejects_bulk_spec_and_identity_updates(db_session, tmp_path):
    setup = make_lineage_setup(db_session, tmp_path)
    pipeline = _pipeline(db_session, setup)
    row = persist_visualization(
        db_session,
        workspace_id=setup["alpha"].id,
        pipeline_run_id=pipeline.id,
        visualization_type="confusion_matrix",
        spec={"title": "matrix"},
    )
    db_session.commit()
    with pytest.raises(IntegrityError):
        db_session.execute(
            text(
                """
                INSERT INTO visualizations (
                    id, workspace_id, pipeline_run_id, visualization_type,
                    spec_version, spec, content_digest
                ) VALUES (
                    gen_random_uuid(), :workspace, :run, 'roc_curve', '1',
                    '{"points":[1]}'::jsonb, :digest
                )
                """
            ),
            {
                "workspace": setup["alpha"].id,
                "run": pipeline.id,
                "digest": "c" * 64,
            },
        )
        db_session.commit()
    db_session.rollback()
    with pytest.raises((DBAPIError, IntegrityError), match="immutable"):
        db_session.execute(
            text("UPDATE visualizations SET spec = '{}'::jsonb WHERE id = :id"),
            {"id": row.id},
        )
        db_session.commit()
    db_session.rollback()
    with pytest.raises((DBAPIError, IntegrityError), match="immutable"):
        db_session.execute(
            text("DELETE FROM visualizations WHERE id = :id"),
            {"id": row.id},
        )
        db_session.commit()
    db_session.rollback()


def test_orm_blocks_visualization_mutation(db_session, tmp_path):
    setup = make_lineage_setup(db_session, tmp_path)
    pipeline = _pipeline(db_session, setup)
    row = persist_visualization(
        db_session,
        workspace_id=setup["alpha"].id,
        pipeline_run_id=pipeline.id,
        visualization_type="missingness",
        spec={"title": "nulls"},
    )
    db_session.commit()
    stored = db_session.get(Visualization, row.id)
    assert stored is not None
    stored.spec = {"title": "changed"}
    with pytest.raises(ValueError, match="immutable"):
        db_session.flush()
    db_session.rollback()


def test_association_set_null_is_allowed_identity_is_not(db_session, tmp_path):
    setup = make_lineage_setup(db_session, tmp_path)
    pipeline = _pipeline(db_session, setup)
    row = persist_visualization(
        db_session,
        workspace_id=setup["alpha"].id,
        project_id=setup["alpha_project"].id,
        pipeline_run_id=pipeline.id,
        visualization_type="distribution",
        spec={"title": "histogram"},
    )
    db_session.commit()
    db_session.execute(
        text("UPDATE visualizations SET project_id = NULL WHERE id = :id"),
        {"id": row.id},
    )
    db_session.commit()
    stored = db_session.get(Visualization, row.id)
    assert stored is not None
    assert stored.project_id is None
    assert stored.spec == {"title": "histogram"}
    assert stored.pipeline_run_id == pipeline.id
    with pytest.raises((DBAPIError, IntegrityError), match="immutable"):
        db_session.execute(
            text(
                "UPDATE visualizations SET visualization_type = 'roc_curve' "
                "WHERE id = :id"
            ),
            {"id": row.id},
        )
        db_session.commit()
    db_session.rollback()


def test_cross_tenant_pipeline_run_is_rejected(db_session, tmp_path):
    setup = make_lineage_setup(db_session, tmp_path)
    alpha_pipeline = _pipeline(db_session, setup)
    beta_run = create_workflow_run(
        db_session,
        workspace_id=setup["beta"].id,
        workflow=setup["beta_workflow"],
        requester=setup["beta_admin"],
        trigger_type="manual",
        source_type="dataset",
    )
    beta_pipeline = create_pipeline_run(
        db_session,
        workflow_run=beta_run,
        environment=setup["env"],
        dataset=setup["beta_dataset"],
        task=setup["task"],
        commit=False,
    )
    with pytest.raises(IdentityError):
        persist_visualization(
            db_session,
            workspace_id=setup["alpha"].id,
            pipeline_run_id=beta_pipeline.id,
            visualization_type="roc_curve",
        )
    with pytest.raises(IdentityError):
        persist_visualization(
            db_session,
            workspace_id=setup["alpha"].id,
            pipeline_run_id=alpha_pipeline.id,
            candidate_id=uuid4(),
            visualization_type="roc_curve",
        )
