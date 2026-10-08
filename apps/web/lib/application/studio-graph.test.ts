import assert from "node:assert/strict";
import test from "node:test";
import {
  builtFrom, decisionMarkers, graphSummary, groupByKind, layoutGraph, nodeLink, refBadges, staleLines, type GraphNodeLike,
} from "./studio-graph.ts";

const P = "99999999-9999-4999-8999-999999999999";
const uuid = (n: number) => `00000000-0000-4000-8000-${n.toString(16).padStart(12, "0")}`;
const node = (kind: string, n: number, extra: Partial<GraphNodeLike> = {}): GraphNodeLike => ({
  kind, id: uuid(n), key: `${kind}:${uuid(n)}`, label: `${kind} ${n}`, stale: false, created_at: `2026-10-01T00:00:${String(n % 60).padStart(2, "0")}Z`, ...extra,
});
const edge = (from: GraphNodeLike, to: GraphNodeLike, relation: string, attribute = false) => ({
  from: { kind: from.kind, id: from.id, key: from.key }, to: { kind: to.kind, id: to.id, key: to.key }, relation, attribute,
});

const spec = node("problem_spec", 1);
const ds = node("dataset_version", 2);
const prepared = node("dataset_version", 3, { derived: true });
const split = node("split_plan", 4);
const e1 = node("experiment", 5);
const e2 = node("experiment", 6);
const mv = node("model_version", 7);
const NODES = [spec, ds, prepared, split, e1, e2, mv];
const EDGES = [
  edge(split, ds, "partitions"),
  edge(e1, ds, "uses_dataset"), edge(e1, split, "uses_split_plan"), edge(e1, spec, "uses_problem_spec"), edge(e1, prepared, "prepared_as", true),
  edge(e2, e1, "branch_of"), edge(e2, ds, "uses_dataset"), edge(e2, split, "uses_split_plan"),
  edge(mv, e2, "produced_by"), edge(mv, ds, "uses_dataset"),
];

test("layout layers follow the longest built-from path; sources sit just before their first consumer", () => {
  const layout = layoutGraph(NODES, EDGES);
  const layerOf = (n: GraphNodeLike) => layout.nodes.get(n.key)!.layer;
  assert.equal(layerOf(ds), 0);
  assert.equal(layerOf(spec), 1, "the spec is used only by e1 (layer 2)");
  assert.equal(layerOf(prepared), 1, "the prepared table is used only by e1");
  assert.equal(layerOf(split), 1);
  assert.equal(layerOf(e1), 2);
  assert.equal(layerOf(e2), 3);
  assert.equal(layerOf(mv), 4);
  assert.equal(layout.layers.length, 5);
  assert.equal(layout.edges.length, EDGES.length);
  assert.equal(layout.cyclic, 0);
  for (const e of layout.edges) assert.match(e.path, /^M\d+(\.\d+)?,\d+(\.\d+)? C/);
});

test("layout is deterministic regardless of input order", () => {
  const a = layoutGraph(NODES, EDGES);
  const b = layoutGraph([...NODES].reverse(), [...EDGES].reverse());
  const pos = (layout: ReturnType<typeof layoutGraph>) => NODES.map((n) => { const p = layout.nodes.get(n.key)!; return `${p.layer}/${p.row}/${p.x}/${p.y}`; });
  assert.deepEqual(pos(a), pos(b));
  assert.equal(a.width, b.width);
  assert.equal(a.height, b.height);
});

test("cycles are guarded and flagged, never looped", () => {
  const x = node("experiment", 10);
  const y = node("experiment", 11);
  const z = node("experiment", 12);
  const layout = layoutGraph([x, y, z], [edge(x, y, "branch_of"), edge(y, z, "branch_of"), edge(z, x, "branch_of")]);
  assert.equal(layout.nodes.size, 3);
  assert.equal(layout.cyclic, 1);
  assert.deepEqual(new Set([...layout.nodes.values()].map((n) => n.layer)), new Set([0, 1, 2]));
});

test("disconnected nodes, dangling edges and self edges", () => {
  const lone = node("feature_recipe", 20);
  const layout = layoutGraph([ds, lone], [edge(e1, ds, "uses_dataset"), edge(ds, ds, "partitions")]);
  assert.equal(layout.nodes.get(lone.key)!.layer, 0);
  assert.equal(layout.nodes.get(ds.key)!.layer, 0);
  assert.equal(layout.edges.length, 0);
  assert.equal(layout.layers[0].length, 2);
});

test("0, 1 and 500 nodes", () => {
  const empty = layoutGraph([], []);
  assert.equal(empty.nodes.size, 0);
  assert.equal(empty.width, 0);
  assert.equal(empty.height, 0);
  const one = layoutGraph([ds], []);
  assert.equal(one.nodes.get(ds.key)!.x, 16);
  assert.ok(one.width > 0 && one.height > 0);

  const many: GraphNodeLike[] = [ds, split];
  const edges = [edge(split, ds, "partitions")];
  for (let i = 0; i < 498; i += 1) {
    const exp = node("experiment", 1000 + i);
    many.push(exp);
    edges.push(edge(exp, ds, "uses_dataset"), edge(exp, split, "uses_split_plan"));
    if (i % 10) edges.push(edge(exp, many[many.length - 2], "branch_of"));
  }
  const started = performance.now();
  const big = layoutGraph(many, edges);
  assert.ok(performance.now() - started < 1000, "500-node layout stays fast");
  assert.equal(big.nodes.size, 500);
  assert.equal(big.cyclic, 0);
  const seen = new Set([...big.nodes.values()].map((n) => `${n.x},${n.y}`));
  assert.equal(seen.size, 500, "no two nodes overlap");
});

test("ref badges, stale lines and built-from read the API fields", () => {
  const stale = node("experiment", 30, {
    stale: true, ref_kinds: ["champion_model", "split_plan"],
    stale_reasons: [{ ref_kind: "dataset", expected: { kind: "dataset_version", id: uuid(40) }, actual: { kind: "dataset_version", id: uuid(2) } }],
  });
  assert.deepEqual(refBadges(stale), ["★ in use", "★ test design"]);
  assert.deepEqual(refBadges(ds), []);
  assert.deepEqual(staleLines(stale), ["Built on an older version: the data in use is now dataset version 00000000; this was built from 00000000"]);
  assert.deepEqual(staleLines(ds), []);
  assert.deepEqual(builtFrom(EDGES, e1.key).map((b) => b.relation), ["uses_dataset", "uses_split_plan", "uses_problem_spec", "prepared_as"]);
  assert.deepEqual(builtFrom(EDGES, ds.key), []);
  assert.deepEqual(groupByKind(NODES).map((g) => [g.kind, g.nodes.length]), [
    ["problem_spec", 1], ["dataset_version", 2], ["split_plan", 1], ["experiment", 2], ["model_version", 1],
  ]);
});

test("drawer links only for UUID nodes of a kind with an inspector", () => {
  assert.deepEqual(nodeLink(P, e1), { href: `/projects/${P}/experiments/${e1.id}`, label: "Open the experiment" });
  assert.deepEqual(nodeLink(P, ds), { href: `/projects/${P}/data/${ds.id}`, label: "Open the dataset version" });
  assert.deepEqual(nodeLink(P, mv), { href: `/projects/${P}/models/${mv.id}`, label: "Open the model version" });
  assert.equal(nodeLink(P, spec), null);
  assert.equal(nodeLink(P, { kind: "constructor", id: e1.id }), null);
  assert.equal(nodeLink(P, { kind: "experiment", id: "../../admin" }), null);
  assert.equal(nodeLink("not-a-uuid", e1), null);
});

test("decision markers come only from decision-point records about experiments", () => {
  const base = { effective_state: "accepted", recorded_at: "2026-10-05T10:00:00Z", actor: { kind: "rule" } };
  const markers = decisionMarkers([
    { ...base, id: "d1", decision_type: "decision_point_resolved", subject: { kind: "experiment", id: e1.id },
      details: { decision_point: "column.is_identifier", level: 1, agreement: "agree", agreement_counts: { disagree: 1, agree: 3, odd: "x" }, columns_total: 4,
        columns: [{ column: "customer_code", rule: "identifier", ai: "identifier", used: "identifier", source: "rule" }, { column: "plan", rule: "categorical", ai: null, used: "categorical", source: "rule" }] } },
    { ...base, id: "d2", decision_type: "decision_point_resolved", subject: { kind: "experiment", id: e1.id }, details: { level: 7 }, details_truncated: true },
    { ...base, id: "d3", decision_type: "ref_moved", subject: { kind: "experiment", id: e1.id }, details: { level: 2 } },
    { ...base, id: "d4", decision_type: "decision_point_resolved", subject: { kind: "project", id: P } },
  ]);
  assert.deepEqual([...markers.keys()], [e1.id]);
  const [first, second] = markers.get(e1.id)!;
  assert.equal(first.point, "column.is_identifier");
  assert.equal(first.level, 1);
  assert.deepEqual(first.counts, [["agree", 3], ["disagree", 1]]);
  assert.deepEqual(first.answers[1], { column: "plan", rule: "categorical", ai: "no answer", used: "categorical", source: "rule" });
  assert.equal(first.answersTotal, 4);
  assert.equal(second.point, null);
  assert.equal(second.level, null, "an out-of-range level is not shown as a level");
  assert.equal(second.detailsTruncated, true);
  assert.equal(decisionMarkers([]).size, 0);
});

test("summary states truncation explicitly", () => {
  const full = graphSummary({ nodes: NODES, edges: EDGES, truncated: false, experiment_limit: 500 });
  assert.deepEqual(full, { nodes: 7, edges: 10, stale: 0, truncated: false, note: null });
  const cut = graphSummary({ nodes: [...NODES, node("experiment", 50, { outside_window: true, stale: true })], edges: EDGES, truncated: true, truncated_kinds: ["experiment"], experiment_limit: 2 });
  assert.equal(cut.stale, 1);
  assert.equal(cut.note, "Showing 8 nodes: the newest 2 experiments (window of 2); capped kinds: experiment. Older lineage is not drawn on this page.");
});
