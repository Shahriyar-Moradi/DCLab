from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models import SimulationRun
from app.db.session import get_db
from app.domain.simulation import (
    KNOWN_USE_CASES,
    SimulationDecisionResponse,
    SimulationRunListResponse,
    SimulationRunRead,
    SimulationRunRequest,
)
from app.sim.runner import run_all, run_use_case

router = APIRouter(prefix="/simulations", tags=["simulations"])


def _persist(db: Session, payload: dict) -> SimulationRun:
    row = SimulationRun(
        use_case=str(payload["use_case"]),
        model_version=str(payload["model_version"]),
        policy_version=str(payload["policy_version"]),
        fusion=str(payload["fusion"]),
        payload=payload,
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    return row


@router.post("/run")
def run_simulation(body: SimulationRunRequest, db: Session = Depends(get_db)):
    name = body.use_case.strip().lower()
    if name == "all":
        rows = [_persist(db, payload) for payload in run_all()]
        return SimulationRunListResponse(
            items=[SimulationRunRead.model_validate(row) for row in rows],
            total=len(rows),
        )
    if name not in KNOWN_USE_CASES:
        raise HTTPException(status_code=400, detail=f"Unknown use case {body.use_case!r}")
    try:
        payload = run_use_case(name)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    row = _persist(db, payload)
    return SimulationRunRead.model_validate(row)


@router.get("/runs", response_model=SimulationRunListResponse)
def list_runs(db: Session = Depends(get_db)):
    rows = list(db.scalars(select(SimulationRun).order_by(SimulationRun.created_at.desc())))
    return SimulationRunListResponse(
        items=[SimulationRunRead.model_validate(row) for row in rows],
        total=len(rows),
    )


@router.get("/runs/{run_id}", response_model=SimulationRunRead)
def get_run(run_id: UUID, db: Session = Depends(get_db)):
    row = db.get(SimulationRun, run_id)
    if row is None:
        raise HTTPException(status_code=404, detail="simulation run not found")
    return SimulationRunRead.model_validate(row)


@router.get("/runs/{run_id}/decisions/{external_id}", response_model=SimulationDecisionResponse)
def get_decision(run_id: UUID, external_id: str, db: Session = Depends(get_db)):
    row = db.get(SimulationRun, run_id)
    if row is None:
        raise HTTPException(status_code=404, detail="simulation run not found")
    payload = row.payload or {}
    match = None
    for item in list(payload.get("heroes") or []) + list(payload.get("sample_decisions") or []):
        if str(item.get("external_id")) == external_id:
            match = item
            break
    if match is None:
        raise HTTPException(status_code=404, detail=f"no decision for {external_id}")
    action_table = match.get("action_table") or []
    reasoning = [
        f"Fused P(Y)={match.get('probability')}",
        f"Selected {match.get('recommended_action')} with expected value {match.get('expected_value')}",
        "Action uplifts are simulated, not causal estimates.",
    ]
    return SimulationDecisionResponse(
        run_id=row.id,
        use_case=row.use_case,
        external_id=external_id,
        conversion_probability=float(match.get("probability") or 0),
        expected_revenue=float(match.get("expected_value") or 0),
        recommended_action=str(match.get("recommended_action")),
        confidence=float(match.get("agreement") or 0),
        reasoning=reasoning,
        model_version=row.model_version,
        policy_version=row.policy_version,
        action_table=action_table,
        evidence=match.get("evidence") or {},
        uplift_is_simulated=True,
    )
