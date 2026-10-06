"use client";

import Link from "next/link";
import { useEffect, useRef, type ReactNode } from "react";
import { KeyValue } from "@/components/studio/KeyValue";
import { Level } from "@/components/studio/Level";
import { Pill } from "@/components/studio/Pill";
import { safeInternalHref } from "@/components/studio/safe-href";
import { Term } from "@/components/studio/Term";
import { PhaseEmpty, QueryNotice, formatWhen } from "@/app/components/studio-app/StudioParts";
import { plainText } from "@/lib/application/command-search";
import { useNodeImpact, type StudioGraph, type StudioGraphNode } from "@/lib/application/studio-data-hooks";
import {
  REF_BADGE, RELATION_LABEL, builtFrom, groupByKind, kindLabel, nodeLink, shortId, staleLines, type DecisionMarker,
} from "@/lib/application/studio-graph";

/** Typed slot for the full per-kind inspectors (reason, evidence, code) that P4.3-A adds. */
export type NodeInspectorSlot = (node: StudioGraphNode) => ReactNode;

const IMPACT_TERM = "The nodes downstream of this one along lineage edges. If this node were replaced (for example a ref moved off it), these are the nodes that would be built from an old version, so the graph would mark them stale. Nothing is retrained automatically.";

function NodeRefButton({ node, known, onSelect }: { node: { kind: string; id: string; key: string }; known: boolean; onSelect: (key: string) => void }) {
  const text = <>{kindLabel(node.kind)} <span className="mono">{shortId(node.id)}</span></>;
  return known ? <button type="button" className="linkish" onClick={() => onSelect(node.key)}>{text}</button> : <span>{text} <span className="muted">(not loaded)</span></span>;
}

function Impact({ node, known, onSelect }: { node: StudioGraphNode; known: Set<string>; onSelect: (key: string) => void }) {
  const impact = useNodeImpact(node.kind, node.id);
  if (impact.isError) return <QueryNotice error={impact.error} what="impact of this node" />;
  if (!impact.data) return <p role="status">Loading what becomes stale…</p>;
  const { items, total, truncated, graph_truncated: graphTruncated } = impact.data;
  if (total === 0) return <p>Nothing downstream: a change to this node would make no other node stale.</p>;
  const groups = groupByKind(items.map((item) => ({ ...item, label: "", stale: false })));
  return (
    <>
      <p>{total} node{total === 1 ? "" : "s"} downstream{truncated ? `; the first ${items.length} are listed` : ""}.</p>
      {graphTruncated ? <p className="muted">The project graph was cut at its experiment window, so the closure may miss older nodes.</p> : null}
      <ul className="plain-list" aria-label="What becomes stale">
        {groups.map((group) => (
          <li key={group.kind}>
            <b>{kindLabel(group.kind)}</b> ({impact.data.counts_by_kind[group.kind] ?? group.nodes.length}):{" "}
            {group.nodes.map((item, i) => (
              <span key={item.key}>{i ? ", " : ""}<NodeRefButton node={item} known={known.has(item.key)} onSelect={onSelect} /></span>
            ))}
          </li>
        ))}
      </ul>
    </>
  );
}

function DecisionPoints({ markers }: { markers: DecisionMarker[] }) {
  if (!markers.length) return <p className="muted">No AI decision point is recorded for this experiment (AI was off, or no question was asked).</p>;
  return (
    <ul className="plain-list">
      {markers.map((m) => (
        <li key={m.id} className="graph-point">
          <span className="toolbar">
            <Pill tone="ai"><span aria-hidden="true">◆ </span>{m.point ?? "decision point"}</Pill>
            {m.level !== null ? <Level level={m.level} /> : null}
            <span className="muted">{m.state} · by {m.actor} · {formatWhen(m.recordedAt)}</span>
          </span>
          {m.agreement ? <span>Rule and AI: {m.agreement.replaceAll("_", " ")}{m.counts.length ? ` (${m.counts.map(([k, v]) => `${k} ${v}`).join(", ")})` : ""}</span> : null}
          {m.answers.length ? (
            <table className="graph-answers">
              <caption className="sr-only">Rule answer beside AI answer for {m.point ?? "this decision point"}</caption>
              <thead><tr><th scope="col">Column</th><th scope="col">Rule</th><th scope="col">AI</th><th scope="col">Used</th></tr></thead>
              <tbody>
                {m.answers.map((a, i) => (
                  <tr key={`${a.column}-${i}`}><td className="mono">{plainText(a.column, 60)}</td><td>{plainText(a.rule, 40)}</td><td>{plainText(a.ai, 40)}</td><td>{plainText(a.used, 40)} <span className="muted">({plainText(a.source, 20)})</span></td></tr>
                ))}
              </tbody>
            </table>
          ) : null}
          {m.answersTotal !== null && m.answersTotal > m.answers.length ? <span className="muted">{m.answers.length} of {m.answersTotal} answers shown; the Decisions page has the record.</span> : null}
          {m.detailsTruncated ? <span className="muted">The record&apos;s details were too large for the list view; only service keys are shown.</span> : null}
        </li>
      ))}
    </ul>
  );
}

export function GraphInspector({ projectId, graph, node, markers, known, onSelect, onClose, slot }: {
  projectId: string; graph: StudioGraph; node: StudioGraphNode; markers: DecisionMarker[]; known: Set<string>;
  onSelect: (key: string) => void; onClose: () => void; slot?: NodeInspectorSlot;
}) {
  const heading = useRef<HTMLHeadingElement>(null);
  useEffect(() => { heading.current?.focus(); }, [node.key]);
  const refs = graph.refs.filter((ref) => ref.target.key === node.key);
  const from = builtFrom(graph.edges, node.key);
  const stale = staleLines(node);
  const link = nodeLink(projectId, node);
  const href = link ? safeInternalHref(link.href) : null;
  return (
    <aside className="graph-drawer card" aria-labelledby="graph-drawer-title" onKeyDown={(event) => { if (event.key === "Escape") onClose(); }}>
      <h2 id="graph-drawer-title" ref={heading} tabIndex={-1}>
        {kindLabel(node.kind)} <span className="mono">{shortId(node.id)}</span>
        <button type="button" className="btn sp" onClick={onClose}>Close<span className="sr-only"> the inspector</span></button>
      </h2>
      <p>{plainText(node.label, 200)}</p>
      <KeyValue items={[
        { key: "kind", label: "Kind", value: kindLabel(node.kind) },
        { key: "id", label: "Id", value: <span className="mono">{node.id}</span> },
        { key: "digest", label: "Digest", value: node.digest ? <span className="mono">{node.digest}</span> : "—" },
        ...(node.version ? [{ key: "version", label: "Version", value: node.version }] : []),
        { key: "created", label: "Created", value: formatWhen(node.created_at) },
        { key: "status", label: "Status", value: node.status ? node.status.toLowerCase().replaceAll("_", " ") : "—" },
        {
          key: "refs", label: <Term definition="A ref is a named pointer to the version the project uses now. Moving it is a recorded decision.">Refs here</Term>,
          value: refs.length ? refs.map((ref) => `${REF_BADGE[ref.ref_kind] ?? ref.ref_kind} (version ${ref.version}, moved ${formatWhen(ref.moved_at)})${ref.staleness_bearing ? "" : ", does not mark nodes stale yet"}`).join("; ") : "No ref points here",
        },
        {
          key: "stale", label: <Term definition="Stale: this node was built from a version that a project ref no longer points at. Computed on read from the refs; nothing is changed or retrained.">Stale</Term>,
          value: node.stale ? <>{stale.length ? stale.map((line) => <span key={line} className="block"><span aria-hidden="true">⚠ </span>{line}</span>) : "stale"}</> : "Not stale",
        },
        ...(node.intent ? [{ key: "intent", label: "Intent", value: <span>{plainText(node.intent, 300)} <span className="muted">(written by a person or agent)</span></span> }] : []),
        ...(node.lineage_incomplete ? [{ key: "lineage", label: "Lineage", value: "Incomplete: an older run without a recorded source dataset" }] : []),
        ...(node.outside_window ? [{ key: "window", label: "Window", value: "Parent from an older experiment window (not fully loaded)" }] : []),
        ...(node.notes?.length ? [{ key: "notes", label: "Notes", value: node.notes.map((n) => plainText(n, 120)).join("; ") }] : []),
      ]} />
      <h3>Built from</h3>
      {from.length ? (
        <ul className="plain-list">
          {from.map((item) => (
            <li key={`${item.relation}-${item.node.id}`}>
              {RELATION_LABEL[item.relation] ?? item.relation}{" "}
              <NodeRefButton node={{ ...item.node, key: item.node.key ?? `${item.node.kind}:${item.node.id}` }} known={known.has(item.node.key ?? "")} onSelect={onSelect} />
              {item.attribute ? <span className="muted"> (attribute)</span> : null}
            </li>
          ))}
        </ul>
      ) : <p className="muted">Nothing: this node is a source.</p>}
      <h3><Term definition={IMPACT_TERM}>What becomes stale</Term></h3>
      <Impact node={node} known={known} onSelect={onSelect} />
      {node.kind === "experiment" ? (<><h3>AI decision points</h3><DecisionPoints markers={markers} /></>) : null}
      <div className="toolbar">
        {href && link ? <Link className="btn" href={href}>{link.label}</Link> : null}
      </div>
      {slot ? slot(node) : <PhaseEmpty title="The full inspector (reason, evidence and code) for this node" phase="P4.3-A" />}
    </aside>
  );
}
