"use client";

import { formatNumericMetrics, formatWhen, sourceTone } from "@/app/components/admin/format";
import { Badge } from "@/app/components/ui/Badge";
import { CollectionSearch } from "@/app/components/ui/CollectionSearch";
import { DataTable } from "@/app/components/ui/DataTable";
import { ErrorState } from "@/app/components/ui/ErrorState";
import { PageHeader } from "@/app/components/ui/PageHeader";
import { Skeleton } from "@/app/components/ui/Skeleton";
import { StatusBadge } from "@/app/components/ui/StatusBadge";
import { filterByText } from "@/app/components/ui/localCollection";
import { useAdminModelRegistry } from "@/lib/application";
import type { RegisteredModel } from "@/lib/domain/schemas";
import Link from "next/link";
import { useState } from "react";

function DetailLink({ row }: { row: RegisteredModel }) {
  if (row.source === "experiment") {
    return (
      <Link className="font-mono text-data text-navy hover:underline" href={`/admin/lab/experiments/${row.id}`}>
        {row.id}
      </Link>
    );
  }
  if (row.source === "client_trial") {
    return (
      <Link className="font-mono text-data text-navy hover:underline" href={`/admin/models/client-trials/${row.id}`}>
        {row.id}
      </Link>
    );
  }
  return <span className="font-mono text-data text-ink-muted">{row.id}</span>;
}

export default function ModelRegistryPage() {
  const query = useAdminModelRegistry();
  const [queryText, setQueryText] = useState("");

  if (query.isPending) {
    return (
      <div>
        <PageHeader
          eyebrow="Registry"
          title="Model registry"
          description="Every trained model across Labs, uploaded data, and the bundled simulation pack."
        />
        <Skeleton className="h-64" />
      </div>
    );
  }
  if (query.isError) {
    return <ErrorState body="Could not load the model registry." onRetry={() => void query.refetch()} />;
  }

  const allRows = query.data ?? [];
  const rows = filterByText(allRows, queryText, (row) => [
    row.name,
    row.source,
    row.status,
    row.model_family,
    row.fusion,
    row.id,
  ]);

  return (
    <div>
      <PageHeader
        eyebrow="Registry"
        title="Model registry"
        description="Every trained model across Labs, uploaded data, and the bundled simulation pack."
      />
      <div className="mt-8">
        <CollectionSearch value={queryText} onChange={setQueryText} shown={rows.length} total={allRows.length} />
        <DataTable
          columns={[
            {
              id: "source",
              header: "Source",
              cell: (row) => <Badge tone={sourceTone(row.source)}>{row.source}</Badge>,
            },
            {
              id: "name",
              header: "Name",
              cell: (row) => (
                <div>
                  <p>{row.name}</p>
                  <DetailLink row={row} />
                </div>
              ),
            },
            {
              id: "status",
              header: "Status",
              cell: (row) => <StatusBadge status={row.status} />,
            },
            { id: "family", header: "Model family", mono: true, cell: (row) => row.model_family ?? "—" },
            { id: "fusion", header: "Fusion", mono: true, cell: (row) => row.fusion ?? "—" },
            {
              id: "metrics",
              header: "Metrics",
              mono: true,
              cell: (row) => formatNumericMetrics(row.metrics) || "—",
            },
            { id: "candidates", header: "Candidates", mono: true, cell: (row) => String(row.candidate_count ?? "—") },
            { id: "created", header: "Created", mono: true, cell: (row) => formatWhen(row.created_at) || "—" },
          ]}
          rows={rows}
          rowKey={(row) => `${row.source}:${row.id}`}
          emptyTitle="No models trained yet."
          emptyBody={
            queryText.trim()
              ? "Nothing on this list matches that filter."
              : "Registry rows appear after experiments, client trials, or simulation runs persist models."
          }
        />
      </div>
    </div>
  );
}
