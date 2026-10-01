"""Stage: materialize + load the upload, then profile it (EDA + quality)."""

from __future__ import annotations

from typing import Any

import pandas as pd
from pydantic import BaseModel, ConfigDict

from app.db.models import ClientLabUpload
from app.domain.lab_run_stages import ANALYZING
from app.engine.data.quality import quality_report
from app.services.auto_train.context import RunContext, StageHalt, service_module


class LoadProfileInput(BaseModel):
    model_config = ConfigDict(arbitrary_types_allowed=True)

    upload: ClientLabUpload


class LoadProfileOutput(BaseModel):
    model_config = ConfigDict(arbitrary_types_allowed=True)

    frame: pd.DataFrame
    columns: list[str]
    profile: dict[str, Any]
    quality: dict[str, Any]


def run_load_profile(ctx: RunContext, inp: LoadProfileInput) -> LoadProfileOutput:
    svc = service_module()
    upload = inp.upload
    evidence_timer = ctx.evidence_start("file_ingestion")
    with svc.materialize_client_upload(ctx.db, upload) as source:
        frame = svc._load_upload_frame(str(source))
    ctx.current_rows = int(len(frame))
    frame.columns = [str(c) for c in frame.columns]
    columns = list(frame.columns)
    if not columns or frame.empty:
        ctx.evidence_finish(evidence_timer, status="failed")
        ctx.fail("the file loaded but had no usable rows or columns")
        raise StageHalt
    ctx.evidence_finish(evidence_timer)

    ctx.stage(ANALYZING)
    evidence_timer = ctx.evidence_start("profiling")
    profile = svc.profile_frame(frame)
    quality = quality_report(frame)
    ctx.evidence_finish(evidence_timer)
    ctx.trace(
        "profiling",
        "app.engine.schema.profiler.profile_frame",
        row_count=profile["row_count"],
        column_count=profile["column_count"],
        column_names=list(profile.get("column_names") or []),
        missing_count=profile.get("missing_count"),
        duplicate_rows=profile.get("duplicate_rows", profile.get("duplicate_count")),
    )
    return LoadProfileOutput(frame=frame, columns=columns, profile=profile, quality=quality)
