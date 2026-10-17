"""Versioned ProblemSpec records. User intent only — not TaskSpec replacement."""

from __future__ import annotations

import hashlib
import json
import re
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.db.models import ProblemSpec, Project, User
from app.domain.errors import IdentityError, ProblemSpecNotFoundError, ProjectNotFoundError
from app.domain.workspace_identity import ProblemSpecDetailRead, creator_read
from app.services.authorization_service import can_perform_ml_write, can_read_workspace


def problem_spec_digest(payload: dict[str, Any]) -> str:
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def _content_payload(
    *,
    task_type: str,
    business_objective: str,
    target_column: str | None,
    prediction_unit: str | None,
    prediction_time_column: str | None,
    prediction_horizon: str | None,
    primary_metric: str | None,
    constraints: dict[str, Any],
    success_criteria: dict[str, Any],
) -> dict[str, Any]:
    return {
        "business_objective": business_objective,
        "constraints": constraints,
        "prediction_horizon": prediction_horizon,
        "prediction_time_column": prediction_time_column,
        "prediction_unit": prediction_unit,
        "primary_metric": primary_metric,
        "success_criteria": success_criteria,
        "target_column": target_column,
        "task_type": task_type,
    }


def _next_version(db: Session, project_id: UUID) -> int:
    current = db.scalar(
        select(func.max(ProblemSpec.version)).where(ProblemSpec.project_id == project_id)
    )
    return int(current or 0) + 1


def create_problem_spec(
    db: Session,
    *,
    actor: User,
    workspace_id: UUID,
    project_id: UUID,
    task_type: str,
    business_objective: str,
    target_column: str | None = None,
    prediction_unit: str | None = None,
    prediction_time_column: str | None = None,
    prediction_horizon: str | None = None,
    primary_metric: str | None = None,
    constraints: dict[str, Any] | None = None,
    success_criteria: dict[str, Any] | None = None,
    status: str = "draft",
    created_by_service_token_id: UUID | None = None,
    plan: UUID | None = None,
    record_goal: str | None = None,
) -> ProblemSpec:
    if not can_perform_ml_write(db, actor, workspace_id):
        raise IdentityError(
            "creating a problem spec requires an ML-write workspace role",
            status_code=403,
        )
    project = db.get(Project, project_id)
    if project is None or project.workspace_id != workspace_id:
        raise ProjectNotFoundError("project not found")
    if status not in {"draft", "locked"}:
        raise IdentityError("problem spec status must be draft or locked")
    constraints = constraints or {}
    success_criteria = success_criteria or {}
    if plan is not None:  # P6.9-A: a run plan's target / metric answers fill the spec (both L1)
        target_column, primary_metric = _plan_answers(db, project, plan, task_type=task_type, constraints=constraints,
                                                      target_column=target_column, primary_metric=primary_metric)
    if created_by_service_token_id is not None:
        _check_agent_spec_text(
            task_type=task_type, business_objective=business_objective, target_column=target_column,
            prediction_unit=prediction_unit, prediction_time_column=prediction_time_column,
            prediction_horizon=prediction_horizon, primary_metric=primary_metric,
            constraints=constraints, success_criteria=success_criteria,
        )
    locked_at = datetime.now(UTC) if status == "locked" else None
    spec = ProblemSpec(
        workspace_id=project.workspace_id,
        project_id=project.id,
        version=_next_version(db, project.id),
        task_type=task_type.strip(),
        target_column=target_column,
        prediction_unit=prediction_unit,
        prediction_time_column=prediction_time_column,
        prediction_horizon=prediction_horizon,
        primary_metric=primary_metric,
        business_objective=business_objective.strip(),
        constraints=constraints,
        success_criteria=success_criteria,
        status=status,
        content_digest=problem_spec_digest(
            _content_payload(
                task_type=task_type.strip(),
                business_objective=business_objective.strip(),
                target_column=target_column,
                prediction_unit=prediction_unit,
                prediction_time_column=prediction_time_column,
                prediction_horizon=prediction_horizon,
                primary_metric=primary_metric,
                constraints=constraints,
                success_criteria=success_criteria,
            )
        ),
        created_by=actor.id,
        created_by_service_token_id=created_by_service_token_id,
        locked_at=locked_at,
    )
    db.add(spec)
    db.flush()
    if record_goal is not None:  # D3a: a goal saved in the product ("author" or an accepted AI "assistant" proposal)
        from app.services.goal_decision_service import record_goal_version

        record_goal_version(db, spec=spec, user=actor, service_token_id=created_by_service_token_id,
                            from_plan=plan is not None, from_proposal=record_goal == "assistant")
    return spec


def _plan_answers(db: Session, project: Project, plan: UUID, *, task_type: str, constraints: dict[str, Any],
                  target_column: str | None, primary_metric: str | None) -> tuple[str | None, str | None]:
    """(target, metric) with the plan's answers; a request value that differs is refused,
    and a plan metric must be valid for the task (the request validator's own check)."""

    from app.domain.errors import PlanRefusedError
    from app.engine.modeling.objective import parse_objective
    from app.services.run_plan_service import plan_spec_answers

    from app.services.run_plan_service import plan_spec_refusals

    answers = plan_spec_answers(db, workspace_id=project.workspace_id, project_id=project.id, plan_id=plan)
    given = {"target_column": target_column, "primary_metric": primary_metric}
    refused = plan_spec_refusals(db, workspace_id=project.workspace_id, project_id=project.id, plan_id=plan)
    if any(given.get(name) is not None for name in refused):  # the request may not carry what the plan may not
        raise PlanRefusedError(next(iter(refused.values())))
    if any(given[name] is not None and given[name] != value for name, value in answers.items()):
        raise PlanRefusedError("plan_conflicts_with_request")
    merged = {**given, **answers}
    try:
        parse_objective(task_type, primary_metric=merged["primary_metric"], constraints=constraints)
    except ValueError as exc:
        raise PlanRefusedError("plan_metric_invalid_for_task") from exc
    return merged["target_column"], merged["primary_metric"]


def _check_agent_spec_text(**fields: Any) -> None:
    """Agent-written specs get the decision-record text gate: no control characters,
    no secret-like text (``dclab_st_...``, bearer tokens, keys) in any field.
    Raises ``InvalidDecisionRecordError`` (422)."""

    from app.services.decision_record_service import check_agent_text, clean_json_object

    for name, value in fields.items():
        if isinstance(value, dict):
            clean_json_object(value, label=name)
        elif value is not None:
            check_agent_text(str(value), label=name)


def populate_unlocked_problem_spec_target(
    db: Session,
    spec: ProblemSpec,
    target_column: str,
) -> ProblemSpec:
    """Fill a NULL target on an unlocked draft. Locked specs are never mutated."""

    name = (target_column or "").strip()
    if not name:
        return spec
    if spec.locked_at is not None or spec.status == "locked":
        return spec
    if (spec.target_column or "").strip():
        return spec
    spec.target_column = name
    spec.content_digest = problem_spec_digest(
        _content_payload(
            task_type=spec.task_type,
            business_objective=spec.business_objective,
            target_column=spec.target_column,
            prediction_unit=spec.prediction_unit,
            prediction_time_column=spec.prediction_time_column,
            prediction_horizon=spec.prediction_horizon,
            primary_metric=spec.primary_metric,
            constraints=spec.constraints or {},
            success_criteria=spec.success_criteria or {},
        )
    )
    db.flush()
    return spec


def list_problem_specs(
    db: Session, *, actor: User, workspace_id: UUID, project_id: UUID
) -> list[ProblemSpec]:
    if not can_read_workspace(db, actor, workspace_id):
        raise IdentityError("not authorized for this workspace", status_code=403)
    project = db.get(Project, project_id)
    if project is None or project.workspace_id != workspace_id:
        raise ProjectNotFoundError("project not found")
    return list(
        db.scalars(
            select(ProblemSpec)
            .where(
                ProblemSpec.workspace_id == workspace_id,
                ProblemSpec.project_id == project_id,
            )
            .order_by(ProblemSpec.version.desc())
        )
    )


def get_problem_spec(
    db: Session,
    *,
    actor: User,
    workspace_id: UUID,
    project_id: UUID,
    spec_id: UUID,
) -> ProblemSpec:
    if not can_read_workspace(db, actor, workspace_id):
        raise IdentityError("not authorized for this workspace", status_code=403)
    spec = db.get(ProblemSpec, spec_id)
    if (
        spec is None
        or spec.workspace_id != workspace_id
        or spec.project_id != project_id
    ):
        raise ProblemSpecNotFoundError("problem spec not found")
    return spec


def _current_spec_id(db: Session, workspace_id: UUID, project_id: UUID) -> UUID | None:
    from app.services.project_ref_service import current_refs

    ref = current_refs(db, workspace_id=workspace_id, project_id=project_id).get("problem_spec")
    return ref.problem_spec_id if ref is not None else None


_HOLDOUT_KEY = re.compile(r"holdout|final_test", re.IGNORECASE)


def strip_holdout_keys(value: Any) -> Any:
    """Drop every mapping key matching ``holdout|final_test`` at any depth (ADR 0008 §2b)."""

    if isinstance(value, dict):
        return {k: strip_holdout_keys(v) for k, v in value.items() if not _HOLDOUT_KEY.search(str(k))}
    if isinstance(value, list):
        return [strip_holdout_keys(item) for item in value]
    return value


def problem_spec_detail(spec: ProblemSpec, *, viewer_id: UUID | None, current_id: UUID | None,
                        agent_reader: bool = False) -> ProblemSpecDetailRead:
    """V7 BE-C read shape: the spec without user ids; ``viewer_id`` None for service tokens.

    ``agent_reader`` (a service token / agent): user-written ``constraints`` and ``success_criteria``
    lose keys matching holdout|final_test and ``business_objective`` gets the public untrusted-text
    projection. People keep the full text."""

    from app.services.graph_service import untrusted_text

    constraints, criteria = dict(spec.constraints or {}), dict(spec.success_criteria or {})
    objective = spec.business_objective
    if agent_reader:
        constraints, criteria = strip_holdout_keys(constraints), strip_holdout_keys(criteria)
        objective = untrusted_text(objective, 4000) or ""
    return ProblemSpecDetailRead(
        id=spec.id, workspace_id=spec.workspace_id, project_id=spec.project_id, version=spec.version,
        task_type=spec.task_type, target_column=spec.target_column, prediction_unit=spec.prediction_unit,
        prediction_time_column=spec.prediction_time_column, prediction_horizon=spec.prediction_horizon,
        primary_metric=spec.primary_metric, business_objective=objective,
        constraints=constraints, success_criteria=criteria,
        status=spec.status, content_digest=spec.content_digest, is_current=spec.id == current_id,
        creator=creator_read(user_id=spec.created_by, service_token_id=spec.created_by_service_token_id,
                             viewer_id=viewer_id, kind="person"),
        created_at=spec.created_at, locked_at=spec.locked_at,
    )


def list_problem_spec_details(
    db: Session, *, actor: User, workspace_id: UUID, project_id: UUID, viewer_id: UUID | None, limit: int,
    agent_reader: bool = False,
) -> list[ProblemSpecDetailRead]:
    """Newest version first, at most ``limit``."""

    specs = list_problem_specs(db, actor=actor, workspace_id=workspace_id, project_id=project_id)[:limit]
    current = _current_spec_id(db, workspace_id, project_id)
    return [problem_spec_detail(spec, viewer_id=viewer_id, current_id=current, agent_reader=agent_reader)
            for spec in specs]


def get_problem_spec_detail(
    db: Session, *, actor: User, workspace_id: UUID, project_id: UUID, spec_id: UUID, viewer_id: UUID | None,
    agent_reader: bool = False,
) -> ProblemSpecDetailRead:
    spec = get_problem_spec(db, actor=actor, workspace_id=workspace_id, project_id=project_id, spec_id=spec_id)
    return problem_spec_detail(spec, viewer_id=viewer_id, current_id=_current_spec_id(db, workspace_id, project_id),
                               agent_reader=agent_reader)
