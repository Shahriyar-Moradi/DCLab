"use client";

import { ErrorState } from "@/app/components/ui/ErrorState";
import { Skeleton } from "@/app/components/ui/Skeleton";
import { Table, Td, Th } from "@/app/components/ui/Table";
import { useLabDatasets } from "@/lib/application";
import Link from "next/link";

export default function LabDatasetsPage() {
  const query = useLabDatasets();
  if (query.isPending) return <Skeleton className="h-64" />;
  if (query.isError) return <ErrorState body="Could not load datasets." onRetry={() => void query.refetch()} />;
  return (
    <div>
      <h1 className="font-display text-title text-ink">Datasets</h1>
      <p className="mt-2 font-body text-body text-ink-muted">Immutable versions used by experiments.</p>
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
                  <Link className="text-navy underline-offset-2 hover:underline" href={`/lab/datasets/${row.id}`}>
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
