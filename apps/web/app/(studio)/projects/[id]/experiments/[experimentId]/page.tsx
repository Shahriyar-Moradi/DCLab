"use client";

import Link from "next/link";
import { useParams } from "next/navigation";
import { ModelBuildInspector } from "@/app/components/model-build/ModelBuildInspector";
import { Banner } from "@/components/studio/Banner";
import { Card } from "@/components/studio/Card";
import { KeyValue } from "@/components/studio/KeyValue";
import { PageHead } from "@/components/studio/PageHead";
import { Pill } from "@/components/studio/Pill";
import { QueryNotice, STATUS_TONE, formatWhen } from "@/app/components/studio-app/StudioParts";
import { useStudioExperiment } from "@/lib/application";
import { projectHref } from "@/lib/application/command-search";

const mono = (value: string | null | undefined) => (value ? <span className="mono">{value}</span> : "—");

export default function ExperimentPage() {
  const { id, experimentId } = useParams<{ id: string; experimentId: string }>();
  const query = useStudioExperiment(experimentId);
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
  return (
    <>
      <PageHead
        title={experiment.intent || `Experiment ${experiment.id.slice(0, 8)}`}
        badges={<Pill tone={STATUS_TONE[experiment.status] ?? "gray"}>{experiment.status.replaceAll("_", " ")}</Pill>}
        subtitle="Stages update live while the run is in progress."
        actions={<Link className="btn" href={`/projects/${id}/experiments`}>All experiments</Link>}
      />
      {experiment.failure_reason ? <Banner tone="crit">{experiment.failure_reason}</Banner> : null}
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
      <div className="legacy-surface">
        <ModelBuildInspector workspaceId={experiment.workspace_id} pipelineRunId={experiment.id} />
      </div>
    </>
  );
}
