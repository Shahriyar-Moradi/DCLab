"use client";

import Link from "next/link";
import { useParams } from "next/navigation";
import { useState } from "react";
import { PageHead } from "@/components/studio/PageHead";
import { TabPanel, Tabs } from "@/components/studio/Tabs";
import { ModelInspector } from "@/app/components/studio-app/Inspectors";
import { ScoreNewData } from "@/app/components/studio-app/ScoreNewData";

const TABS = [{ id: "version", label: "Version" }, { id: "score", label: "Score new data" }];

export default function ModelInspectorPage() {
  const { id, modelId } = useParams<{ id: string; modelId: string }>();
  const [tab, setTab] = useState("version");
  return (
    <>
      <PageHead title="Model version" eyebrow={`${modelId.slice(0, 8)}`} subtitle="What this version is, what it was built from, its champion state, and scoring new data with it." actions={<Link className="btn" href={`/projects/${id}/graph`}>Back to the graph</Link>} />
      <Tabs items={TABS} value={tab} onChange={setTab} idPrefix="model" label="Model version sections" />
      <TabPanel idPrefix="model" id="version" active={tab === "version"}><ModelInspector projectId={id} modelVersionId={modelId} /></TabPanel>
      <TabPanel idPrefix="model" id="score" active={tab === "score"}><ScoreNewData projectId={id} modelVersionId={modelId} /></TabPanel>
    </>
  );
}
