"use client";

import Link from "next/link";
import { useParams, useSearchParams } from "next/navigation";
import { Suspense } from "react";
import { BranchPanel, CancelPanel, ChampionPanel } from "@/app/components/studio-app/CompareBranch";
import { compareHref } from "@/lib/application/studio-compare";
import { OperatingPointsPanel } from "@/app/components/studio-app/OperatingPoints";
import { FindingsPanel } from "@/app/components/studio-app/FindingsPanel";
import { ExperimentInspector } from "@/app/components/studio-app/Inspectors";
import { Banner } from "@/components/studio/Banner";
import { Card } from "@/components/studio/Card";
import { KeyValue } from "@/components/studio/KeyValue";
import { PageGuide } from "@/components/studio/PageGuide";
import { PageHead } from "@/components/studio/PageHead";
import { Term } from "@/components/studio/Term";
import { Pill } from "@/components/studio/Pill";
import { WaitingPanel } from "@/app/components/studio-app/WaitingPanel";
import { QueryNotice, STATUS_TONE, formatWhen } from "@/app/components/studio-app/StudioParts";
import { useStudioExperiment } from "@/lib/application";
import { projectHref } from "@/lib/application/command-search";
import { useExperimentFindings, useProjectGraph } from "@/lib/application/studio-data-hooks";
import { attentionCount, findingsState, parseBranchPrefill } from "@/lib/application/studio-findings";

const mono = (value: string | null | undefined) => (value ? <span className="mono">{value}</span> : "—");

function ExperimentPageInner() {
  const { id, experimentId } = useParams<{ id: string; experimentId: string }>();
  const query = useStudioExperiment(experimentId);
  const graph = useProjectGraph(id);
  const findings = useExperimentFindings(experimentId);
  const params = useSearchParams();
  const prefill = parseBranchPrefill(params);
  const attention = attentionCount(findings.data);
  if (query.isPending) return <p role="status">Loading experiment…</p>;
  if (query.isError) return <QueryNotice error={query.error} what="experiment" />;
  const experiment = query.data;
  if (experiment.project_id !== id) {
    const href = projectHref(experiment.project_id, "experiments", experiment.id);
    return (
      <Banner tone="warn">
        This experiment belongs to another project.{" "}
        {href ? <Link href={href}>Open it in its project</Link> : null}
      </Banner>
    );
  }
  const parentCompare = experiment.lineage.parent_experiment_id && experiment.status === "completed" ? compareHref(id, [experiment.lineage.parent_experiment_id, experiment.id]) : null;
  const live = experiment.status === "queued" || experiment.status === "running";
  return (
    <>
      <PageHead
        title={experiment.intent || `Experiment ${experiment.id.slice(0, 8)}`}
        badges={(
          <>
            <Pill tone={STATUS_TONE[experiment.status] ?? "gray"}>{experiment.status.replaceAll("_", " ")}</Pill>
            {findingsState(findings.data) === "attention" ? <Pill tone="warn">{attention} finding{attention === 1 ? "" : "s"} need{attention === 1 ? "s" : ""} attention</Pill> : null}
            {findingsState(findings.data) === "all_passed" ? <Pill tone="ok">All checks passed</Pill> : null}
          </>
        )}
        subtitle="Stages update live while the run is in progress. Reason, candidates, folds, importance, code and evidence are in the inspector below."
        actions={(
          <>
            {parentCompare ? <Link className="btn" href={parentCompare}>Compare with parent</Link> : null}
            {experiment.status === "completed" ? <Link className="btn" href={`/projects/${id}/experiments?select=${experiment.id}`}>Compare with…</Link> : null}
            <Link className="btn" href={`/projects/${id}/experiments`}>All experiments</Link>
          </>
        )}
      />
      <PageGuide
        purpose="Understand one run: why it exists, what it tried, how it scored and how to reproduce it."
        howTo={<>Use the inspector tabs: Candidates and Per-fold show the <Term definition="Cross-validation: the training rows are split into folds and each is held out once. Models are compared on these scores only.">cross-validation</Term> evidence, Code is a plain-text script you can copy or download.</>}
        youGet="Config, change set, CV metrics, per-fold results, feature importance, generated code and the stage evidence. Two-class runs also get a threshold chooser (precision and recall by threshold, on training folds). An AI Critic review appears only when one exists."
        attention="The final holdout is scored once for the locked winner and is not shown in this inspector."
      />
      {experiment.failure_reason ? <Banner tone="crit">{experiment.failure_reason}</Banner> : null}
      {experiment.status === "needs_input" ? <WaitingPanel requestId={experiment.lineage.execution_request_id} projectId={id} experimentId={experiment.id} /> : null}
      <Card title="Facts">
        <KeyValue
          items={[
            { key: "id", label: "Experiment id", value: mono(experiment.id) },
            { key: "task", label: "Task", value: experiment.task_type ?? "—" },
            { key: "target", label: "Target column", value: mono(experiment.target_column) },
            { key: "created", label: "Created", value: formatWhen(experiment.created_at) },
            { key: "started", label: "Started", value: formatWhen(experiment.started_at) },
            { key: "ended", label: "Ended", value: formatWhen(experiment.ended_at) },
            { key: "parent", label: "Parent experiment", value: mono(experiment.lineage.parent_experiment_id) },
            { key: "split", label: "Split plan", value: mono(experiment.lineage.split_plan_id) },
            { key: "spec", label: "Problem spec", value: mono(experiment.lineage.problem_spec_id) },
            { key: "dataset", label: "Dataset version", value: mono(experiment.lineage.source_dataset_id) },
          ]}
        />
      </Card>
      <div id="findings"><Card title="Findings" aside={<Pill tone="det">deterministic checks</Pill>}><FindingsPanel projectId={id} experimentId={experiment.id} /></Card></div>
      {experiment.status === "completed" ? (
        <div id="operating-points">
          <Card title="Per-fold and threshold" aside={<Pill tone="det">on training folds (out-of-fold)</Pill>}>
            <OperatingPointsPanel projectId={id} experimentId={experiment.id} />
          </Card>
        </div>
      ) : null}
      {live ? <Card title="Stop this run"><CancelPanel experimentId={experiment.id} /></Card> : null}
      {experiment.status === "completed" ? (
        <>
          <Card title="Champion"><ChampionPanel projectId={id} experimentId={experiment.id} /></Card>
          <div id="branch"><BranchPanel key={params.toString()} projectId={id} experimentId={experiment.id} initial={prefill} /></div>
        </>
      ) : null}
      <ExperimentInspector projectId={id} experimentId={experiment.id} workspaceId={experiment.workspace_id} lineage={experiment.lineage} graph={graph.data} />
    </>
  );
}

export default function ExperimentPage() {
  return <Suspense fallback={<p role="status">Loading experiment…</p>}><ExperimentPageInner /></Suspense>;
}
