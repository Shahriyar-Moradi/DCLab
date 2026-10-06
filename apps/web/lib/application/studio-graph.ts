/**
 * Graph view logic (P4.2-A), pure so it runs under `npm run test:components`:
 * a deterministic layered layout of `GET /v1/projects/{id}/graph` (layer = longest
 * path over "built from" edges, cycles guarded), and the view model of each node
 * (ref badges, stale text, AI decision-point markers from decision records).
 * Every value shown comes from an API field; nothing is invented here.
 */
import { isUuid, projectHref } from "./command-search.ts";
import type { GraphEdgeLike } from "./studio-data.ts";

export type NodeRefLike = { kind: string; id: string; key?: string };
export type StaleReasonLike = { ref_kind: string; expected: NodeRefLike; actual: NodeRefLike };
export type GraphNodeLike = NodeRefLike & {
  key: string;
  label: string;
  status?: string | null;
  created_at?: string | null;
  version?: string | null;
  digest?: string | null;
  stale: boolean;
  stale_reasons?: StaleReasonLike[];
  ref_kinds?: string[];
  intent?: string | null;
  outside_window?: boolean;
  lineage_incomplete?: boolean;
  derived?: boolean;
  notes?: string[];
};

export const KIND_ORDER = ["problem_spec", "dataset_version", "split_plan", "feature_recipe", "experiment", "model_version"] as const;
export const KIND_LABEL: Record<string, string> = {
  problem_spec: "Problem spec", dataset_version: "Dataset version", split_plan: "Split plan",
  feature_recipe: "Feature recipe", experiment: "Experiment", model_version: "Model version",
};
export const REF_BADGE: Record<string, string> = {
  champion_model: "★ champion", problem_spec: "★ spec", dataset: "★ data", split_plan: "★ split", feature_recipe: "★ features",
};
export const RELATION_LABEL: Record<string, string> = {
  uses_problem_spec: "uses spec", uses_dataset: "trained on", prepared_as: "prepared as", uses_split_plan: "uses split",
  branch_of: "branch of", partitions: "partitions", produced_by: "produced by", uses_feature_recipe: "uses features",
};

export const kindLabel = (kind: string) => KIND_LABEL[kind] ?? kind.replaceAll("_", " ");
export const shortId = (id: string) => id.slice(0, 8);
export const nodeKey = (ref: NodeRefLike) => ref.key ?? `${ref.kind}:${ref.id}`;

// --- layout -----------------------------------------------------------------------

export const NODE_W = 232;
export const NODE_H = 92;
const COL_GAP = 84;
const ROW_GAP = 18;
const PAD = 16;

export type LaidNode = { key: string; layer: number; row: number; x: number; y: number; cyclic: boolean };
export type LaidEdge = { key: string; from: string; to: string; relation: string; attribute: boolean; path: string };
export type GraphLayout = { nodes: Map<string, LaidNode>; layers: string[][]; edges: LaidEdge[]; width: number; height: number; cyclic: number };

function kindRank(kind: string): number {
  const index = (KIND_ORDER as readonly string[]).indexOf(kind);
  return index === -1 ? KIND_ORDER.length : index;
}

/** Stable order: kind, then creation time, then key (ties never depend on input order). */
export function compareNodes(a: GraphNodeLike, b: GraphNodeLike): number {
  return kindRank(a.kind) - kindRank(b.kind)
    || (a.created_at ?? "").localeCompare(b.created_at ?? "")
    || a.key.localeCompare(b.key);
}

/**
 * Layered left-to-right layout. An edge `from → to` means "from is built from to", so
 * `to` sits in an earlier layer. Layer = 1 + the deepest dependency (sources at 0).
 * Nodes on a cycle (never expected from the API) are placed after their placed
 * dependencies and flagged `cyclic` instead of looping. Rows: one barycenter pass.
 */
export function layoutGraph(nodes: GraphNodeLike[], edges: GraphEdgeLike[]): GraphLayout {
  const sorted = [...nodes].sort(compareNodes);
  const index = new Map(sorted.map((node, i) => [node.key, i]));
  const deps = new Map<string, Set<string>>(sorted.map((node) => [node.key, new Set<string>()]));
  const dependents = new Map<string, string[]>(sorted.map((node) => [node.key, []]));
  const kept: Array<{ from: string; to: string; relation: string; attribute: boolean }> = [];
  for (const edge of edges) {
    const from = nodeKey(edge.from);
    const to = nodeKey(edge.to);
    if (from === to || !index.has(from) || !index.has(to)) continue;
    kept.push({ from, to, relation: edge.relation, attribute: Boolean(edge.attribute) });
    if (deps.get(from)!.has(to)) continue;
    deps.get(from)!.add(to);
    dependents.get(to)!.push(from);
  }

  const layer = new Map<string, number>();
  const pending = new Map(sorted.map((node) => [node.key, deps.get(node.key)!.size]));
  const place = (key: string) => {
    let depth = 0;
    for (const dep of deps.get(key)!) if (layer.has(dep)) depth = Math.max(depth, layer.get(dep)! + 1);
    layer.set(key, depth);
  };
  // Kahn's algorithm over the stable order; ready nodes drain in sorted order.
  let ready = sorted.filter((node) => pending.get(node.key) === 0).map((node) => node.key);
  const cyclic = new Set<string>();
  while (layer.size < sorted.length) {
    if (ready.length === 0) {
      // Cycle guard: place the first unplaced node (stable order) and carry on.
      const next = sorted.find((node) => !layer.has(node.key))!.key;
      cyclic.add(next);
      ready = [next];
    }
    const batch = ready;
    ready = [];
    for (const key of batch) {
      if (layer.has(key)) continue;
      place(key);
      for (const dependent of dependents.get(key)!) {
        const left = pending.get(dependent)! - 1;
        pending.set(dependent, left);
        if (left === 0 && !layer.has(dependent)) ready.push(dependent);
      }
    }
    ready.sort((a, b) => index.get(a)! - index.get(b)!);
  }

  // Pull each source next to its nearest consumer (a spec sits beside the experiment that uses
  // it, not behind the split plan), so edges rarely run under other nodes.
  for (const node of sorted) {
    const consumers = dependents.get(node.key)!;
    if (deps.get(node.key)!.size || !consumers.length) continue;
    layer.set(node.key, Math.max(layer.get(node.key)!, Math.min(...consumers.map((key) => layer.get(key)!)) - 1));
  }

  const layers: string[][] = [];
  for (const node of sorted) (layers[layer.get(node.key)!] ??= []).push(node.key);
  for (let l = 0; l < layers.length; l += 1) layers[l] ??= [];
  const row = new Map<string, number>();
  layers.forEach((keys, l) => {
    if (l > 0) {
      const centre = (key: string) => {
        const placed = [...deps.get(key)!].filter((dep) => row.has(dep));
        return placed.length ? placed.reduce((sum, dep) => sum + row.get(dep)!, 0) / placed.length : Number.POSITIVE_INFINITY;
      };
      const weight = new Map(keys.map((key) => [key, centre(key)]));
      keys.sort((a, b) => weight.get(a)! - weight.get(b)! || index.get(a)! - index.get(b)!);
    }
    keys.forEach((key, r) => row.set(key, r));
  });

  const laid = new Map<string, LaidNode>();
  for (const node of sorted) {
    const l = layer.get(node.key)!;
    const r = row.get(node.key)!;
    laid.set(node.key, { key: node.key, layer: l, row: r, x: PAD + l * (NODE_W + COL_GAP), y: PAD + r * (NODE_H + ROW_GAP), cyclic: cyclic.has(node.key) });
  }
  const laidEdges = kept.map((edge, i) => {
    const up = laid.get(edge.to)!;
    const down = laid.get(edge.from)!;
    const x1 = up.x + NODE_W;
    const y1 = up.y + NODE_H / 2;
    const x2 = down.x;
    const y2 = down.y + NODE_H / 2;
    const bend = Math.max(40, Math.abs(x2 - x1) / 2);
    return { key: `${i}:${edge.from}>${edge.to}:${edge.relation}`, ...edge, path: `M${x1},${y1} C${x1 + bend},${y1} ${x2 - bend},${y2} ${x2},${y2}` };
  });
  const rows = Math.max(0, ...layers.map((keys) => keys.length));
  return {
    nodes: laid,
    layers,
    edges: laidEdges,
    width: layers.length ? PAD * 2 + layers.length * NODE_W + (layers.length - 1) * COL_GAP : 0,
    height: rows ? PAD * 2 + rows * NODE_H + (rows - 1) * ROW_GAP : 0,
    cyclic: cyclic.size,
  };
}

// --- view model -------------------------------------------------------------------

export function refBadges(node: GraphNodeLike): string[] {
  return (node.ref_kinds ?? []).map((kind) => REF_BADGE[kind] ?? `★ ${kind.replaceAll("_", " ")}`);
}

/** "data ref points at dataset version 1a2b3c4d; this was built from 5e6f7a8b" — one line per reason. */
export function staleLines(node: GraphNodeLike): string[] {
  return (node.stale_reasons ?? []).map((reason) =>
    `${(REF_BADGE[reason.ref_kind] ?? reason.ref_kind).replace("★ ", "")} ref points at ${kindLabel(reason.expected.kind).toLowerCase()} ${shortId(reason.expected.id)}; this was built from ${shortId(reason.actual.id)}`);
}

export type BuiltFrom = { relation: string; attribute: boolean; node: NodeRefLike };

/** Upstream nodes of `key` ("built from …"), in edge order, one per (relation, node). */
export function builtFrom(edges: GraphEdgeLike[], key: string): BuiltFrom[] {
  const seen = new Set<string>();
  const out: BuiltFrom[] = [];
  for (const edge of edges) {
    if (nodeKey(edge.from) !== key) continue;
    const id = `${edge.relation}|${nodeKey(edge.to)}`;
    if (seen.has(id)) continue;
    seen.add(id);
    out.push({ relation: edge.relation, attribute: Boolean(edge.attribute), node: edge.to });
  }
  return out;
}

export function groupByKind<T extends GraphNodeLike>(nodes: T[]): Array<{ kind: string; nodes: T[] }> {
  const groups = new Map<string, T[]>();
  for (const node of [...nodes].sort(compareNodes)) {
    if (!groups.has(node.kind)) groups.set(node.kind, []);
    groups.get(node.kind)!.push(node);
  }
  return [...groups].map(([kind, items]) => ({ kind, nodes: items }));
}

/** Drawer links: the experiment page, or the Data page for a dataset version. Ids must be UUIDs. */
export function nodeLink(projectId: string, node: NodeRefLike): { href: string; label: string } | null {
  if (!isUuid(node.id)) return null;
  if (node.kind === "experiment") {
    const href = projectHref(projectId, "experiments", node.id);
    return href ? { href, label: "Open the experiment" } : null;
  }
  if (node.kind === "dataset_version") {
    const href = projectHref(projectId, "data");
    return href ? { href, label: "Open the Data page" } : null;
  }
  return null;
}

// --- AI decision-point markers (P6.9 records) -------------------------------------

export type DecisionLike = {
  id: string;
  decision_type: string;
  effective_state: string;
  recorded_at: string;
  subject: { kind: string; id: string };
  actor: { kind: string; rule?: string | null };
  details?: Record<string, unknown>;
  details_truncated?: boolean;
  facts?: Record<string, unknown>;
};
export type PointAnswer = { column: string; rule: string; ai: string; used: string; source: string };
export type DecisionMarker = {
  id: string;
  point: string | null;
  level: 0 | 1 | 2 | 3 | null;
  agreement: string | null;
  counts: Array<[string, number]>;
  actor: string;
  state: string;
  recordedAt: string;
  answers: PointAnswer[];
  answersTotal: number | null;
  detailsTruncated: boolean;
};

const text = (value: unknown) => (typeof value === "string" ? value : value === null || value === undefined ? null : JSON.stringify(value));
const ANSWER_LIMIT = 12;

/** Decision-point records grouped by the experiment they are about; fields only as the API returns them. */
export function decisionMarkers(records: DecisionLike[]): Map<string, DecisionMarker[]> {
  const out = new Map<string, DecisionMarker[]>();
  for (const record of records) {
    if (record.decision_type !== "decision_point_resolved" || record.subject.kind !== "experiment") continue;
    const details = record.details ?? {};
    const level = details.level;
    const counts = details.agreement_counts && typeof details.agreement_counts === "object" ? Object.entries(details.agreement_counts as Record<string, unknown>) : [];
    const columns = Array.isArray(details.columns) ? details.columns : [];
    const marker: DecisionMarker = {
      id: record.id,
      point: typeof details.decision_point === "string" ? details.decision_point : null,
      level: level === 0 || level === 1 || level === 2 || level === 3 ? level : null,
      agreement: typeof details.agreement === "string" ? details.agreement : null,
      counts: counts.filter((entry): entry is [string, number] => typeof entry[1] === "number").sort(([a], [b]) => a.localeCompare(b)),
      actor: record.actor.kind,
      state: record.effective_state,
      recordedAt: record.recorded_at,
      answers: columns.slice(0, ANSWER_LIMIT).filter((c): c is Record<string, unknown> => !!c && typeof c === "object").map((c) => ({
        column: text(c.column) ?? "—", rule: text(c.rule) ?? "—", ai: text(c.ai) ?? "no answer", used: text(c.used) ?? "—", source: text(c.source) ?? "—",
      })),
      answersTotal: typeof details.columns_total === "number" ? details.columns_total : null,
      detailsTruncated: Boolean(record.details_truncated),
    };
    const list = out.get(record.subject.id) ?? [];
    list.push(marker);
    out.set(record.subject.id, list);
  }
  return out;
}

/** Markers of one node: only experiments are subjects of decision-point records. */
export function markersFor(markers: Map<string, DecisionMarker[]>, node: NodeRefLike): DecisionMarker[] {
  return node.kind === "experiment" ? markers.get(node.id) ?? [] : [];
}

export type GraphSummary = { nodes: number; edges: number; stale: number; truncated: boolean; note: string | null };

/** Counts for the header, and the explicit truncation note (the API loads the newest experiment window). */
export function graphSummary(graph: { nodes: GraphNodeLike[]; edges: unknown[]; truncated: boolean; truncated_kinds?: string[]; experiment_limit: number; next_cursor?: string | null }): GraphSummary {
  const stale = graph.nodes.filter((node) => node.stale).length;
  const experiments = graph.nodes.filter((node) => node.kind === "experiment" && !node.outside_window).length;
  let note: string | null = null;
  if (graph.truncated) {
    const kinds = (graph.truncated_kinds ?? []).map((kind) => kindLabel(kind).toLowerCase());
    note = `Showing ${graph.nodes.length} nodes: the newest ${experiments} experiments (window of ${graph.experiment_limit})`
      + (kinds.length ? `; capped kinds: ${kinds.join(", ")}` : "")
      + ". Older lineage is not drawn on this page.";
  }
  return { nodes: graph.nodes.length, edges: graph.edges.length, stale, truncated: graph.truncated, note };
}
