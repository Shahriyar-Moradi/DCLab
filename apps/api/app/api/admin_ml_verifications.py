"""Admin-only APIs for persistent ML pipeline verification attempts."""

from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import Response
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.domain.ml_verification import VerificationAttemptResponse
from app.services.ml_run_docx import render_ml_run_report_docx
from app.services.pipeline_audit_service import (
    RunNotFoundError,
    RunReportNotReadyError,
    canonical_report_for_run,
    latest_verification_attempt,
    list_verification_attempts,
    request_pipeline_verification,
)

router = APIRouter(prefix="/lab/runs", tags=["admin-ml-verifications"])


def _not_found(exc: Exception) -> HTTPException:
    if isinstance(exc, RunNotFoundError):
        return HTTPException(status_code=404, detail="ML run not found")
    return HTTPException(status_code=409, detail="ML run report is not ready")


@router.get("/{run_id}/verification", response_model=VerificationAttemptResponse)
def latest_verification(run_id: UUID, db: Session = Depends(get_db)) -> VerificationAttemptResponse:
    try:
        attempt = latest_verification_attempt(db, run_id)
    except RunNotFoundError as exc:
        raise _not_found(exc) from exc
    if attempt is None:
        raise HTTPException(status_code=404, detail="No verification attempt exists")
    return VerificationAttemptResponse.model_validate(attempt)


@router.get("/{run_id}/verifications", response_model=list[VerificationAttemptResponse])
def verification_history(run_id: UUID, db: Session = Depends(get_db)) -> list[VerificationAttemptResponse]:
    try:
        attempts = list_verification_attempts(db, run_id)
    except RunNotFoundError as exc:
        raise _not_found(exc) from exc
    return [VerificationAttemptResponse.model_validate(item) for item in attempts]


def _request(db: Session, run_id: UUID, *, deep: bool) -> VerificationAttemptResponse:
    try:
        attempt = request_pipeline_verification(db, run_id, deep=deep)
    except (RunNotFoundError, RunReportNotReadyError) as exc:
        raise _not_found(exc) from exc
    return VerificationAttemptResponse.model_validate(attempt)


@router.post("/{run_id}/verification", response_model=VerificationAttemptResponse)
def request_verification(run_id: UUID, db: Session = Depends(get_db)) -> VerificationAttemptResponse:
    return _request(db, run_id, deep=False)


@router.post("/{run_id}/verification/deep", response_model=VerificationAttemptResponse)
def request_deep_verification(run_id: UUID, db: Session = Depends(get_db)) -> VerificationAttemptResponse:
    return _request(db, run_id, deep=True)


@router.get("/{run_id}/report")
def technical_report(run_id: UUID, db: Session = Depends(get_db)) -> dict:
    try:
        return canonical_report_for_run(db, run_id)
    except (RunNotFoundError, RunReportNotReadyError) as exc:
        raise _not_found(exc) from exc


@router.get("/{run_id}/report.docx")
def technical_report_docx(run_id: UUID, db: Session = Depends(get_db)) -> Response:
    try:
        report = canonical_report_for_run(db, run_id)
    except (RunNotFoundError, RunReportNotReadyError) as exc:
        raise _not_found(exc) from exc
    return Response(
        content=render_ml_run_report_docx(report),
        media_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        headers={"Content-Disposition": 'attachment; filename="DCLab ML Run Report.docx"'},
    )

