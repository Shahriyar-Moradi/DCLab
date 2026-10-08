"use client";

import Link from "next/link";
import { useParams, useRouter, useSearchParams } from "next/navigation";
import { Suspense, useState } from "react";
import { DataTable, type Column } from "@/components/studio/DataTable";
import { PageGuide } from "@/components/studio/PageGuide";
import { PageHead } from "@/components/studio/PageHead";
import { Pill } from "@/components/studio/Pill";
import { GlossaryTerm } from "@/components/studio/GlossaryTerm";
import { Term } from "@/components/studio/Term";
import { QueryNotice, STATUS_TONE, formatWhen } from "@/app/components/studio-app/StudioParts";
import { useProjectExperiments, type StudioExperimentItem } from "@/lib/application";
import { COMPARE_MAX, COMPARE_MIN, compareHref } from "@/lib/application/studio-compare";
import { isUuid } from "@/lib/application/command-search";
import { FINDINGS_BADGE_CAP, useFindingsBadges } from "@/lib/application/studio-data-hooks";
import { attentionCount, findingsState } from "@/lib/application/studio-findings";
import { designLabels, runName, runOrdinals } from "@/lib/application/studio-names";

function ExperimentsPageInner() {
  const { id } = useParams<{ id: string }>();
  const router = useRouter();
  const preselect = useSearchParams().get("select");
  const [selected, setSelected] = useState<string[]>(() => (isUuid(preselect) ? [preselect.toLowerCase()] : []));
  const experiments = useProjectExperiments(id);
  const allRuns = experiments.data?.items ?? [];
  const ordinals = runOrdinals(allRuns);
  const partial = !!experiments.data?.next_cursor;
  const nameOf = (e: StudioExperimentItem) => runName(ordinals.get(e.id), e.intent, partial ? e.id.slice(0, 8) : undefined);
  const designs = designLabels([...allRuns].sort((a, b) => a.created_at.localeCompare(b.created_at)).map((e) => e.split_plan_id));
  const completedIds = (experiments.data?.items ?? []).filter((e) => e.status === "completed").map((e) => e.id);
  const badges = useFindingsBadges(completedIds);
  const toggle = (experimentId: string) => setSelected((all) => (all.includes(experimentId) ? all.filter((x) => x !== experimentId) : all.length < COMPARE_MAX ? [...all, experimentId] : all));
  const href = compareHref(id, selected);
  const columns: Column<StudioExperimentItem>[] = [
    {
      key: "pick", header: "Compare",
      render: (e) => (
        <input type="checkbox" checked={selected.includes(e.id)} onChange={() => toggle(e.id)}
          aria-label={`Select ${nameOf(e)} to compare`} disabled={e.status !== "completed"} />
      ),
    },
    {
      key: "experiment", header: "Run", sortValue: (e) => ordinals.get(e.id) ?? 0,
      render: (e) => <Link href={`/projects/${id}/experiments/${e.id}`}>{nameOf(e)}</Link>,
    },
    { key: "status", header: "Status", sortValue: (e) => e.status, render: (e) => <Pill tone={STATUS_TONE[e.status] ?? "gray"}>{e.status.replaceAll("_", " ")}</Pill> },
    { key: "parent", header: "Based on", render: (e) => (e.parent_experiment_id ? (ordinals.has(e.parent_experiment_id) && !partial ? `Run ${ordinals.get(e.parent_experiment_id)}` : `Run ${e.parent_experiment_id.slice(0, 8)}`) : "Started from scratch") },
    {
      key: "findings", header: "Trust checks",
      render: (e) => {
        if (e.status !== "completed") return "—";
        const result = badges.get(e.id);
        if (!result) return <span className="muted" title={`Findings are loaded for the first ${FINDINGS_BADGE_CAP} completed runs; open the run to see them.`}>open run</span>;
        if (result.isPending) return <span className="muted">…</span>;
        if (result.isError) return <span className="muted">unavailable</span>;
        const state = findingsState(result.data);
        const n = attentionCount(result.data);
        if (state === "not_computed") return <span className="muted">not computed</span>;
        return n ? <Link href={`/projects/${id}/experiments/${e.id}#findings`}><Pill tone="warn">{n} to review</Pill></Link> : <Pill tone="ok">all passed</Pill>;
      },
    },
    { key: "change", header: "What changed", render: (e) => (e.has_change_set ? <Pill tone="det">changed from the run it is based on</Pill> : "—") },
    { key: "split", header: "Test design", render: (e) => (e.split_plan_id ? designs.get(e.split_plan_id) ?? "—" : "—") },
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
        subtitle="Every run of this project. A run based on another keeps its test design, so the two can be compared fairly. The best run is chosen on cross-validation only; the final test set (used once) is scored one time per run."
      />
      <PageGuide
        purpose="See every run of this project and which run is based on which."
        howTo={<>Open a run to follow its steps live. &ldquo;What changed&rdquo; marks a run that tries one change on top of another. Runs with the same <Term definition="How the rows are split into training folds and a final test set. Runs with the same test design are scored on the same rows, so they can be compared.">test design</Term> are comparable. Runs are scored on <GlossaryTerm term="cv" /> only.</>}
        youGet="Status, what each run is based on, trust checks and timing. Tick two or more completed runs and press Compare selected; open a run to try a change, cancel it or put its model in use. Start a new run with New run."
      />
      {experiments.isError ? <QueryNotice error={experiments.error} what="experiment list" /> : null}
      {experiments.isPending ? <p role="status">Loading runs…</p> : null}
      {experiments.data ? (
        <>
          <p className="muted" role="status">{items.length} runs{live ? ` · ${live} in progress` : ""}{experiments.data.next_cursor ? " · showing the newest 100" : ""}</p>
          <DataTable caption="Runs" columns={columns} rows={items} rowKey={(e) => e.id} emptyMessage="No runs yet. Start the first run with New run." />
        </>
      ) : null}
    </>
  );
}

export default function ExperimentsPage() {
  return <Suspense fallback={<p role="status">Loading runs…</p>}><ExperimentsPageInner /></Suspense>;
}
