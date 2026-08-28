"use client";

import { ErrorState } from "@/app/components/ui/ErrorState";
import { Skeleton } from "@/app/components/ui/Skeleton";
import { Table, Td, Th } from "@/app/components/ui/Table";
import { useAdminOrganizations } from "@/lib/application";
import Link from "next/link";

export default function OrganizationsPage() {
  const query = useAdminOrganizations();

  if (query.isPending) return <Skeleton className="h-64" />;
  if (query.isError) {
    return <ErrorState body="Could not load organizations." onRetry={() => void query.refetch()} />;
  }

  const rows = query.data ?? [];

  return (
    <div>
      <p className="font-body text-eyebrow uppercase tracking-[0.06em] text-ink-muted">DCLab Admin</p>
      <h1 className="mt-2 font-display text-title text-ink">Organizations</h1>
      <p className="mt-2 max-w-2xl font-body text-body text-ink-muted">
        Every client workspace: who has access, how much of their own data is connected, and how much
        of the product they have actually used.
      </p>
      <div className="mt-8">
        <Table>
          <thead>
            <tr>
              <Th>Organization</Th>
              <Th>Users</Th>
              <Th>Opportunities</Th>
              <Th>Decisions</Th>
              <Th>Trial runs</Th>
              <Th>Created</Th>
            </tr>
          </thead>
          <tbody>
            {rows.map((row) => (
              <tr key={row.id}>
                <Td>
                  <Link className="font-body font-semibold text-navy underline-offset-2 hover:underline" href={`/admin/organizations/${row.id}`}>
                    {row.name}
                  </Link>
                  <span className="ml-2 font-mono text-data text-ink-muted">{row.slug}</span>
                </Td>
                <Td mono>{row.user_count}</Td>
                <Td mono>{row.opportunity_count}</Td>
                <Td mono>{row.decision_count}</Td>
                <Td mono>{row.trial_run_count}</Td>
                <Td mono>{new Date(row.created_at).toLocaleDateString()}</Td>
              </tr>
            ))}
          </tbody>
        </Table>
        {rows.length === 0 ? <p className="mt-6 font-body text-body text-ink-muted">No organizations yet.</p> : null}
      </div>
    </div>
  );
}
