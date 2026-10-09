"use client";

import Link from "next/link";
import { useParams, useSearchParams } from "next/navigation";
import { Suspense } from "react";
import { useQueries } from "@tanstack/react-query";
import { ChampionPanel } from "@/app/components/studio-app/CompareBranch";
import { QueryNotice, formatWhen } from "@/app/components/studio-app/StudioParts";
import { Banner } from "@/components/studio/Banner";
import { Card } from "@/components/studio/Card";
import { DataTable, type Column } from "@/components/studio/DataTable";
import { KeyValue } from "@/components/studio/KeyValue";
import { PageGuide } from "@/components/studio/PageGuide";
import { PageHead } from "@/components/studio/PageHead";
import { Pill } from "@/components/studio/Pill";
import { Term } from "@/components/studio/Term";
import { plainText, projectHref } from "@/lib/application/command-search";
import { useCompare } from "@/lib/application/studio-compare-hooks";
import { changeSetDiff, compareRefusal, deltaWording, durationText, formatDelta, metricRows, parseCompareIds, type MetricRow } from "@/lib/application/studio-compare";
import { useProjectExperiments } from "@/lib/application";
import { metricInfo, ruleStatusText } from "@/lib/application/studio-goal";
import { designLabels, runName, runOrdinals } from "@/lib/application/studio-names";
import { changeSentences, familyLabel, selectionScore } from "@/lib/application/studio-runs";
import { formatNumber } from "@/lib/application/studio-inspect";
import { ExperimentDetailSchema } from "@/lib/application/studio-inspect-hooks";
import { workspaceQueryKey } from "@/lib/infrastructure/active-workspace";
import { v1Get } from "@/lib/infrastructure/v1/client";

const short = (id: string) => id.slice(0, 8);

function useDetails(ids: string[]) {
  return useQueries({
    queries: ids.map((id) => ({
      queryKey: workspaceQueryKey("v1", "experiment-detail", id),
      queryFn: ({ signal }: { signal: AbortSignal }) => v1Get("/v1/experiments/{experiment_id}", ExperimentDetailSchema, { params: { experiment_id: id }, signal }),
      retry: false,
    })),
  });
}

function CompareBody({ projectId, ids }: { projectId: string; ids: string[] }) {
  const compare = useCompare(ids);
  const details = useDetails(ids);
  const siblings = useProjectExperiments(projectId);
  const runs = siblings.data?.items ?? [];
  const partial = !!siblings.data?.next_cursor;
  const ordinals = runOrdinals(runs);
  const intentOf = (experimentId: string) => runs.find((r) => r.id === experimentId)?.intent;
  const runLabel = (experimentId: string) => runName(partial ? undefined : ordinals.get(experimentId), intentOf(experimentId) ? plainText(intentOf(experimentId), 60) : null, partial || !ordinals.has(experimentId) ? experimentId.slice(0, 8) : undefined);
  const designName = (planId: string) => designLabels([...runs].sort((a, b) => a.created_at.localeCompare(b.created_at)).map((r) => r.split_plan_id)).get(planId) ?? "one shared test design";
  const back = projectHref(projectId, "experiments");
  if (compare.isPending) return <p role="status">Comparing…</p>;
  if (compare.isError) {
    const reason = compareRefusal(compare.error);
    return reason ? (
      <Banner tone="warn" actions={back ? <Link className="btn" href={back}>Back to experiments</Link> : null}><b>These runs cannot be compared.</b> {reason}</Banner>
    ) : <QueryNotice error={compare.error} what="comparison" />;
  }
  const data = compare.data;
  const items = [...data.experiments].sort((a, b) => ids.indexOf(a.experiment_id) - ids.indexOf(b.experiment_id));
  const rows = metricRows(items.map((i) => ({ ...i, cv: i.cv })), data.common.cv);
  const label = (i: number) => runLabel(items[i].experiment_id);
  const tag = (i: number) => runName(partial ? undefined : ordinals.get(items[i].experiment_id), null, partial || !ordinals.has(items[i].experiment_id) ? short(items[i].experiment_id) : undefined);
  const columns: Column<MetricRow>[] = [
    { key: "metric", header: "Score (cross-validation)", render: (r) => plainText(metricInfo(r.metric)?.label ?? r.metric, 60) },
    ...items.map((item, i) => ({ key: item.experiment_id, header: tag(i), numeric: true, render: (r: MetricRow) => (r.values[i] === null ? "—" : formatNumber(r.values[i] as number)) })),
    { key: "delta", header: `Difference (${tag(items.length - 1)} minus ${tag(0)})`, numeric: true, render: (r) => <>{formatDelta(r.delta)} <span className="muted">{deltaWording(r.metric, r.delta)}</span></> },
  ];
  const lines = details.map((d) => changeSentences(d.data?.change_set));
  const diff = items.length === 2 ? changeSetDiff(lines[0] ?? [], lines[1] ?? []) : null;
  return (
    <>
      <Banner tone="info">
        <b>Comparable.</b> All {items.length} runs use {designName(data.split_plan_id)}, so they were scored on the same folds and the same final test set rows.
      </Banner>
      <h2>Scores</h2>
      <DataTable caption="Cross-validation metric comparison" columns={columns} rows={rows} rowKey={(r) => r.metric}
        emptyMessage="These runs share no cross-validation metric." />
      <p className="muted">&ldquo;Better&rdquo; and &ldquo;worse&rdquo; only describe the direction of the score. A model is chosen on cross-validation by a fixed rule; the final test set is not shown here.</p>
      <h2>Runs</h2>
      <div className="grid">
        {items.map((item, i) => {
          const detail = details[i]?.data;
          const href = projectHref(projectId, "experiments", item.experiment_id);
          return (
            <Card key={item.experiment_id} title={<>{label(i)} <span className="mono">{short(item.experiment_id)}</span></>} aside={href ? <Link href={href}>Open</Link> : null}>
              <KeyValue items={[
                { key: "intent", label: "Why this run", value: detail?.intent ? <span>{plainText(detail.intent, 200)} <span className="muted">(written by a person or agent)</span></span> : "—" },
                { key: "family", label: "Model", value: familyLabel(item.family) ?? "—" },
                { key: "sel", label: "Ranked on", value: <>{item.selection_metric ? plainText(metricInfo(item.selection_metric)?.label ?? item.selection_metric, 40) : "—"}{selectionScore(item.selection_metric, item.cv, item.selected_score) !== null ? <> = {formatNumber(selectionScore(item.selection_metric, item.cv, item.selected_score))} <span className="muted">(cross-validation)</span></> : null}</> },
                { key: "thr", label: <Term definition="The probability above which a row is predicted positive. Chosen on cross-validation.">Decision threshold</Term>, value: item.decision_threshold != null ? formatNumber(item.decision_threshold) : "—" },
                { key: "constraint", label: "Business rule", value: ruleStatusText(item.constraint_status) },
                { key: "time", label: "Time", value: <>{durationText(detail?.started_at, detail?.ended_at)} <span className="muted">(started {formatWhen(detail?.started_at)})</span></> },
                { key: "parent", label: "Based on", value: item.parent_experiment_id ? runLabel(item.parent_experiment_id) : "Started from scratch" },
                { key: "split", label: "Test design", value: designName(data.split_plan_id) },
              ]} />
            </Card>
          );
        })}
      </div>
      <h2><Term definition="What was changed on top of the run each one is based on.">What changed</Term></h2>
      {details.some((d) => d.isError) ? <p className="muted">Some run details could not be read, so their changes are not listed.</p> : null}
      {diff ? (
        <Card flat>
          <KeyValue items={[
            { key: "a", label: `Only in ${label(0)}`, value: diff.onlyLeft.length ? <ul className="plain-list">{diff.onlyLeft.map((l) => <li key={l}>{plainText(l, 300)}</li>)}</ul> : "—" },
            { key: "b", label: `Only in ${label(1)}`, value: diff.onlyRight.length ? <ul className="plain-list">{diff.onlyRight.map((l) => <li key={l}>{plainText(l, 300)}</li>)}</ul> : "—" },
            { key: "s", label: "In both", value: diff.shared.length ? <ul className="plain-list">{diff.shared.map((l) => <li key={l}>{plainText(l, 300)}</li>)}</ul> : "—" },
          ]} />
        </Card>
      ) : (
        <KeyValue items={items.map((item, i) => ({ key: item.experiment_id, label: label(i), value: lines[i]?.length ? <ul className="plain-list">{lines[i].map((l) => <li key={l}>{plainText(l, 300)}</li>)}</ul> : "No changes: started from scratch" }))} />
      )}
      <h2>Put a model in use</h2>
      <p className="muted">Putting a run&apos;s model in use switches the model the project uses and is saved in History. You can change your mind later; the earlier entry stays.</p>
      <div className="grid">
        {items.map((item, i) => (
          <Card key={item.experiment_id} title={`Use the model of ${label(i)}`}>
            <ChampionPanel projectId={projectId} experimentId={item.experiment_id} compact />
          </Card>
        ))}
      </div>
    </>
  );
}

function ComparePageInner() {
  const { id } = useParams<{ id: string }>();
  const ids = parseCompareIds(useSearchParams().get("ids"));
  const back = projectHref(id, "experiments");
  return (
    <>
      <PageHead title="Compare runs" subtitle="Side-by-side cross-validation scores, what changed, time and test design of two or more runs."
        actions={back ? <Link className="btn" href={back}>All runs</Link> : null} />
      <PageGuide
        purpose="Decide which run to keep."
        howTo={<>Runs are comparable only on the same <Term definition="How the rows are split into folds and a final test set. Runs on one test design are scored on the same rows.">test design</Term>. Read the score table, check what each change did, then put the run you want in use.</>}
        youGet="Score differences on cross-validation, what changed, time and the shared test design."
        attention="If the runs are not comparable, the reason is shown and nothing is compared."
      />
      {ids ? <CompareBody projectId={id} ids={ids} /> : (
        <Banner tone="warn" actions={back ? <Link className="btn" href={back}>Choose runs</Link> : null}>Choose two to ten runs on the Experiments page, then press Compare selected. The link needs 2 to 10 different runs.</Banner>
      )}
      <Pill tone="gray">Final test set values are not shown on this page</Pill>
    </>
  );
}

export default function ComparePage() {
  return <Suspense fallback={<p role="status">Loading…</p>}><ComparePageInner /></Suspense>;
}
