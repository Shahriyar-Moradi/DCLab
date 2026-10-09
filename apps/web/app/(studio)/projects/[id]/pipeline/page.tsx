"use client";

import Link from "next/link";
import { useParams } from "next/navigation";
import { DataTable, type Column } from "@/components/studio/DataTable";
import { PageGuide } from "@/components/studio/PageGuide";
import { PageHead } from "@/components/studio/PageHead";
import { Pill } from "@/components/studio/Pill";
import { Term } from "@/components/studio/Term";
import { QueryNotice, formatWhen, statusTone } from "@/app/components/studio-app/StudioParts";
import { useProjectExperiments, type StudioExperimentItem } from "@/lib/application";
import { plainText } from "@/lib/application/command-search";
import { runName, runOrdinals } from "@/lib/application/studio-names";
import { pipelineHref } from "@/lib/application/studio-pipeline";
import { statusWords } from "@/lib/application/studio-runs";

/** Pick a run: the Pipeline page shows one run's stage-by-stage evidence. */
export default function PipelineListPage() {
  const { id } = useParams<{ id: string }>();
  const experiments = useProjectExperiments(id);
  const items = experiments.data?.items ?? [];
  const ordinals = runOrdinals(items);
  const partial = !!experiments.data?.next_cursor;
  const nameOf = (e: StudioExperimentItem) => runName(partial ? undefined : ordinals.get(e.id), e.intent ? plainText(e.intent, 80) : null, partial ? e.id.slice(0, 8) : undefined);
  const columns: Column<StudioExperimentItem>[] = [
    { key: "run", header: "Run", sortValue: (e) => ordinals.get(e.id) ?? 0, render: (e) => { const href = pipelineHref(id, e.id); const name = nameOf(e); return href ? <Link href={href}>{name}</Link> : name; } },
    { key: "status", header: "Status", sortValue: (e) => e.status, render: (e) => <Pill tone={statusTone(e.status)}>{statusWords(e.status)}</Pill> },
    { key: "id", header: "Short id", render: (e) => <span className="mono">{e.id.slice(0, 8)}</span> },
    { key: "started", header: "Started", sortValue: (e) => e.created_at, render: (e) => formatWhen(e.created_at) },
  ];
  return (
    <>
      <PageHead title="Run evidence" subtitle="Step-by-step evidence for one run." />
      <PageGuide
        purpose={<>Open the evidence of a run: every <Term definition="One step the run takes, in a fixed order, such as profiling the data or setting the final test set aside.">step</Term>, its result, checksums, trust checks and History entries.</>}
        howTo="Pick a run below."
        youGet="The steps of that run in order, the choices made, any AI runs with replay, saved files and how to reproduce the run."
      />
      {experiments.isError ? <QueryNotice error={experiments.error} what="runs" /> : null}
      {experiments.isPending ? <p role="status">Loading runs…</p> : null}
      {experiments.data ? <DataTable caption="Runs of this project" columns={columns} rows={items} rowKey={(e) => e.id} emptyMessage="This project has no runs yet. Start one from Experiments." /> : null}
    </>
  );
}
