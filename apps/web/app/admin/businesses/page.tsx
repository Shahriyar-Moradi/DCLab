"use client";

import { ErrorState } from "@/app/components/ui/ErrorState";
import { Skeleton } from "@/app/components/ui/Skeleton";
import { Table, Td, Th } from "@/app/components/ui/Table";
import { usePlatformBusinesses } from "@/lib/application";
import Link from "next/link";

export default function BusinessesPage() {
  const query = usePlatformBusinesses();
  if (query.isPending) return <Skeleton className="h-72" />;
  if (query.isError) return <ErrorState body="Could not load businesses." onRetry={() => void query.refetch()} />;
  return (
    <div>
      <p className="text-eyebrow uppercase tracking-[0.08em] text-ink-muted">Platform administration</p>
      <h1 className="mt-2 font-display text-title text-ink">Businesses</h1>
      <p className="mt-2 max-w-3xl text-body text-ink-muted">Explore every tenant from business context through domains, workflows, runs, pipelines, and managed model versions.</p>
      <div className="mt-8 rounded bg-paper-raised p-4">
        <Table>
          <thead><tr><Th>Business</Th><Th>Domains</Th><Th>Workflows</Th><Th>Runs</Th><Th>Pipelines</Th><Th>Models</Th><Th>Members</Th></tr></thead>
          <tbody>{(query.data ?? []).map((row) => (
            <tr key={row.id}>
              <Td><Link className="font-semibold text-navy hover:underline" href={`/admin/businesses/${row.id}`}>{row.name}</Link><p className="font-mono text-data text-ink-muted">{row.slug}{row.industry ? ` · ${row.industry}` : ""}</p></Td>
              <Td mono>{row.domain_count}</Td><Td mono>{row.workflow_count}</Td><Td mono>{row.run_count}</Td><Td mono>{row.pipeline_count}</Td><Td mono>{row.model_count}</Td><Td mono>{row.membership_count}</Td>
            </tr>
          ))}</tbody>
        </Table>
      </div>
    </div>
  );
}
