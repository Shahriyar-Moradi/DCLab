"use client";

import { ErrorState } from "@/app/components/ui/ErrorState";
import { Skeleton } from "@/app/components/ui/Skeleton";
import { Table, Td, Th } from "@/app/components/ui/Table";
import { PageIntro, WorkspaceShell } from "@/app/components/workspace/PageIntro";
import { useLabDatasets } from "@/lib/application";
import Link from "next/link";

export default function LabDatasetsPage() {
  const query = useLabDatasets();
  if (query.isPending) {
    return (
      <WorkspaceShell>
        <Skeleton className="h-64" />
      </WorkspaceShell>
    );
  }
  if (query.isError) {
    return (
      <WorkspaceShell>
        <ErrorState body="Could not load datasets." onRetry={() => void query.refetch()} />
      </WorkspaceShell>
    );
  }
  return (
    <WorkspaceShell>
      <PageIntro eyebrow="Experimentation lab" title="Datasets" subtitle="Immutable versions used by experiments." />
      <div className="mt-8 rounded-2xl bg-white p-4 shadow-sm ring-1 ring-hairline">
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
                  <Link className="text-brand underline-offset-2 hover:underline" href={`/lab/datasets/${row.id}`}>
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
    </WorkspaceShell>
  );
}
