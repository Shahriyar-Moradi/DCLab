"use client";

import { Button } from "@/app/components/ui/Button";
import { CollectionSearch } from "@/app/components/ui/CollectionSearch";
import { DataTable } from "@/app/components/ui/DataTable";
import { ErrorState } from "@/app/components/ui/ErrorState";
import { PageHeader } from "@/app/components/ui/PageHeader";
import { Skeleton } from "@/app/components/ui/Skeleton";
import { UploadZone } from "@/app/components/ui/UploadZone";
import { filterByText } from "@/app/components/ui/localCollection";
import { useCreateLabWorkbook, useLabDatasets, useSession, useUploadLabDataset } from "@/lib/application";
import { ApiError } from "@/lib/infrastructure";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { useState } from "react";

export default function LabDatasetsPage() {
  const query = useLabDatasets();
  const upload = useUploadLabDataset();
  const sample = useCreateLabWorkbook();
  const router = useRouter();
  const [progress, setProgress] = useState(0);
  const [queryText, setQueryText] = useState("");
  const { user } = useSession();
  const canWrite = user?.role === "dclab_admin";

  function send(file: File) {
    if (!canWrite) return;
    setProgress(0);
    upload.mutate(
      { file, onProgress: setProgress },
      {
        onSuccess: (dataset) => {
          router.push(`/admin/lab/datasets/${dataset.id}`);
        },
      },
    );
  }

  if (query.isPending) {
    return (
      <div>
        <DatasetsHeader />
        <Skeleton className="h-64" />
      </div>
    );
  }
  if (query.isError) return <ErrorState body="Could not load datasets." onRetry={() => void query.refetch()} />;
  const allRows = query.data ?? [];
  const rows = filterByText(allRows, queryText, (row) => [row.name, row.version, row.source_type, row.id]);

  return (
    <div>
      <DatasetsHeader />
      {!canWrite ? (
        <p className="mt-4 rounded-xl border border-hairline bg-navy-soft p-4 text-body text-ink-muted">
          Read-only platform access. Upload and training actions require DCLab Admin.
        </p>
      ) : null}
      <UploadZone
        className="mt-8"
        accept=".csv,.parquet,.pq,text/csv"
        disabled={!canWrite || upload.isPending}
        label="Drop a CSV here, or click to choose a file"
        hint={upload.isPending ? `Uploading ${progress}%` : "CSV or Parquet. After upload, open the dataset to train ready use cases."}
        error={
          upload.isError
            ? upload.error instanceof ApiError
              ? upload.error.message
              : "Upload failed."
            : undefined
        }
        onFiles={(files) => {
          const file = files[0];
          if (file) send(file);
        }}
      />
      <p className="mt-4">
        <Button
          variant="secondary"
          disabled={!canWrite || sample.isPending}
          onClick={() =>
            sample.mutate(undefined, {
              onSuccess: (dataset) => router.push(`/admin/lab/datasets/${dataset.id}`),
            })
          }
        >
          {sample.isPending ? "Building sample workbook…" : "Load sample workbook (all five use cases)"}
        </Button>
      </p>
      {sample.isError ? (
        <p className="mt-2 text-body text-oxblood">
          {sample.error instanceof ApiError ? sample.error.message : "Could not create the sample workbook."}
        </p>
      ) : null}
      <div className="mt-8">
        <CollectionSearch value={queryText} onChange={setQueryText} shown={rows.length} total={allRows.length} />
        <DataTable
          columns={[
            {
              id: "name",
              header: "Name",
              cell: (row) => (
                <Link className="text-navy hover:underline" href={`/admin/lab/datasets/${row.id}`}>
                  {row.name}
                </Link>
              ),
            },
            { id: "rows", header: "Rows", mono: true, cell: (row) => String(row.row_count) },
            { id: "columns", header: "Columns", mono: true, cell: (row) => String(row.column_count) },
            { id: "version", header: "Version", mono: true, cell: (row) => row.version },
            { id: "source", header: "Source", mono: true, cell: (row) => row.source_type },
          ]}
          rows={rows}
          rowKey={(row) => row.id}
          emptyTitle="No datasets"
          emptyBody={
            queryText.trim()
              ? "Nothing on this list matches that filter."
              : "Upload a CSV or load the sample workbook to create an immutable lab dataset."
          }
        />
      </div>
    </div>
  );
}

function DatasetsHeader() {
  return (
    <PageHeader
      breadcrumbs={[{ label: "Labs", href: "/admin/lab" }, { label: "Datasets" }]}
      title="Datasets"
      description="A CSV becomes an immutable lab dataset. After upload, open it to train the five use-case models."
    />
  );
}
