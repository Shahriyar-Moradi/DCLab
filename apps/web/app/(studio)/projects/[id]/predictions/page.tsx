"use client";

/**
 * Predictions (V7-A5): score a new file with a model of this project and see what was scored in this browser session.
 * Existing reads only: the model list is the project graph, the results are the prediction read. Nothing here is scheduled
 * or explained per row (not built), and there is no list read for scorings, so the history is this person's, in this browser tab.
 */
import Link from "next/link";
import { useParams } from "next/navigation";
import { useMemo, useState } from "react";
import { Banner } from "@/components/studio/Banner";
import { Card } from "@/components/studio/Card";
import { DataTable, type Column } from "@/components/studio/DataTable";
import { PageHead } from "@/components/studio/PageHead";
import { Pill } from "@/components/studio/Pill";
import { ScoreNewData } from "@/app/components/studio-app/ScoreNewData";
import { QueryNotice, formatWhen, statusTone } from "@/app/components/studio-app/StudioParts";
import { useProjectExperiments } from "@/lib/application";
import { projectHref } from "@/lib/application/command-search";
import { modelRows } from "@/lib/application/studio-card";
import { useProjectGraph } from "@/lib/application/studio-data-hooks";
import { runRef } from "@/lib/application/studio-model";
import { runOrdinals } from "@/lib/application/studio-names";
import { historyRows, modelChoiceLabel, pickModel, type HistoryRow, type ReadState, type SessionEntry } from "@/lib/application/studio-scoring";
import { readSessionScorings, usePredictionsById, useScoringScope } from "@/lib/application/studio-scoring-hooks";

export default function PredictionsPage() {
  const { id } = useParams<{ id: string }>();
  const graph = useProjectGraph(id);
  const runs = useProjectExperiments(id);
  const [chosen, setChosen] = useState<string | null>(null);
  const [refresh, setRefresh] = useState(0);
  const scope = useScoringScope();

  const models = useMemo(() => (graph.data ? modelRows(id, graph.data.nodes, graph.data.edges) : []), [graph.data, id]);
  const ordinals = useMemo(() => runOrdinals(runs.data?.items ?? []), [runs.data]);
  const partial = !!runs.data?.next_cursor || !runs.data;
  const labels = useMemo(
    () => new Map(models.map((m) => [m.id, modelChoiceLabel(m, runRef(ordinals, partial, m.experimentId))])),
    [models, ordinals, partial],
  );
  const modelId = pickModel(models, chosen);
  const model = models.find((m) => m.id === modelId) ?? null;

  // Scorings this person started in this browser tab, for the models listed here (read again after each new scoring).
  const entries = useMemo<SessionEntry[]>(
    () => (typeof window === "undefined" || !scope ? [] : models.flatMap((m) => readSessionScorings(scope, m.id).map((s) => ({ ...s, modelVersionId: m.id })))),
    // `refresh` is bumped after a scoring was started; the session store is not reactive.
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [models, scope, refresh],
  );
  const reads = usePredictionsById(entries.map((e) => e.id));
  const history = historyRows(
    entries,
    new Map<string, ReadState>(entries.map((e, i) => [e.id, { data: reads[i]?.data, failed: !!reads[i]?.isError && !reads[i]?.data }] as const)),
    (mv) => labels.get(mv) ?? null,
    (mv) => { const base = projectHref(id, "models", mv); return base ? `${base}?tab=score` : null; },
  );

  const columns: Column<HistoryRow>[] = [
    { key: "file", header: "File", render: (r) => r.file },
    { key: "model", header: "Model", render: (r) => r.model },
    { key: "when", header: "Started", sortValue: (r) => r.when ?? "", render: (r) => formatWhen(r.when) },
    { key: "status", header: "Status", render: (r) => <Pill tone={statusTone(r.statusKey)}>{r.status}</Pill> },
    { key: "rows", header: "Rows scored", numeric: true, render: (r) => r.rows },
    { key: "by", header: "Run by", render: (r) => r.by },
    { key: "open", header: "Open", render: (r) => (r.href ? <Link href={r.href}>Open on the model page<span className="sr-only">: {r.file}</span></Link> : "—") },
  ];

  return (
    <>
      <PageHead title="Predictions" subtitle="Run a model on a new file and keep the predictions file." actions={<Link className="btn" href={`/projects/${id}/models`}>All models</Link>} />
      {graph.isError ? <QueryNotice error={graph.error} what="models of this project" /> : null}
      {graph.isPending ? <p role="status">Loading the models…</p> : null}
      {graph.data && !models.length ? (
        <div className="empty">
          <p><b>There is no model to score with yet.</b></p>
          <p>Train a run; its best model appears here. <Link href={`/projects/${id}/experiments`}>Open the experiments</Link>.</p>
        </div>
      ) : null}
      {model ? (
        <ScoreNewData
          key={model.id}
          projectId={id}
          modelVersionId={model.id}
          modelLabel={labels.get(model.id) ?? null}
          onScored={() => setRefresh((n) => n + 1)}
          showPast={false}
          before={
            <Card title="Model to use" aside={model.champion ? <Pill tone="ok">in use</Pill> : <Pill tone="gray">not in use</Pill>}>
              <label className="field"><span>Model</span>
                <select value={model.id} onChange={(event) => setChosen(event.target.value)}>
                  {models.map((m) => <option key={m.id} value={m.id}>{labels.get(m.id)}</option>)}
                </select>
              </label>
              {!model.champion ? <Banner tone="info">{models.some((m) => m.champion) ? "This is not the model in use. Check the model number and the run before you score a file." : "No model is in use yet. Check the model number and the run before you score a file."}</Banner> : null}
              {model.href ? <p><Link href={model.href}>Open this model</Link></p> : null}
              {graph.data?.truncated ? <p className="muted">Only models from the newest runs are listed. <Link href={`/projects/${id}/graph`}>See the full project diagram</Link> for older ones.</p> : null}
            </Card>
          }
        />
      ) : null}
      {models.length ? (
        <Card title="Predictions run" aside={<span className="muted">this browser tab</span>}>
          <DataTable caption="Predictions run in this browser tab" columns={columns} rows={history} rowKey={(r) => r.id} emptyMessage="You have not scored a file in this browser tab yet." />
          <p className="muted">Only files you scored in this browser tab, with the models listed above, are shown. Earlier scorings, and those started by a connected tool, by someone else or in another tab, cannot be listed yet.</p>
        </Card>
      ) : null}
    </>
  );
}
