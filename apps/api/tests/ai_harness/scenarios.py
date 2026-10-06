"""Scenario runners shared by the harness tests (and the R3 runner): one specialist run,
one lead-agent turn, the three Jev decision points of auto-train. Each takes any
``FakeProvider`` (a recorded ``Scenario``, a script or a ``Chaos`` handler) and drives the
real harness and gateway. Fixtures come from the existing suites (``ac``, ``lead``, ``hk``)."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any
from uuid import UUID

from sqlalchemy import func, select, text

from app.agents.classes import dataset_investigator as inv
from app.agents.classes import experiment_critic as critic
from app.agents.classes import experiment_planner as planner
from app.agents.contracts import LeadTurn, RunLimits, Untrusted
from app.agents.gateway.limits import GatewayLimits
from app.agents.gateway.providers.fake import FakeProvider
from app.agents.gateway.service import GatewayService
from app.agents.harness.service import AgentService
from app.agents.lead.fake_driver import answer, tools
from ai_harness.kit import holdout_canaries
import test_auto_train_decision_points as tadp
from app.db.models import (
    AgentProposal,
    Dataset,
    DatasetColumn,
    EvaluationMetric,
    Experiment,
    ModelEvaluation,
    ModelSelectionDecision,
    ModelVersion,
    ProblemSpec,
    ProjectDecisionRecord,
    ProjectRef,
    SplitPlan,
)
from test_agent_classes import ON, _review, _spec
from test_auto_train_decision_points import jev, leakage, roles

CV_VALUE = 0.8123  # the seeded CV winner: reaches the model when the data policy allows it
CANARY_CELL = "zq-canary-region-7731"  # a raw cell in every dataset file and column value list
CANARY_FILE_ROW = 424242.5  # a cell only in an extra dataset-file row (never in any DB field)
CANARY_TRAIN_CELL = 913041  # a training-frame cell of the Jev scenario
# An extra final-holdout evaluation (far from every CV value): outcomes (canaries) and its size (allowed).
CANARY_HOLDOUT_METRICS = {"roc_auc": 0.6417, "positive_rate": 0.3436}
CANARY_HOLDOUT_SUMMARY = {"n_rows": 1237, "label_counts": {"0": 812, "1": 425}}


def sentinels(db: Any, workspace_id: UUID) -> dict[str, tuple]:
    """Everything that must never reach a provider: the workspace's final-holdout outcomes
    (values, renderings, label counts — from the database) and the raw-cell / file-row canaries."""

    canaries = holdout_canaries(db, workspace_id)
    return {"numbers": (*canaries["numbers"], CANARY_FILE_ROW), "renderings": canaries["renderings"],
            "counts": (*canaries["counts"], CANARY_TRAIN_CELL), "cells": (CANARY_CELL,)}


LEAD_TEXT = "Check the branch and set up a churn spec."
TIGHT = dict(steps=8, tokens=60000, wall_s=120, tool_calls=20, cost_micros=1_000)  # the worst case cannot fit

SPECIALIST_AGENTS = {"experiment_critic": critic.AGENT_KEY, "dataset_investigator": inv.AGENT_KEY,
                     "experiment_planner": planner.AGENT_KEY}


def specialist_answer(ac: Any, agent_key: str) -> dict[str, Any]:
    if agent_key == critic.AGENT_KEY:
        return json.loads(json.dumps(_review()).replace("{exp}", str(ac.g.exp[1])))
    if agent_key == inv.AGENT_KEY:
        return {"target_candidates": [{"column": "target", "rank": 1, "reason": "binary outcome name"}],
                "missing_values": [{"column": "feature", "action": "impute_median", "reason": "numeric"}],
                "leakage_suspects": [], "questions": ["Is signup_date known at prediction time?"], "confidence": 0.6}
    return {"task_type": "binary", "primary_metric": "pr_auc", "split": None, "families": ["logistic_regression"],
            "max_training_seconds": 300.0, "rationale": "Imbalanced outcome: rank by precision-recall.",
            "confidence": 0.7}


def gateway_for(provider: FakeProvider, settings: Any, *, providers: dict | None = None,
                limits: GatewayLimits | None = None) -> GatewayService:
    return GatewayService(providers=providers or {"openai": provider}, limits=limits or GatewayLimits(),
                          settings=lambda: settings)


def plant_canaries(ns: Any, db: Any, workspace_id: UUID) -> None:
    """Once per graph: a canary row in every dataset file and canary cells in every column's
    value lists (``top_values`` / ``sample_values``); none may ever reach a provider."""

    if getattr(ns, "canaries", False):
        return
    ns.canaries = True
    for dataset in db.scalars(select(Dataset).where(Dataset.workspace_id == workspace_id)):
        path = Path(dataset.location or "")
        if path.suffix == ".csv" and path.is_file():
            with path.open("a", encoding="utf-8") as handle:
                handle.write(f"{CANARY_FILE_ROW},{CANARY_CELL}\n")
    for column in db.scalars(select(DatasetColumn).where(DatasetColumn.workspace_id == workspace_id)):
        column.stats = {**(column.stats or {}), "top_values": [{"value": CANARY_CELL, "count": 1}],
                        "sample_values": [CANARY_CELL]}
    db.commit()
    selected = db.scalar(select(ModelSelectionDecision).where(ModelSelectionDecision.workspace_id == workspace_id)
                         .order_by(ModelSelectionDecision.created_at, ModelSelectionDecision.id).limit(1))
    seeded = selected and db.scalar(select(ModelEvaluation).where(
        ModelEvaluation.workspace_id == workspace_id, ModelEvaluation.evaluation_scope == "final_holdout")
        .order_by(ModelEvaluation.created_at, ModelEvaluation.id).limit(1))
    if seeded:  # on the CV winner the Critic and the lead read (exp[1]'s selected candidate)
        evaluation = ModelEvaluation(workspace_id=workspace_id, project_id=selected.project_id,
                                     candidate_id=selected.selected_candidate_id, evaluation_type="final_holdout",
                                     evaluation_scope="final_holdout", dataset_id=seeded.dataset_id,
                                     status="completed", summary=CANARY_HOLDOUT_SUMMARY)
        seed_locked(db, evaluation, lambda: [EvaluationMetric(model_evaluation_id=evaluation.id, metric_name=name,
                                                              metric_value=value)
                                             for name, value in CANARY_HOLDOUT_METRICS.items()])


def seed_locked(db: Any, first: Any, then: Any = lambda: []) -> None:
    """Test-only seed of evidence rows on an already evidence-locked run: the lock triggers are
    skipped for this one transaction (``SET LOCAL``) and are back on right after."""

    db.execute(text("SET LOCAL session_replication_role = replica"))
    db.add(first)
    db.flush()
    db.add_all(then())
    db.commit()
    assert db.scalar(text("SHOW session_replication_role")) == "origin"


def run_specialist(ac: Any, agent_key: str, gateway: GatewayService, **spec: Any) -> Any:
    plant_canaries(ac, ac.db, ac.g.ws)
    if "limits" in spec and isinstance(spec["limits"], dict):
        spec["limits"] = RunLimits(**spec["limits"])
    result = AgentService(gateway=gateway, settings=lambda: ON).run(ac.db, _spec(ac, agent_key, **spec))
    ac.db.expire_all()
    return result


def lead_steps(g: Any) -> list[dict[str, Any]]:
    project, e1 = str(g.project.id), g.exp[1]
    return [tools(("get_experiment", {"experiment_id": str(e1)})),
            tools(("propose_problem_spec", {"project_id": project, "task_type": "binary", "target_column": "target",
                                            "business_objective": "Predict churn.", "rationale": "binary target",
                                            "primary_metric": "roc_auc"})),
            answer(f"The spec awaits your confirmation. [The branch](dclab://experiment/{e1}) reaches CV roc_auc "
                   "0.812.", ("experiment", e1))]


def run_lead(lead: Any, handler: Any, gateway: GatewayService | None = None, **spec: Any) -> Any:
    """``handler`` answers ``lead.fake`` (the lead fixture's provider); ``gateway`` defaults
    to a fresh gateway over it."""

    plant_canaries(lead, lead.db, lead.g.ws)
    lead.driver = handler
    if "limits" in spec and isinstance(spec["limits"], dict):
        spec["limits"] = RunLimits(**spec["limits"])
    gateway = gateway or gateway_for(lead.fake, lead.settings)
    turn = LeadTurn(user_text=Untrusted(untrusted_text=LEAD_TEXT))
    result = AgentService(gateway=gateway, settings=lambda: lead.settings).run(lead.db, lead.spec(turn=turn, **spec))
    lead.db.expire_all()
    return result


JEV_WRONG = {"column.is_identifier": {"*": (0.99, None)},
             "column.semantic_role": {"feature": ("categorical_code", 0.9)},
             "feature.leakage_suspect": {"*": (0.97, None)}}


def jev_script(table: dict = JEV_WRONG):
    fake = jev(table)
    return lambda call: fake.complete(call).output


def run_jev(hk: Any, provider: FakeProvider | None, level: int = 0, *, service: Any = None) -> dict[str, Any]:
    """Column roles + leakage through the Jev decision points (``provider=None``: the AI-off
    run); returns the values used."""

    from app.agents.governance.snapshot import PolicySnapshot

    plant_canaries(hk, hk.db, hk.ws_a)
    ctx = tadp.Ctx(hk, PolicySnapshot.off("ai_disabled")) if provider is None else hk.ctx(
        provider, level, service=service)
    frame, tadp.FRAME = tadp.FRAME, tadp.FRAME.copy()  # the training frame carries a canary cell
    tadp.FRAME.loc[0, "feature"] = CANARY_TRAIN_CELL
    try:
        numeric, categorical, applied = roles(ctx)
        point = leakage(ctx)
    finally:
        tadp.FRAME = frame
    return {"numeric": numeric, "categorical": categorical, "applied": sorted(applied),
            "leakage": [(a.question_key, a.used) for a in point.answers], "ctx": ctx}


def columns(db: Any, workspace_id: UUID) -> list[str]:
    return list(db.scalars(select(DatasetColumn.name).where(DatasetColumn.workspace_id == workspace_id)))


def graph_state(db: Any, workspace_id: UUID) -> dict[str, Any]:
    """The product state an AI run must never change (the AI-off run changes none of it)."""

    db.expire_all()
    count = {model.__name__: db.scalar(select(func.count()).select_from(model).where(
        model.workspace_id == workspace_id)) for model in (ProblemSpec, AgentProposal, ProjectDecisionRecord,
                                                           ModelSelectionDecision, ModelEvaluation, ModelVersion,
                                                           SplitPlan)}

    def rows(model, *fields):
        return sorted(json.dumps([getattr(r, f) for f in fields], sort_keys=True, default=str)
                      for r in db.scalars(select(model).where(model.workspace_id == workspace_id)))

    return {**count, "experiments": rows(Experiment, "id", "status", "result", "config"),
            "selections": rows(ModelSelectionDecision, "id", "selected_candidate_id", "selected_score"),
            "metrics": sorted(json.dumps([str(m), n, v]) for m, n, v in db.execute(
                select(EvaluationMetric.model_evaluation_id, EvaluationMetric.metric_name, EvaluationMetric.metric_value)
                .join(ModelEvaluation, ModelEvaluation.id == EvaluationMetric.model_evaluation_id)
                .where(ModelEvaluation.workspace_id == workspace_id))),
            "refs": rows(ProjectRef, "ref_kind", "model_version_id", "split_plan_id", "version"),
            "columns": rows(DatasetColumn, "id", "role", "semantic_type", "model_use_policy")}
