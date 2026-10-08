"use client";

import Link from "next/link";
import { useParams, useSearchParams } from "next/navigation";
import { Suspense, useState } from "react";
import { Banner } from "@/components/studio/Banner";
import { Card } from "@/components/studio/Card";
import { PageGuide } from "@/components/studio/PageGuide";
import { PageHead } from "@/components/studio/PageHead";
import { Pill } from "@/components/studio/Pill";
import { TabPanel, Tabs } from "@/components/studio/Tabs";
import { Term } from "@/components/studio/Term";
import { GLOSSARY } from "@/components/studio/glossary";
import { ModelInspector } from "@/app/components/studio-app/Inspectors";
import { ModelCard } from "@/app/components/studio-app/ModelCard";
import { OperatingPointsPanel } from "@/app/components/studio-app/OperatingPoints";
import { ScoreNewData } from "@/app/components/studio-app/ScoreNewData";
import { QueryNotice } from "@/app/components/studio-app/StudioParts";
import { useModelVersionRead } from "@/lib/application/studio-inspect-hooks";
import { modelName } from "@/lib/application/studio-names";
import { modelHeader } from "@/lib/application/studio-model";
import { isUuid, plainText } from "@/lib/application/command-search";

const TABS = [
  { id: "version", label: "Summary" },
  { id: "threshold", label: "Threshold" },
  { id: "card", label: "Model card" },
  { id: "score", label: "Score new data" },
];

/** The threshold choice belongs to the run that built this model; it is the same card the run page shows. */
function ThresholdTab({ projectId, modelVersionId }: { projectId: string; modelVersionId: string }) {
  const model = useModelVersionRead(modelVersionId);
  if (model.isError) return <QueryNotice error={model.error} what="model" />;
  if (!model.data) return <p role="status">Loading the model…</p>;
  if (model.data.project_id && model.data.project_id !== projectId) return <Banner tone="warn">This model belongs to another project.</Banner>;
  return (
    <>
      <PageGuide
        purpose="See how many rows this model flags at each threshold, and record the threshold you would prefer."
        howTo={<>Read the chart, select a row of the table, then record your choice with a reason. A lower <Term definition={GLOSSARY.threshold.definition}>threshold</Term> flags more rows: more positives found, more false alarms.</>}
        youGet="Precision and recall at each candidate threshold, the locked threshold scoring uses, and a record of your choice in History."
        attention="Choosing a threshold records a decision only. Scoring with this model keeps its locked threshold; applying your choice needs a new model version. All figures are measured on the training folds, which can look better than new data."
      />
      <div id="operating-points">
        <Card title="Threshold: how many rows get flagged" aside={<Pill tone="det">chosen on cross-validation, not on the final test set</Pill>}>
          <OperatingPointsPanel projectId={projectId} experimentId={model.data.lineage.experiment_id} />
        </Card>
      </div>
    </>
  );
}

function ModelPageInner() {
  const { id, modelId } = useParams<{ id: string; modelId: string }>();
  const requested = useSearchParams().get("tab");
  const [tab, setTab] = useState(() => (TABS.some((t) => t.id === requested) ? (requested as string) : "version"));
  const model = useModelVersionRead(modelId);
  const validId = isUuid(modelId);
  const header = modelHeader({ validId, isError: model.isError, data: model.data }, id, (v) => plainText(modelName(plainText(v, 40)), 60));
  const badge = <Pill tone={header.pill.tone}>{header.pill.text}</Pill>;
  const title = header.title;
  const other = header.pill.text === "other project";
  const usable = validId && !other;
  return (
    <>
      <PageHead
        title={title}
        badges={badge}
        subtitle="What this model is, its threshold, its card, whether it is in use, and scoring a new file with it."
        actions={<><Link className="btn" href={`/projects/${id}/models`}>All models</Link><Link className="btn" href={`/projects/${id}/graph`}>Back to the project diagram</Link></>}
      />
      {!validId ? <Banner tone="warn">This is not a valid model link. <Link href={`/projects/${id}/models`}>See all models</Link>.</Banner> : null}
      {other ? <Banner tone="warn">This model belongs to another project. <Link href={`/projects/${id}/models`}>See the models of this project</Link>.</Banner> : null}
      {usable ? <Tabs items={TABS} value={tab} onChange={setTab} idPrefix="model" label="Model sections" /> : null}
      <TabPanel idPrefix="model" id="version" active={usable && tab === "version"}>{usable ? <ModelInspector projectId={id} modelVersionId={modelId} /> : null}</TabPanel>
      <TabPanel idPrefix="model" id="threshold" active={usable && tab === "threshold"}>{usable && tab === "threshold" ? <ThresholdTab projectId={id} modelVersionId={modelId} /> : null}</TabPanel>
      <TabPanel idPrefix="model" id="card" active={usable && tab === "card"}>{usable && tab === "card" ? <ModelCard projectId={id} modelVersionId={modelId} /> : null}</TabPanel>
      <TabPanel idPrefix="model" id="score" active={usable && tab === "score"}>{usable ? <ScoreNewData projectId={id} modelVersionId={modelId} /> : null}</TabPanel>
    </>
  );
}

export default function ModelInspectorPage() {
  return <Suspense fallback={<p role="status">Loading…</p>}><ModelPageInner /></Suspense>;
}
