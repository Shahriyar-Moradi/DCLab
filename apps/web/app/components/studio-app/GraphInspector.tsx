"use client";

import Link from "next/link";
import { useEffect, useRef, type ReactNode } from "react";
import { KeyValue } from "@/components/studio/KeyValue";
import { Level } from "@/components/studio/Level";
import { Pill } from "@/components/studio/Pill";
import { safeInternalHref } from "@/components/studio/safe-href";
import { Term } from "@/components/studio/Term";
import { knownPointLabel } from "@/lib/application/studio-runs";
import { QueryNotice, formatWhen } from "@/app/components/studio-app/StudioParts";
import { plainText } from "@/lib/application/command-search";
import { useNodeImpact, type StudioGraph, type StudioGraphNode } from "@/lib/application/studio-data-hooks";
import {
  builtFrom, groupByKind, kindLabel, nameLookup, nodeLink, refBadge, relationLabel, staleLines, type DecisionMarker, type NameOf,
} from "@/lib/application/studio-graph";

/** Typed slot for the compact per-kind inspector (reason, facts, link to the full inspector). */
export type NodeInspectorSlot = (node: StudioGraphNode) => ReactNode;

const IMPACT_TERM = "The items made from this one. If this one were replaced (for example other data put in use), these would be built on an older version. Only moving the goal, the data, the test design or the model in use does this. Nothing is retrained automatically.";

function NodeRefButton({ node, known, nameOf, onSelect }: { node: { kind: string; id: string; key: string }; known: boolean; nameOf: NameOf; onSelect: (key: string) => void }) {
  const text = <>{nameOf(node)}</>;
  return known ? <button type="button" className="linkish" onClick={() => onSelect(node.key)}>{text}</button> : <span>{text} <span className="muted">(not loaded)</span></span>;
}

function Impact({ node, known, nameOf, onSelect }: { node: StudioGraphNode; known: Set<string>; nameOf: NameOf; onSelect: (key: string) => void }) {
  const impact = useNodeImpact(node.kind, node.id);
  if (impact.isError) return <QueryNotice error={impact.error} what="what depends on this item" />;
  if (!impact.data) return <p role="status">Loading what would be built on an older version…</p>;
  const { items, total, truncated, graph_truncated: graphTruncated } = impact.data;
  if (total === 0) return <p>Nothing is built from it: a change to this item would leave nothing else built on an older version.</p>;
  const groups = groupByKind(items.map((item) => ({ ...item, label: "", stale: false })));
  return (
    <>
      <p>{total} item{total === 1 ? "" : "s"} built from this one{truncated ? `; the first ${items.length} are listed` : ""}.</p>
      {graphTruncated ? <p className="muted">The project graph was cut at its experiment window, so older items may be missing from this list.</p> : null}
      <ul className="plain-list" aria-label="What would be built on an older version">
        {groups.map((group) => (
          <li key={group.kind}>
            <b>{kindLabel(group.kind)}</b> ({impact.data.counts_by_kind[group.kind] ?? group.nodes.length}):{" "}
            {group.nodes.map((item, i) => (
              <span key={item.key}>{i ? ", " : ""}<NodeRefButton node={item} known={known.has(item.key)} nameOf={nameOf} onSelect={onSelect} /></span>
            ))}
          </li>
        ))}
      </ul>
    </>
  );
}

/** The registry's own words for the kind of decision; a key it does not know gets a generic text, never the raw key. */
const pointWords = (point: string | null): string => (point === null ? null : knownPointLabel(point)) ?? "another kind of decision";

/** The assistant's recorded answers for a run. Shown only when there are some: with AI off there is nothing to show. */
function AiAnswers({ markers }: { markers: DecisionMarker[] }) {
  if (!markers.length) return null;
  return (
    <>
    <h3>AI answers for this run</h3>
    <ul className="plain-list">
      {markers.map((m) => (
        <li key={m.id} className="graph-point">
          <span className="toolbar">
            <Pill tone="ai"><span aria-hidden="true">◆ </span>{pointWords(m.point)}</Pill>
            {m.level !== null ? <Level level={m.level} /> : null}
            <span className="muted">{m.state} · by {m.actorLabel} · {formatWhen(m.recordedAt)}</span>
          </span>
          {m.agreement ? <span>Rules and AI: {plainText(m.agreement.replaceAll("_", " "), 40)}{m.counts.length ? ` (${m.counts.map(([k, v]) => `${k} ${v}`).join(", ")})` : ""}</span> : null}
          {m.answers.length ? (
            <table className="graph-answers">
              <caption className="sr-only">The rules&apos; answer beside the AI&apos;s answer for {pointWords(m.point)}</caption>
              <thead><tr><th scope="col">Column</th><th scope="col">Rule</th><th scope="col">AI</th><th scope="col">Used</th></tr></thead>
              <tbody>
                {m.answers.map((a, i) => (
                  <tr key={`${a.column}-${i}`}><td className="mono">{plainText(a.column, 60)}</td><td>{plainText(a.rule, 40)}</td><td>{plainText(a.ai, 40)}</td><td>{plainText(a.used, 40)} <span className="muted">({plainText(a.source, 20)})</span></td></tr>
                ))}
              </tbody>
            </table>
          ) : null}
          {m.answersTotal !== null && m.answersTotal > m.answers.length ? <span className="muted">{m.answers.length} of {m.answersTotal} answers shown; History has the full record.</span> : null}
          {m.detailsTruncated ? <span className="muted">The record was too large to show in full here.</span> : null}
        </li>
      ))}
    </ul>
    </>
  );
}

export function GraphInspector({ projectId, graph, node, name, names, markers, known, onSelect, onClose, slot }: {
  projectId: string; graph: StudioGraph; node: StudioGraphNode; name: string; names: ReadonlyMap<string, string>; markers: DecisionMarker[]; known: Set<string>;
  onSelect: (key: string) => void; onClose: () => void; slot?: NodeInspectorSlot;
}) {
  const heading = useRef<HTMLHeadingElement>(null);
  useEffect(() => { heading.current?.focus(); }, [node.key]);
  const refs = graph.refs.filter((ref) => ref.target.key === node.key);
  const from = builtFrom(graph.edges, node.key);
  const nameOf = nameLookup(names);
  const stale = staleLines(node, names);
  const link = nodeLink(projectId, node);
  const href = link ? safeInternalHref(link.href) : null;
  return (
    <aside className="graph-drawer card" aria-labelledby="graph-drawer-title" onKeyDown={(event) => { if (event.key === "Escape") onClose(); }}>
      <h2 id="graph-drawer-title" ref={heading} tabIndex={-1}>
        {name}
        <button type="button" className="btn sp" onClick={onClose}>Close<span className="sr-only"> the details of {name}</span></button>
      </h2>
      <p className="muted">{kindLabel(node.kind)}</p>
      <KeyValue items={[
        { key: "kind", label: "Kind", value: kindLabel(node.kind) },
        { key: "id", label: "Reference", value: <span className="mono">{node.id}</span> },
        ...(node.version ? [{ key: "version", label: "Version", value: plainText(node.version, 40) }] : []),
        { key: "created", label: "Created", value: formatWhen(node.created_at) },
        { key: "status", label: "Status", value: node.status ? node.status.toLowerCase().replaceAll("_", " ") : "—" },
        {
          key: "refs", label: <Term definition="The versions the project uses now (goal, data, test design, features, model). Changing one is saved in History.">Versions in use here</Term>,
          value: refs.length ? refs.map((ref) => `${refBadge(ref.ref_kind)} (last changed ${formatWhen(ref.moved_at)})${ref.staleness_bearing ? "" : ". Changing it does not mark anything as built on an older version"}`).join("; ") : "Not in use",
        },
        {
          key: "stale", label: <Term definition="Built on an older version: this item was made from a version that is no longer in use. Nothing is changed or retrained.">Built on an older version</Term>,
          value: node.stale ? <>{stale.length ? stale.map((line) => <span key={line} className="block"><span aria-hidden="true">⚠ </span>{line}</span>) : "yes"}</> : "No",
        },
        ...(node.intent ? [{ key: "intent", label: "Why it was tried", value: <span>{plainText(node.intent, 300)} <span className="muted">(written by a person, a connected tool or the assistant)</span></span> }] : []),
        ...(node.lineage_incomplete ? [{ key: "lineage", label: "What it was built from", value: "Incomplete: an older run without a recorded data file" }] : []),
        ...(node.outside_window ? [{ key: "window", label: "Loaded", value: "Not loaded: it comes from an older run than the newest ones shown here" }] : []),
        ...(node.notes?.length ? [{ key: "notes", label: "Notes", value: node.notes.map((n) => plainText(n, 120)).join("; ") }] : []),
      ]} />
      <h3>Built from</h3>
      {from.length ? (
        <ul className="plain-list">
          {from.map((item) => (
            <li key={`${item.relation}-${item.node.id}`}>
              {relationLabel(item.relation)}{" "}
              <NodeRefButton node={{ ...item.node, key: item.node.key ?? `${item.node.kind}:${item.node.id}` }} known={known.has(item.node.key ?? "")} nameOf={nameOf} onSelect={onSelect} />
              {item.attribute ? <span className="muted"> (a detail, not part of what it was built from)</span> : null}
            </li>
          ))}
        </ul>
      ) : <p className="muted">Nothing: this is a starting point.</p>}
      <h3><Term definition={IMPACT_TERM}>What would be built on an older version</Term></h3>
      <Impact node={node} known={known} nameOf={nameOf} onSelect={onSelect} />
      {node.kind === "experiment" ? <AiAnswers markers={markers} /> : null}
      {slot ? slot(node) : (
        <div className="toolbar">
          {href && link ? <Link className="btn" href={href}>{link.label}</Link> : null}
        </div>
      )}
    </aside>
  );
}
