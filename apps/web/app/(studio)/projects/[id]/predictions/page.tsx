"use client";

/**
 * Predictions (V7-A5): score a new file with a model of this project and see what was scored in this browser session.
 * Existing reads only: the model list is the project graph, the results are the prediction read. The weekly scoring card
 * (V7-B2b-UI) shows and changes the schedule; there is no list read for scorings, so the table is this person's scorings in this
 * browser tab plus the weeks the schedule queued, each labelled scheduled or by hand.
 * UI-D (v7 parity): tiles and "This week's list" (on the list tab) (rows with their reasons) for the newest finished scoring listed here,
 * and three tabs: the list, scoring a file, the weekly schedule. The score and schedule components are unchanged.
 */
import Link from "next/link";
import { useParams, useSearchParams } from "next/navigation";
import { Suspense, useMemo, useState } from "react";
import { Banner } from "@/components/studio/Banner";
import { Card } from "@/components/studio/Card";
import { DataTable, type Column } from "@/components/studio/DataTable";
import { PageHead } from "@/components/studio/PageHead";
import { Pill } from "@/components/studio/Pill";
import { Stat } from "@/components/studio/Stat";
import { TabPanel, Tabs } from "@/components/studio/Tabs";
import { PredictionList } from "@/app/components/studio-app/PredictionList";
import { WeeklyScoring } from "@/app/components/studio-app/WeeklyScoring";
import { ScoreNewData } from "@/app/components/studio-app/ScoreNewData";
import { QueryNotice, formatWhen, statusTone } from "@/app/components/studio-app/StudioParts";
import { useProjectExperiments } from "@/lib/application";
import { isUuid, plainText, projectHref } from "@/lib/application/command-search";
import { modelRows } from "@/lib/application/studio-card";
import { useProjectGraph } from "@/lib/application/studio-data-hooks";
import { runRef } from "@/lib/application/studio-model";
import { runOrdinals } from "@/lib/application/studio-names";
import { historyRows, modelChoiceLabel, pickModel, type HistoryRow, type ReadState, type SessionEntry } from "@/lib/application/studio-scoring";
import { pickList, predictionTiles } from "@/lib/application/studio-prediction-list";
import { utcWhen } from "@/lib/application/studio-schedule";
import { useDatasetNames, useScoringSchedule } from "@/lib/application/studio-schedule-hooks";
import { readSessionScorings, usePredictionsById, useScoringScope, type StudioPrediction } from "@/lib/application/studio-scoring-hooks";

const TABS = [
  { id: "list", label: "This week's list" },
  { id: "score", label: "Score a file" },
  { id: "schedule", label: "Weekly schedule" },
];

function PredictionsPageInner() {
  const { id } = useParams<{ id: string }>();
  const requested = useSearchParams().get("tab");
  const [tab, setTab] = useState(() => (TABS.some((t) => t.id === requested) ? (requested as string) : "list"));
  const [listId, setListId] = useState<string | null>(null);
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
  const schedule = useScoringSchedule(id);
  const mine = useMemo<SessionEntry[]>(
    () => (typeof window === "undefined" || !scope ? [] : models.flatMap((m) => readSessionScorings(scope, m.id).map((s) => ({ ...s, modelVersionId: m.id })))),
    // `refresh` is bumped after a scoring was started; the session store is not reactive.
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [models, scope, refresh],
  );
  // The weeks the schedule queued (from the schedule read), unless this tab already lists that scoring.
  const weeklyRuns = useMemo(
    () => (schedule.data?.recent_runs ?? []).filter((r) => r.outcome === "queued" && isUuid(r.batch_prediction_id) && isUuid(r.model_version_id)),
    [schedule.data],
  );
  // The file a weekly run scored is named from its own dataset read; if that is unavailable the row says so instead of guessing.
  const datasetNames = useDatasetNames(useMemo(() => weeklyRuns.flatMap((r) => (isUuid(r.input_dataset_id) ? [r.input_dataset_id as string] : [])), [weeklyRuns]));
  const entries = useMemo<SessionEntry[]>(() => {
    const seen = new Set(mine.map((e) => e.id));
    const weekly = weeklyRuns.filter((r) => !seen.has(r.batch_prediction_id as string)).map((r) => ({
      id: r.batch_prediction_id as string,
      fileName: (r.input_dataset_id && datasetNames.get(r.input_dataset_id)) || "Chosen by the weekly schedule (file not named here)",
      modelVersionId: r.model_version_id as string,
      scheduled: true,
    }));
    return [...mine, ...weekly];
  }, [mine, weeklyRuns, datasetNames]);
  const reads = usePredictionsById(entries.map((e) => e.id));
  const history = historyRows(
    entries,
    new Map<string, ReadState>(entries.map((e, i) => [e.id, { data: reads[i]?.data, failed: !!reads[i]?.isError && !reads[i]?.data }] as const)),
    (mv) => labels.get(mv) ?? null,
    (mv) => { const base = projectHref(id, "models", mv); return base ? `${base}?tab=score` : null; },
    { when: formatWhen, utc: utcWhen, inUseNow: (mv) => models.find((row) => row.id === mv)?.champion ?? null },
  );

  // The finished scorings this page lists (this tab's and the schedule's), for the tiles and the list; the newest by default.
  const finished = entries.flatMap((e, i) => {
    const data = reads[i]?.data;
    return data && data.status === "completed" && data.model_version_id === e.modelVersionId ? [{ entry: e, data }] : [];
  });
  const chosenList = pickList(finished.map((f) => ({ ...f.data, entry: f.entry })), listId);
  const listTitle = (p: StudioPrediction & { entry: SessionEntry }) =>
    `${plainText(p.entry.fileName, 80)} · ${labels.get(p.model_version_id) ?? "a model of this project"} · scored ${utcWhen(p.completed_at)}${p.scheduled ? " · weekly schedule" : ""}`;
  const tiles = chosenList ? predictionTiles(chosenList) : null;
  const readsPending = entries.length > 0 && reads.some((r) => r.isPending);

  const columns: Column<HistoryRow>[] = [
    { key: "file", header: "File", render: (r) => <span id={`prediction-${r.id}`}>{r.file}</span> },
    { key: "origin", header: "How it started", render: (r) => r.origin },
    { key: "model", header: "Model", render: (r) => r.model },
    { key: "used", header: "Approval used", render: (r) => r.approval },
    { key: "when", header: "Started", sortValue: (r) => r.when ?? "", render: (r) => formatWhen(r.when) },
    { key: "status", header: "Status", render: (r) => <><Pill tone={statusTone(r.statusKey)}>{r.status}</Pill>{r.failure ? <><br /><span className="muted">{r.failure}</span></> : null}</> },
    { key: "rows", header: "Rows scored", numeric: true, render: (r) => r.rows },
    { key: "by", header: "Run by", render: (r) => r.by },
    { key: "open", header: "Open", render: (r) => (r.href ? <Link href={r.href}>Open on the model page<span className="sr-only">: {r.file}</span></Link> : "—") },
  ];

  return (
    <>
      <PageHead title="Predictions" subtitle="Run a model on a new file and keep the predictions file, or let DCLab score the newest file every week. See who was flagged and, for each row, the top reasons the model used." actions={<Link className="btn" href={`/projects/${id}/models`}>All models</Link>} />
      <Tabs items={TABS} value={tab} onChange={setTab} idPrefix="pred" label="Predictions sections" />
      <TabPanel idPrefix="pred" id="list" active={tab === "list"}>
        {tiles ? (
          <section className="v7-pred-head" aria-label="The list shown below">
            <div className="grid cols-3 v7-pred-tiles">
              <Stat value={tiles.flagged.value} label={tiles.flagged.label} hint={tiles.flagged.hint} />
              <Stat value={tiles.scored.value} label={tiles.scored.label} hint={tiles.scored.hint} />
              <Stat value={tiles.last.value} label={tiles.last.label} hint={tiles.last.hint} />
            </div>
          </section>
        ) : null}
        {chosenList ? (
          <>
            {finished.length > 1 ? (
              <label className="field v7-pred-choose"><span>List to show</span>
                <select value={chosenList.id} onChange={(event) => setListId(event.target.value)}>
                  {finished.map((f) => <option key={f.data.id} value={f.data.id}>{listTitle({ ...f.data, entry: f.entry })}</option>)}
                </select>
              </label>
            ) : null}
            <PredictionList key={chosenList.id} prediction={chosenList} title={`From ${listTitle(chosenList)}.`} />
          </>
        ) : readsPending || (graph.isPending && !graph.isError) ? (
          <p role="status">Loading the scorings…</p>
        ) : (
          <div className="empty">
            <p><b>No finished scoring is listed on this page yet.</b></p>
            <p>Score a file or turn on the weekly schedule; the newest finished list appears here. This page lists the files you scored in this browser tab and the weeks the schedule scored.</p>
          </div>
        )}
        {models.length ? (
          <Card title="Predictions run" aside={<span className="muted">this browser tab and the weekly schedule</span>}>
            <DataTable caption="Predictions run in this browser tab and by the weekly schedule" columns={columns} rows={history} rowKey={(r) => r.id} emptyMessage="You have not scored a file in this browser tab yet." />
            <p className="muted">Shown: files you scored in this browser tab with the models listed above, and the last weeks the schedule started scoring. Earlier scorings, and those started by a connected tool, by someone else or in another tab, cannot be listed yet.</p>
          </Card>
        ) : null}
      </TabPanel>
      <TabPanel idPrefix="pred" id="score" active={tab === "score"}>
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
      </TabPanel>
      <TabPanel idPrefix="pred" id="schedule" active={tab === "schedule"}>
        <WeeklyScoring projectId={id} />
      </TabPanel>
    </>
  );
}

export default function PredictionsPage() {
  return <Suspense fallback={<p role="status">Loading…</p>}><PredictionsPageInner /></Suspense>;
}
