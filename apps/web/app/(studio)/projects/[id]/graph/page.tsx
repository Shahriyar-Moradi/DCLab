"use client";

import Link from "next/link";
import { useParams } from "next/navigation";
import { useCallback, useEffect, useMemo, useState } from "react";
import { Banner } from "@/components/studio/Banner";
import { PageGuide } from "@/components/studio/PageGuide";
import { PageHead } from "@/components/studio/PageHead";
import { Pill } from "@/components/studio/Pill";
import { safeInternalHref } from "@/components/studio/safe-href";
import { TabPanel, Tabs } from "@/components/studio/Tabs";
import { Term } from "@/components/studio/Term";
import { GraphCanvas, GraphLegend, GraphList, nodeDomId } from "@/app/components/studio-app/GraphCanvas";
import { GraphInspector } from "@/app/components/studio-app/GraphInspector";
import { NodeInspectorBody } from "@/app/components/studio-app/Inspectors";
import { QueryNotice } from "@/app/components/studio-app/StudioParts";
import { projectHref } from "@/lib/application/command-search";
import { useDecisionPoints, useProjectGraph } from "@/lib/application/studio-data-hooks";
import { REF_BADGE, decisionMarkers, graphSummary, kindLabel, layoutGraph, markersFor, shortId } from "@/lib/application/studio-graph";

const TERMS = {
  lineage: "Which versions each node was built from: a split plan partitions a dataset version, an experiment uses a spec, a dataset and a split plan, a model version is produced by an experiment.",
  stale: "Built from a version that a project ref no longer points at. Computed on read from the refs; nothing is changed or retrained.",
  ref: "A named pointer (spec, data, split, features, champion) to the version the project uses now. Moving it is a recorded decision.",
  split: "SplitPlan: the fixed assignment of rows to the final holdout and cross-validation folds. Experiments on the same plan are comparable.",
};
const ZOOMS = [0.3, 0.45, 0.6, 0.8, 1, 1.25];

export default function GraphPage() {
  const { id } = useParams<{ id: string }>();
  const [cursors, setCursors] = useState<string[]>([]);
  const cursor = cursors.at(-1) ?? null;
  const graph = useProjectGraph(id, cursor);
  const points = useDecisionPoints(id);
  const [view, setView] = useState("graph");
  const [zoom, setZoom] = useState(4);
  const [selected, setSelected] = useState<string | null>(null);

  const data = graph.data;
  const layout = useMemo(() => (data ? layoutGraph(data.nodes, data.edges) : null), [data]);
  const markers = useMemo(() => decisionMarkers(points.data?.items ?? []), [points.data]);
  const summary = useMemo(() => (data ? graphSummary(data) : null), [data]);
  const byKey = useMemo(() => new Map((data?.nodes ?? []).map((node) => [node.key, node])), [data]);
  const known = useMemo(() => new Set(byKey.keys()), [byKey]);
  const node = selected ? byKey.get(selected) ?? null : null;

  // Keep the selected node in view when the drawer opens and the canvas narrows.
  useEffect(() => {
    if (node && view === "graph") document.getElementById(nodeDomId(node))?.scrollIntoView?.({ block: "nearest", inline: "center" });
  }, [node, view]);
  const select = useCallback((key: string) => setSelected(key), []);
  const close = useCallback(() => {
    const target = selected ? byKey.get(selected) : null;
    setSelected(null);
    if (target && view === "graph") document.getElementById(nodeDomId(target))?.focus();
  }, [byKey, selected, view]);

  const experiments = projectHref(id, "experiments");
  const newRun = experiments ? safeInternalHref(`${experiments}/new`) : null;
  const staleKinds = Object.entries(data?.stale_counts_by_kind ?? {}).filter(([, n]) => n > 0);
  const outside = (data?.refs ?? []).filter((ref) => !ref.target_in_graph);
  const pointsTotal = [...markers.values()].reduce((sum, list) => sum + list.length, 0);

  return (
    <>
      <PageHead
        title="Graph"
        subtitle="Every versioned node of the project with its lineage. ★ marks the current refs; orange nodes marked stale were built from a version a ref has moved away from. Select a node to inspect it and see what would become stale."
      />
      <PageGuide
        purpose={<>See how the project&apos;s versions connect (<Term definition={TERMS.lineage}>lineage</Term>) and which results are out of date.</>}
        howTo={<>Select a node, or switch to &ldquo;As a list&rdquo; for a keyboard-friendly table. Arrows point from what a node was built from. The <Term definition={TERMS.ref}>refs</Term> and the <Term definition={TERMS.split}>split plan</Term> decide what is comparable.</>}
        youGet="Kind, id, digest, status and refs of each node, the nodes it was built from, and the nodes a change to it would make stale. AI decision points appear only when the API recorded one."
        attention={<>A <Term definition={TERMS.stale}>stale</Term> node is not wrong, only built from an older version. Nothing here changes the project; ref moves, compare and branch arrive with P4.4-A.</>}
      />
      {graph.isError ? <QueryNotice error={graph.error} what="project graph" /> : null}
      {points.isError ? <QueryNotice error={points.error} what="decision points" /> : null}
      {graph.isPending ? <p role="status">Loading the graph…</p> : null}
      {data && layout && summary ? (
        <>
          <section className="toolbar" aria-label="Graph summary">
            <Pill tone="gray">{summary.nodes} nodes</Pill>
            <Pill tone="gray">{summary.edges} edges</Pill>
            {summary.stale ? <Pill tone="warn"><span aria-hidden="true">⚠ </span>{summary.stale} stale</Pill> : <Pill tone="ok">nothing stale</Pill>}
            {staleKinds.length ? <span className="muted">{staleKinds.map(([kind, n]) => `${n} ${kindLabel(kind).toLowerCase()}`).join(" · ")}</span> : null}
            {pointsTotal ? <Pill tone="ai"><span aria-hidden="true">◆ </span>{pointsTotal} AI decision points</Pill> : <span className="muted">No AI decision points recorded.</span>}
            {!data.refs_initialized ? <Pill tone="gray">No refs yet</Pill> : null}
            {layout.cyclic ? <Pill tone="crit">{layout.cyclic} node(s) on a cycle</Pill> : null}
          </section>
          {outside.length ? (
            <p className="muted">
              Refs whose target is not drawn (no loaded node is built from it yet):{" "}
              {outside.map((ref) => `${REF_BADGE[ref.ref_kind] ?? ref.ref_kind} → ${kindLabel(ref.target.kind).toLowerCase()} ${shortId(ref.target.id)} (version ${ref.version})`).join("; ")}.
            </p>
          ) : null}
          {summary.note || cursors.length ? (
            <Banner tone="warn" actions={<>
              {cursors.length ? <button type="button" className="btn" onClick={() => { setCursors([]); setSelected(null); }}>Back to the newest</button> : null}
              {data.next_cursor ? <button type="button" className="btn" onClick={() => { setCursors((list) => [...list, data.next_cursor!]); setSelected(null); }}>Show older experiments</button> : null}
            </>}>
              {cursors.length ? `Older window ${cursors.length}. ` : ""}{summary.note ?? "This is an older experiment window."}
            </Banner>
          ) : null}
          {data.nodes.length === 0 ? (
            <div className="empty">No versioned nodes yet. The first upload and run create them. {newRun ? <Link href={newRun}>Start a run</Link> : null}</div>
          ) : (
            <>
              <div className="toolbar graph-tools">
                <Tabs idPrefix="graph" label="Graph view" value={view} onChange={setView} items={[{ id: "graph", label: "Graph" }, { id: "list", label: "As a list", count: data.nodes.length }]} />
                {view === "graph" ? (
                  <span className="toolbar" role="group" aria-label="Zoom">
                    <button type="button" className="btn" aria-label="Zoom out" disabled={zoom === 0} onClick={() => setZoom((z) => Math.max(0, z - 1))}>−</button>
                    <span className="mono" aria-live="polite">{Math.round(ZOOMS[zoom] * 100)}%</span>
                    <button type="button" className="btn" aria-label="Zoom in" disabled={zoom === ZOOMS.length - 1} onClick={() => setZoom((z) => Math.min(ZOOMS.length - 1, z + 1))}>+</button>
                    <button type="button" className="btn" onClick={() => setZoom(4)}>Reset zoom</button>
                  </span>
                ) : null}
              </div>
              <div className={node ? "graph-side open" : "graph-side"}>
                <div className="graph-main">
                  <TabPanel idPrefix="graph" id="graph" active={view === "graph"}>
                    {view === "graph" ? <GraphCanvas graph={data} layout={layout} markers={markers} selected={selected} zoom={ZOOMS[zoom]} onSelect={select} /> : null}
                  </TabPanel>
                  <TabPanel idPrefix="graph" id="list" active={view === "list"}>
                    {view === "list" ? <GraphList graph={data} markers={markers} selected={selected} onSelect={select} /> : null}
                  </TabPanel>
                </div>
                {node ? <GraphInspector projectId={id} graph={data} node={node} markers={markersFor(markers, node)} known={known} onSelect={select} onClose={close} slot={(selectedNode) => <NodeInspectorBody projectId={id} node={selectedNode} graph={data} />} /> : null}
              </div>
            </>
          )}
          <GraphLegend />
        </>
      ) : null}
    </>
  );
}
