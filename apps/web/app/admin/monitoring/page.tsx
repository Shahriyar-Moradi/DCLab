"use client";

import { datasetHealthTone, formatWhen, sourceTone } from "@/app/components/admin/format";
import { Badge } from "@/app/components/ui/Badge";
import { Panel } from "@/app/components/ui/Card";
import { CollectionSearch } from "@/app/components/ui/CollectionSearch";
import { DataTable } from "@/app/components/ui/DataTable";
import { ErrorState } from "@/app/components/ui/ErrorState";
import { PageHeader } from "@/app/components/ui/PageHeader";
import { Skeleton } from "@/app/components/ui/Skeleton";
import { StatusBadge } from "@/app/components/ui/StatusBadge";
import { filterByText } from "@/app/components/ui/localCollection";
import { useAdminMonitoring } from "@/lib/application";
import type { RetrainEvent } from "@/lib/domain/schemas";
import Link from "next/link";
import { useState } from "react";

function retrainHref(event: RetrainEvent): string | null {
  if (event.source === "experiment") return `/admin/lab/experiments/${event.id}`;
  if (event.source === "client_trial") return `/admin/models/client-trials/${event.id}`;
  return null;
}

function metricChange(event: RetrainEvent): string {
  if (Object.keys(event.metric_deltas).length === 0) return "first run of this task/use case";
  return Object.entries(event.metric_deltas)
    .map(
      ([key, delta]) =>
        `${key}: ${delta.previous.toFixed(3)} \u2192 ${delta.current.toFixed(3)} (${
          delta.delta >= 0 ? "+" : ""
        }${delta.delta.toFixed(3)})`,
    )
    .join(" · ");
}

export default function MonitoringPage() {
  const query = useAdminMonitoring();
  const [retrainQuery, setRetrainQuery] = useState("");
  const [datasetQuery, setDatasetQuery] = useState("");

  if (query.isPending) {
    return (
      <div>
        <PageHeader
          eyebrow="Operations"
          title="Monitoring"
          description="Retrain history, evaluation deltas, and dataset synchronization health."
        />
        <Skeleton className="h-64" />
      </div>
    );
  }
  if (query.isError || !query.data) {
    return <ErrorState body="Could not load monitoring." onRetry={() => void query.refetch()} />;
  }

  const { retrain_events: retrainEvents, dataset_health: datasetHealth, drift_detection_note: driftNote } = query.data;
  const retrains = filterByText(retrainEvents, retrainQuery, (event) => [event.name, event.source, event.status]);
  const datasets = filterByText(datasetHealth, datasetQuery, (dataset) => [dataset.name, dataset.status]);

  return (
    <div>
      <PageHeader
        eyebrow="Operations"
        title="Monitoring"
        description="Retrain history, evaluation deltas, and dataset synchronization health."
      />

      <Panel className="mt-8" title="Retrain history & metric deltas">
        <CollectionSearch
          value={retrainQuery}
          onChange={setRetrainQuery}
          shown={retrains.length}
          total={retrainEvents.length}
        />
        <DataTable
          columns={[
            {
              id: "source",
              header: "Source",
              cell: (event) => <Badge tone={sourceTone(event.source)}>{event.source}</Badge>,
            },
            {
              id: "name",
              header: "Name",
              cell: (event) => {
                const href = retrainHref(event);
                return href ? (
                  <Link className="text-navy hover:underline" href={href}>
                    {event.name}
                  </Link>
                ) : (
                  event.name
                );
              },
            },
            {
              id: "status",
              header: "Status",
              cell: (event) => <StatusBadge status={event.status} />,
            },
            { id: "delta", header: "Metric change", mono: true, cell: metricChange },
            { id: "created", header: "Created", mono: true, cell: (event) => formatWhen(event.created_at) || "—" },
          ]}
          rows={retrains}
          rowKey={(event) => `${event.source}:${event.id}`}
          emptyTitle="No retrains recorded yet."
          emptyBody={
            retrainQuery.trim()
              ? "Nothing on this list matches that filter."
              : "Retrain events appear after experiments or trials persist evaluation history."
          }
        />
      </Panel>

      <Panel className="mt-6" title="Dataset sync health">
        <CollectionSearch
          value={datasetQuery}
          onChange={setDatasetQuery}
          shown={datasets.length}
          total={datasetHealth.length}
        />
        <DataTable
          columns={[
            {
              id: "name",
              header: "Dataset",
              cell: (dataset) => (
                <Link className="text-navy hover:underline" href={`/admin/lab/datasets/${dataset.id}`}>
                  {dataset.name}
                </Link>
              ),
            },
            { id: "rows", header: "Rows", mono: true, cell: (dataset) => String(dataset.row_count) },
            { id: "columns", header: "Columns", mono: true, cell: (dataset) => String(dataset.column_count) },
            {
              id: "profiled",
              header: "Last profiled",
              mono: true,
              cell: (dataset) => (dataset.last_profiled_at ? formatWhen(dataset.last_profiled_at) : "—"),
            },
            {
              id: "status",
              header: "Status",
              cell: (dataset) => (
                <StatusBadge status={dataset.status.replaceAll("_", " ")} tone={datasetHealthTone(dataset.status)} />
              ),
            },
          ]}
          rows={datasets}
          rowKey={(dataset) => dataset.id}
          emptyTitle="No datasets ingested yet."
          emptyBody={
            datasetQuery.trim()
              ? "Nothing on this list matches that filter."
              : "Dataset health uses the recorded profile status from the API."
          }
        />
      </Panel>

      <Panel className="mt-6">
        <p className="text-body text-ink-muted">{driftNote}</p>
      </Panel>
    </div>
  );
}
