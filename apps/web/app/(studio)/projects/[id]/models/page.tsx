"use client";

import Link from "next/link";
import { useParams } from "next/navigation";
import { Banner } from "@/components/studio/Banner";
import { Card } from "@/components/studio/Card";
import { DataTable, type Column } from "@/components/studio/DataTable";
import { PageGuide } from "@/components/studio/PageGuide";
import { PageHead } from "@/components/studio/PageHead";
import { Pill } from "@/components/studio/Pill";
import { GlossaryTerm } from "@/components/studio/GlossaryTerm";
import { Term } from "@/components/studio/Term";
import { QueryNotice, formatWhen } from "@/app/components/studio-app/StudioParts";
import { modelRows, type ModelRow } from "@/lib/application/studio-card";
import { useProjectGraph } from "@/lib/application/studio-data-hooks";
import { modelName, runOrdinals } from "@/lib/application/studio-names";
import { runRef } from "@/lib/application/studio-model";
import { useProjectExperiments } from "@/lib/application";
import { projectHref } from "@/lib/application/command-search";

export default function ModelsPage() {
  const { id } = useParams<{ id: string }>();
  const graph = useProjectGraph(id);
  const runs = useProjectExperiments(id);
  const rows = graph.data ? modelRows(id, graph.data.nodes, graph.data.edges) : [];
  const ordinals = runOrdinals(runs.data?.items ?? []);
  const partial = !!runs.data?.next_cursor || !runs.data;
  const columns: Column<ModelRow>[] = [
    { key: "version", header: "Model", sortValue: (m) => m.version, render: (m) => (m.href ? <Link href={m.href}>{modelName(m.version)}</Link> : modelName(m.version)) },
    {
      key: "run", header: "Built by", sortValue: partial ? undefined : (m) => (m.experimentId ? (ordinals.get(m.experimentId) ?? 0) : 0),
      render: (m) => {
        const label = runRef(ordinals, partial, m.experimentId);
        const href = m.experimentId ? projectHref(id, "experiments", m.experimentId) : null;
        return label ? (href ? <Link href={href}>{label}</Link> : label) : "—";
      },
    },
    { key: "champion", header: "Status", render: (m) => (m.champion ? <Pill tone="ok">In use</Pill> : <span className="muted">Not in use</span>) },
    { key: "created", header: "Created", sortValue: (m) => m.created ?? "", render: (m) => formatWhen(m.created) },
    { key: "card", header: "Card", render: (m) => (m.href ? <Link href={`${m.href}?tab=card`} aria-label={`Open the card of ${modelName(m.version)}`}>Open card</Link> : "—") },
  ];
  return (
    <>
      <PageHead title="Model" subtitle="Every model of this project, the one in use first, with the run that built it and the card that explains it." actions={<Link className="btn" href={`/projects/${id}/graph`}>See how models were built</Link>} />
      <PageGuide
        purpose="Find a model and open its card."
        howTo="Open a model to see its summary, choose its threshold, read and print its card, or score a new file with it."
        youGet="The model version, the run that built it, which one is in use, when it was made and a link to its card."
        attention={<>Check the model number and the run before you score a file: any model can score a file from its own page, not only the one <Term definition="The model the project uses now. Other models are earlier or alternative results.">in use</Term>. The card shows how a model compares with the <GlossaryTerm term="baseline" />.</>}
      />
      <Card title="Models" aside={<Pill tone="gray">{rows.length}</Pill>}>
        {graph.isError ? <QueryNotice error={graph.error} what="project graph" /> : graph.isPending ? <p role="status">Loading models…</p> : (
          <DataTable columns={columns} rows={rows} rowKey={(m) => m.id} caption="Models" emptyMessage="No model yet. Train a run and its best model appears here." />
        )}
        {graph.data?.truncated ? <Banner tone="info">Only models from the newest runs are listed. <Link href={`/projects/${id}/graph`}>See the full project diagram</Link> for older ones.</Banner> : null}
      </Card>
    </>
  );
}
