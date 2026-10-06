"""Read tools of the catalog (ADR 0009 §6): input schema, fetch, neutral shaper.

``fetch(reads, args)`` composes ``/v1`` reads exactly like ``packages/dclab_mcp`` does
over the SDK; in-process ``reads`` is ``ServiceReads`` (the service functions the
``/v1`` routes call, with the same authorization and ``include_final_evaluation=False``).
Composite fetches authorize first (``get_evidence``: the model build read authorizes
before ``list_artifacts``; ``accept_proposal``: ``get_project`` before the record is
read out); the workspace id is never ``None``. Shapers are principal-independent and
holdout-free (``cv_record`` / ``cv_only`` / ``withhold_holdout_code`` / the card's final
evaluation dropped); their MCP rendering equals ``dclab_mcp``'s output.
"""

from __future__ import annotations

import logging
from collections.abc import Iterator
from contextlib import contextmanager
from typing import Annotated, Any, Literal
from uuid import UUID

from pydantic import BaseModel, Field

from app.agents.tools.catalog import ToolContext, ToolError
from app.agents.tools.shaping import (
    HOLDOUT_RESULT_STAGES,
    Code,
    Shaped,
    bound,
    cap_code,
    code,
    cv_only,
    cv_record,
    data_text,
    sample_text,
    text,
    withhold_holdout_code,
)

logger = logging.getLogger("dclab.agents.tools")
LIST_LIMIT = 50
GRAPH_NODE_LIMIT = 40
RECENT_EXPERIMENTS = 10
FINDING_LIMIT = 10
FINDING_EVIDENCE_CHARS = 1500
REF_MOVE_TYPES = frozenset({"ref_moved", "champion_promoted"})
GraphKind = Literal["problem_spec", "dataset_version", "split_plan", "feature_recipe", "experiment", "model_version"]

Id = Annotated[UUID, Field(description="Resource UUID.")]


class InspectProjectInput(BaseModel):
    project_id: UUID | None = Field(default=None, description="Project UUID; omit to list projects.")


class InspectDatasetInput(BaseModel):
    dataset_id: UUID | None = Field(default=None, description="Dataset UUID; omit to list datasets.")
    project_id: UUID | None = Field(default=None, description="Filter the list by project (applied to the newest 50 datasets: /v1/datasets has no project filter yet).")  # noqa: E501


class ExperimentInput(BaseModel):
    experiment_id: Id


class CompareInput(BaseModel):
    experiment_ids: list[UUID] = Field(min_length=2, max_length=10, description="2-10 experiment UUIDs on one split plan.")  # noqa: E501


class CodeInput(BaseModel):
    experiment_id: Id
    notebook: bool = Field(default=False, description="Return the notebook instead of the script.")


class ListDecisionsInput(BaseModel):
    project_id: Id
    effective_state: str | None = Field(default=None, description="proposed | accepted | rejected | superseded")
    decision_type: str | None = None
    limit: int = Field(default=20, ge=1, le=LIST_LIMIT)
    cursor: str | None = Field(default=None, max_length=256)


class ListProposalsInput(BaseModel):
    project_id: Id
    status: str | None = Field(default=None, description="proposed | accepted | applied | rejected | reverted | "
                                                         "superseded | expired | shadow | rejected_by_validator")
    decision_point_key: str | None = Field(default=None, max_length=64)
    proposal_type: str | None = Field(default=None, max_length=64)
    limit: int = Field(default=20, ge=1, le=LIST_LIMIT)
    cursor: str | None = Field(default=None, max_length=256)


class ModelInput(BaseModel):
    model_version_id: Id


class PredictionInput(BaseModel):
    prediction_id: Id


class ProposalInput(BaseModel):
    proposal_id: Id


class ImpactInput(BaseModel):
    kind: GraphKind = Field(description="Graph node kind.")
    node_id: Id


# --- in-process /v1 reads --------------------------------------------------------------------


@contextmanager
def map_service_errors() -> Iterator[None]:
    """Domain errors as typed tool errors (generic messages; nothing internal leaks)."""

    from app.domain.errors import IdentityError, InvalidCursorError

    try:
        yield
    except ToolError:
        raise
    except IdentityError as exc:
        status = exc.status_code
        name = {401: "unauthenticated", 403: "forbidden", 404: "not_found"}.get(status, "invalid_request")
        raise ToolError(name, "not authorized for this workspace" if status == 403 else name, status=status) from None
    except LookupError:
        raise ToolError("not_found", "not found", status=404) from None
    except InvalidCursorError:
        raise ToolError("invalid_cursor", "cursor is not valid for this request", status=400) from None
    except Exception as exc:  # typed domain refusals carry a stable code and status
        status = getattr(exc, "status_code", None)
        if isinstance(status, int) and isinstance(getattr(exc, "code", None), str):
            raise ToolError(exc.code, str(exc).split(": ", 1)[-1][:300], status=status) from None
        logger.exception("agent tool read failed")
        raise ToolError("internal_error", "the tool failed; retry later", status=500) from None


def _found(value: Any) -> Any:
    if value is None:
        raise ToolError("not_found", "not found", status=404)
    return value


class ServiceReads:
    """The ``/v1`` GETs the tools use, in-process, under ``ctx``'s actor and workspace."""

    def __init__(self, ctx: ToolContext) -> None:
        from app.services.authorization_service import can_read_workspace

        # Explorer services read platform-wide with None: never. Authorize up front like
        # /v1's require_workspace_read (no 403-vs-404 oracle for non-members).
        if ctx.workspace_id is None or not can_read_workspace(ctx.db, ctx.actor, ctx.workspace_id):
            raise ToolError("forbidden", "not authorized for this workspace", status=403)
        self.db, self.actor, self.ws = ctx.db, ctx.actor, ctx.workspace_id
        self._who = {"actor": ctx.actor, "workspace_id": ctx.workspace_id}

    def projects(self) -> list[Any]:
        from app.domain.workspace_identity import ProjectRead
        from app.services.project_service import list_projects

        return [ProjectRead.model_validate(row) for row in list_projects(self.db, **self._who)]

    def project(self, project_id: UUID) -> Any:
        from app.domain.workspace_identity import ProjectRead
        from app.services.project_service import get_project

        return ProjectRead.model_validate(get_project(self.db, **self._who, project_id=project_id))

    def graph(self, project_id: UUID, limit: int) -> Any:
        from app.services.graph_service import project_graph

        return project_graph(self.db, **self._who, project_id=project_id, limit=limit)

    def experiments(self, project_id: UUID, limit: int) -> Any:
        from app.services.experiment_service import list_experiments

        return list_experiments(self.db, **self._who, project_id=project_id, limit=limit)

    def datasets(self, limit: int) -> list[Any]:
        from app.services.technical_explorer_service import list_datasets

        return list_datasets(self.db, self.actor, workspace_id=self.ws, limit=limit)

    def dataset(self, dataset_id: UUID) -> Any:
        from app.services.technical_explorer_service import get_dataset

        return _found(get_dataset(self.db, self.actor, dataset_id, workspace_id=self.ws))

    def experiment(self, experiment_id: UUID) -> Any:
        from app.services.experiment_service import experiment_read

        return experiment_read(self.db, **self._who, experiment_id=experiment_id)

    def compare(self, experiment_ids: list[UUID]) -> Any:
        from app.domain.experiment_resources import ExperimentComparisonRead
        from app.services.experiment_branch_service import compare_side_by_side
        from app.services.experiment_service import experiments_for_compare

        rows = experiments_for_compare(self.db, **self._who, experiment_ids=experiment_ids)  # authorizes
        return ExperimentComparisonRead.model_validate(compare_side_by_side(self.db, rows))

    def code(self, experiment_id: UUID) -> Any:
        from app.services.model_build_reproduction_service import get_experiment_code

        return _found(get_experiment_code(self.db, self.actor, self.ws, experiment_id))

    def model_build(self, experiment_id: UUID) -> Any:
        from app.services.model_build_service import get_pipeline_model_build

        return get_pipeline_model_build(self.db, self.actor, self.ws, experiment_id)

    def artifacts(self, experiment_id: UUID) -> list[Any]:
        """Only after ``model_build`` authorized (``list_artifacts`` does not)."""

        from app.services.artifact_service import list_artifacts
        from app.services.audience_projection import artifact_read

        return [artifact_read(row) for row in list_artifacts(self.db, workspace_id=self.ws,
                                                             pipeline_run_id=experiment_id)]

    def findings(self, experiment_id: UUID) -> Any:
        from app.services.experiment_service import experiment_findings

        return experiment_findings(self.db, **self._who, experiment_id=experiment_id)

    def decisions(self, project_id: UUID, **filters: Any) -> Any:
        from app.services.decision_record_service import list_decisions

        return list_decisions(self.db, **self._who, project_id=project_id, **filters)

    def decision(self, decision_id: UUID) -> Any:
        from app.services import decision_record_service as drs
        from app.services.project_service import get_project

        row = drs.find_record(self.db, workspace_id=self.ws, record_id=decision_id)
        get_project(self.db, **self._who, project_id=row.project_id)  # authorizes the reader
        return drs.record_read(self.db, row)

    def proposals(self, project_id: UUID, **filters: Any) -> Any:
        from app.domain.proposal_reviews import ProposalPage
        from app.services import proposal_review_service as prs
        from app.services.project_service import get_project

        get_project(self.db, **self._who, project_id=project_id)  # authorizes the reader (404-safe)
        rows, cursor, limit = prs.list_proposals(self.db, workspace_id=self.ws, project_id=project_id, viewer=self.actor,
                                                 agent=True, **filters)  # no assistant tool calls for agents
        return ProposalPage(items=[prs.proposal_read(row, agent=True) for row in rows], next_cursor=cursor, limit=limit)

    def model_version(self, model_version_id: UUID) -> Any:
        from app.services.experiment_service import model_version_read

        return _found(model_version_read(self.db, **self._who, model_version_id=model_version_id))

    def model_card(self, model_version_id: UUID) -> Any:
        from app.services.experiment_service import model_card_read

        return _found(model_card_read(self.db, **self._who, model_version_id=model_version_id,
                                      include_final_evaluation=False))

    def prediction(self, prediction_id: UUID) -> Any:
        from app.services.batch_prediction_service import batch_prediction_read

        return _found(batch_prediction_read(self.db, **self._who, prediction_id=prediction_id))

    def impact(self, kind: str, node_id: UUID) -> Any:
        from app.services.graph_service import impact

        return impact(self.db, **self._who, kind=kind, node_id=node_id)


# --- neutral summaries (same shapes as dclab_mcp.server's) ------------------------------------


def _ids(*values: Any) -> tuple[UUID, ...]:
    return tuple(dict.fromkeys(UUID(str(v)) for v in values if v is not None))


def _project(p: Any) -> dict[str, Any]:
    return {"id": p.id, "name": text(p.name, 200), "slug": text(p.slug, 200), "description": text(p.description),
            "status": code(p.status), "created_at": p.created_at}


def _dataset(d: Any) -> dict[str, Any]:
    return {"id": d.id, "project_id": d.project_id, "name": data_text(d.name, 200), "version": d.version,
            "row_count": d.row_count, "column_count": d.column_count, "content_digest": d.content_digest,
            "created_at": d.created_at}


def _experiment(e: Any) -> dict[str, Any]:
    return {
        "id": e.id, "project_id": e.project_id, "status": code(e.status), "created_at": e.created_at,
        "ended_at": e.ended_at, "cancel_requested": e.cancel_requested_at is not None, "task_type": code(e.task_type),
        "target_column": data_text(e.target_column, 200), "intent": data_text(e.intent),
        "failure_reason": data_text(e.failure_reason, 500), "lineage": e.lineage.model_dump(mode="json"),
        "change_set": data_text(e.change_set, 2000), "metrics": cv_record(e.metrics),
        "diff_vs_parent": bound(cv_only(e.diff_vs_parent), max_items=20),
    }


def decision_summary(r: Any) -> dict[str, Any]:
    # Agent-authored rationale may echo dataset content: dataset text (ADR 0005 labels).
    rationale = data_text(r.rationale) if r.content_origin == "agent" else text(r.rationale)
    return {
        "id": r.id, "project_id": r.project_id, "decision_type": code(r.decision_type), "state": code(r.state),
        "effective_state": code(r.effective_state), "supersedes_id": r.supersedes_id, "subject": Code(r.subject.key),
        "actor_kind": code(r.actor.kind), "content_origin": code(r.content_origin), "rationale": rationale,
        "rationale_label": code(r.rationale_label),
        # Facts/details may carry CV scores (e.g. the locked selected_score): aggregates of the
        # project's datasets (list_decisions); without a recorded source they are omitted.
        "facts": data_text(cv_only(r.facts), 1500, aggregate=True),
        "details": data_text(cv_only(r.details), 1500, aggregate=True),
        "evidence_refs": cv_only([ref.model_dump(mode="json", exclude_none=True) for ref in r.evidence_refs[:20]]),
        "recorded_at": r.recorded_at, "replayed": getattr(r, "idempotent_replay", False),
    }


def _graph(g: Any) -> dict[str, Any]:
    nodes = [{"key": Code(n.key), "status": code(n.status), "label": data_text(n.label, 200),
              "intent": data_text(n.intent, 300), "stale": n.stale, "ref_kinds": [Code(k) for k in n.ref_kinds]}
             for n in g.nodes[:GRAPH_NODE_LIMIT]]
    refs = [{"ref_kind": Code(r.ref_kind), "target": Code(r.target.key), "version": r.version, "stale": r.stale}
            for r in g.refs]
    return {"refs_initialized": g.refs_initialized, "refs": refs, "counts_by_kind": g.counts_by_kind,
            "stale_counts_by_kind": g.stale_counts_by_kind, "nodes": nodes,
            "nodes_omitted": max(0, len(g.nodes) - GRAPH_NODE_LIMIT), "edge_count": len(g.edges),
            "truncated": g.truncated}


def prediction_summary(p: Any) -> dict[str, Any]:
    output = p.output and {"artifact_id": p.output.artifact_id, "content_digest": p.output.content_digest,
                           "size_bytes": p.output.size_bytes, "mime_type": p.output.mime_type}
    return {
        "id": p.id, "project_id": p.project_id, "model_version_id": p.model_version_id,
        "input_dataset_id": p.input_dataset_id, "status": code(p.status), "output_format": code(p.output_format),
        "rows_in": p.rows_in, "rows_out": p.rows_out, "decision_threshold": p.decision_threshold,
        "contract_check": data_text(p.contract_check, 1500), "error_code": code(p.error_code),
        "error_message": data_text(p.error_message, 300), "output": output, "created_at": p.created_at,
        "started_at": p.started_at, "completed_at": p.completed_at,
        "download": output and {"human_api": f"GET /v1/predictions/{p.id}/download",
                                "cli": f"dclab-cli predict download {p.id} -o FILE",
                                "note": "Predicted rows are never returned to agents."},
    }


# --- per-tool fetch + shape -------------------------------------------------------------------


def _inspect_project_fetch(reads: Any, a: InspectProjectInput) -> dict[str, Any]:
    if a.project_id is None:
        return {"projects": reads.projects()}
    return {"project": reads.project(a.project_id), "graph": reads.graph(a.project_id, GRAPH_NODE_LIMIT),
            "recent": reads.experiments(a.project_id, RECENT_EXPERIMENTS)}


def _inspect_project_shape(raw: dict[str, Any]) -> Shaped:
    if "projects" in raw:
        rows = raw["projects"]
        return Shaped({"projects": [_project(p) for p in rows[:LIST_LIMIT]], "omitted": max(0, len(rows) - LIST_LIMIT)})
    recent = raw["recent"].items
    datasets = _ids(*(n.id for n in raw["graph"].nodes if n.kind == "dataset_version"),
                    *(i.source_dataset_id for i in recent))
    return Shaped({
        "project": _project(raw["project"]), "graph": _graph(raw["graph"]),
        "recent_experiments": [
            {"id": i.id, "status": code(i.status), "created_at": i.created_at,
             "parent_experiment_id": i.parent_experiment_id, "has_change_set": i.has_change_set,
             "intent": data_text(i.intent, 300)}
            for i in recent
        ],
    }, source_datasets=datasets)


_DATASET_NOTE = "Row and column counts only; dataset rows are never returned."


def _inspect_dataset_fetch(reads: Any, a: InspectDatasetInput) -> dict[str, Any]:
    if a.dataset_id is not None:
        return {"dataset": reads.dataset(a.dataset_id)}
    return {"datasets": [d for d in reads.datasets(LIST_LIMIT) if a.project_id is None or d.project_id == a.project_id]}


def _inspect_dataset_shape(raw: dict[str, Any]) -> Shaped:
    if "dataset" in raw:
        return Shaped({"dataset": _dataset(raw["dataset"]), "note": _DATASET_NOTE}, source_datasets=_ids(raw["dataset"].id))
    rows = raw["datasets"]
    return Shaped({"datasets": [_dataset(d) for d in rows], "note": _DATASET_NOTE},
                  source_datasets=_ids(*(d.id for d in rows)))


def _experiment_fetch(reads: Any, a: ExperimentInput) -> dict[str, Any]:
    return {"experiment": reads.experiment(a.experiment_id)}


def _cv_shaped(payload: dict[str, Any], experiment: Any, *aggregates: str) -> Shaped:
    return Shaped(payload, data_class="aggregates", outcome_scope="cv",
                  source_datasets=_ids(experiment.lineage.source_dataset_id), aggregates=aggregates)


def _get_experiment_shape(raw: dict[str, Any]) -> Shaped:
    return _cv_shaped({"experiment": _experiment(raw["experiment"])}, raw["experiment"],
                      "experiment.metrics", "experiment.diff_vs_parent")


def _compare_fetch(reads: Any, a: CompareInput) -> dict[str, Any]:
    comparison = reads.compare(list(a.experiment_ids))
    return {"comparison": comparison, "first": reads.experiment(a.experiment_ids[0])}  # one split plan: one dataset


def _compare_shape(raw: dict[str, Any]) -> Shaped:
    c = raw["comparison"]
    return _cv_shaped({"comparison": {"split_plan_id": c.split_plan_id, "authoritative": c.authoritative,
                                      "experiments": [cv_record(item) for item in c.experiments],
                                      "common_cv_metrics": [Code(m) for m in c.common.cv]},
                       "note": "CV metrics only; final-holdout values are never shown to agents."},
                      raw["first"], "comparison.experiments")


def _code_fetch(reads: Any, a: CodeInput) -> dict[str, Any]:
    return {"code": reads.code(a.experiment_id), "experiment": reads.experiment(a.experiment_id),
            "notebook": a.notebook}


def _code_shape(raw: dict[str, Any]) -> Shaped:
    code_read = raw["code"]
    doc = code_read.notebook if raw["notebook"] else code_read.script
    source, truncated = cap_code(withhold_holdout_code(doc.source))
    return _cv_shaped({
        "experiment_id": code_read.experiment_id, "generator_version": code_read.generator_version,
        "filename": doc.filename, "content_digest": doc.content_digest, "source_truncated": truncated,
        "note": "Generated by DCLab; it embeds dataset column names, which are user data.",
        "source": data_text(source, len(source)), "standalone_cv": code_read.standalone_cv,
        "inputs": [{"name": i.name, "env_var": i.env_var, "artifact_id": i.artifact_id,
                    "content_digest": i.content_digest} for i in code_read.inputs],
        "helper_requirements": code_read.helper_requirements,
    }, raw["experiment"], "source")  # the script embeds the locked CV score


def _evidence_fetch(reads: Any, a: ExperimentInput) -> dict[str, Any]:
    experiment = reads.experiment(a.experiment_id)
    build = reads.model_build(a.experiment_id)  # authorizes the artifacts read below
    return {"experiment": experiment, "build": build,
            "artifacts": reads.artifacts(a.experiment_id) if build is not None else None}


def _evidence_shape(raw: dict[str, Any]) -> Shaped:
    experiment, build = raw["experiment"], raw["build"]
    evidence: dict[str, Any] = {"experiment_id": experiment.id, "status": code(experiment.status),
                                "metrics": cv_record(experiment.metrics),
                                "diff_vs_parent": bound(cv_only(experiment.diff_vs_parent), max_items=20)}
    aggregates = ("metrics", "diff_vs_parent")
    if build is None:
        return _cv_shaped({**evidence, "model_build": None}, experiment, *aggregates)
    evidence["model_build"] = {
        "status": code(build.pipeline_run_status), "evidence_locked_at": build.scientific_evidence_locked_at,
        "reproduction_spec_digest": build.reproduction_spec_digest,
        "stages": [{"key": Code(s.key), "title": s.title, "status": code(s.status), "duration_ms": s.duration_ms,
                    "rows_in": s.rows_in, "rows_out": s.rows_out,
                    **({} if s.key in HOLDOUT_RESULT_STAGES else {
                        "decision_summary": data_text(s.decision_summary, 300), "reason": data_text(s.reason, 300)})}
                   for s in build.stages],
    }
    evidence["artifacts"] = [{"id": item.id, "artifact_type": code(item.artifact_type), "size_bytes": item.size_bytes,
                              "content_digest": item.content_digest} for item in raw["artifacts"]]
    return _cv_shaped(evidence, experiment, *aggregates)


def _findings_fetch(reads: Any, a: ExperimentInput) -> dict[str, Any]:
    return {"findings": reads.findings(a.experiment_id), "experiment": reads.experiment(a.experiment_id)}


def _findings_shape(raw: dict[str, Any]) -> Shaped:
    result = raw["findings"]
    checks = [{"check": code(f.check), "status": code(f.status), "severity": code(f.severity),
               "recommendation_kind": code(f.recommendation_kind),
               # Messages and evidence name dataset columns (user data).
               "message": data_text(f.message, 800),
               "evidence": data_text(cv_only(f.evidence), FINDING_EVIDENCE_CHARS)}
              for f in result.checks[:FINDING_LIMIT]]
    return _cv_shaped({"experiment_id": result.experiment_id, "investigated": result.investigated,
                       "version": result.version, "summary": result.summary.model_dump(mode="json"), "checks": checks,
                       "note": "Trust checks use training rows and CV folds only; never final-holdout values."},
                      raw["experiment"], "checks")


def _decisions_fetch(reads: Any, a: ListDecisionsInput) -> dict[str, Any]:
    page = reads.decisions(a.project_id, effective_state=a.effective_state, decision_type=a.decision_type,
                           limit=a.limit, cursor=a.cursor)
    graph = reads.graph(a.project_id, GRAPH_NODE_LIMIT)  # the project's datasets source agent/dataset text
    return {"page": page, "datasets": _ids(*(n.id for n in graph.nodes if n.kind == "dataset_version"))}


def _decisions_shape(raw: dict[str, Any]) -> Shaped:
    page = raw["page"]
    return Shaped({"decisions": [decision_summary(r) for r in page.items], "next_cursor": page.next_cursor},
                  data_class="aggregates", outcome_scope="cv", source_datasets=raw["datasets"])


def proposal_summary(p: Any) -> dict[str, Any]:
    """A proposal for an agent reader: codes, ids and flags; the agent's own text (rationale, tool
    arguments, payload strings) is data, never instructions."""

    subject = f"{p.subject.kind}:{p.subject.id}" if p.subject.id else p.subject.kind
    return {
        "id": p.id, "project_id": p.project_id, "source": code(p.source), "run_id": p.run_id,
        "decision_point_key": code(p.decision_point_key), "level_at_proposal": p.level_at_proposal,
        "proposal_type": code(p.proposal_type), "proposed_by": code(p.proposed_by), "status": code(p.status),
        "supersede_reason": code(p.supersede_reason), "open": p.open, "subject": Code(subject),
        "validator_verdict": code(p.validator_verdict), "tool_name": code(p.tool_name),
        "tool_arguments": data_text(cv_only(p.tool_arguments), 1500, aggregate=True),
        "payload": data_text(cv_only(p.payload), 1500, aggregate=True),
        "proposed_rationale": data_text(p.proposed_rationale), "rationale_label": code(p.proposed_rationale_label),
        "decision_record_id": p.decision_record_id, "created_at": p.created_at,
    }


def _proposals_fetch(reads: Any, a: ListProposalsInput) -> dict[str, Any]:
    page = reads.proposals(a.project_id, status=a.status, decision_point_key=a.decision_point_key,
                           proposal_type=a.proposal_type, limit=a.limit, cursor=a.cursor)
    graph = reads.graph(a.project_id, GRAPH_NODE_LIMIT)  # the project's datasets source the agents' text
    return {"page": page, "datasets": _ids(*(n.id for n in graph.nodes if n.kind == "dataset_version"))}


def _proposals_shape(raw: dict[str, Any]) -> Shaped:
    page = raw["page"]
    return Shaped({"proposals": [proposal_summary(p) for p in page.items], "next_cursor": page.next_cursor},
                  data_class="aggregates", outcome_scope="cv", source_datasets=raw["datasets"])


def _model_fetch(reads: Any, a: ModelInput) -> Any:
    return reads.model_version(a.model_version_id)


def _model_shape(m: Any) -> Shaped:
    return Shaped({"model_version": {
        "id": m.id, "project_id": m.project_id, "version": m.version, "created_at": m.created_at,
        "family": code(m.family), "algorithm": code(m.algorithm), "is_champion": m.is_champion,
        "ref_kinds": [Code(k) for k in m.ref_kinds], "content_digest": m.content_digest,
        "lineage": m.lineage.model_dump(mode="json"), "metrics": cv_record(m.metrics),
        "artifacts": [{"role": Code(a.role), "id": a.id, "artifact_type": code(a.artifact_type),
                       "size_bytes": a.size_bytes, "content_digest": a.content_digest} for a in m.artifacts],
    }}, data_class="aggregates", outcome_scope="cv", source_datasets=_ids(m.lineage.source_dataset_id),
        aggregates=("model_version.metrics",))


_CARD_KEYS = ("card_version", "model_version_id", "version", "experiment_id", "project_id", "family", "algorithm",
              "created_at", "content_digest", "baseline", "llm")


def _card_fetch(reads: Any, a: ModelInput) -> Any:
    return reads.model_card(a.model_version_id)


def _card_shape(card: Any) -> Shaped:
    data = card.model_dump(mode="json")
    # Holdout-blind whatever the principal: the final evaluation (and its Markdown section) is dropped.
    markdown = card.markdown.split("\n## Final evaluation", 1)[0].rstrip("\n")
    # Target labels are values of the target column (sample_values); the words and the
    # Markdown embed them when present.
    labelled = bool(data["target"].get("positive_label") or data["target"].get("class_labels"))
    words = sample_text if labelled else data_text
    objective, cv = data["objective"], cv_only(data["cv"])
    payload = {"model_card": {
        **{key: data[key] for key in _CARD_KEYS if key in data},
        "objective": {**objective, "primary_metric": code(objective.get("primary_metric")),
                      "business_objective": text(objective.get("business_objective"), 1000),
                      "primary_metric_reason": data_text(objective.get("primary_metric_reason"), 512,
                                                         aggregate=True)},
        "cv": {**cv, "metric": code(cv.get("metric"))} if "metric" in cv else cv,
        "split": {**cv_only(data["split"]), "group_column": data_text(data["split"].get("group_column"), 256),
                  "time_column": data_text(data["split"].get("time_column"), 256)},
        # Column names, labels, dataset names, messages: user data.
        "target": sample_text(data["target"], 1500),
        "metric_in_words": words(data["metric_in_words"], 1000),
        "drivers": data_text(data["drivers"], 3000),
        "risks": data_text(data["risks"], 3000),
        "data": data_text(data["data"], 800),
        "final_evaluation": {"status": "withheld",
                             "note": "Agents never see final-evaluation values; use the CV evidence."},
        "markdown": words(markdown, 12000),
    }}
    return Shaped(payload, data_class="aggregates", outcome_scope="cv",
                  source_datasets=_ids(card.data.source_dataset_id),
                  aggregates=tuple(f"model_card.{k}" for k in ("objective", "cv", "baseline", "metric_in_words", "markdown",
                                                               "target",
                                                               "drivers", "risks")))


def _prediction_fetch(reads: Any, a: PredictionInput) -> dict[str, Any]:
    prediction = reads.prediction(a.prediction_id)
    return {"prediction": prediction, "model": reads.model_version(prediction.model_version_id)}


def _prediction_shape(raw: dict[str, Any]) -> Shaped:
    p = raw["prediction"]
    return Shaped({"prediction": prediction_summary(p)}, data_class="aggregates", outcome_scope="cv",
                  source_datasets=_ids(p.input_dataset_id, raw["model"].lineage.source_dataset_id),
                  aggregates=("prediction.decision_threshold",))  # locked on out-of-fold training rows


def _proposal_fetch(reads: Any, a: ProposalInput) -> Any:
    return reads.decision(a.proposal_id)


def _proposal_shape(record: Any) -> Shaped:
    pid = str(record.project_id)
    if record.decision_type in REF_MOVE_TYPES:
        api_hint = f"POST /v1/projects/{pid}/refs/<ref_kind> with proposal_id={record.id} and If-Match (human session only)"
    else:
        api_hint = f"POST /v1/decisions/{record.id}/accept (human session only)"
    return Shaped({
        "status": Code("requires_human_acceptance" if record.effective_state == "proposed" else "not_open"),
        "write_performed": False, "proposal_id": record.id, "project_id": record.project_id,
        "proposal": decision_summary(record),
        "studio": {"path": f"/projects/{pid}", "tab": Code("decisions"), "decision_id": record.id},
        "instructions": ("Service tokens are propose-only agents and can never accept. Ask a human with "
                         "ML-write access to review this proposal in DCLab Studio (project -> Decisions) "
                         "and accept or reject it there."),
        "human_api": api_hint,
    }, data_class="aggregates", outcome_scope="cv")  # the record's facts/details may carry CV scores


def _impact_fetch(reads: Any, a: ImpactInput) -> Any:
    return reads.impact(a.kind, a.node_id)


def _node(ref: Any) -> dict[str, Any]:
    return {"kind": Code(ref.kind), "id": ref.id, "key": Code(ref.key)}


def _impact_shape(r: Any) -> Shaped:
    return Shaped({"impact": {"node": _node(r.node), "project_id": r.project_id,
                              "items": [_node(item) for item in r.items[:LIST_LIMIT]],
                              "items_omitted": max(0, len(r.items) - LIST_LIMIT), "counts_by_kind": r.counts_by_kind,
                              "total": r.total, "truncated": r.truncated, "graph_truncated": r.graph_truncated}})


def _list_decisions_validator(args: ListDecisionsInput) -> ListDecisionsInput:
    from app.domain.decision_records import DecisionEffectiveState, DecisionType

    for value, allowed, name in ((args.effective_state, DecisionEffectiveState, "effective_state"),
                                 (args.decision_type, DecisionType, "decision_type")):
        if value is not None and value not in allowed.__args__:  # type: ignore[attr-defined]
            raise ToolError("invalid_arguments", f"{name} is not one of the allowed values")
    return args


def _compare_validator(args: CompareInput) -> CompareInput:
    if len(set(args.experiment_ids)) != len(args.experiment_ids):
        raise ToolError("invalid_arguments", "experiment_ids must be distinct")
    return args
