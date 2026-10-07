"use client";

import Link from "next/link";
import { useParams, useSearchParams } from "next/navigation";
import { Suspense, useState } from "react";
import { PageHead } from "@/components/studio/PageHead";
import { TabPanel, Tabs } from "@/components/studio/Tabs";
import { ModelInspector } from "@/app/components/studio-app/Inspectors";
import { ModelCard } from "@/app/components/studio-app/ModelCard";
import { ScoreNewData } from "@/app/components/studio-app/ScoreNewData";

const TABS = [{ id: "version", label: "Version" }, { id: "card", label: "Card" }, { id: "score", label: "Score new data" }];

function ModelPageInner() {
  const { id, modelId } = useParams<{ id: string; modelId: string }>();
  const requested = useSearchParams().get("tab");
  const [tab, setTab] = useState(() => (TABS.some((t) => t.id === requested) ? (requested as string) : "version"));
  return (
    <>
      <PageHead title="Model version" eyebrow={`${modelId.slice(0, 8)}`} subtitle="What this version is, its card, its champion state, and scoring new data with it." actions={<><Link className="btn" href={`/projects/${id}/models`}>All models</Link><Link className="btn" href={`/projects/${id}/graph`}>Back to the graph</Link></>} />
      <Tabs items={TABS} value={tab} onChange={setTab} idPrefix="model" label="Model version sections" />
      <TabPanel idPrefix="model" id="version" active={tab === "version"}><ModelInspector projectId={id} modelVersionId={modelId} /></TabPanel>
      <TabPanel idPrefix="model" id="card" active={tab === "card"}>{tab === "card" ? <ModelCard projectId={id} modelVersionId={modelId} /> : null}</TabPanel>
      <TabPanel idPrefix="model" id="score" active={tab === "score"}><ScoreNewData projectId={id} modelVersionId={modelId} /></TabPanel>
    </>
  );
}

export default function ModelInspectorPage() {
  return <Suspense fallback={<p role="status">Loading…</p>}><ModelPageInner /></Suspense>;
}
