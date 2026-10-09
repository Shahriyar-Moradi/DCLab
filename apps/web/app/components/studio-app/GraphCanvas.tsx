"use client";

import { memo, useMemo } from "react";
import { DataTable, type Column } from "@/components/studio/DataTable";
import { Pill } from "@/components/studio/Pill";
import { STATUS_TONE } from "@/app/components/studio-app/StudioParts";
import type { StudioGraph, StudioGraphNode } from "@/lib/application/studio-data-hooks";
import {
  NODE_H, NODE_W, REF_BADGE, builtFrom, groupByKind, kindLabel, markersFor, nameLookup, refBadges, relationLabel,
  type DecisionMarker, type GraphLayout,
} from "@/lib/application/studio-graph";

export const nodeDomId = (node: { kind: string; id: string }) => `gn-${node.kind}-${node.id}`;

/** "★ in use" -> "in use", "★ data" -> "data in use". */
const inUseWords = (badge: string) => (badge === REF_BADGE.champion_model ? badge.replace("★ ", "") : `${badge.replace("★ ", "")} in use`);

/** Spoken name of a node: its name ("Run 3", "Model v2"), status, stale, versions in use and AI markers (colour is never the only signal). */
export function nodeName(node: StudioGraphNode, markers: number, name: string): string {
  return [
    name,
    node.status ? `status ${node.status.toLowerCase()}` : "",
    node.stale ? "built on an older version" : "",
    ...refBadges(node).map(inUseWords),
    markers ? `${markers} AI answer${markers === 1 ? "" : "s"} recorded` : "",
  ].filter(Boolean).join(", ");
}

function Badges({ node, markers }: { node: StudioGraphNode; markers: number }) {
  return (
    <>
      {node.status ? <Pill tone={STATUS_TONE[node.status.toLowerCase()] ?? "gray"}>{node.status.toLowerCase().replaceAll("_", " ")}</Pill> : null}
      {refBadges(node).map((badge) => <Pill key={badge} tone={badge === REF_BADGE.champion_model ? "ok" : "det"}>{badge}</Pill>)}
      {node.stale ? <Pill tone="warn"><span aria-hidden="true">⚠ </span>built on an older version</Pill> : null}
      {markers ? <Pill tone="ai"><span aria-hidden="true">◆ </span>AI ×{markers}</Pill> : null}
      {node.outside_window ? <Pill tone="gray">from an older run</Pill> : null}
      {node.derived ? <Pill tone="gray">made by a run</Pill> : null}
    </>
  );
}

const GraphNodeButton = memo(function GraphNodeButton({ node, name, x, y, markers, selected, onSelect }: {
  node: StudioGraphNode; name: string; x: number; y: number; markers: number; selected: boolean; onSelect: (key: string) => void;
}) {
  return (
    <button
      type="button"
      id={nodeDomId(node)}
      className={`gnode${node.stale ? " stale" : ""}${node.ref_kinds?.length ? " ref" : ""}${markers ? " ai" : ""}`}
      style={{ left: x, top: y, width: NODE_W, height: NODE_H }}
      aria-pressed={selected}
      aria-label={nodeName(node, markers, name)}
      onClick={() => onSelect(node.key)}
    >
      <span className="gn-kind">{kindLabel(node.kind)}</span>
      <span className="gn-label">{name}</span>
      <span className="gn-meta"><Badges node={node} markers={markers} /></span>
    </button>
  );
});

export function GraphCanvas({ graph, layout, markers, names, selected, zoom, onSelect }: {
  graph: StudioGraph; layout: GraphLayout; markers: Map<string, DecisionMarker[]>; names: ReadonlyMap<string, string>; selected: string | null; zoom: number; onSelect: (key: string) => void;
}) {
  const near = useMemo(() => {
    if (!selected) return new Set<string>();
    return new Set(layout.edges.filter((edge) => edge.from === selected || edge.to === selected).map((edge) => edge.key));
  }, [layout, selected]);
  return (
    <div className="graph-scroll" role="group" aria-label={`Lineage diagram, ${graph.nodes.length} items. Use Tab to move between items; Enter opens the details.`}>
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
              <GraphNodeButton key={node.key} node={node} name={names.get(node.key) ?? kindLabel(node.kind)} x={pos.x} y={pos.y} markers={markersFor(markers, node).length}
                selected={selected === node.key} onSelect={onSelect} />
            );
          })}
        </div>
      </div>
    </div>
  );
}

/** Accessible list fallback: every node grouped by kind, with old-data flag, versions in use, assistant markers and its "built from" edges. */
export function GraphList({ graph, markers, names, selected, onSelect }: {
  graph: StudioGraph; markers: Map<string, DecisionMarker[]>; names: ReadonlyMap<string, string>; selected: string | null; onSelect: (key: string) => void;
}) {
  const nameOf = nameLookup(names);
  const byKey = useMemo(() => new Map(graph.nodes.map((node) => [node.key, node])), [graph.nodes]);
  const groups = useMemo(() => groupByKind(graph.nodes), [graph.nodes]);
  const columns: Column<StudioGraphNode>[] = [
    {
      key: "node", header: "Item", sortValue: (n) => n.created_at ?? "",
      render: (n) => (
        <button type="button" className="linkish" aria-pressed={selected === n.key} onClick={() => onSelect(n.key)}>
          {names.get(n.key) ?? kindLabel(n.kind)}
        </button>
      ),
    },
    { key: "state", header: "Status and flags", render: (n) => <span className="toolbar"><Badges node={n} markers={markersFor(markers, n).length} /></span> },
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
                  {relationLabel(item.relation)} {nameOf(item.node)}
                  {target ? null : <span className="muted"> (not loaded)</span>}
                  {item.attribute ? <span className="muted"> (a detail, not used to mark an older version)</span> : null}
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
          <DataTable caption={`${kindLabel(group.kind)} items`} columns={columns} rows={group.nodes} rowKey={(n) => n.key}
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
        <li><span className="gnode-sample" aria-hidden="true" /> An item: goal, data, test design, features, run or model. Arrows point from what it was built from.</li>
        <li><span className="gnode-sample ref" aria-hidden="true" /> <Pill tone="det">★ data</Pill> <Pill tone="ok">★ in use</Pill> the project uses this version now.</li>
        <li><span className="gnode-sample stale" aria-hidden="true" /> <Pill tone="warn"><span aria-hidden="true">⚠ </span>built on an older version</Pill> made from a version that is no longer in use.</li>
        <li><span className="gnode-sample ai" aria-hidden="true" /> <Pill tone="ai"><span aria-hidden="true">◆ </span>AI ×2</Pill> answers the assistant gave for this run, recorded next to the rules&apos; answers.</li>
        <li><svg width="40" height="10" aria-hidden="true"><path d="M2,5 H38" className="gedge attr" /></svg> dashed: the table a run prepared from the data (a detail, not part of what it was built from).</li>
      </ul>
    </section>
  );
}
