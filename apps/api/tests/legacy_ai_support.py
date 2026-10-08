"""Test support for the legacy LLM paths on the AI gateway (ADR 0009 §4).

``enable_legacy_ai`` seeds governance, syncs the code-owned prompt releases, turns
``AI_ENABLED`` on for the writers (P6.9-A retired the legacy flags; kill switches act per
call) and installs a gateway whose only provider is the deterministic fake (no network). ``label_dataset`` publishes ADR 0005
labels so an upload's columns may reach a model; ``sent`` reads back what one provider
call carried.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from types import SimpleNamespace
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import func, select

from app.agents.gateway.limits import GatewayLimits
from app.agents.gateway.providers.fake import FakeProvider
from app.agents.gateway.service import GatewayService
from app.agents.governance.seed import seed_platform_governance
from app.agents.prompt_releases import sync_prompt_releases
from app.db.models import ClientLabUpload, DatasetColumn, DatasetPolicyRevision, UserRole
from app.services.auth_service import create_user
from app.services.dataset_column_service import publish_dataset_policy_defaults, set_dataset_column_policy


@dataclass
class LegacyAI:
    fake: FakeProvider
    service: GatewayService
    settings: SimpleNamespace


def enable_legacy_ai(monkeypatch, db, *, handler=None, responses=(), ai_enabled: bool = True) -> LegacyAI:
    seed_platform_governance(db, environment="test")
    sync_prompt_releases(db)
    db.commit()
    fake = FakeProvider(responses, handler=handler, environment="test")
    service = GatewayService(
        providers={"openai": fake}, limits=GatewayLimits(),
        settings=lambda: SimpleNamespace(ai_enabled=ai_enabled, dclab_env="test"),
    )
    settings = SimpleNamespace(ai_enabled=ai_enabled, dclab_env="test", pipeline_llm_timeout_seconds=1.0)
    monkeypatch.setattr("app.agents.legacy.gateway_service", lambda: service)
    monkeypatch.setattr("app.services.lab_decision_ledger.get_settings", lambda: settings)
    monkeypatch.setattr("app.engine.lab.llm_client.get_settings", lambda: settings)
    return LegacyAI(fake=fake, service=service, settings=settings)


def forbid_gateway(monkeypatch) -> None:
    def boom():
        raise AssertionError("the gateway must not be called")

    monkeypatch.setattr("app.agents.legacy.gateway_service", boom)


def label_dataset(db, *, workspace_id: UUID, dataset_id: UUID, exposure: str = "allow",
                  columns: dict[str, str] | None = None) -> dict[str, UUID]:
    """Publish complete ADR 0005 labels (``exposure`` unless ``columns`` overrides a column)."""

    actor = create_user(db, email=f"labeler-{uuid4().hex}@test.invalid", password="test-password",
                        role=UserRole.DCLAB_ADMIN)
    revision = db.scalar(select(func.max(DatasetPolicyRevision.revision)).where(
        DatasetPolicyRevision.dataset_id == dataset_id)) or 0
    publish_dataset_policy_defaults(
        db, actor=actor, workspace_id=workspace_id, dataset_id=dataset_id, expected_revision=revision,
        sensitivity_class="internal", llm_exposure_policy=exposure, retention_class="standard",
        residency_class="home_cloud_only", classification_source="manual", classification_confidence=1.0,
    )
    ids = {}
    for column in db.scalars(select(DatasetColumn).where(DatasetColumn.dataset_id == dataset_id)):
        set_dataset_column_policy(
            db, workspace_id=workspace_id, column_id=column.id, sensitivity_class="internal",
            classification_source="manual", model_use_policy="allow",
            llm_exposure_policy=(columns or {}).get(column.name, exposure), retention_class="standard",
            residency_class="home_cloud_only", classification_confidence=1.0,
        )
        ids[column.name] = column.id
    db.commit()
    return ids


def upload_via_api(auth_client, db, monkeypatch, frame, *, filename: str = "data.csv",
                   target: str | None = None, exposure: str | None = "allow") -> ClientLabUpload:
    """A Lab upload with its dataset, workflow run and experiment (attributable lineage)."""

    monkeypatch.setattr("app.services.client_lab_upload_service.enqueue_auto_train", lambda _id: None)
    data = {"category": "Revenue", **({"target_column": target} if target else {})}
    response = auth_client.post("/app/labs/uploads", data=data,
                                files={"file": (filename, frame.to_csv(index=False).encode(), "text/csv")})
    assert response.status_code == 200, response.text
    upload = db.get(ClientLabUpload, response.json()["id"])
    if exposure is not None:
        label_dataset(db, workspace_id=upload.workspace_id, dataset_id=upload.dataset_id, exposure=exposure)
    return upload


def sent(call) -> dict[str, Any]:
    """The redacted context one provider call carried: {field key: wire value}."""

    return {item["key"]: item["value"] for item in json.loads(call.input_json)["context"]}


def column_of(call) -> str:
    return sent(call)["column"]["untrusted_text"]
