"use client";

import { Button } from "@/app/components/ui/Button";
import { Fact, FactGrid, Panel } from "@/app/components/ui/Card";
import { DataTable } from "@/app/components/ui/DataTable";
import { ErrorState } from "@/app/components/ui/ErrorState";
import { PageHeader } from "@/app/components/ui/PageHeader";
import { SectionHeader } from "@/app/components/ui/SectionHeader";
import { Skeleton } from "@/app/components/ui/Skeleton";
import { StatusBadge } from "@/app/components/ui/StatusBadge";
import { apiGet } from "@/lib/infrastructure";
import { LabDatasetSchema } from "@/lib/domain";
import { useLabUseCasePlan, useSession, useTrainLabUseCase } from "@/lib/application";
import { useQuery } from "@tanstack/react-query";
import { z } from "zod";
import Link from "next/link";
import { useParams } from "next/navigation";
import { useState } from "react";

export default function DatasetDetailPage() {
  const params = useParams<{ id: string }>();
  const datasetId = params.id;
  const [activeSlug, setActiveSlug] = useState<string | null>(null);
  const [batchMessage, setBatchMessage] = useState<string | null>(null);
  const dataset = useQuery({
    queryKey: ["lab", "dataset", datasetId],
    queryFn: () => apiGet(`/admin/datasets/${datasetId}`, LabDatasetSchema),
  });
  const profile = useQuery({
    queryKey: ["lab", "profile", datasetId],
    queryFn: () => apiGet(`/admin/datasets/${datasetId}/profile`, z.object({ id: z.string(), stats: z.unknown() })),
    retry: 0,
  });
  const plan = useLabUseCasePlan(datasetId);
  const train = useTrainLabUseCase(datasetId);
  const { user } = useSession();
  const canWrite = user?.role === "dclab_admin";

  if (dataset.isPending) return <Skeleton className="h-64" />;
  if (dataset.isError || !dataset.data) {
    return <ErrorState body="Dataset not found." onRetry={() => void dataset.refetch()} />;
  }
  const stats = profile.data?.stats as
    | { columns?: Array<{ name: string; missing_pct: number; dtype: string }>; row_count?: number }
    | undefined;
  const trainable = (plan.data?.use_cases ?? []).filter((item) => item.trainable);
  const profileColumns = (stats?.columns ?? []).slice(0, 40);

  async function trainAll() {
    setBatchMessage(null);
    for (const item of trainable) {
      setActiveSlug(item.slug);
      try {
        await train.mutateAsync(item.slug);
      } catch (error) {
        setBatchMessage(error instanceof Error ? error.message : "Training failed.");
        setActiveSlug(null);
        return;
      }
    }
    setActiveSlug(null);
    setBatchMessage(`Trained ${trainable.length} use case${trainable.length === 1 ? "" : "s"}.`);
  }

  return (
    <div>
      <PageHeader
        breadcrumbs={[
          { label: "Labs", href: "/admin/lab" },
          { label: "Datasets", href: "/admin/lab/datasets" },
          { label: dataset.data.name },
        ]}
        title={dataset.data.name}
        identifier={dataset.data.id}
        description={`${dataset.data.row_count} rows · ${dataset.data.column_count} columns · ${dataset.data.version}`}
      />
      <Panel className="mt-8">
        <FactGrid>
          <Fact label="Rows" value={String(dataset.data.row_count)} mono />
          <Fact label="Columns" value={String(dataset.data.column_count)} mono />
          <Fact label="Version" value={dataset.data.version} mono />
          <Fact label="Source" value={dataset.data.source_type} mono />
          <Fact label="Location" value={dataset.data.location} mono />
          {plan.data?.entity_column ? <Fact label="Entity column" value={plan.data.entity_column} mono /> : null}
          {plan.data?.time_column ? <Fact label="Time column" value={plan.data.time_column} mono /> : null}
        </FactGrid>
      </Panel>

      <SectionHeader
        className="mt-10"
        title="Use cases"
        description="Five models per use case: a baseline, a linear model on all features, a random forest on all features, and two tree models on different feature-group combinations."
      />
      {plan.isPending ? <Skeleton className="mt-4 h-40" /> : null}
      {plan.isError ? (
        <p className="mt-4 text-body text-oxblood">Could not plan use cases for this file.</p>
      ) : null}
      <div className="mt-4 grid gap-4">
        {(plan.data?.use_cases ?? []).map((item) => (
          <Panel
            key={item.slug}
            title={item.name}
            description={item.description}
            actions={
              <Button
                disabled={!canWrite || !item.trainable || train.isPending}
                onClick={() => {
                  setActiveSlug(item.slug);
                  setBatchMessage(null);
                  train.mutate(item.slug, { onSettled: () => setActiveSlug(null) });
                }}
              >
                {train.isPending && activeSlug === item.slug ? "Training…" : "Train 5 models"}
              </Button>
            }
          >
            {item.trainable ? (
              <p className="font-mono text-data text-ink">
                label {item.target_column} · {item.task_type} · {item.model_families.length} families
              </p>
            ) : (
              <p className="text-body text-oxblood">{item.skip_reason}</p>
            )}
            {item.trainable && Object.keys(item.feature_groups).length > 0 ? (
              <ul className="mt-3 font-mono text-data text-ink-muted">
                {Object.entries(item.feature_groups).map(([group, cols]) => (
                  <li key={group}>
                    {group}: {cols.join(", ")}
                  </li>
                ))}
              </ul>
            ) : null}
            {item.latest_experiment_id ? (
              <p className="mt-3 text-body">
                Last run {item.latest_status ? <StatusBadge status={item.latest_status} /> : null} ·{" "}
                <Link className="text-navy hover:underline" href={`/admin/lab/experiments/${item.latest_experiment_id}`}>
                  Open report
                </Link>
              </p>
            ) : null}
          </Panel>
        ))}
      </div>
      <p className="mt-6">
        <Button disabled={!canWrite || trainable.length === 0 || train.isPending} onClick={() => void trainAll()}>
          {train.isPending && activeSlug ? `Training ${activeSlug}…` : `Train all ready use cases (${trainable.length})`}
        </Button>
      </p>
      {!canWrite ? <p className="mt-3 text-body text-ink-muted">Read-only platform access. Training requires DCLab Admin.</p> : null}
      {train.isError ? <p className="mt-3 text-body text-oxblood">{train.error.message}</p> : null}
      {batchMessage ? <p className="mt-3 text-body text-ink">{batchMessage}</p> : null}

      <SectionHeader className="mt-10" title="Profile" />
      {profile.isError ? (
        <p className="mt-2 text-body text-ink-muted">No profile yet.</p>
      ) : (
        <div className="mt-4">
          <DataTable
            columns={[
              { id: "name", header: "Column", mono: true, cell: (col) => col.name },
              { id: "dtype", header: "Type", mono: true, cell: (col) => col.dtype },
              {
                id: "missing",
                header: "Missing",
                mono: true,
                cell: (col) => `${Math.round((col.missing_pct ?? 0) * 100)}%`,
              },
            ]}
            rows={profileColumns}
            rowKey={(col) => col.name}
            emptyTitle="No profile yet."
            emptyBody="Column statistics appear after the dataset is profiled."
          />
        </div>
      )}
    </div>
  );
}
