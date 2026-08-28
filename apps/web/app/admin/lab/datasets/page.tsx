"use client";

import { Button } from "@/app/components/ui/Button";
import { ErrorState } from "@/app/components/ui/ErrorState";
import { Skeleton } from "@/app/components/ui/Skeleton";
import { Table, Td, Th } from "@/app/components/ui/Table";
import { useCreateLabWorkbook, useLabDatasets, useUploadLabDataset } from "@/lib/application";
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
  const [drag, setDrag] = useState(false);

  function send(file: File) {
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

  if (query.isPending) return <Skeleton className="h-64" />;
  if (query.isError) return <ErrorState body="Could not load datasets." onRetry={() => void query.refetch()} />;

  return (
    <div>
      <h1 className="font-display text-title text-ink">Datasets</h1>
      <p className="mt-2 max-w-2xl font-body text-body text-ink-muted">
        A CSV becomes an immutable lab dataset. After upload, open it to train the five use-case models.
      </p>
      <label
        className={`mt-8 block cursor-pointer rounded border border-hairline bg-paper-raised px-8 py-12 text-center ${drag ? "bg-navy-soft" : ""}`}
        onDragOver={(event) => {
          event.preventDefault();
          setDrag(true);
        }}
        onDragLeave={() => setDrag(false)}
        onDrop={(event) => {
          event.preventDefault();
          setDrag(false);
          const file = event.dataTransfer.files[0];
          if (file) send(file);
        }}
      >
        <input
          type="file"
          accept=".csv,.parquet,.pq,text/csv"
          className="sr-only"
          onChange={(event) => {
            const file = event.target.files?.[0];
            if (file) send(file);
          }}
        />
        <span className="font-body text-body text-ink">Drop a CSV here, or click to choose a file</span>
      </label>
      {upload.isPending ? <p className="mt-4 font-mono text-data text-ink">Uploading {progress}%</p> : null}
      {upload.isError ? (
        <p className="mt-4 font-body text-body text-oxblood">
          {upload.error instanceof ApiError ? upload.error.message : "Upload failed."}
        </p>
      ) : null}
      <p className="mt-4">
        <Button
          variant="secondary"
          disabled={sample.isPending}
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
        <p className="mt-2 font-body text-body text-oxblood">
          {sample.error instanceof ApiError ? sample.error.message : "Could not create the sample workbook."}
        </p>
      ) : null}
      <div className="mt-8 rounded bg-paper-raised p-4">
        <Table>
          <thead>
            <tr>
              <Th>Name</Th>
              <Th>Rows</Th>
              <Th>Columns</Th>
              <Th>Version</Th>
            </tr>
          </thead>
          <tbody>
            {(query.data ?? []).map((row) => (
              <tr key={row.id}>
                <Td>
                  <Link className="text-navy underline-offset-2 hover:underline" href={`/admin/lab/datasets/${row.id}`}>
                    {row.name}
                  </Link>
                </Td>
                <Td mono>{String(row.row_count)}</Td>
                <Td mono>{String(row.column_count)}</Td>
                <Td mono>{row.version}</Td>
              </tr>
            ))}
          </tbody>
        </Table>
      </div>
    </div>
  );
}
