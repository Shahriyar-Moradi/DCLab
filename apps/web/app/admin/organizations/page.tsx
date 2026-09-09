"use client";

import { formatWhen } from "@/app/components/admin/format";
import { CollectionSearch } from "@/app/components/ui/CollectionSearch";
import { DataTable } from "@/app/components/ui/DataTable";
import { ErrorState } from "@/app/components/ui/ErrorState";
import { PageHeader } from "@/app/components/ui/PageHeader";
import { Skeleton } from "@/app/components/ui/Skeleton";
import { filterByText } from "@/app/components/ui/localCollection";
import { useAdminOrganizations } from "@/lib/application";
import Link from "next/link";
import { useState } from "react";

export default function OrganizationsPage() {
  const query = useAdminOrganizations();
  const [queryText, setQueryText] = useState("");

  if (query.isPending) {
    return (
      <div>
        <PageHeader
          eyebrow="Administration"
          title="Organizations"
          description="Every client workspace: who has access, how much of their own data is connected, and how much of the product they have actually used."
        />
        <Skeleton className="h-64" />
      </div>
    );
  }
  if (query.isError) {
    return <ErrorState body="Could not load organizations." onRetry={() => void query.refetch()} />;
  }

  const allRows = query.data ?? [];
  const rows = filterByText(allRows, queryText, (row) => [row.name, row.slug]);

  return (
    <div>
      <PageHeader
        eyebrow="Administration"
        title="Organizations"
        description="Every client workspace: who has access, how much of their own data is connected, and how much of the product they have actually used."
      />
      <div className="mt-8">
        <CollectionSearch value={queryText} onChange={setQueryText} shown={rows.length} total={allRows.length} />
        <DataTable
          columns={[
            {
              id: "org",
              header: "Organization",
              cell: (row) => (
                <div>
                  <Link className="font-semibold text-navy hover:underline" href={`/admin/organizations/${row.id}`}>
                    {row.name}
                  </Link>
                  <p className="font-mono text-data text-ink-muted">{row.slug}</p>
                </div>
              ),
            },
            { id: "users", header: "Users", mono: true, cell: (row) => String(row.user_count) },
            { id: "opps", header: "Opportunities", mono: true, cell: (row) => String(row.opportunity_count) },
            { id: "decisions", header: "Decisions", mono: true, cell: (row) => String(row.decision_count) },
            { id: "trials", header: "Trial runs", mono: true, cell: (row) => String(row.trial_run_count) },
            { id: "created", header: "Created", mono: true, cell: (row) => formatWhen(row.created_at) || "—" },
          ]}
          rows={rows}
          rowKey={(row) => row.id}
          emptyTitle="No organizations yet."
          emptyBody={
            queryText.trim()
              ? "Nothing on this list matches that filter."
              : "Organization rows come from registered workspaces."
          }
        />
      </div>
    </div>
  );
}
