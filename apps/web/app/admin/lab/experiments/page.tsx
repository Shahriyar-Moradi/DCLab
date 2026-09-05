"use client";

import { DataTable } from "@/app/components/ui/DataTable";
import { CollectionSearch } from "@/app/components/ui/CollectionSearch";
import { ErrorState } from "@/app/components/ui/ErrorState";
import { PageHeader } from "@/app/components/ui/PageHeader";
import { Skeleton } from "@/app/components/ui/Skeleton";
import { StatusBadge } from "@/app/components/ui/StatusBadge";
import { filterByText } from "@/app/components/ui/localCollection";
import { useLabExperiments } from "@/lib/application";
import Link from "next/link";
import { useState } from "react";

export default function LabExperimentsPage() {
  const query = useLabExperiments();
  const [queryText, setQueryText] = useState("");
  if (query.isPending) {
    return (
      <div>
        <PageHeader
          breadcrumbs={[{ label: "Labs", href: "/admin/lab" }, { label: "Experiments" }]}
          title="Experiments"
          description="Each run is one use case on one dataset."
        />
        <Skeleton className="h-64" />
      </div>
    );
  }
  if (query.isError) return <ErrorState body="Could not load experiments." onRetry={() => void query.refetch()} />;
  const allRows = query.data ?? [];
  const rows = filterByText(allRows, queryText, (row) => [
    row.task_name,
    row.use_case,
    row.id,
    row.status,
    row.dataset_name,
    row.dataset_id,
  ]);
  return (
    <div>
      <PageHeader
        breadcrumbs={[{ label: "Labs", href: "/admin/lab" }, { label: "Experiments" }]}
        title="Experiments"
        description="Each run is one use case on one dataset."
      />
      <div className="mt-8">
        <CollectionSearch value={queryText} onChange={setQueryText} shown={rows.length} total={allRows.length} />
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
            {
              id: "dataset",
              header: "Dataset",
              cell: (row) =>
                row.dataset_id ? (
                  <Link className="text-navy hover:underline" href={`/admin/lab/datasets/${row.dataset_id}`}>
                    {row.dataset_name ?? row.dataset_id}
                  </Link>
                ) : (
                  (row.dataset_name ?? "—")
                ),
            },
            { id: "use_case", header: "Use case", mono: true, cell: (row) => row.use_case ?? "—" },
            { id: "id", header: "Id", mono: true, cell: (row) => row.id },
          ]}
          rows={rows}
          rowKey={(row) => row.id}
          emptyTitle="No experiments"
          emptyBody={queryText.trim() ? "Nothing on this list matches that filter." : "Train a use case from a dataset to create an experiment."}
        />
      </div>
    </div>
  );
}
