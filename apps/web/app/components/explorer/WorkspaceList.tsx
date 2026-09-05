"use client";

import { Badge } from "@/app/components/ui/Badge";
import { CollectionSearch } from "@/app/components/ui/CollectionSearch";
import { DataTable } from "@/app/components/ui/DataTable";
import { EmptyState } from "@/app/components/ui/EmptyState";
import { PageHeader } from "@/app/components/ui/PageHeader";
import { filterByText } from "@/app/components/ui/localCollection";
import type { BusinessWorkspaceSummary, PlatformBusinessSummary } from "@/lib/domain";
import Link from "next/link";
import { useState } from "react";
import { explorerBase } from "./paths";

export function PlatformBusinessList({ rows }: { rows: PlatformBusinessSummary[] }) {
  const [query, setQuery] = useState("");
  const filtered = filterByText(rows, query, (row) => [row.name, row.slug, row.industry, row.legal_name]);
  return (
    <div>
      <PageHeader
        eyebrow="Platform administration"
        title="Businesses"
        description="Explore every tenant from business context through domains, workflows, runs, pipelines, and managed model versions."
      />
      {rows.length === 0 ? (
        <EmptyState title="No businesses" body="No workspaces are registered on this platform." />
      ) : (
        <>
          <CollectionSearch value={query} onChange={setQuery} shown={filtered.length} total={rows.length} />
          <DataTable
            columns={[
              {
                id: "business",
                header: "Business",
                cell: (row) => (
                  <div>
                    <Link className="font-semibold text-navy hover:underline" href={explorerBase(row.id, false)}>
                      {row.name}
                    </Link>
                    <p className="font-mono text-data text-ink-muted">
                      {row.slug}
                      {row.industry ? ` · ${row.industry}` : ""}
                    </p>
                  </div>
                ),
              },
              { id: "domains", header: "Domains", mono: true, cell: (row) => String(row.domain_count) },
              { id: "workflows", header: "Workflows", mono: true, cell: (row) => String(row.workflow_count) },
              { id: "runs", header: "Runs", mono: true, cell: (row) => String(row.run_count) },
              { id: "pipelines", header: "Pipelines", mono: true, cell: (row) => String(row.pipeline_count) },
              { id: "models", header: "Models", mono: true, cell: (row) => String(row.model_count) },
              { id: "members", header: "Members", mono: true, cell: (row) => String(row.membership_count) },
            ]}
            rows={filtered}
            rowKey={(row) => row.id}
            emptyTitle="No matching businesses"
            emptyBody="Nothing on this list matches that filter."
          />
        </>
      )}
    </div>
  );
}

export function TenantWorkspaceList({ rows }: { rows: BusinessWorkspaceSummary[] }) {
  const [query, setQuery] = useState("");
  const filtered = filterByText(rows, query, (row) => [row.name, row.slug, row.role]);
  return (
    <div>
      <PageHeader
        eyebrow="Tenant administration"
        title="Business administration"
        description="Open an authorized workspace to inspect its enabled domains, workflows, runs, models, and capability-controlled pipeline detail."
      />
      {rows.length === 0 ? (
        <EmptyState title="No authorized businesses" body="This account is not a member of a business workspace." />
      ) : (
        <>
          <CollectionSearch value={query} onChange={setQuery} shown={filtered.length} total={rows.length} />
          <DataTable
            columns={[
              {
                id: "business",
                header: "Business",
                cell: (row) => (
                  <div>
                    <Link className="font-semibold text-navy hover:underline" href={explorerBase(row.id, true)}>
                      {row.name}
                    </Link>
                    <p className="font-mono text-data text-ink-muted">{row.slug}</p>
                  </div>
                ),
              },
              { id: "role", header: "Role", cell: (row) => row.role },
              { id: "domains", header: "Domains", mono: true, cell: (row) => String(row.domain_count) },
              { id: "workflows", header: "Workflows", mono: true, cell: (row) => String(row.workflow_count) },
              { id: "runs", header: "Runs", mono: true, cell: (row) => String(row.run_count) },
              { id: "models", header: "Models", mono: true, cell: (row) => String(row.model_count) },
              {
                id: "access",
                header: "Access",
                cell: (row) => (
                  <Badge tone={row.can_write ? "green" : "amber"}>{row.can_write ? "Read / write" : "Read only"}</Badge>
                ),
              },
            ]}
            rows={filtered}
            rowKey={(row) => row.id}
            emptyTitle="No matching businesses"
            emptyBody="Nothing on this list matches that filter."
          />
        </>
      )}
    </div>
  );
}
