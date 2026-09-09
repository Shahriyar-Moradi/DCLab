"use client";

import { formatWhen } from "@/app/components/admin/format";
import { buttonClassName } from "@/app/components/ui/Button";
import { Fact, FactGrid, Panel } from "@/app/components/ui/Card";
import { CollectionSearch } from "@/app/components/ui/CollectionSearch";
import { DataTable } from "@/app/components/ui/DataTable";
import { ErrorState } from "@/app/components/ui/ErrorState";
import { MetricCard } from "@/app/components/ui/MetricCard";
import { PageHeader } from "@/app/components/ui/PageHeader";
import { Skeleton } from "@/app/components/ui/Skeleton";
import { StatusBadge } from "@/app/components/ui/StatusBadge";
import { filterByText } from "@/app/components/ui/localCollection";
import { useAdminOrganization } from "@/lib/application";
import Link from "next/link";
import { useParams } from "next/navigation";
import { useState } from "react";

export default function OrganizationDetailPage() {
  const params = useParams<{ id: string }>();
  const query = useAdminOrganization(params.id);
  const [userQuery, setUserQuery] = useState("");

  if (query.isPending) return <Skeleton className="h-64" />;
  if (query.isError || !query.data) {
    return <ErrorState body="Organization not found." onRetry={() => void query.refetch()} />;
  }

  const org = query.data;
  const users = filterByText(org.users, userQuery, (user) => [user.email, user.full_name, user.role]);

  return (
    <div>
      <PageHeader
        breadcrumbs={[{ label: "Organizations", href: "/admin/organizations" }, { label: org.name }]}
        eyebrow="Organization"
        title={org.name}
        identifier={org.id}
        description={`${org.slug} · created ${formatWhen(org.created_at) || org.created_at}`}
        actions={
          <Link href={`/admin/businesses/${org.id}`} className={buttonClassName({ variant: "secondary" })}>
            Open in explorer
          </Link>
        }
      />

      <div className="mt-8 grid gap-4 md:grid-cols-4">
        <MetricCard label="Users" value={String(org.user_count)} />
        <MetricCard label="Opportunities" value={String(org.opportunity_count)} />
        <MetricCard label="Decisions" value={String(org.decision_count)} />
        <MetricCard label="Trial runs" value={String(org.trial_run_count)} />
      </div>

      <Panel className="mt-8">
        <FactGrid>
          <Fact label="Slug" value={org.slug} mono />
          <Fact label="Created" value={formatWhen(org.created_at) || org.created_at} mono />
        </FactGrid>
      </Panel>

      <Panel className="mt-6" title="Users">
        <CollectionSearch value={userQuery} onChange={setUserQuery} shown={users.length} total={org.users.length} />
        <DataTable
          columns={[
            { id: "email", header: "Email", mono: true, cell: (user) => user.email },
            { id: "name", header: "Name", cell: (user) => user.full_name || "—" },
            { id: "role", header: "Role", cell: (user) => user.role },
            {
              id: "status",
              header: "Status",
              cell: (user) => <StatusBadge status={user.is_active ? "Active" : "Disabled"} />,
            },
            { id: "created", header: "Created", mono: true, cell: (user) => formatWhen(user.created_at) || "—" },
          ]}
          rows={users}
          rowKey={(user) => user.id}
          emptyTitle="No users yet."
          emptyBody={
            userQuery.trim()
              ? "Nothing on this list matches that filter."
              : "Users appear after they are members of this workspace."
          }
        />
      </Panel>
    </div>
  );
}
