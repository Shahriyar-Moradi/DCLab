"""Read-only ML state graph projection (ADR 0006 §1, §6; P2.3-A).

``project_graph()`` loads one bounded, in-memory project graph with a fixed set
of workspace-filtered statements (the numbered plan in ADR §6; no per-node
queries), then computes staleness from refs and ``impact()`` as the downstream
closure. Nothing is stored and nothing is written.

Staleness: node N is stale iff its strict upstream closure contains a node U
of a ref's node kind with U != that ref's target. Only refs define "current"
(founder Q3); ``feature_recipe`` is excluded until Phase 5 (founder Q10). A
project with no refs has no stale nodes. ``prepared_as`` is an attribute edge
and never enters staleness or impact.
"""

from __future__ import annotations

from collections import defaultdict, deque
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Iterable
from uuid import UUID

from sqlalchemy import and_, select, tuple_
from sqlalchemy.orm import Session

from app.db.models import (
    Dataset,
    Experiment,
    ExperimentCandidate,
    FeatureSetVersion,
    ModelVersion,
    ProblemSpec,
    Project,
    ProjectRef,
    SplitPlan,
    User,
    WorkflowRun,
)
from app.domain.errors import (
    GraphNodeNotFoundError,
    IdentityError,
    InvalidCursorError,
    InvalidGraphCursorError,
)
from app.domain.project_graph import (
    GraphEdge,
    GraphNode,
    GraphNodeRef,
    GraphRef,
    NodeImpactRead,
    ProjectGraphRead,
    StaleReason,
)
from app.domain.state_graph import (
    GRAPH_ATTRIBUTE_RELATIONS,
    GRAPH_EXPERIMENT_WINDOW,
    GRAPH_IMPACT_CAP,
    GRAPH_INTENT_MAX_CHARS,
    GRAPH_KIND_CAP,
    GRAPH_LABEL_MAX_CHARS,
    GRAPH_NO_SOURCE_DATASET_NOTE,
    GRAPH_NO_SPLIT_PLAN_NOTE,
    GRAPH_STALE_REASON_CAP,
    REF_KINDS,
    REF_TARGET_COLUMNS,
    REF_TARGET_NODE_KINDS,
    STALENESS_REF_KINDS,
    node_key,
)
from app.domain.workspace_identity import ProjectRead
from app.services.audience_projection import public_diagnostic
from app.services.authorization_service import can_read_workspace
from app.services.cursor_codec import open_cursor, sign_cursor
from app.services.project_service import get_project

Key = tuple[str, UUID]

# Impact targets resolve their project with one workspace-filtered lookup.
_NODE_TABLES: dict[str, Any] = {
    "problem_spec": ProblemSpec,
    "dataset_version": Dataset,
    "split_plan": SplitPlan,
    "feature_recipe": FeatureSetVersion,
    "experiment": Experiment,
    "model_version": ModelVersion,
}
_STALENESS_NODE_KINDS = frozenset(REF_TARGET_NODE_KINDS[kind] for kind in STALENESS_REF_KINDS)


@dataclass
class _Node:
    kind: str
    id: UUID
    label: str
    status: str | None = None
    created_at: datetime | None = None
    version: str | None = None
    digest: str | None = None
    intent: str | None = None
    outside_window: bool = False
    lineage_incomplete: bool = False
    derived: bool = False
    notes: list[str] = field(default_factory=list)


@dataclass
class _Graph:
    project: Project
    refs: list[ProjectRef]
    nodes: dict[Key, _Node]
    edges: list[tuple[Key, Key, str]]
    limit: int
    truncated_kinds: set[str]
    next_cursor: str | None

    @property
    def truncated(self) -> bool:
        return bool(self.truncated_kinds) or self.next_cursor is not None


# --- cursor --------------------------------------------------------------------


def _cursor_scope(project: Project) -> str:
    return f"graph:{project.workspace_id}:{project.id}"


def encode_cursor(project: Project, created_at: datetime, experiment_id: UUID) -> str:
    """Signed, project-bound keyset position (shared /v1 cursor codec, P3.1-A)."""

    return sign_cursor(_cursor_scope(project), [created_at.isoformat(), str(experiment_id)])


def decode_cursor(project: Project, cursor: str) -> tuple[datetime, UUID]:
    message = "cursor is not a cursor of this project graph"
    try:
        created_raw, id_raw = open_cursor(cursor, _cursor_scope(project), message=message)
        created_at = datetime.fromisoformat(created_raw)
        if created_at.tzinfo is None:
            raise ValueError("cursor timestamp must be timezone-aware")
        return created_at, UUID(id_raw)
    except (InvalidCursorError, ValueError, TypeError) as exc:
        raise InvalidGraphCursorError(message) from exc


# --- loader (ADR §6 statement plan) ---------------------------------------------


def _capped(rows: list, kind: str, truncated: set[str]) -> list:
    if len(rows) > GRAPH_KIND_CAP:
        truncated.add(kind)
        return rows[:GRAPH_KIND_CAP]
    return rows


def _experiment_columns():
    return (
        Experiment.id,
        Experiment.created_at,
        Experiment.status,
        Experiment.pipeline_name,
        Experiment.run_number,
        Experiment.source_dataset_id,
        Experiment.dataset_id,
        Experiment.split_plan_id,
        Experiment.parent_pipeline_run_id,
        Experiment.intent,
        Experiment.branch_reason,
        WorkflowRun.problem_spec_id,
    )


def _experiments_query(workspace_id: UUID, project_id: UUID):
    return (
        select(*_experiment_columns())
        .outerjoin(
            WorkflowRun,
            and_(
                WorkflowRun.id == Experiment.workflow_run_id,
                WorkflowRun.workspace_id == Experiment.workspace_id,
            ),
        )
        .where(Experiment.workspace_id == workspace_id, Experiment.project_id == project_id)
    )


def _load_graph(
    db: Session, project: Project, *, cursor: str | None, limit: int
) -> _Graph:
    ws, pid = project.workspace_id, project.id
    after = decode_cursor(project, cursor) if cursor else None
    truncated: set[str] = set()
    nodes: dict[Key, _Node] = {}
    edges: list[tuple[Key, Key, str]] = []

    # 1. project_refs by project.
    refs = list(
        db.scalars(
            select(ProjectRef)
            .where(ProjectRef.workspace_id == ws, ProjectRef.project_id == pid)
            .order_by(ProjectRef.ref_kind)
        )
    )

    # 2. problem_specs by project.
    specs = _capped(
        db.execute(
            select(
                ProblemSpec.id,
                ProblemSpec.version,
                ProblemSpec.status,
                ProblemSpec.content_digest,
                ProblemSpec.created_at,
                ProblemSpec.task_type,
                ProblemSpec.target_column,
            )
            .where(ProblemSpec.workspace_id == ws, ProblemSpec.project_id == pid)
            .order_by(ProblemSpec.version.desc(), ProblemSpec.id.desc())
            .limit(GRAPH_KIND_CAP + 1)
        ).all(),
        "problem_spec",
        truncated,
    )
    for row in specs:
        target = f" ({row.task_type}, target {row.target_column})" if row.target_column else ""
        nodes[("problem_spec", row.id)] = _Node(
            "problem_spec", row.id, f"problem spec v{row.version}{target}", row.status,
            row.created_at, str(row.version), row.content_digest,
        )

    # 3. experiments by project, newest window, joined to workflow_runs.
    window_query = _experiments_query(ws, pid)
    if after is not None:
        window_query = window_query.where(
            tuple_(Experiment.created_at, Experiment.id) < tuple_(after[0], after[1])
        )
    window = db.execute(
        window_query.order_by(Experiment.created_at.desc(), Experiment.id.desc()).limit(limit + 1)
    ).all()
    next_cursor = None
    if len(window) > limit:
        window = window[:limit]
        next_cursor = encode_cursor(project, window[-1].created_at, window[-1].id)
    window_ids = [row.id for row in window]
    window_set = set(window_ids)

    # 4. parent stubs outside the window (one statement, bounded by the window).
    missing_parents = sorted(
        {row.parent_pipeline_run_id for row in window if row.parent_pipeline_run_id}
        - window_set
    )
    stubs = (
        db.execute(_experiments_query(ws, pid).where(Experiment.id.in_(missing_parents))).all()
        if missing_parents
        else []
    )

    # 5. split_plans by project.
    plans = _capped(
        db.execute(
            select(
                SplitPlan.id,
                SplitPlan.version,
                SplitPlan.dataset_id,
                SplitPlan.plan_digest,
                SplitPlan.created_at,
                SplitPlan.holdout_strategy,
                SplitPlan.validation_strategy,
                SplitPlan.validation_folds,
            )
            .where(SplitPlan.workspace_id == ws, SplitPlan.project_id == pid)
            .order_by(SplitPlan.version.desc(), SplitPlan.id.desc())
            .limit(GRAPH_KIND_CAP + 1)
        ).all(),
        "split_plan",
        truncated,
    )
    for row in plans:
        nodes[("split_plan", row.id)] = _Node(
            "split_plan", row.id,
            f"split plan v{row.version} ({row.holdout_strategy}, "
            f"{row.validation_folds}-fold {row.validation_strategy})",
            "locked", row.created_at, str(row.version), row.plan_digest,
        )

    # 6. datasets by id: source datasets, prepared datasets, split-plan datasets
    #    (project scope alone is not enough: legacy rows have NULL project_id).
    experiments = [(row, False) for row in window] + [(row, True) for row in stubs]
    source_ids = {row.source_dataset_id for row, _ in experiments if row.source_dataset_id}
    prepared_ids = {
        row.dataset_id
        for row in window
        if row.dataset_id and row.dataset_id != row.source_dataset_id
    }
    dataset_ids = source_ids | prepared_ids | {row.dataset_id for row in plans}
    datasets = (
        _capped(
            db.execute(
                select(
                    Dataset.id,
                    Dataset.name,
                    Dataset.version,
                    Dataset.content_digest,
                    Dataset.created_at,
                )
                .where(Dataset.workspace_id == ws, Dataset.id.in_(sorted(dataset_ids)))
                .order_by(Dataset.created_at.desc(), Dataset.id.desc())
                .limit(GRAPH_KIND_CAP + 1)
            ).all(),
            "dataset_version",
            truncated,
        )
        if dataset_ids
        else []
    )
    shared_ids = source_ids | {row.dataset_id for row in plans}
    for row in datasets:
        nodes[("dataset_version", row.id)] = _Node(
            "dataset_version", row.id, f"{row.name} {row.version}", None, row.created_at,
            row.version, row.content_digest,
            derived=row.id in prepared_ids and row.id not in shared_ids,
        )

    for row, is_stub in experiments:
        node = _Node(
            "experiment", row.id,
            f"run #{row.run_number}" if row.run_number is not None else row.pipeline_name,
            row.status, row.created_at, None, None,
            intent=row.intent or row.branch_reason,
            outside_window=is_stub,
            lineage_incomplete=row.source_dataset_id is None,
        )
        if row.split_plan_id is None:
            node.notes.append(GRAPH_NO_SPLIT_PLAN_NOTE)
        if row.source_dataset_id is None:
            node.notes.append(GRAPH_NO_SOURCE_DATASET_NOTE)
        nodes[("experiment", row.id)] = node
    for row, is_stub in experiments:
        key = ("experiment", row.id)
        if row.problem_spec_id:
            edges.append((key, ("problem_spec", row.problem_spec_id), "uses_problem_spec"))
        if row.source_dataset_id:
            edges.append((key, ("dataset_version", row.source_dataset_id), "uses_dataset"))
        if not is_stub and row.dataset_id in prepared_ids:
            edges.append((key, ("dataset_version", row.dataset_id), "prepared_as"))
        if row.split_plan_id:
            edges.append((key, ("split_plan", row.split_plan_id), "uses_split_plan"))
        if row.parent_pipeline_run_id:
            edges.append((key, ("experiment", row.parent_pipeline_run_id), "branch_of"))
    for row in plans:
        edges.append((("split_plan", row.id), ("dataset_version", row.dataset_id), "partitions"))

    recipe_pairs: list = []
    versions: list = []
    if window_ids:
        # 7. distinct (experiment, feature recipe) from candidates, by experiment
        #    ids (candidates.project_id is nullable); partial index from 0063.
        recipe_pairs = _capped(
            db.execute(
                select(
                    ExperimentCandidate.experiment_id,
                    ExperimentCandidate.feature_set_version_id,
                )
                .where(
                    ExperimentCandidate.workspace_id == ws,
                    ExperimentCandidate.experiment_id.in_(window_ids),
                    ExperimentCandidate.feature_set_version_id.is_not(None),
                )
                .distinct()
                .limit(GRAPH_KIND_CAP + 1)
            ).all(),
            "feature_recipe",
            truncated,
        )
        # 8. model_versions by experiment ids (model_versions.project_id is nullable).
        versions = _capped(
            db.execute(
                select(
                    ModelVersion.id,
                    ModelVersion.version,
                    ModelVersion.content_digest,
                    ModelVersion.created_at,
                    ModelVersion.pipeline_run_id,
                    ModelVersion.feature_set_version_id,
                )
                .where(
                    ModelVersion.workspace_id == ws,
                    ModelVersion.pipeline_run_id.in_(window_ids),
                )
                .order_by(ModelVersion.created_at.desc(), ModelVersion.id.desc())
                .limit(GRAPH_KIND_CAP + 1)
            ).all(),
            "model_version",
            truncated,
        )

    # 9. feature_set_versions by id (from candidates and model versions).
    recipe_ids = {row.feature_set_version_id for row in recipe_pairs} | {
        row.feature_set_version_id for row in versions if row.feature_set_version_id
    }
    recipes = (
        _capped(
            db.execute(
                select(
                    FeatureSetVersion.id,
                    FeatureSetVersion.version,
                    FeatureSetVersion.content_digest,
                    FeatureSetVersion.created_at,
                    FeatureSetVersion.locked_at,
                )
                .where(
                    FeatureSetVersion.workspace_id == ws,
                    FeatureSetVersion.id.in_(sorted(recipe_ids)),
                )
                .limit(GRAPH_KIND_CAP + 1)
            ).all(),
            "feature_recipe",
            truncated,
        )
        if recipe_ids
        else []
    )
    for row in recipes:
        nodes[("feature_recipe", row.id)] = _Node(
            "feature_recipe", row.id, f"feature recipe v{row.version}",
            "locked" if row.locked_at else "draft", row.created_at, str(row.version),
            row.content_digest,
        )
    for row in recipe_pairs:
        edges.append(
            (("feature_recipe", row.feature_set_version_id), ("experiment", row.experiment_id), "produced_by")
        )

    source_by_experiment = {row.id: row.source_dataset_id for row in window}
    for row in versions:
        key = ("model_version", row.id)
        nodes[key] = _Node(
            "model_version", row.id, f"model {row.version}", None, row.created_at,
            row.version, row.content_digest,
        )
        edges.append((key, ("experiment", row.pipeline_run_id), "produced_by"))
        if row.feature_set_version_id:
            edges.append((key, ("feature_recipe", row.feature_set_version_id), "uses_feature_recipe"))
        source = source_by_experiment.get(row.pipeline_run_id)
        if source:
            edges.append((key, ("dataset_version", source), "uses_dataset"))

    # Never emit a dangling edge (capped kinds, cross-project parents).
    edges = list(dict.fromkeys(e for e in edges if e[0] in nodes and e[1] in nodes))
    return _Graph(project, refs, nodes, edges, limit, truncated, next_cursor)


# --- staleness and impact ---------------------------------------------------------


def _adjacency(graph: _Graph, *, reverse: bool) -> dict[Key, list[Key]]:
    adjacency: dict[Key, list[Key]] = defaultdict(list)
    for source, target, relation in graph.edges:
        if relation in GRAPH_ATTRIBUTE_RELATIONS:
            continue
        if reverse:
            adjacency[target].append(source)
        else:
            adjacency[source].append(target)
    for neighbours in adjacency.values():
        neighbours.sort(key=lambda key: (key[0], str(key[1])))
    return adjacency


def _relevant_ancestors(graph: _Graph) -> dict[Key, frozenset[Key]]:
    """Strict upstream closure per node, restricted to staleness-bearing kinds.

    Memoized iterative DFS, O(V + E); the lineage guard makes the graph acyclic,
    and a defensive ``visiting`` set keeps a corrupt cycle from looping.
    """

    upstream = _adjacency(graph, reverse=False)
    memo: dict[Key, frozenset[Key]] = {}
    for start in graph.nodes:
        if start in memo:
            continue
        visiting = {start}
        stack = [(start, iter(upstream.get(start, ())))]
        while stack:
            node, parents = stack[-1]
            parent = next(parents, None)
            if parent is None:
                stack.pop()
                visiting.discard(node)
                found: set[Key] = set()
                for up in upstream.get(node, ()):
                    if up[0] in _STALENESS_NODE_KINDS:
                        found.add(up)
                    found |= memo.get(up, frozenset())
                memo[node] = frozenset(found)
            elif parent not in memo and parent not in visiting:
                visiting.add(parent)
                stack.append((parent, iter(upstream.get(parent, ()))))
    return memo


def _ref_target(ref: ProjectRef) -> Key:
    return (REF_TARGET_NODE_KINDS[ref.ref_kind], getattr(ref, REF_TARGET_COLUMNS[ref.ref_kind]))


def _node_ref(key: Key) -> GraphNodeRef:
    return GraphNodeRef(kind=key[0], id=key[1], key=node_key(*key))


def _stale_reasons(graph: _Graph) -> dict[Key, list[StaleReason]]:
    if not graph.refs:
        return {}
    targets = {
        ref.ref_kind: _ref_target(ref) for ref in graph.refs if ref.ref_kind in STALENESS_REF_KINDS
    }
    ancestors = _relevant_ancestors(graph)
    reasons: dict[Key, list[StaleReason]] = {}
    for key, upstream in ancestors.items():
        ordered = sorted(upstream, key=lambda k: str(k[1]))
        found: list[StaleReason] = []
        for ref_kind in STALENESS_REF_KINDS:
            expected = targets.get(ref_kind)
            if expected is None:
                continue
            for actual in ordered:
                if actual[0] == expected[0] and actual != expected:
                    found.append(
                        StaleReason(
                            ref_kind=ref_kind, expected=_node_ref(expected), actual=_node_ref(actual)
                        )
                    )
        if found:
            # Bounded response: legacy branch chains may carry many differing ancestors.
            reasons[key] = found[:GRAPH_STALE_REASON_CAP]
    return reasons


def _downstream(graph: _Graph, start: Key) -> list[Key]:
    """Reverse BFS (nearest first); attribute edges excluded."""

    downstream = _adjacency(graph, reverse=True)
    seen = {start}
    order: list[Key] = []
    queue = deque([start])
    while queue:
        for child in downstream.get(queue.popleft(), ()):
            if child not in seen:
                seen.add(child)
                order.append(child)
                queue.append(child)
    return order


def _counts(keys: Iterable[Key]) -> dict[str, int]:
    counts: dict[str, int] = defaultdict(int)
    for kind, _ in keys:
        counts[kind] += 1
    return dict(sorted(counts.items()))


def untrusted_text(value: str | None, max_chars: int) -> str | None:
    """User/agent-authored text (intent, names, target columns) on the public read path.

    Same fail-closed projection as other /v1 diagnostics (paths, emails, bearer
    tokens, ``sk-`` keys redacted), then a hard length cap. Never instructions.
    """

    if not value:
        return None
    projected = public_diagnostic(str(value))
    return projected[:max_chars] if isinstance(projected, str) else None


def _read(graph: _Graph) -> ProjectGraphRead:
    reasons = _stale_reasons(graph)
    ref_kinds_by_node: dict[Key, list[str]] = defaultdict(list)
    for ref in graph.refs:
        ref_kinds_by_node[_ref_target(ref)].append(ref.ref_kind)
    nodes = [
        GraphNode(
            kind=node.kind,
            id=node.id,
            key=node_key(node.kind, node.id),
            label=untrusted_text(node.label, GRAPH_LABEL_MAX_CHARS) or node.kind,
            status=node.status,
            created_at=node.created_at,
            version=node.version,
            digest=node.digest,
            stale=key in reasons,
            stale_reasons=reasons.get(key, []),
            ref_kinds=sorted(ref_kinds_by_node.get(key, []), key=REF_KINDS.index),
            intent=untrusted_text(node.intent, GRAPH_INTENT_MAX_CHARS),
            outside_window=node.outside_window,
            lineage_incomplete=node.lineage_incomplete,
            derived=node.derived,
            notes=node.notes,
        )
        for key, node in graph.nodes.items()
    ]
    refs = []
    for ref in sorted(graph.refs, key=lambda r: REF_KINDS.index(r.ref_kind)):
        target = _ref_target(ref)
        refs.append(
            GraphRef(
                ref_kind=ref.ref_kind,
                target=_node_ref(target),
                version=ref.version,
                moved_at=ref.moved_at,
                decision_record_id=ref.decision_record_id,
                target_in_graph=target in graph.nodes,
                staleness_bearing=ref.ref_kind in STALENESS_REF_KINDS,
                stale=target in reasons,
                stale_reasons=reasons.get(target, []),
            )
        )
    edges = [
        GraphEdge(
            from_=_node_ref(source),
            to=_node_ref(target),
            relation=relation,
            attribute=relation in GRAPH_ATTRIBUTE_RELATIONS,
        )
        for source, target, relation in graph.edges
    ]
    return ProjectGraphRead(
        project=ProjectRead.model_validate(graph.project),
        refs_initialized=bool(graph.refs),
        refs=refs,
        nodes=nodes,
        edges=edges,
        counts_by_kind=_counts(graph.nodes),
        stale_counts_by_kind=_counts(reasons),
        experiment_limit=graph.limit,
        truncated=graph.truncated,
        truncated_kinds=sorted(graph.truncated_kinds),
        next_cursor=graph.next_cursor,
    )


# --- public API -------------------------------------------------------------------


def _bounded_limit(limit: int) -> int:
    return max(1, min(int(limit), GRAPH_EXPERIMENT_WINDOW))


def _load_project_graph(
    db: Session,
    project: Project,
    *,
    cursor: str | None = None,
    limit: int = GRAPH_EXPERIMENT_WINDOW,
) -> ProjectGraphRead:
    """Project an already-authorized project (≤ 9 statements, ADR §6).

    Performs no authorization: callers (routes, MCP, agents) must obtain
    ``project`` through ``project_service.get_project`` first.
    """

    return _read(_load_graph(db, project, cursor=cursor, limit=_bounded_limit(limit)))


def project_graph(
    db: Session,
    *,
    actor: User,
    workspace_id: UUID,
    project_id: UUID,
    cursor: str | None = None,
    limit: int = GRAPH_EXPERIMENT_WINDOW,
) -> ProjectGraphRead:
    """Graph of one project in the actor's workspace; other tenants' ids are not found."""

    project = get_project(db, actor=actor, workspace_id=workspace_id, project_id=project_id)
    return _load_project_graph(db, project, cursor=cursor, limit=limit)


def impact(
    db: Session,
    *,
    actor: User,
    workspace_id: UUID,
    kind: str,
    node_id: UUID,
) -> NodeImpactRead:
    """Downstream closure of one node in its project's graph (capped, ADR §6)."""

    if not can_read_workspace(db, actor, workspace_id):
        raise IdentityError("not authorized for this workspace", status_code=403)
    model = _NODE_TABLES.get(kind)
    if model is None:
        raise GraphNodeNotFoundError("node not found")
    project_id = db.scalar(
        select(model.project_id).where(model.workspace_id == workspace_id, model.id == node_id)
    )
    if project_id is None:
        # Unknown, another tenant's, or not part of any project graph (legacy
        # NULL-project rows) — indistinguishable on purpose.
        raise GraphNodeNotFoundError("node not found")
    project = get_project(db, actor=actor, workspace_id=workspace_id, project_id=project_id)
    graph = _load_graph(db, project, cursor=None, limit=GRAPH_EXPERIMENT_WINDOW)
    start: Key = (kind, node_id)
    closure = _downstream(graph, start) if start in graph.nodes else []
    return NodeImpactRead(
        node=_node_ref(start),
        project_id=project.id,
        items=[_node_ref(key) for key in closure[:GRAPH_IMPACT_CAP]],
        counts_by_kind=_counts(closure),
        total=len(closure),
        truncated=len(closure) > GRAPH_IMPACT_CAP,
        graph_truncated=graph.truncated or start not in graph.nodes,
    )
