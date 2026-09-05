"""Persist and read runtime, code-snapshot, and model-artifact lineage.

Source packages and lockfiles go to object storage through Artifact. This
module never returns storage credentials.
"""

from __future__ import annotations

import hashlib
import io
import json
import os
import platform
import sys
import zipfile
from dataclasses import dataclass
from importlib.metadata import distributions
from pathlib import Path
from typing import Any
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import REPO_ROOT, get_settings
from app.db.models import (
    Artifact,
    CodeSnapshot,
    Experiment,
    ExperimentCandidate,
    ModelVersion,
    RuntimeEnvironment,
    User,
)
from app.domain.errors import ArtifactNotFoundError, IdentityError
from app.domain.reproducibility import ENGINE_ENTRYPOINT
from app.services.artifact_service import (
    get_artifact,
    list_artifacts,
    read_artifact_bytes,
    store_artifact,
)
from app.services.authorization_service import can_read_workspace
from app.services.scientific_lineage_service import latest_pipeline_run_feature_set_version
from app.storage.factory import get_object_storage


@dataclass
class ReproducibilityPersistResult:
    runtime_environment: RuntimeEnvironment
    code_snapshot: CodeSnapshot | None
    model_artifact: Artifact | None
    preprocessor_artifact: Artifact | None
    feature_manifest_artifact: Artifact | None
    dependency_lock_artifact: Artifact | None
    feature_set_version_id: UUID | None


def _digest(payload: dict[str, Any]) -> str:
    encoded = json.dumps(payload, sort_keys=True, default=str, separators=(",", ":"))
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def _dependency_lock_text() -> str:
    rows = []
    for dist in distributions():
        name = (dist.metadata.get("Name") or dist.name or "").strip()
        version = (dist.version or "").strip()
        if name and version:
            rows.append(f"{name}=={version}")
    return "\n".join(sorted(set(rows), key=str.lower)) + "\n"


def _engine_source_zip() -> bytes:
    root = REPO_ROOT / "apps" / "api" / "app" / "engine"
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        if root.is_dir():
            for path in sorted(root.rglob("*")):
                if not path.is_file():
                    continue
                if "__pycache__" in path.parts or path.suffix in {".pyc", ".pyo"}:
                    continue
                archive.write(path, path.relative_to(root).as_posix())
        archive.writestr(
            "MANIFEST.json",
            json.dumps(
                {"entrypoint": ENGINE_ENTRYPOINT, "package": "app.engine"},
                indent=2,
            )
            + "\n",
        )
    return buffer.getvalue()


def _store_bytes(
    db: Session,
    experiment: Experiment,
    *,
    artifact_type: str,
    filename: str,
    data: bytes,
    mime_type: str,
    role: str,
) -> Artifact:
    return store_artifact(
        db,
        workspace_id=experiment.workspace_id,
        project_id=experiment.project_id,
        artifact_type=artifact_type,
        filename=filename,
        data=data,
        mime_type=mime_type,
        extra_metadata={
            "pipeline_run_id": str(experiment.id),
            "role": role,
        },
    )


def _store_path_if_exists(
    db: Session,
    experiment: Experiment,
    path: Path,
    *,
    artifact_type: str,
    role: str,
    mime_type: str,
) -> Artifact | None:
    if not path.is_file():
        return None
    return _store_bytes(
        db,
        experiment,
        artifact_type=artifact_type,
        filename=path.name,
        data=path.read_bytes(),
        mime_type=mime_type,
        role=role,
    )


def _preprocessor_bytes(experiment: Experiment, winner_key: str | None) -> bytes | None:
    artifact_dir = Path(experiment.artifact_dir or "")
    if not artifact_dir.is_dir() or not winner_key:
        return None
    member = artifact_dir / "members" / f"{winner_key}.joblib"
    if not member.is_file():
        return None
    try:
        import joblib
    except Exception:  # noqa: BLE001
        return None
    try:
        pipeline = joblib.load(member)
        prep = getattr(pipeline, "named_steps", {}).get("prep")
        if prep is None:
            return None
        buffer = io.BytesIO()
        joblib.dump(prep, buffer)
        return buffer.getvalue()
    except Exception:  # noqa: BLE001
        return None


def capture_runtime_environment(
    db: Session, experiment: Experiment, *, lock_text: str, lock_digest: str
) -> tuple[RuntimeEnvironment, Artifact]:
    lock_artifact = _store_bytes(
        db,
        experiment,
        artifact_type="dependency_lock",
        filename="requirements.lock",
        data=lock_text.encode("utf-8"),
        mime_type="text/plain",
        role="dependency_lock",
    )
    python_version = platform.python_version()
    os_name = platform.system() or sys.platform
    os_version = platform.release() or ""
    architecture = platform.machine() or ""
    container_image = os.environ.get("CONTAINER_IMAGE") or None
    container_digest = os.environ.get("CONTAINER_DIGEST") or None
    hardware = {
        "machine": platform.machine(),
        "processor": platform.processor(),
        "cpu_count": os.cpu_count(),
        "platform": platform.platform(),
    }
    digest = _digest(
        {
            "python_version": python_version,
            "os_name": os_name,
            "os_version": os_version,
            "architecture": architecture,
            "container_image": container_image or "",
            "container_digest": container_digest or "",
            "dependency_lock_digest": lock_digest,
        }
    )
    existing = db.scalar(
        select(RuntimeEnvironment).where(RuntimeEnvironment.environment_digest == digest)
    )
    if existing is not None:
        if existing.dependency_lock_artifact_id is None:
            existing.dependency_lock_artifact_id = lock_artifact.id
            db.flush()
        return existing, lock_artifact
    row = RuntimeEnvironment(
        python_version=python_version,
        os_name=os_name,
        os_version=os_version,
        architecture=architecture,
        container_image=container_image,
        container_digest=container_digest,
        dependency_lock_artifact_id=lock_artifact.id,
        hardware=hardware,
        environment_digest=digest,
    )
    db.add(row)
    db.flush()
    return row, lock_artifact


def persist_reproducibility(
    db: Session, experiment: Experiment, result: dict[str, Any]
) -> ReproducibilityPersistResult:
    """Register runtime, source, and model blobs produced by this PipelineRun."""

    existing = db.scalar(
        select(CodeSnapshot).where(CodeSnapshot.pipeline_run_id == experiment.id)
    )
    feature_set_version = latest_pipeline_run_feature_set_version(db, experiment)
    feature_set_version_id = feature_set_version.id if feature_set_version is not None else None
    if existing is not None:
        by_role = {
            str((row.extra_metadata or {}).get("role") or ""): row
            for row in artifacts_for_pipeline_run(db, experiment)
        }
        return ReproducibilityPersistResult(
            runtime_environment=existing.runtime_environment,
            code_snapshot=existing,
            model_artifact=by_role.get("model"),
            preprocessor_artifact=by_role.get("preprocessor"),
            feature_manifest_artifact=by_role.get("feature_manifest"),
            dependency_lock_artifact=by_role.get("dependency_lock"),
            feature_set_version_id=feature_set_version_id,
        )
    lock_text = _dependency_lock_text()
    lock_digest = hashlib.sha256(lock_text.encode("utf-8")).hexdigest()
    runtime, lock_artifact = capture_runtime_environment(
        db, experiment, lock_text=lock_text, lock_digest=lock_digest
    )
    selection = result.get("selection") if isinstance(result.get("selection"), dict) else {}
    winner_key = str(
        selection.get("selected_candidate_id") or selection.get("candidate_id") or ""
    )
    winner = None
    if winner_key:
        winner = db.scalar(
            select(ExperimentCandidate).where(
                ExperimentCandidate.experiment_id == experiment.id,
                ExperimentCandidate.candidate_key == winner_key,
            )
        )
    artifact_dir = Path(experiment.artifact_dir or "")
    model_path = artifact_dir / "model.joblib"
    member_path = artifact_dir / "members" / f"{winner_key}.joblib" if winner_key else None
    model_file = member_path if member_path is not None and member_path.is_file() else model_path
    model_artifact = _store_path_if_exists(
        db,
        experiment,
        model_file,
        artifact_type="model",
        role="model",
        mime_type="application/octet-stream",
    )
    preprocessor_bytes = _preprocessor_bytes(experiment, winner_key or None)
    preprocessor_artifact = None
    if preprocessor_bytes:
        preprocessor_artifact = _store_bytes(
            db,
            experiment,
            artifact_type="preprocessor",
            filename="preprocessor.joblib",
            data=preprocessor_bytes,
            mime_type="application/octet-stream",
            role="preprocessor",
        )
    features = list((winner.payload or {}).get("feature_set") or []) if winner is not None else []
    if not features:
        features = list((result.get("model_development_plan") or {}).get("allowed_features") or [])
    manifest = {
        "pipeline_run_id": str(experiment.id),
        "feature_set_version_id": str(feature_set_version_id) if feature_set_version_id else None,
        "features": features,
        "candidate_id": winner.candidate_key if winner is not None else None,
    }
    feature_manifest_artifact = _store_bytes(
        db,
        experiment,
        artifact_type="feature_manifest",
        filename="feature_manifest.json",
        data=(json.dumps(manifest, indent=2) + "\n").encode("utf-8"),
        mime_type="application/json",
        role="feature_manifest",
    )
    code_snapshot = None
    if get_settings().reproducible_code_export_enabled:
        source_bytes = _engine_source_zip()
        source_artifact = _store_bytes(
            db,
            experiment,
            artifact_type="source_code",
            filename="engine-source.zip",
            data=source_bytes,
            mime_type="application/zip",
            role="source_package",
        )
        code_snapshot = CodeSnapshot(
            workspace_id=experiment.workspace_id,
            project_id=experiment.project_id,
            pipeline_run_id=experiment.id,
            pipeline_stage_run_id=None,
            candidate_id=winner.id if winner is not None else None,
            artifact_id=source_artifact.id,
            language="python",
            entrypoint=ENGINE_ENTRYPOINT,
            git_commit=experiment.git_commit,
            code_digest=source_artifact.content_digest,
            dependency_lock_digest=lock_digest,
            runtime_environment_id=runtime.id,
        )
        db.add(code_snapshot)
        db.flush()
    db.flush()
    return ReproducibilityPersistResult(
        runtime_environment=runtime,
        code_snapshot=code_snapshot,
        model_artifact=model_artifact,
        preprocessor_artifact=preprocessor_artifact,
        feature_manifest_artifact=feature_manifest_artifact,
        dependency_lock_artifact=lock_artifact,
        feature_set_version_id=feature_set_version_id,
    )


def store_report_artifacts(db: Session, experiment: Experiment) -> None:
    artifact_dir = Path(experiment.artifact_dir or "")
    _store_path_if_exists(
        db,
        experiment,
        artifact_dir / "report.md",
        artifact_type="report",
        role="technical_report",
        mime_type="text/markdown",
    )
    _store_path_if_exists(
        db,
        experiment,
        artifact_dir / "result.json",
        artifact_type="result_json",
        role="result_json",
        mime_type="application/json",
    )


def artifacts_for_pipeline_run(db: Session, experiment: Experiment) -> list[Artifact]:
    rows = list_artifacts(db, workspace_id=experiment.workspace_id, project_id=None)
    run_id = str(experiment.id)
    return [
        row
        for row in rows
        if (row.extra_metadata or {}).get("pipeline_run_id") == run_id
    ]


def artifacts_for_model_version(db: Session, model_version: ModelVersion) -> list[Artifact]:
    ids = [
        model_version.model_artifact_id,
        model_version.preprocessor_artifact_id,
        model_version.feature_manifest_artifact_id,
    ]
    snapshot = model_version.code_snapshot
    if snapshot is not None:
        ids.append(snapshot.artifact_id)
        if snapshot.runtime_environment is not None:
            ids.append(snapshot.runtime_environment.dependency_lock_artifact_id)
    if model_version.runtime_environment is not None:
        ids.append(model_version.runtime_environment.dependency_lock_artifact_id)
    wanted = {item for item in ids if item is not None}
    found = []
    if wanted:
        found = list(db.scalars(select(Artifact).where(Artifact.id.in_(wanted))))
    extras = artifacts_for_pipeline_run(db, model_version.pipeline_run)
    by_id = {row.id: row for row in [*found, *extras]}
    return [by_id[key] for key in by_id]


def _require_workspace_read(db: Session, actor: User, workspace_id: UUID) -> None:
    if not can_read_workspace(db, actor, workspace_id):
        raise IdentityError("not authorized for this workspace", status_code=403)


def get_model_version_for_actor(
    db: Session,
    actor: User,
    *,
    model_version_id: UUID,
    workspace_id: UUID | None = None,
) -> ModelVersion:
    row = db.get(ModelVersion, model_version_id)
    if row is None:
        raise IdentityError("model version not found", status_code=404)
    if workspace_id is not None and row.workspace_id != workspace_id:
        raise IdentityError("model version not found", status_code=404)
    _require_workspace_read(db, actor, row.workspace_id)
    return row


def get_artifact_for_actor(
    db: Session,
    actor: User,
    *,
    artifact_id: UUID,
    workspace_id: UUID | None = None,
) -> Artifact:
    if workspace_id is not None:
        try:
            row = get_artifact(db, workspace_id=workspace_id, artifact_id=artifact_id)
        except ArtifactNotFoundError as exc:
            raise IdentityError("artifact not found", status_code=404) from exc
        _require_workspace_read(db, actor, row.workspace_id)
        return row
    row = db.get(Artifact, artifact_id)
    if row is None:
        raise IdentityError("artifact not found", status_code=404)
    _require_workspace_read(db, actor, row.workspace_id)
    return row


def signed_url_for_artifact(
    db: Session,
    actor: User,
    *,
    artifact_id: UUID,
    workspace_id: UUID | None = None,
    expires_in: int = 3600,
) -> tuple[Artifact, str, int]:
    artifact = get_artifact_for_actor(
        db, actor, artifact_id=artifact_id, workspace_id=workspace_id
    )
    url = get_object_storage().signed_url(artifact.object_key, expires_in=expires_in)
    return artifact, url, expires_in


def download_artifact_bytes(
    db: Session,
    actor: User,
    *,
    artifact_id: UUID,
    workspace_id: UUID | None = None,
) -> tuple[Artifact, bytes]:
    artifact = get_artifact_for_actor(
        db, actor, artifact_id=artifact_id, workspace_id=workspace_id
    )
    payload = read_artifact_bytes(
        db, workspace_id=artifact.workspace_id, artifact_id=artifact.id
    )
    return artifact, payload
