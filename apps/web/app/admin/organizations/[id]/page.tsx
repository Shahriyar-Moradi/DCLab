"use client";

import { ErrorState } from "@/app/components/ui/ErrorState";
import { Skeleton } from "@/app/components/ui/Skeleton";
import { Table, Td, Th } from "@/app/components/ui/Table";
import { useAdminOrganization } from "@/lib/application";
import { useParams } from "next/navigation";

export default function OrganizationDetailPage() {
  const params = useParams<{ id: string }>();
  const query = useAdminOrganization(params.id);

  if (query.isPending) return <Skeleton className="h-64" />;
  if (query.isError || !query.data) {
    return <ErrorState body="Organization not found." onRetry={() => void query.refetch()} />;
  }

  const org = query.data;

  return (
    <div>
      <p className="font-body text-eyebrow uppercase tracking-[0.06em] text-ink-muted">DCLab Admin · Organization</p>
      <h1 className="mt-2 font-display text-title text-ink">{org.name}</h1>
      <p className="mt-2 font-mono text-data text-ink-muted">
        {org.slug} · created {new Date(org.created_at).toLocaleDateString()}
      </p>

      <div className="mt-8 grid gap-4 md:grid-cols-4">
        <Stat label="Users" value={String(org.user_count)} />
        <Stat label="Opportunities" value={String(org.opportunity_count)} />
        <Stat label="Decisions" value={String(org.decision_count)} />
        <Stat label="Trial runs" value={String(org.trial_run_count)} />
      </div>

      <h2 className="mt-12 font-display text-section text-ink">Users</h2>
      <div className="mt-4">
        <Table>
          <thead>
            <tr>
              <Th>Email</Th>
              <Th>Name</Th>
              <Th>Role</Th>
              <Th>Status</Th>
              <Th>Created</Th>
            </tr>
          </thead>
          <tbody>
            {org.users.map((user) => (
              <tr key={user.id}>
                <Td mono>{user.email}</Td>
                <Td>{user.full_name || "—"}</Td>
                <Td>{user.role}</Td>
                <Td>{user.is_active ? "Active" : "Disabled"}</Td>
                <Td mono>{new Date(user.created_at).toLocaleDateString()}</Td>
              </tr>
            ))}
          </tbody>
        </Table>
        {org.users.length === 0 ? <p className="mt-6 font-body text-body text-ink-muted">No users yet.</p> : null}
      </div>
    </div>
  );
}

function Stat({ label, value }: { label: string; value: string }) {
  return (
    <div className="rounded bg-paper-raised p-6">
      <p className="font-body text-eyebrow uppercase tracking-[0.06em] text-ink-muted">{label}</p>
      <p className="mt-2 font-mono text-title text-ink">{value}</p>
    </div>
  );
}
