"""Step 5 — Client Labs.

A bounded, client-triggered trial run on top of the exact same engine the admin
Simulations feature uses (`app.sim.runner.run_use_case`) — real training, real
evaluation, not a scripted walkthrough. The problem catalog is fixed (the eight
use cases already mapped to Insight categories in Step 1/3); a client picks one
rather than configuring anything open-ended.

Bounds are enforced here, not just documented:
  - MAX_UPLOAD_ROWS caps an uploaded trial file's size (DCLab's own bundled
    sample data is already a fixed, small size and is exempt from this cap).
  - MAX_TRIAL_RUNS_PER_PROBLEM caps how many times a workspace may run a given
    problem at all — the "bounded free trial" part.
  - TRIAL_TIMEOUT_SECONDS caps the wall-clock budget for a single run; exceeding
    it produces a stored FAILED row with a safe message, not a hung request or
    a crash. (The worker thread itself cannot be force-killed by
    ThreadPoolExecutor — for the small, capped datasets this trial allows, a run
    finishes in a few seconds in practice, so the timeout is a defensive ceiling
    rather than the normal path.)

Every stored result is already translated through app.translation — this module
never persists a raw probability, model family, feature-importance value, or
metric for a trial, even at rest.
"""

from __future__ import annotations

import shutil
import tempfile
from concurrent.futures import ThreadPoolExecutor
from concurrent.futures import TimeoutError as FutureTimeoutError
from pathlib import Path
from typing import Any
from uuid import UUID

import pandas as pd
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.db.models import DEFAULT_WORKSPACE_ID, ClientLabRun, ClientLabRunAudit, User
from app.domain.client_lab import ClientLabProblem, ClientLabQuotaRead
from app.domain.errors import (
    TrialDatasetColumnsError,
    TrialDatasetTooLargeError,
    TrialQuotaExceededError,
    UnknownLabProblemError,
)
from app.ml.feature_groups import group_features, load_layer_config
from app.sim.catalog import UseCase, all_use_cases
from app.sim.catalog import use_case as get_use_case_spec
from app.sim.runner import ensure_data, run_use_case
from app.translation.models import ClientFacingInsight
from app.translation.simulations import CATEGORY_BY_USE_CASE, translate_simulation_outcome

MAX_UPLOAD_ROWS = 500
MAX_TRIAL_RUNS_PER_PROBLEM = 3
TRIAL_TIMEOUT_SECONDS = 30
MAX_INSIGHTS_PER_RUN = 6


def _workspace_id_for(user: User) -> UUID:
    return user.workspace_id or DEFAULT_WORKSPACE_ID


def _required_columns(spec: UseCase) -> list[str]:
    config = load_layer_config(spec.layer_path)
    groups = group_features(config)
    columns: list[str] = [spec.target, "external_id"]
    seen = set(columns)
    for cols in groups.values():
        for column in cols:
            if column not in seen:
                columns.append(column)
                seen.add(column)
    return columns


def _sample_row_count(spec: UseCase) -> int:
    ensure_data()
    try:
        with open(spec.csv_path, newline="") as handle:
            return max(sum(1 for _ in handle) - 1, 0)
    except OSError:
        return 0


def list_problems() -> list[ClientLabProblem]:
    problems: list[ClientLabProblem] = []
    for spec in all_use_cases():
        category = CATEGORY_BY_USE_CASE.get(spec.name)
        if category is None:
            continue
        problems.append(
            ClientLabProblem(
                use_case=spec.name,
                category=category,
                question=spec.question,
                sample_scenario=spec.company,
                sample_row_count=_sample_row_count(spec),
                max_upload_rows=MAX_UPLOAD_ROWS,
                max_trial_runs=MAX_TRIAL_RUNS_PER_PROBLEM,
                required_columns=_required_columns(spec),
            )
        )
    return problems


def runs_used(db: Session, workspace_id: UUID, use_case_name: str) -> int:
    return (
        db.scalar(
            select(func.count(ClientLabRun.id)).where(
                ClientLabRun.workspace_id == workspace_id,
                ClientLabRun.use_case == use_case_name,
            )
        )
        or 0
    )


def get_quota(db: Session, user: User, use_case_name: str) -> ClientLabQuotaRead:
    if use_case_name not in CATEGORY_BY_USE_CASE:
        raise UnknownLabProblemError(f"{use_case_name!r} is not one of the fixed Labs problems")
    used = runs_used(db, _workspace_id_for(user), use_case_name)
    return ClientLabQuotaRead(
        use_case=use_case_name,
        max_trial_runs=MAX_TRIAL_RUNS_PER_PROBLEM,
        runs_used=used,
        runs_remaining=max(MAX_TRIAL_RUNS_PER_PROBLEM - used, 0),
    )


def _validate_upload(csv_path: Path, spec: UseCase) -> int:
    try:
        frame = pd.read_csv(csv_path)
    except Exception as exc:
        raise TrialDatasetColumnsError("This file could not be read as a CSV.") from exc
    if len(frame) > MAX_UPLOAD_ROWS:
        raise TrialDatasetTooLargeError(
            f"This trial accepts at most {MAX_UPLOAD_ROWS} rows; the uploaded file has {len(frame)}."
        )
    required = set(_required_columns(spec))
    missing = sorted(required - set(frame.columns))
    if missing:
        raise TrialDatasetColumnsError(
            "The uploaded file is missing required columns: " + ", ".join(missing)
        )
    return len(frame)


def _translate_result(use_case_name: str, result: dict[str, Any]) -> list[ClientFacingInsight]:
    insights: list[ClientFacingInsight] = []
    seen: set[str] = set()
    for item in list(result.get("heroes") or []) + list(result.get("sample_decisions") or []):
        external_id = str(item.get("external_id") or "")
        if not external_id or external_id in seen:
            continue
        seen.add(external_id)
        insights.append(
            translate_simulation_outcome(
                use_case_name,
                external_id=external_id,
                features=dict(item.get("features") or {}),
                agreement=float(item.get("agreement") or 0.0),
                recommended_action_key=str(item.get("action_key") or "do_nothing"),
                expected_value=float(item.get("expected_value") or 0.0),
                incremental_value=float(item.get("incremental_value") or 0.0),
            )
        )
        if len(insights) >= MAX_INSIGHTS_PER_RUN:
            break
    return insights


def _store_failed_run(
    db: Session, *, workspace_id: UUID, user: User, use_case_name: str, data_source: str, row_count: int, reason: str
) -> ClientLabRun:
    row = ClientLabRun(
        workspace_id=workspace_id,
        requested_by=user.id,
        use_case=use_case_name,
        category=CATEGORY_BY_USE_CASE[use_case_name].value,
        data_source=data_source,
        row_count=row_count,
        status="failed",
        failure_reason=reason,
        insights=[],
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    return row


def run_trial(
    db: Session,
    *,
    user: User,
    use_case_name: str,
    uploaded_bytes: bytes | None,
) -> ClientLabRun:
    if use_case_name not in CATEGORY_BY_USE_CASE:
        raise UnknownLabProblemError(f"{use_case_name!r} is not one of the fixed Labs problems")

    workspace_id = _workspace_id_for(user)
    used = runs_used(db, workspace_id, use_case_name)
    if used >= MAX_TRIAL_RUNS_PER_PROBLEM:
        raise TrialQuotaExceededError(
            f"This workspace has used all {MAX_TRIAL_RUNS_PER_PROBLEM} trial runs for this problem."
        )

    spec = get_use_case_spec(use_case_name)
    ensure_data()
    tmp_root = Path(tempfile.mkdtemp(prefix=f"client_lab_{use_case_name}_"))
    try:
        if uploaded_bytes is None:
            csv_path = spec.csv_path
            data_source = "sample"
            row_count = _sample_row_count(spec)
        else:
            csv_path = tmp_root / "upload.csv"
            csv_path.write_bytes(uploaded_bytes)
            data_source = "uploaded"
            row_count = _validate_upload(csv_path, spec)  # raises before any run is attempted

        model_dir = tmp_root / "model"

        def _execute() -> dict[str, Any]:
            return run_use_case(use_case_name, csv_path=csv_path, model_dir=model_dir)

        # Not a `with ThreadPoolExecutor(...) as pool:` block on purpose: that form
        # calls shutdown(wait=True) on exit, which would block this request until
        # the runaway thread finishes — defeating the timeout entirely. Abandoning
        # the pool on timeout (shutdown(wait=False)) is what actually lets the
        # request return promptly; the thread itself keeps running in the
        # background until it naturally finishes (a known ThreadPoolExecutor
        # limitation — a real hard-kill would need a separate process).
        pool = ThreadPoolExecutor(max_workers=1)
        try:
            result = pool.submit(_execute).result(timeout=TRIAL_TIMEOUT_SECONDS)
        except FutureTimeoutError:
            pool.shutdown(wait=False)
            return _store_failed_run(
                db,
                workspace_id=workspace_id,
                user=user,
                use_case_name=use_case_name,
                data_source=data_source,
                row_count=row_count,
                reason="This run took longer than the trial's time budget. Try again with a smaller file.",
            )
        except Exception:  # noqa: BLE001 - never let an engine error crash the request
            pool.shutdown(wait=False)
            return _store_failed_run(
                db,
                workspace_id=workspace_id,
                user=user,
                use_case_name=use_case_name,
                data_source=data_source,
                row_count=row_count,
                reason="This run could not be completed. Try again, or try a different sample file.",
            )
        pool.shutdown(wait=False)

        insights = _translate_result(use_case_name, result)
        row = ClientLabRun(
            workspace_id=workspace_id,
            requested_by=user.id,
            use_case=use_case_name,
            category=CATEGORY_BY_USE_CASE[use_case_name].value,
            data_source=data_source,
            row_count=row_count,
            status="completed",
            insights=[insight.model_dump(mode="json") for insight in insights],
        )
        db.add(row)
        db.commit()
        db.refresh(row)

        # Step 7 DoD: whatever ML task a client's request triggers must be
        # logged/reviewable on the Admin Model Registry side with full detail.
        # `result` is the same raw shape an admin-run simulation persists —
        # store it exactly like that, linked back to this row, and never
        # exposed on any /app response.
        db.add(ClientLabRunAudit(client_lab_run_id=row.id, use_case=use_case_name, payload=result))
        db.commit()
        return row
    finally:
        shutil.rmtree(tmp_root, ignore_errors=True)


def list_runs(db: Session, user: User, use_case_name: str | None = None) -> list[ClientLabRun]:
    stmt = select(ClientLabRun).where(ClientLabRun.workspace_id == _workspace_id_for(user))
    if use_case_name:
        stmt = stmt.where(ClientLabRun.use_case == use_case_name)
    stmt = stmt.order_by(ClientLabRun.created_at.desc()).limit(50)
    return list(db.scalars(stmt))


def get_run(db: Session, user: User, run_id: UUID) -> ClientLabRun | None:
    row = db.get(ClientLabRun, run_id)
    if row is None or row.workspace_id != _workspace_id_for(user):
        return None
    return row
