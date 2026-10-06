"use client";

import { memo, useMemo } from "react";
import { DataTable, type Column } from "@/components/studio/DataTable";
import { Pill } from "@/components/studio/Pill";
import { STATUS_TONE } from "@/app/components/studio-app/StudioParts";
import { plainText } from "@/lib/application/command-search";
import type { StudioGraph, StudioGraphNode } from "@/lib/application/studio-data-hooks";
import {
  NODE_H, NODE_W, RELATION_LABEL, builtFrom, groupByKind, kindLabel, markersFor, refBadges, shortId,
  type DecisionMarker, type GraphLayout,
} from "@/lib/application/studio-graph";

export const nodeDomId = (node: { kind: string; id: string }) => `gn-${node.kind}-${node.id}`;

/** Spoken name of a node: kind, short id, label, status, stale, refs and AI markers (colour is never the only signal). */
export function nodeName(node: StudioGraphNode, markers: number): string {
  return [
    `${kindLabel(node.kind)} ${shortId(node.id)}`,
    plainText(node.label, 80),
    node.status ? `status ${node.status.toLowerCase()}` : "",
    node.stale ? "stale" : "",
    ...refBadges(node).map((badge) => badge.replace("★", "ref")),
    markers ? `${markers} AI decision point${markers === 1 ? "" : "s"}` : "",
  ].filter(Boolean).join(", ");
}

function Badges({ node, markers }: { node: StudioGraphNode; markers: number }) {
  return (
    <>
      {node.status ? <Pill tone={STATUS_TONE[node.status.toLowerCase()] ?? "gray"}>{node.status.toLowerCase().replaceAll("_", " ")}</Pill> : null}
      {refBadges(node).map((badge) => <Pill key={badge} tone={badge === "★ champion" ? "ok" : "det"}>{badge}</Pill>)}
      {node.stale ? <Pill tone="warn"><span aria-hidden="true">⚠ </span>stale</Pill> : null}
      {markers ? <Pill tone="ai"><span aria-hidden="true">◆ </span>AI ×{markers}</Pill> : null}
      {node.outside_window ? <Pill tone="gray">older window</Pill> : null}
      {node.derived ? <Pill tone="gray">prepared</Pill> : null}
    </>
  );
}

const GraphNodeButton = memo(function GraphNodeButton({ node, x, y, markers, selected, onSelect }: {
  node: StudioGraphNode; x: number; y: number; markers: number; selected: boolean; onSelect: (key: string) => void;
}) {
  return (
    <button
      type="button"
      id={nodeDomId(node)}
      className={`gnode${node.stale ? " stale" : ""}${node.ref_kinds?.length ? " ref" : ""}${markers ? " ai" : ""}`}
      style={{ left: x, top: y, width: NODE_W, height: NODE_H }}
      aria-pressed={selected}
      aria-label={nodeName(node, markers)}
      onClick={() => onSelect(node.key)}
    >
      <span className="gn-kind">{kindLabel(node.kind)} · <span className="mono">{shortId(node.id)}</span>{node.version ? ` · v${node.version}` : ""}</span>
      <span className="gn-label">{plainText(node.label, 80)}</span>
      <span className="gn-meta"><Badges node={node} markers={markers} /></span>
    </button>
  );
});

export function GraphCanvas({ graph, layout, markers, selected, zoom, onSelect }: {
  graph: StudioGraph; layout: GraphLayout; markers: Map<string, DecisionMarker[]>; selected: string | null; zoom: number; onSelect: (key: string) => void;
}) {
  const near = useMemo(() => {
    if (!selected) return new Set<string>();
    return new Set(layout.edges.filter((edge) => edge.from === selected || edge.to === selected).map((edge) => edge.key));
  }, [layout, selected]);
  return (
    <div className="graph-scroll" role="group" aria-label={`Lineage graph, ${graph.nodes.length} nodes. Use Tab to move between nodes; Enter opens the inspector.`}>
      <div style={{ width: layout.width * zoom, height: layout.height * zoom }}>
        <div className="graph-plane" style={{ width: layout.width, height: layout.height, transform: `scale(${zoom})` }}>
          <svg width={layout.width} height={layout.height} aria-hidden="true" focusable="false">
            <defs>
              <marker id="graph-arrow" viewBox="0 0 8 8" refX="7" refY="4" markerWidth="7" markerHeight="7" orient="auto">
                <path d="M0,0 L8,4 L0,8 z" className="graph-arrow" />
              </marker>
            </defs>
            {layout.edges.map((edge) => (
              <path key={edge.key} d={edge.path} markerEnd="url(#graph-arrow)"
                className={`gedge${edge.attribute ? " attr" : ""}${near.has(edge.key) ? " near" : ""}`} />
            ))}
          </svg>
          {graph.nodes.map((node) => {
            const pos = layout.nodes.get(node.key);
            if (!pos) return null;
            return (
              <GraphNodeButton key={node.key} node={node} x={pos.x} y={pos.y} markers={markersFor(markers, node).length}
                selected={selected === node.key} onSelect={onSelect} />
            );
          })}
        </div>
      </div>
    </div>
  );
}

/** Accessible list fallback: every node grouped by kind, with stale flag, refs, AI markers and its "built from" edges. */
export function GraphList({ graph, markers, selected, onSelect }: {
  graph: StudioGraph; markers: Map<string, DecisionMarker[]>; selected: string | null; onSelect: (key: string) => void;
}) {
  const byKey = useMemo(() => new Map(graph.nodes.map((node) => [node.key, node])), [graph.nodes]);
  const groups = useMemo(() => groupByKind(graph.nodes), [graph.nodes]);
  const columns: Column<StudioGraphNode>[] = [
    {
      key: "node", header: "Node", sortValue: (n) => n.created_at ?? "",
      render: (n) => (
        <button type="button" className="linkish" aria-pressed={selected === n.key} onClick={() => onSelect(n.key)}>
          <span className="mono">{shortId(n.id)}</span> {plainText(n.label, 80)}
        </button>
      ),
    },
    { key: "state", header: "Status, refs, flags", render: (n) => <span className="toolbar"><Badges node={n} markers={markersFor(markers, n).length} /></span> },
    {
      key: "from", header: "Built from",
      render: (n) => {
        const items = builtFrom(graph.edges, n.key);
        if (!items.length) return <span className="muted">nothing (a source)</span>;
        return (
          <ul className="plain-list tight">
            {items.map((item) => {
              const target = byKey.get(item.node.key ?? "");
              return (
                <li key={`${item.relation}-${item.node.id}`}>
                  {RELATION_LABEL[item.relation] ?? item.relation} {kindLabel(item.node.kind).toLowerCase()} <span className="mono">{shortId(item.node.id)}</span>
                  {target ? null : <span className="muted"> (not loaded)</span>}
                  {item.attribute ? <span className="muted"> (attribute, not used for staleness)</span> : null}
                </li>
              );
            })}
          </ul>
        );
      },
    },
  ];
  return (
    <div className="graph-list">
      {groups.map((group) => (
        <section key={group.kind} aria-labelledby={`graph-list-${group.kind}`}>
          <h3 id={`graph-list-${group.kind}`}>{kindLabel(group.kind)} <span className="muted">({group.nodes.length})</span></h3>
          <DataTable caption={`${kindLabel(group.kind)} nodes`} columns={columns} rows={group.nodes} rowKey={(n) => n.key}
            highlightRow={(n) => n.key === selected} />
        </section>
      ))}
    </div>
  );
}

export function GraphLegend() {
  return (
    <section className="graph-legend" aria-label="Legend">
      <h2>Legend</h2>
      <ul>
        <li><span className="gnode-sample" aria-hidden="true" /> A node: problem spec, dataset version, split plan, feature recipe, experiment or model version. Arrows point from what it was built from.</li>
        <li><span className="gnode-sample ref" aria-hidden="true" /> <Pill tone="det">★ data</Pill> <Pill tone="ok">★ champion</Pill> a project ref points at this node.</li>
        <li><span className="gnode-sample stale" aria-hidden="true" /> <Pill tone="warn"><span aria-hidden="true">⚠ </span>stale</Pill> built from a version a ref no longer points at.</li>
        <li><span className="gnode-sample ai" aria-hidden="true" /> <Pill tone="ai"><span aria-hidden="true">◆ </span>AI ×2</Pill> recorded AI decision points about this experiment.</li>
        <li><svg width="40" height="10" aria-hidden="true"><path d="M2,5 H38" className="gedge attr" /></svg> dashed: the run&apos;s prepared table (an attribute, not lineage for staleness).</li>
      </ul>
    </section>
  );
}
