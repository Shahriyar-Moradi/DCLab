"use client";

import Link from "next/link";
import { useParams } from "next/navigation";
import { Banner } from "@/components/studio/Banner";
import { Card } from "@/components/studio/Card";
import { DataTable, type Column } from "@/components/studio/DataTable";
import { PageGuide } from "@/components/studio/PageGuide";
import { PageHead } from "@/components/studio/PageHead";
import { Pill } from "@/components/studio/Pill";
import { Term } from "@/components/studio/Term";
import { QueryNotice, formatWhen } from "@/app/components/studio-app/StudioParts";
import { plainText } from "@/lib/application/command-search";
import { modelRows, type ModelRow } from "@/lib/application/studio-card";
import { useProjectGraph } from "@/lib/application/studio-data-hooks";

export default function ModelsPage() {
  const { id } = useParams<{ id: string }>();
  const graph = useProjectGraph(id);
  const rows = graph.data ? modelRows(id, graph.data.nodes) : [];
  const columns: Column<ModelRow>[] = [
    { key: "version", header: "Model version", sortValue: (m) => m.version, render: (m) => (m.href ? <Link href={m.href}>{m.version || m.label}</Link> : plainText(m.version || m.label, 80)) },
    { key: "champion", header: "Champion", render: (m) => (m.champion ? <Pill tone="ok">★ champion</Pill> : <span className="muted">no</span>) },
    { key: "created", header: "Created", sortValue: (m) => m.created ?? "", render: (m) => formatWhen(m.created) },
    { key: "digest", header: "Digest", render: (m) => <span className="mono">{m.digest ? m.digest.slice(0, 16) : "—"}</span> },
    { key: "card", header: "Card", render: (m) => (m.href ? <Link href={`${m.href}?tab=card`} aria-label={`Open the card of ${m.version || m.label}`}>Open card</Link> : "—") },
  ];
  return (
    <>
      <PageHead title="Models" subtitle="Every model version of this project, the champion first, with the card that explains each one." />
      <PageGuide
        purpose="Find a model version and open its card."
        howTo="Open a version to see its details, score new data with it, or read and print its card."
        youGet="Version, champion marker, creation time and a link to the card of each version."
        attention={<>Only the <Term definition="The version the project's champion ref points at: the model currently in use.">champion</Term> is in use; other versions are earlier or alternative results.</>}
      />
      <Card title="Model versions" aside={<Pill tone="gray">{rows.length}</Pill>}>
        {graph.isError ? <QueryNotice error={graph.error} what="project graph" /> : graph.isPending ? <p role="status">Loading model versions…</p> : (
          <DataTable columns={columns} rows={rows} rowKey={(m) => m.id} caption="Model versions" emptyMessage="No model version yet. Train an experiment and its winner appears here." />
        )}
        {graph.data?.truncated ? <Banner tone="info">Only model versions from the newest experiments are listed. <Link href={`/projects/${id}/graph`}>See the full graph</Link> for older ones.</Banner> : null}
      </Card>
    </>
  );
}
