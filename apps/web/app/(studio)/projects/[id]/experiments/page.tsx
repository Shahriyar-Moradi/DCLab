"use client";

import Link from "next/link";
import { useParams, useRouter, useSearchParams } from "next/navigation";
import { Suspense, useState } from "react";
import { DataTable, type Column } from "@/components/studio/DataTable";
import { PageGuide } from "@/components/studio/PageGuide";
import { PageHead } from "@/components/studio/PageHead";
import { Pill } from "@/components/studio/Pill";
import { Term } from "@/components/studio/Term";
import { QueryNotice, STATUS_TONE, formatWhen } from "@/app/components/studio-app/StudioParts";
import { useProjectExperiments, type StudioExperimentItem } from "@/lib/application";
import { COMPARE_MAX, COMPARE_MIN, compareHref } from "@/lib/application/studio-compare";
import { isUuid } from "@/lib/application/command-search";

const short = (id: string | null | undefined) => (id ? id.slice(0, 8) : "—");

function ExperimentsPageInner() {
  const { id } = useParams<{ id: string }>();
  const router = useRouter();
  const preselect = useSearchParams().get("select");
  const [selected, setSelected] = useState<string[]>(() => (isUuid(preselect) ? [preselect.toLowerCase()] : []));
  const experiments = useProjectExperiments(id);
  const toggle = (experimentId: string) => setSelected((all) => (all.includes(experimentId) ? all.filter((x) => x !== experimentId) : all.length < COMPARE_MAX ? [...all, experimentId] : all));
  const href = compareHref(id, selected);
  const columns: Column<StudioExperimentItem>[] = [
    {
      key: "pick", header: "Compare",
      render: (e) => (
        <input type="checkbox" checked={selected.includes(e.id)} onChange={() => toggle(e.id)}
          aria-label={`Select ${e.intent || `experiment ${short(e.id)}`} to compare`} disabled={e.status !== "completed"} />
      ),
    },
    {
      key: "experiment", header: "Experiment", sortValue: (e) => e.intent ?? e.id,
      render: (e) => <Link href={`/projects/${id}/experiments/${e.id}`}>{e.intent || `Experiment ${short(e.id)}`}</Link>,
    },
    { key: "status", header: "Status", sortValue: (e) => e.status, render: (e) => <Pill tone={STATUS_TONE[e.status] ?? "gray"}>{e.status.replaceAll("_", " ")}</Pill> },
    { key: "parent", header: "Parent", render: (e) => <span className="mono">{e.parent_experiment_id ? short(e.parent_experiment_id) : "root"}</span> },
    { key: "change", header: "Change set", render: (e) => (e.has_change_set ? <Pill tone="det">typed change</Pill> : "—") },
    { key: "split", header: "Split plan", render: (e) => <span className="mono">{short(e.split_plan_id)}</span> },
    { key: "created", header: "Started", sortValue: (e) => e.created_at, render: (e) => formatWhen(e.created_at) },
    { key: "ended", header: "Ended", render: (e) => formatWhen(e.ended_at) },
  ];
  const items = experiments.data?.items ?? [];
  const live = items.filter((e) => e.status === "running" || e.status === "queued").length;
  return (
    <>
      <PageHead
        title="Experiments"
        actions={(
          <>
            <button type="button" className="btn" disabled={selected.length < COMPARE_MIN || !href} onClick={() => href && router.push(href)}>Compare selected ({selected.length})</button>
            <Link className="btn primary" href={`/projects/${id}/experiments/new`}>New run</Link>
          </>
        )}
        subtitle="Every run is an experiment. Branches share the parent's split plan, so they are comparable. Selection follows a fixed rule on cross-validation; the final holdout is scored once per experiment."
      />
      <PageGuide
        purpose="See every run of this project and how runs relate to each other."
        howTo={<>Open an experiment to follow its stages live. A <Term definition="A typed list of changes (for example a different feature recipe) applied on top of a parent experiment.">change set</Term> marks a branch; the <Term definition="The fixed assignment of rows to folds and the final holdout. Experiments with the same split plan can be compared.">split plan</Term> says which experiments are comparable.</>}
        youGet="Status, lineage and timing of each experiment. Tick two or more completed runs and press Compare selected; open a run to branch it, cancel it or make it the champion. Start a new run with New run."
      />
      {experiments.isError ? <QueryNotice error={experiments.error} what="experiment list" /> : null}
      {experiments.isPending ? <p role="status">Loading experiments…</p> : null}
      {experiments.data ? (
        <>
          <p className="muted" role="status">{items.length} experiments{live ? ` · ${live} in progress` : ""}{experiments.data.next_cursor ? " · showing the newest 100" : ""}</p>
          <DataTable caption="Experiments" columns={columns} rows={items} rowKey={(e) => e.id} emptyMessage="No experiments yet. Start the first run with New run." />
        </>
      ) : null}
    </>
  );
}

export default function ExperimentsPage() {
  return <Suspense fallback={<p role="status">Loading experiments…</p>}><ExperimentsPageInner /></Suspense>;
}
