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
import { changeSetDiff, compareRefusal, deltaWording, describeChanges, durationText, formatDelta, metricRows, parseCompareIds, type MetricRow } from "@/lib/application/studio-compare";
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
  const label = (i: number) => `Run ${String.fromCharCode(65 + i)}`;
  const columns: Column<MetricRow>[] = [
    { key: "metric", header: "Metric (cross-validation)", render: (r) => <span className="mono">{plainText(r.metric, 60)}</span> },
    ...items.map((item, i) => ({ key: item.experiment_id, header: `${label(i)} (${short(item.experiment_id)})`, numeric: true, render: (r: MetricRow) => (r.values[i] === null ? "—" : formatNumber(r.values[i] as number)) })),
    { key: "delta", header: `Change (${label(items.length - 1)} minus ${label(0)})`, numeric: true, render: (r) => <>{formatDelta(r.delta)} <span className="muted">{deltaWording(r.metric, r.delta)}</span></> },
  ];
  const lines = details.map((d) => describeChanges(d.data?.change_set));
  const diff = items.length === 2 ? changeSetDiff(lines[0] ?? [], lines[1] ?? []) : null;
  return (
    <>
      <Banner tone="info">
        <b>Comparable.</b> All {items.length} runs use split plan <span className="mono">{short(data.split_plan_id)}</span>, so they were scored on the same folds and the same holdout rows.
      </Banner>
      <h2>Scores</h2>
      <DataTable caption="Cross-validation metric comparison" columns={columns} rows={rows} rowKey={(r) => r.metric}
        emptyMessage="These runs share no cross-validation metric." />
      <p className="muted">Wording of a change (better, worse) is only a reading aid for the direction of the metric. A model is chosen on cross-validation by the fixed selection rule; the final holdout is not shown here.</p>
      <h2>Runs</h2>
      <div className="grid">
        {items.map((item, i) => {
          const detail = details[i]?.data;
          const href = projectHref(projectId, "experiments", item.experiment_id);
          return (
            <Card key={item.experiment_id} title={<>{label(i)} <span className="mono">{short(item.experiment_id)}</span></>} aside={href ? <Link href={href}>Open</Link> : null}>
              <KeyValue items={[
                { key: "intent", label: "Intent", value: detail?.intent ? <span>{plainText(detail.intent, 200)} <span className="muted">(written by a person or agent)</span></span> : "—" },
                { key: "family", label: "Winning family", value: item.family ? plainText(item.family, 60) : "—" },
                { key: "sel", label: "Selection metric", value: <>{item.selection_metric ? <span className="mono">{plainText(item.selection_metric, 40)}</span> : "—"}{item.selected_score != null ? <> = {formatNumber(item.selected_score)} <span className="muted">(CV)</span></> : null}</> },
                { key: "thr", label: <Term definition="The probability above which a row is predicted positive. Chosen on cross-validation.">Decision threshold</Term>, value: item.decision_threshold != null ? formatNumber(item.decision_threshold) : "—" },
                { key: "constraint", label: "Constraint status", value: item.constraint_status ?? "—" },
                { key: "time", label: "Time", value: <>{durationText(detail?.started_at, detail?.ended_at)} <span className="muted">(started {formatWhen(detail?.started_at)})</span></> },
                { key: "cost", label: "Cost", value: <span className="muted">Not recorded: the API has no cost field for a run yet.</span> },
                { key: "parent", label: "Parent", value: item.parent_experiment_id ? <span className="mono">{short(item.parent_experiment_id)}</span> : "root run" },
                { key: "split", label: "Split plan", value: <span className="mono">{short(data.split_plan_id)}</span> },
              ]} />
            </Card>
          );
        })}
      </div>
      <h2><Term definition="A typed list of changes applied on top of a parent experiment.">Change sets</Term></h2>
      {details.some((d) => d.isError) ? <p className="muted">Some run details could not be read.</p> : null}
      {diff ? (
        <Card flat>
          <KeyValue items={[
            { key: "a", label: `Only in ${label(0)}`, value: diff.onlyLeft.length ? <ul className="plain-list">{diff.onlyLeft.map((l) => <li key={l}>{plainText(l, 300)}</li>)}</ul> : "—" },
            { key: "b", label: `Only in ${label(1)}`, value: diff.onlyRight.length ? <ul className="plain-list">{diff.onlyRight.map((l) => <li key={l}>{plainText(l, 300)}</li>)}</ul> : "—" },
            { key: "s", label: "In both", value: diff.shared.length ? <ul className="plain-list">{diff.shared.map((l) => <li key={l}>{plainText(l, 300)}</li>)}</ul> : "—" },
          ]} />
        </Card>
      ) : (
        <KeyValue items={items.map((item, i) => ({ key: item.experiment_id, label: label(i), value: lines[i]?.length ? <ul className="plain-list">{lines[i].map((l) => <li key={l}>{plainText(l, 300)}</li>)}</ul> : "No change set (a root run)" }))} />
      )}
      <h2>Decide</h2>
      <p className="muted">Accepting a run makes its model version the project&apos;s champion: one recorded decision. You can change your mind later; the earlier decision stays in the record.</p>
      <div className="grid">
        {items.map((item, i) => (
          <Card key={item.experiment_id} title={`Accept ${label(i)}`}>
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
      <PageHead title="Compare experiments" subtitle="Side-by-side cross-validation scores, change sets, time and split plan of two or more runs."
        actions={back ? <Link className="btn" href={back}>All experiments</Link> : null} />
      <PageGuide
        purpose="Decide which run to keep."
        howTo={<>Runs are comparable only on the same <Term definition="The fixed assignment of rows to folds and the final holdout. Runs on one split plan are scored on the same rows.">split plan</Term>. Read the metric table, check what each change set did, then accept the run you want as champion.</>}
        youGet="Metric deltas on cross-validation, the change-set difference, time and the split-plan identity."
        attention="If the runs are not comparable, the reason is shown and nothing is compared."
      />
      {ids ? <CompareBody projectId={id} ids={ids} /> : (
        <Banner tone="warn" actions={back ? <Link className="btn" href={back}>Choose runs</Link> : null}>Choose two to ten runs on the experiments page, then press Compare. The link needs 2 to 10 distinct experiment ids.</Banner>
      )}
      <Pill tone="gray">Final holdout values are not shown on this page</Pill>
    </>
  );
}

export default function ComparePage() {
  return <Suspense fallback={<p role="status">Loading…</p>}><ComparePageInner /></Suspense>;
}
