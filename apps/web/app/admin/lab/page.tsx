"use client";

import { formatWhen } from "@/app/components/admin/format";
import { CollectionSearch } from "@/app/components/ui/CollectionSearch";
import { DataTable } from "@/app/components/ui/DataTable";
import { ErrorState } from "@/app/components/ui/ErrorState";
import { MetricCard } from "@/app/components/ui/MetricCard";
import { PageHeader } from "@/app/components/ui/PageHeader";
import { SectionHeader } from "@/app/components/ui/SectionHeader";
import { Skeleton } from "@/app/components/ui/Skeleton";
import { StatusBadge } from "@/app/components/ui/StatusBadge";
import { buttonClassName } from "@/app/components/ui/Button";
import { filterByText } from "@/app/components/ui/localCollection";
import {
  useAdminClientUploads,
  useLabDatasets,
  useLabEnvironments,
  useLabExperiments,
  useLabTasks,
} from "@/lib/application";
import Link from "next/link";
import { useState } from "react";

export default function LabDashboard() {
  const env = useLabEnvironments();
  const datasets = useLabDatasets();
  const tasks = useLabTasks();
  const experiments = useLabExperiments();
  const clientUploads = useAdminClientUploads();
  const [experimentQuery, setExperimentQuery] = useState("");
  const [ingestQuery, setIngestQuery] = useState("");

  if (env.isError || datasets.isError || tasks.isError || experiments.isError || clientUploads.isError) {
    return (
      <ErrorState
        body="Could not load the Lab. Is the API running?"
        onRetry={() => {
          void env.refetch();
          void datasets.refetch();
          void tasks.refetch();
          void experiments.refetch();
          void clientUploads.refetch();
        }}
      />
    );
  }
  if (env.isPending || datasets.isPending || tasks.isPending || experiments.isPending || clientUploads.isPending) {
    return (
      <div>
        <PageHeader
          eyebrow="Internal ML workspace"
          title="Labs"
          description="Upload datasets, inspect use cases, and trace experiments through their candidate models and results."
        />
        <Skeleton className="h-64" />
      </div>
    );
  }

  const environments = env.data ?? [];
  const recent = filterByText((experiments.data ?? []).slice(0, 8), experimentQuery, (row) => [
    row.task_name,
    row.use_case,
    row.id,
    row.status,
    row.dataset_name,
  ]);
  const ingest = filterByText((clientUploads.data ?? []).slice(0, 10), ingestQuery, (row) => [
    row.original_filename,
    row.category,
    row.kind,
    row.pipeline_status,
  ]);

  return (
    <div>
      <PageHeader
        eyebrow="Internal ML workspace"
        title="Labs"
        description="Upload datasets, inspect use cases, and trace experiments through their candidate models and results."
        actions={
          <Link href="/admin/lab/datasets" className={buttonClassName()}>
            Open datasets
          </Link>
        }
      />
      <div className="mt-8 grid gap-4 md:grid-cols-4">
        <Link href="/admin/lab/datasets">
          <MetricCard label="Datasets" value={String(datasets.data?.length ?? 0)} />
        </Link>
        <Link href="/admin/lab/experiments">
          <MetricCard label="Experiments" value={String(experiments.data?.length ?? 0)} />
        </Link>
        <Link href="/admin/lab/tasks">
          <MetricCard label="Tasks" value={String(tasks.data?.length ?? 0)} />
        </Link>
        <MetricCard label="Environments" value={String(environments.length)} />
      </div>

      {environments.length > 0 ? (
        <div className="mt-10">
          <SectionHeader title="Environments" />
          <div className="mt-4">
            <DataTable
              columns={[
                { id: "name", header: "Name", cell: (row) => row.name },
                { id: "id", header: "Id", mono: true, cell: (row) => row.id },
                { id: "org", header: "Org", mono: true, cell: (row) => row.org_id },
              ]}
              rows={environments}
              rowKey={(row) => row.id}
            />
          </div>
        </div>
      ) : null}

      <div className="mt-10">
        <SectionHeader title="Recent experiments" />
        <div className="mt-4">
          <CollectionSearch
            value={experimentQuery}
            onChange={setExperimentQuery}
            shown={recent.length}
            total={Math.min((experiments.data ?? []).length, 8)}
          />
          <DataTable
            columns={[
              {
                id: "name",
                header: "Experiment",
                cell: (row) => (
                  <Link className="text-navy hover:underline" href={`/admin/lab/experiments/${row.id}`}>
                    {row.task_name ?? row.use_case ?? row.id}
                  </Link>
                ),
              },
              {
                id: "status",
                header: "Status",
                cell: (row) => <StatusBadge status={row.status} />,
              },
              { id: "dataset", header: "Dataset", cell: (row) => row.dataset_name ?? "—" },
              { id: "use_case", header: "Use case", mono: true, cell: (row) => row.use_case ?? "—" },
            ]}
            rows={recent}
            rowKey={(row) => row.id}
            emptyTitle="No experiments"
            emptyBody={
              experimentQuery.trim()
                ? "Nothing on this list matches that filter."
                : "Train a use case from a dataset to create an experiment."
            }
          />
        </div>
      </div>

      <div className="mt-10">
        <SectionHeader
          title="Open ingest jobs"
          description="Simple-case auto-train runs behind Labs custom-box uploads (spreadsheet/JSON/table file, named columns, 40+ rows)."
        />
        <div className="mt-4">
          <CollectionSearch
            value={ingestQuery}
            onChange={setIngestQuery}
            shown={ingest.length}
            total={Math.min((clientUploads.data ?? []).length, 10)}
          />
          <DataTable
            columns={[
              {
                id: "file",
                header: "Upload",
                cell: (row) => (
                  <div>
                    <Link className="text-navy hover:underline" href={`/admin/models/client-uploads/${row.id}`}>
                      {row.original_filename}
                    </Link>
                    <p className="font-mono text-data text-ink-muted">
                      {row.category} · {row.kind} · {row.record_count} rows
                    </p>
                  </div>
                ),
              },
              {
                id: "status",
                header: "Status",
                cell: (row) => <StatusBadge status={row.pipeline_status} />,
              },
              { id: "created", header: "Created", mono: true, cell: (row) => formatWhen(row.created_at) || "—" },
            ]}
            rows={ingest}
            rowKey={(row) => row.id}
            emptyTitle="No custom-box uploads yet."
            emptyBody={
              ingestQuery.trim()
                ? "Nothing on this list matches that filter."
                : "Client Labs uploads appear here with their recorded pipeline status."
            }
          />
        </div>
      </div>
    </div>
  );
}
