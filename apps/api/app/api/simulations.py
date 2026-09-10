from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy.orm import Session

from app.api.deps import request_workspace_id, require_workspace_read
from app.db.session import get_db
from app.domain.errors import IdentityError, SimulationRunNotFoundError
from app.domain.simulation import (
    KNOWN_USE_CASES,
    SimulationDecisionResponse,
    SimulationRunListResponse,
    SimulationRunRead,
    SimulationRunRequest,
)
from app.services.simulation_run_service import (
    get_workspace_simulation_run,
    list_workspace_simulation_runs,
    persist_simulation_run,
)
from app.sim.runner import run_all, run_use_case

router = APIRouter(
    prefix="/simulations",
    tags=["simulations"],
    dependencies=[Depends(require_workspace_read)],
)


def _persist(db: Session, workspace_id, payload: dict, project_id) -> SimulationRunRead:
    try:
        row = persist_simulation_run(
            db, workspace_id=workspace_id, payload=payload, project_id=project_id
        )
    except IdentityError as exc:
        raise HTTPException(status_code=exc.status_code, detail=str(exc)) from exc
    return SimulationRunRead.model_validate(row)


@router.post("/run")
def run_simulation(
    request: Request,
    body: SimulationRunRequest,
    db: Session = Depends(get_db),
):
    workspace_id = request_workspace_id(request)
    name = body.use_case.strip().lower()
    if name == "all":
        rows = [
            _persist(db, workspace_id, payload, body.project_id) for payload in run_all()
        ]
        return SimulationRunListResponse(items=rows, total=len(rows))
    if name not in KNOWN_USE_CASES:
        raise HTTPException(status_code=400, detail=f"Unknown use case {body.use_case!r}")
    try:
        payload = run_use_case(name)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return _persist(db, workspace_id, payload, body.project_id)


@router.get("/runs", response_model=SimulationRunListResponse)
def list_runs(request: Request, db: Session = Depends(get_db)):
    workspace_id = request_workspace_id(request)
    rows = list_workspace_simulation_runs(db, workspace_id=workspace_id)
    return SimulationRunListResponse(
        items=[SimulationRunRead.model_validate(row) for row in rows],
        total=len(rows),
    )


@router.get("/runs/{run_id}", response_model=SimulationRunRead)
def get_run(request: Request, run_id: UUID, db: Session = Depends(get_db)):
    try:
        row = get_workspace_simulation_run(
            db, workspace_id=request_workspace_id(request), run_id=run_id
        )
    except SimulationRunNotFoundError as exc:
        raise HTTPException(status_code=404, detail="simulation run not found") from exc
    return SimulationRunRead.model_validate(row)


@router.get("/runs/{run_id}/decisions/{external_id}", response_model=SimulationDecisionResponse)
def get_decision(
    request: Request, run_id: UUID, external_id: str, db: Session = Depends(get_db)
):
    try:
        row = get_workspace_simulation_run(
            db, workspace_id=request_workspace_id(request), run_id=run_id
        )
    except SimulationRunNotFoundError as exc:
        raise HTTPException(status_code=404, detail="simulation run not found") from exc
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
