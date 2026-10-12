"""Model releases (P7.2-A): the immutable approval record behind "model in use".

There is ONE user-facing concept: the model in use (the project's ``champion_model``
ref), "approved for scoring". A release is the append-only record of a person's
approval: putting a model in use (``project_ref_service.move_ref`` moving
``champion_model``, i.e. ``POST /v1/projects/{id}/refs/champion_model`` or accepting
an agent's champion proposal) writes the ``champion_promoted`` decision record, moves
the ref and inserts the release in one transaction. A rule (the first-run bootstrap)
never approves: a bootstrapped model in use has no release until a person approves it.

The release pins WHAT was approved: ``package_manifest_digest`` is the content digest
of the version's ``feature_manifest`` artifact (P7.1-A package manifest), checked again
by the scoring job together with ``saved_format`` (the model artifact's recorded package
format must still equal the approved one). For a ``package_v1`` model the manifest is
verified against the package bytes by the loader. For a ``legacy_quarantined`` (pickle)
release the manifest digest does NOT cover the pickle bytes: those are pinned only by the
model artifact's own immutable ``content_digest``, which the loader checks before
unpickling. ``previous_release_id`` chains the project's approvals (linear, DB-enforced),
so a rollback is approving the previous version again (P7.5-A).

Agent views (service tokens, the catalog's ``list_releases``, MCP) drop the approving
person's id and withhold a rationale that names the final holdout; people see it all.
"""

from __future__ import annotations

from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, Field

from app.domain.data_plane import sql_in_clause
from app.domain.decision_records import RATIONALE_MAX_CHARS

SAVED_FORMATS = ("package_v1", "legacy_quarantined")

# Stable refusal codes (``InvalidDecisionRecordError.reason``, 422).
MODEL_PACKAGE_MISSING = "model_package_missing"
LEGACY_MODEL_QUARANTINED = "legacy_model_quarantined"

# DB CHECK backstops (models.py; Alembic 0078 inlines the same literal SQL).
CK_MODEL_RELEASES_RATIONALE = (
    f"char_length(btrim(rationale)) > 0 AND char_length(rationale) <= {RATIONALE_MAX_CHARS}"
)
CK_MODEL_RELEASES_DIGEST = "package_manifest_digest ~ '^[0-9a-f]{64}$'"
CK_MODEL_RELEASES_SAVED_FORMAT = sql_in_clause("saved_format", SAVED_FORMATS)
CK_MODEL_RELEASES_PREVIOUS_NOT_SELF = "previous_release_id IS NULL OR previous_release_id <> id"


class ModelReleaseSummary(BaseModel):
    """The approval behind a model in use ("approved for scoring"); no person ids."""

    id: UUID
    model_version_id: UUID
    previous_release_id: UUID | None = Field(
        default=None, description="The project's approval before this one (rollback = approve that version again)."
    )
    decision_record_id: UUID = Field(description="The `champion_promoted` decision that approved it.")
    approved_at: datetime
    saved_format: Literal["package_v1", "legacy_quarantined"]
    package_manifest_digest: str = Field(
        description="Content digest of the version's `feature_manifest` artifact at approval; scoring re-checks it."
    )
    is_current: bool = Field(description="This is the approval of the model in use now.")


class ModelReleaseRead(ModelReleaseSummary):
    model_version: str = Field(description="The model version's label (e.g. v3).")
    approved_by_user_id: UUID | None = Field(
        default=None, description="The person who approved it (null for service-token readers)."
    )
    rationale: str | None = Field(
        default=None,
        description=(
            "Why, as the approving person wrote it (never an agent's text; accepting an agent's proposal "
            "without a reason records a default naming the proposal): untrusted plain text, never markup. "
            "Null for service-token readers when it names the final holdout."
        ),
    )


class ModelReleaseList(BaseModel):
    project_id: UUID
    current_release_id: UUID | None = Field(
        default=None, description="The approval of the model in use; null when no person has approved it yet."
    )
    items: list[ModelReleaseRead] = Field(default_factory=list, description="Newest first.")
