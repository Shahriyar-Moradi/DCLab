"use client";

import Link from "next/link";
import { useParams, useSearchParams } from "next/navigation";
import { Suspense, useState } from "react";
import { Banner } from "@/components/studio/Banner";
import { Card } from "@/components/studio/Card";
import { DataTable, type Column } from "@/components/studio/DataTable";
import { PageGuide } from "@/components/studio/PageGuide";
import { PageHead } from "@/components/studio/PageHead";
import { Pill } from "@/components/studio/Pill";
import { GlossaryTerm } from "@/components/studio/GlossaryTerm";
import { TabPanel, Tabs } from "@/components/studio/Tabs";
import { Term } from "@/components/studio/Term";
import { ApprovedForScoring } from "@/app/components/studio-app/ApprovedForScoring";
import { QueryNotice, formatWhen } from "@/app/components/studio-app/StudioParts";
import { VERSIONS_NOTE, versionRows, type VersionRow } from "@/lib/application/studio-model-detail";
import { useProjectModelVersions } from "@/lib/application/studio-model-detail-hooks";
import { runOrdinals } from "@/lib/application/studio-names";
import { utcWhen } from "@/lib/application/studio-schedule";
import { runRef } from "@/lib/application/studio-model";
import { useProjectExperiments } from "@/lib/application";
import { projectHref } from "@/lib/application/command-search";

const TABS = [
  { id: "models", label: "Versions" },
  { id: "approved", label: "Approved for scoring" },
];

function ModelsPageInner() {
  const { id } = useParams<{ id: string }>();
  const requested = useSearchParams().get("tab");
  const [tab, setTab] = useState(() => (TABS.some((t) => t.id === requested) ? (requested as string) : "models"));
  const versions = useProjectModelVersions(id);
  const runs = useProjectExperiments(id);
  const rows = versionRows(id, versions.data?.pages.flatMap((p) => p.items) ?? [], utcWhen);
  const ordinals = runOrdinals(runs.data?.items ?? []);
  const partial = !!runs.data?.next_cursor || !runs.data;
  const showRates = rows.some((m) => m.precision !== null || m.recall !== null);
  const columns: Column<VersionRow>[] = [
    { key: "version", header: "Model", render: (m) => (m.href ? <Link href={m.href}>{m.model}</Link> : m.model) },
    {
      key: "run", header: "Built by", sortValue: partial ? undefined : (m) => ordinals.get(m.experimentId) ?? 0,
      render: (m) => {
        const label = runRef(ordinals, partial, m.experimentId);
        const href = projectHref(id, "experiments", m.experimentId);
        return label ? (href ? <Link href={href}>{label}</Link> : label) : "—";
      },
    },
    { key: "main", header: "Main score (cross-validation)", render: (m) => m.main },
    ...(showRates ? [
      { key: "precision", header: "Precision", numeric: true, render: (m: VersionRow) => m.precision ?? "—" },
      { key: "recall", header: "Recall", numeric: true, render: (m: VersionRow) => m.recall ?? "—" },
    ] : []),
    {
      key: "status", header: "Status",
      render: (m) => (
        <span className="v7-model-status">
          <Pill tone={m.status.tone}>{m.status.text}</Pill>
          {m.note ? <span className="muted">{m.note}</span> : null}
          {m.statusKey === "waiting_for_approval" ? <Link href="/inbox">Decide in the Inbox<span className="sr-only">: {m.model}</span></Link> : null}
        </span>
      ),
    },
    { key: "created", header: "Created", sortValue: (m) => m.created, render: (m) => formatWhen(m.created) },
    { key: "card", header: "Card", render: (m) => (m.href ? <Link href={`${m.href}?tab=card`} aria-label={`Open the card of ${m.model}`}>Open card</Link> : "—") },
  ];
  return (
    <>
      <PageHead title="Model" subtitle="Every model version of this project, newest first, with its cross-validation scores, the run that built it and the card that explains it, and which model is approved for scoring." actions={<Link className="btn" href={`/projects/${id}/graph`}>See how models were built</Link>} />
      <Tabs items={TABS} value={tab} onChange={setTab} idPrefix="models" label="Model sections" />
      <TabPanel idPrefix="models" id="models" active={tab === "models"}>
        <PageGuide
          purpose="Find a model and open its card."
          howTo="Open a model to see its summary, choose its threshold, read and print its card, or score a new file with it."
          youGet="Each model version with its cross-validation scores, the run that built it, whether it is in use, waiting for approval or approved earlier, when it was made and a link to its card."
          attention={<>Check the model number and the run before you score a file: any model can score a file from its own page, not only the one <Term definition="The model the project uses now. Other models are earlier or alternative results.">in use</Term>. The card shows how a model compares with the <GlossaryTerm term="baseline" />.</>}
        />
        <Card title="Versions" aside={<Pill tone="gray">{rows.length}{versions.hasNextPage ? "+" : ""}</Pill>}>
          {versions.isError ? <QueryNotice error={versions.error} what="model versions" /> : versions.isPending ? <p role="status">Loading models…</p> : (
            <>
              <DataTable columns={columns} rows={rows} rowKey={(m) => m.id} caption="Model versions" emptyMessage="No model yet. Train a run and its best model appears here." />
              {rows.length ? <p className="muted">{VERSIONS_NOTE}</p> : null}
              {versions.hasNextPage ? (
                <p><button type="button" className="btn" disabled={versions.isFetchingNextPage} onClick={() => { if (!versions.isFetchingNextPage) void versions.fetchNextPage(); }}>{versions.isFetchingNextPage ? "Loading more models…" : "Show more models"}</button></p>
              ) : null}
              {versions.isFetchNextPageError ? <Banner tone="warn">More models could not be loaded. Try again.</Banner> : null}
              {rows.some((m) => m.statusKey === "approved_earlier") ? <p className="muted">A version approved earlier can be put back in use under Approved for scoring.</p> : null}
            </>
          )}
        </Card>
      </TabPanel>
      <TabPanel idPrefix="models" id="approved" active={tab === "approved"}>{tab === "approved" ? <ApprovedForScoring projectId={id} /> : null}</TabPanel>
    </>
  );
}

export default function ModelsPage() {
  return <Suspense fallback={<p role="status">Loading…</p>}><ModelsPageInner /></Suspense>;
}
