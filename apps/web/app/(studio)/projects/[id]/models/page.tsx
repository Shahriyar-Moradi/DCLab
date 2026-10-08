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
import { modelName } from "@/lib/application/studio-names";

export default function ModelsPage() {
  const { id } = useParams<{ id: string }>();
  const graph = useProjectGraph(id);
  const rows = graph.data ? modelRows(id, graph.data.nodes) : [];
  const columns: Column<ModelRow>[] = [
    { key: "version", header: "Model", sortValue: (m) => m.version, render: (m) => (m.href ? <Link href={m.href}>{modelName(m.version)}</Link> : modelName(m.version)) },
    { key: "champion", header: "In use", render: (m) => (m.champion ? <Pill tone="ok">In use</Pill> : <span className="muted">no</span>) },
    { key: "created", header: "Created", sortValue: (m) => m.created ?? "", render: (m) => formatWhen(m.created) },
    { key: "card", header: "Card", render: (m) => (m.href ? <Link href={`${m.href}?tab=card`} aria-label={`Open the card of ${modelName(m.version)}`}>Open card</Link> : "—") },
  ];
  return (
    <>
      <PageHead title="Model" subtitle="Every model of this project, the one in use first, with the card that explains each one." actions={<Link className="btn" href={`/projects/${id}/graph`}>See how models were built</Link>} />
      <PageGuide
        purpose="Find a model and open its card."
        howTo="Open a model to see its details, score a new file with it, or read and print its card."
        youGet="Model version, which one is in use, when it was made and a link to its card."
        attention={<>Predictions use the model <Term definition="The model the project uses now. Other versions are earlier or alternative results.">in use</Term> by default; any model can still score a file from its own page. The card shows how it compares with the <GlossaryTerm term="baseline" />.</>}
      />
      <Card title="Models" aside={<Pill tone="gray">{rows.length}</Pill>}>
        {graph.isError ? <QueryNotice error={graph.error} what="project graph" /> : graph.isPending ? <p role="status">Loading models…</p> : (
          <DataTable columns={columns} rows={rows} rowKey={(m) => m.id} caption="Models" emptyMessage="No model yet. Train a run and its best model appears here." />
        )}
        {graph.data?.truncated ? <Banner tone="info">Only models from the newest runs are listed. <Link href={`/projects/${id}/graph`}>See the full lineage</Link> for older ones.</Banner> : null}
      </Card>
    </>
  );
}
