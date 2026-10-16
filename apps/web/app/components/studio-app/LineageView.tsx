"use client";

/**
 * Lineage (moved out of the Lineage page so History can show it as a tab, V7 UI-E): how the project's versions connect,
 * which items are built on an older version, a diagram or a list, and the inspector. Plus the "newer file exists" banner
 * from the graph read's `newer_dataset` flag, linking to Monitoring. Nothing here changes the project.
 */
import Link from "next/link";
import { useCallback, useEffect, useMemo, useState } from "react";
import { Banner } from "@/components/studio/Banner";
import { Pill } from "@/components/studio/Pill";
import { safeInternalHref } from "@/components/studio/safe-href";
import { TabPanel, Tabs } from "@/components/studio/Tabs";
import { GraphCanvas, GraphLegend, GraphList, nodeDomId } from "@/app/components/studio-app/GraphCanvas";
import { GraphInspector } from "@/app/components/studio-app/GraphInspector";
import { NodeInspectorBody } from "@/app/components/studio-app/Inspectors";
import { QueryNotice } from "@/app/components/studio-app/StudioParts";
import { projectHref } from "@/lib/application/command-search";
import { useProjectExperiments } from "@/lib/application";
import { useDecisionPoints, useProjectGraph, type StudioGraph } from "@/lib/application/studio-data-hooks";
import { decisionMarkers, graphNodeNames, graphSummary, kindLabel, layoutGraph, markersFor, nameLookup, refBadge } from "@/lib/application/studio-graph";
import { evidenceHref, newerFileText } from "@/lib/application/studio-history";

const ZOOMS = [0.3, 0.45, 0.6, 0.8, 1, 1.25];

/** "A newer training file exists" with links to Monitoring and to the file; nothing when the read has no such file. */
export function NewerFileBanner({ projectId, graph }: { projectId: string; graph: StudioGraph | undefined }) {
  // Not staleness: an informational note. Monitoring is named only when a model is in use.
  const modelInUse = graph ? graph.refs.some((ref) => ref.ref_kind === "champion_model") : null;
  const text = newerFileText(graph?.newer_dataset, modelInUse);
  if (!text) return null;
  const monitoring = modelInUse ? projectHref(projectId, "monitoring") : null;
  const file = graph?.newer_dataset ? evidenceHref(projectId, { kind: "dataset_version", id: graph.newer_dataset.newest.id }) : null;
  return (
    <div className="v7-history-newer" data-testid="newer-file">
      <Banner tone="info" actions={<>
        {monitoring ? <Link className="btn" href={monitoring}>Open Monitoring</Link> : null}
        {file ? <Link className="btn" href={file}>Open the newer file</Link> : null}
      </>}>
        <b>A newer file exists.</b> {text}
      </Banner>
    </div>
  );
}

export function LineageView({ projectId }: { projectId: string }) {
  const id = projectId;
  const [cursors, setCursors] = useState<string[]>([]);
  const cursor = cursors.at(-1) ?? null;
  const graph = useProjectGraph(id, cursor);
  const points = useDecisionPoints(id);
  const runs = useProjectExperiments(id);
  const [view, setView] = useState("graph");
  const [zoom, setZoom] = useState(4);
  const [selected, setSelected] = useState<string | null>(null);

  const data = graph.data;
  const layout = useMemo(() => (data ? layoutGraph(data.nodes, data.edges) : null), [data]);
  const markers = useMemo(() => decisionMarkers(points.data?.items ?? []), [points.data]);
  const summary = useMemo(() => (data ? graphSummary(data) : null), [data]);
  const names = useMemo(() => graphNodeNames(data?.nodes ?? [], runs.data?.items ?? [], !!runs.data?.next_cursor || !runs.data, data?.edges), [data, runs.data]);
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
  const nameOf = nameLookup(names);
  const pointsTotal = [...markers.values()].reduce((sum, list) => sum + list.length, 0);

  return (
    <>
      {graph.isError ? <QueryNotice error={graph.error} what="project graph" /> : null}
      {points.isError ? <QueryNotice error={points.error} what="decision points" /> : null}
      {graph.isPending ? <p role="status">Loading the lineage…</p> : null}
      {runs.isError ? <Banner tone="warn">The run list could not be read, so runs are shown by a short reference instead of their number.</Banner> : null}
      {data && layout && summary ? (
        <>
          <section className="toolbar" aria-label="Lineage summary">
            <Pill tone="gray">{summary.nodes} items</Pill>
            <Pill tone="gray">{summary.edges} links</Pill>
            {summary.stale ? <Pill tone="warn"><span aria-hidden="true">⚠ </span>{summary.stale} built on an older version</Pill> : <Pill tone="ok">nothing built on an older version</Pill>}
            {staleKinds.length ? <span className="muted">{staleKinds.map(([kind, n]) => `${n} × ${kindLabel(kind).toLowerCase()}`).join(" · ")}</span> : null}
            {pointsTotal ? <Pill tone="ai"><span aria-hidden="true">◆ </span>{pointsTotal} AI {pointsTotal === 1 ? "answer" : "answers"} recorded</Pill> : null}
            {!data.refs_initialized ? <Pill tone="gray">No versions in use yet</Pill> : null}
            {layout.cyclic ? <Pill tone="crit">{layout.cyclic} {layout.cyclic === 1 ? "item loops back on itself" : "items loop back on themselves"}</Pill> : null}
          </section>
          {outside.length ? (
            <p className="muted">
              Versions in use that are not drawn (no loaded run was built from them yet):{" "}
              {outside.map((ref) => `${refBadge(ref.ref_kind)} → ${nameOf(ref.target)}`).join("; ")}.
            </p>
          ) : null}
          {summary.note || cursors.length ? (
            <Banner tone="warn" actions={<>
              {cursors.length ? <button type="button" className="btn" onClick={() => { setCursors([]); setSelected(null); }}>Back to the newest</button> : null}
              {data.next_cursor ? <button type="button" className="btn" onClick={() => { setCursors((list) => [...list, data.next_cursor!]); setSelected(null); }}>Show older runs</button> : null}
            </>}>
              {cursors.length ? `Older runs, page ${cursors.length}. ` : ""}{summary.note ?? "These are older runs."}
            </Banner>
          ) : null}
          {data.nodes.length === 0 ? (
            <div className="empty">Nothing is built yet. The first upload and run create the first items. {newRun ? <Link href={newRun}>Start a run</Link> : null}</div>
          ) : (
            <>
              <div className="toolbar graph-tools">
                <Tabs idPrefix="graph" label="Lineage view" value={view} onChange={setView} items={[{ id: "graph", label: "Diagram" }, { id: "list", label: "As a list", count: data.nodes.length }]} />
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
                    {view === "graph" ? <GraphCanvas graph={data} layout={layout} markers={markers} names={names} selected={selected} zoom={ZOOMS[zoom]} onSelect={select} /> : null}
                  </TabPanel>
                  <TabPanel idPrefix="graph" id="list" active={view === "list"}>
                    {view === "list" ? <GraphList graph={data} markers={markers} names={names} selected={selected} onSelect={select} /> : null}
                  </TabPanel>
                </div>
                {node ? <GraphInspector projectId={id} graph={data} node={node} name={names.get(node.key) ?? kindLabel(node.kind)} names={names} markers={markersFor(markers, node)} known={known} onSelect={select} onClose={close} slot={(selectedNode) => <NodeInspectorBody projectId={id} node={selectedNode} graph={data} />} /> : null}
              </div>
            </>
          )}
          <GraphLegend />
        </>
      ) : null}
    </>
  );
}
