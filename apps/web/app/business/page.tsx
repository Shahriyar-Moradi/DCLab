"use client";

import { Badge } from "@/app/components/ui/Badge";
import { ErrorState } from "@/app/components/ui/ErrorState";
import { Skeleton } from "@/app/components/ui/Skeleton";
import { Table, Td, Th } from "@/app/components/ui/Table";
import { useBusinessWorkspaces } from "@/lib/application";
import Link from "next/link";

export default function BusinessAdministrationPage() {
  const query = useBusinessWorkspaces();
  if (query.isPending) return <Skeleton className="h-72" />;
  if (query.isError) return <ErrorState body="Could not load your authorized businesses." onRetry={() => void query.refetch()} />;
  return <div>
    <p className="text-eyebrow uppercase tracking-[0.08em] text-ink-muted">Tenant administration</p>
    <h1 className="mt-2 font-display text-title">Business administration</h1>
    <p className="mt-2 max-w-3xl text-body text-ink-muted">Open an authorized workspace to inspect its enabled domains, workflows, runs, models, and capability-controlled pipeline detail.</p>
    <div className="mt-8 rounded bg-paper-raised p-4"><Table><thead><tr><Th>Business</Th><Th>Role</Th><Th>Domains</Th><Th>Workflows</Th><Th>Runs</Th><Th>Models</Th><Th>Access</Th></tr></thead><tbody>{(query.data ?? []).map((row) => <tr key={row.id}><Td><Link className="font-semibold text-navy hover:underline" href={`/business/workspaces/${row.id}`}>{row.name}</Link><p className="font-mono text-data text-ink-muted">{row.slug}</p></Td><Td>{row.role}</Td><Td mono>{row.domain_count}</Td><Td mono>{row.workflow_count}</Td><Td mono>{row.run_count}</Td><Td mono>{row.model_count}</Td><Td><Badge tone={row.can_write ? "green" : "amber"}>{row.can_write ? "Read / write" : "Read only"}</Badge></Td></tr>)}</tbody></Table></div>
  </div>;
}
