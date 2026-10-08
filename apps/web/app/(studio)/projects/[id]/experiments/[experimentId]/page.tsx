"use client";

import Link from "next/link";
import { useParams, useSearchParams } from "next/navigation";
import { Suspense } from "react";
import { BranchPanel, CancelPanel, ChampionPanel } from "@/app/components/studio-app/CompareBranch";
import { compareHref } from "@/lib/application/studio-compare";
import { OperatingPointsPanel } from "@/app/components/studio-app/OperatingPoints";
import { FindingsPanel } from "@/app/components/studio-app/FindingsPanel";
import { ExperimentInspector } from "@/app/components/studio-app/Inspectors";
import { RunSteps } from "@/app/components/studio-app/RunSteps";
import { Banner } from "@/components/studio/Banner";
import { Card } from "@/components/studio/Card";
import { KeyValue } from "@/components/studio/KeyValue";
import { PageGuide } from "@/components/studio/PageGuide";
import { PageHead } from "@/components/studio/PageHead";
import { GlossaryTerm } from "@/components/studio/GlossaryTerm";
import { Pill } from "@/components/studio/Pill";
import { WaitingPanel } from "@/app/components/studio-app/WaitingPanel";
import { QueryNotice, formatWhen, statusTone } from "@/app/components/studio-app/StudioParts";
import { useProjectExperiments, useStudioExperiment } from "@/lib/application";
import { plainText, projectHref } from "@/lib/application/command-search";
import { useExperimentFindings, useProjectGraph } from "@/lib/application/studio-data-hooks";
import { attentionCount, findingsState, parseBranchPrefill } from "@/lib/application/studio-findings";
import { designLabels, runName, runOrdinals } from "@/lib/application/studio-names";
import { taskLabel } from "@/lib/application/studio-goal";
import { statusWords } from "@/lib/application/studio-runs";

const mono = (value: string | null | undefined) => (value ? <span className="mono">{plainText(value, 80)}</span> : "—");

function ExperimentPageInner() {
  const { id, experimentId } = useParams<{ id: string; experimentId: string }>();
  const query = useStudioExperiment(experimentId);
  const graph = useProjectGraph(id);
  const findings = useExperimentFindings(experimentId);
  const params = useSearchParams();
  const prefill = parseBranchPrefill(params);
  const siblings = useProjectExperiments(id);
  const attention = attentionCount(findings.data);
  if (query.isPending) return <p role="status">Loading the run…</p>;
  if (query.isError) return <QueryNotice error={query.error} what="run" />;
  const experiment = query.data;
  if (experiment.project_id !== id) {
    const href = projectHref(experiment.project_id, "experiments", experiment.id);
    return (
      <Banner tone="warn">
        This run belongs to another project.{" "}
        {href ? <Link href={href}>Open it in its project</Link> : null}
      </Banner>
    );
  }
  const parentCompare = experiment.lineage.parent_experiment_id && experiment.status === "completed" ? compareHref(id, [experiment.lineage.parent_experiment_id, experiment.id]) : null;
  const runs = siblings.data?.items ?? [];
  const partial = !!siblings.data?.next_cursor;
  const ordinals = runOrdinals(runs);
  const ordinal = ordinals.get(experiment.id);
  const title = runName(partial || ordinal === undefined ? undefined : ordinal, experiment.intent ? plainText(experiment.intent, 120) : null, partial || ordinal === undefined ? experiment.id.slice(0, 8) : undefined);
  const parentId = experiment.lineage.parent_experiment_id;
  const parentName = parentId ? (ordinals.has(parentId) && !partial ? `Run ${ordinals.get(parentId)}` : `Run ${parentId.slice(0, 8)}`) : null;
  const designName = experiment.lineage.split_plan_id ? designLabels([...runs].sort((a, b) => a.created_at.localeCompare(b.created_at)).map((r) => r.split_plan_id)).get(experiment.lineage.split_plan_id) ?? null : null;
  const live = experiment.status === "queued" || experiment.status === "running";
  return (
    <>
      <PageHead
        title={title}
        eyebrow={<>Run <span className="mono">{experiment.id.slice(0, 8)}</span></>}
        badges={(
          <>
            <Pill tone={statusTone(experiment.status)}>{statusWords(experiment.status)}</Pill>
            {findingsState(findings.data) === "attention" ? <Pill tone="warn">{attention} trust check{attention === 1 ? "" : "s"} to review</Pill> : null}
            {findingsState(findings.data) === "all_passed" ? <Pill tone="ok">All trust checks passed</Pill> : null}
            {findingsState(findings.data) === "not_computed" ? <Pill tone="gray">Trust checks not recorded yet</Pill> : null}
          </>
        )}
        subtitle="The steps of this run update live while it is in progress. Candidates, folds, importance, code and evidence are in the inspector below."
        actions={(
          <>
            {parentCompare ? <Link className="btn" href={parentCompare}>Compare with {parentName ?? "the run it is based on"}</Link> : null}
            {experiment.status === "completed" ? <Link className="btn" href={`/projects/${id}/experiments?select=${experiment.id}`}>Compare with…</Link> : null}
            <Link className="btn" href={`/projects/${id}/pipeline/${experiment.id}`}>Run evidence</Link>
            <Link className="btn" href={`/projects/${id}/experiments`}>All experiments</Link>
          </>
        )}
      />
      <PageGuide
        purpose="Understand one run: why it exists, what it tried, how it scored and how to reproduce it."
        howTo={<>Read the steps and the trust checks first. In the inspector, Candidates and Per-fold show the <GlossaryTerm term="cv" /> evidence; Code is a plain-text script you can copy or download.</>}
        youGet="The steps with the time each took, trust checks with what to do, the threshold chooser (two-class runs), a way to try a change, and the inspector with settings, scores per fold, feature importance and the generated code. An AI reviewer's note appears only when one exists."
        attention={<>The <GlossaryTerm term="finalTest" /> is scored once for the chosen model and is not shown in this inspector.</>}
      />
      {experiment.failure_reason ? <Banner tone="crit">{experiment.failure_reason}</Banner> : null}
      {experiment.status === "needs_input" ? <WaitingPanel requestId={experiment.lineage.execution_request_id} projectId={id} experimentId={experiment.id} /> : null}
      <RunSteps experimentId={experiment.id} />
      <Card title="About this run">
        <KeyValue
          items={[
            { key: "task", label: "Kind of answer", value: taskLabel(experiment.task_type) ?? "—" },
            { key: "target", label: "Column to predict", value: mono(experiment.target_column) },
            { key: "parent", label: "Based on", value: parentName && parentId ? <Link href={`/projects/${id}/experiments/${parentId}`}>{parentName}</Link> : "Started from scratch" },
            { key: "split", label: "Test design", value: designName ?? "—" },
            { key: "started", label: "Started", value: formatWhen(experiment.started_at ?? experiment.created_at) },
            { key: "ended", label: "Finished", value: formatWhen(experiment.ended_at) },
          ]}
        />
        <details>
          <summary>Technical ids</summary>
          <KeyValue
            items={[
              { key: "id", label: "Run id", value: mono(experiment.id) },
              { key: "parent", label: "Based on (id)", value: mono(experiment.lineage.parent_experiment_id) },
              { key: "split", label: "Test design id", value: mono(experiment.lineage.split_plan_id) },
              { key: "spec", label: "Goal id", value: mono(experiment.lineage.problem_spec_id) },
              { key: "dataset", label: "Data version id", value: mono(experiment.lineage.source_dataset_id) },
            ]}
          />
        </details>
      </Card>
      <div id="findings"><Card title="Trust checks" aside={<Pill tone="det">checked by fixed rules, no AI</Pill>}><FindingsPanel projectId={id} experimentId={experiment.id} /></Card></div>
      {experiment.status === "completed" ? (
        <div id="operating-points">
          <Card title="Per-fold and threshold" aside={<Pill tone="det">chosen on cross-validation, not on the final test set</Pill>}>
            <OperatingPointsPanel projectId={id} experimentId={experiment.id} />
          </Card>
        </div>
      ) : null}
      {live ? <Card title="Stop this run"><CancelPanel experimentId={experiment.id} /></Card> : null}
      {experiment.status === "completed" ? (
        <>
          <Card title="Model in use"><ChampionPanel projectId={id} experimentId={experiment.id} /></Card>
          <div id="branch"><BranchPanel key={params.toString()} projectId={id} experimentId={experiment.id} initial={prefill} /></div>
        </>
      ) : null}
      <ExperimentInspector projectId={id} experimentId={experiment.id} workspaceId={experiment.workspace_id} lineage={experiment.lineage} graph={graph.data} />
    </>
  );
}

export default function ExperimentPage() {
  return <Suspense fallback={<p role="status">Loading the run…</p>}><ExperimentPageInner /></Suspense>;
}
