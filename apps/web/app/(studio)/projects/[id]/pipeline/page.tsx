"use client";

import Link from "next/link";
import { useParams } from "next/navigation";
import { DataTable, type Column } from "@/components/studio/DataTable";
import { PageGuide } from "@/components/studio/PageGuide";
import { PageHead } from "@/components/studio/PageHead";
import { Pill } from "@/components/studio/Pill";
import { Term } from "@/components/studio/Term";
import { QueryNotice, STATUS_TONE, formatWhen } from "@/app/components/studio-app/StudioParts";
import { useProjectExperiments, type StudioExperimentItem } from "@/lib/application";
import { plainText } from "@/lib/application/command-search";
import { pipelineHref } from "@/lib/application/studio-pipeline";

/** Pick a run: the Pipeline page shows one run's stage-by-stage evidence. */
export default function PipelineListPage() {
  const { id } = useParams<{ id: string }>();
  const experiments = useProjectExperiments(id);
  const items = experiments.data?.items ?? [];
  const columns: Column<StudioExperimentItem>[] = [
    { key: "run", header: "Run", sortValue: (e) => e.intent ?? e.id, render: (e) => { const href = pipelineHref(id, e.id); const name = plainText(e.intent || `Run ${e.id.slice(0, 8)}`, 100); return href ? <Link href={href}>{name}</Link> : name; } },
    { key: "status", header: "Status", sortValue: (e) => e.status, render: (e) => <Pill tone={STATUS_TONE[e.status] ?? "gray"}>{e.status.replaceAll("_", " ")}</Pill> },
    { key: "id", header: "Id", render: (e) => <span className="mono">{e.id.slice(0, 8)}</span> },
    { key: "started", header: "Started", sortValue: (e) => e.created_at, render: (e) => formatWhen(e.created_at) },
  ];
  return (
    <>
      <PageHead title="Run evidence" subtitle="Step-by-step evidence for one run." />
      <PageGuide
        purpose={<>Open the evidence of a run: every engine <Term definition="One step the engine runs in a fixed order, such as profiling the data or locking the final test set.">stage</Term>, its result, digests, checks and decision records.</>}
        howTo="Pick a run below."
        youGet="The stages of that run in order, the decision points, agent runs with replay, artifacts and provenance."
      />
      {experiments.isError ? <QueryNotice error={experiments.error} what="runs" /> : null}
      {experiments.isPending ? <p role="status">Loading runs…</p> : null}
      {experiments.data ? <DataTable caption="Runs of this project" columns={columns} rows={items} rowKey={(e) => e.id} emptyMessage="This project has no runs yet. Start one from Experiments." /> : null}
    </>
  );
}
