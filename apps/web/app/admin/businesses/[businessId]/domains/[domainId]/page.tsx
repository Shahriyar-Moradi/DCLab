"use client";

import { ErrorState } from "@/app/components/ui/ErrorState";
import { Skeleton } from "@/app/components/ui/Skeleton";
import { Table, Td, Th } from "@/app/components/ui/Table";
import { usePlatformDomain } from "@/lib/application";
import Link from "next/link";
import { useParams, usePathname } from "next/navigation";

export default function DomainPage() {
  const { businessId, domainId } = useParams<{ businessId: string; domainId: string }>();
  const businessMode = usePathname().startsWith("/business/");
  const query = usePlatformDomain(businessId, domainId, businessMode);
  if (query.isPending) return <Skeleton className="h-80" />;
  if (query.isError || !query.data) return <ErrorState body="Domain not found." onRetry={() => void query.refetch()} />;
  const domain = query.data;
  const root = businessMode ? "/business" : "/admin/businesses";
  const base = businessMode ? `/business/workspaces/${businessId}` : `/admin/businesses/${businessId}`;
  return <div>
    <p className="text-eyebrow uppercase tracking-[0.08em] text-ink-muted"><Link href={root}>{businessMode ? "Business administration" : "Businesses"}</Link> → <Link href={base}>{domain.business_name}</Link> → {domain.name}</p>
    <h1 className="mt-3 font-display text-title">{domain.name}</h1>
    <p className="mt-2 max-w-3xl text-body text-ink-muted">{domain.description || "Configurable business domain"}</p>
    <div className="mt-8 grid gap-4 md:grid-cols-2"><Metric label="Workflows" value={domain.workflow_count} /><Metric label="Runs" value={domain.run_count} /></div>
    <h2 className="mb-4 mt-12 font-display text-section">Workflows</h2>
    <Table><thead><tr><Th>Workflow</Th><Th>Objective</Th><Th>Status</Th><Th>Runs</Th></tr></thead><tbody>{domain.workflows.map((row) => <tr key={row.id}><Td><Link className="font-semibold text-navy hover:underline" href={`${base}/workflows/${row.id}`}>{row.name}</Link></Td><Td>{row.business_objective || row.description || "—"}</Td><Td>{row.status}</Td><Td mono>{row.run_count}</Td></tr>)}</tbody></Table>
    <h2 className="mb-4 mt-12 font-display text-section">Recent runs</h2>
    <Table><thead><tr><Th>Workflow</Th><Th>Run</Th><Th>Status</Th><Th>Pipelines</Th></tr></thead><tbody>{domain.runs.map((row) => <tr key={row.id}><Td>{row.workflow_name}</Td><Td><Link className="font-mono text-data text-navy hover:underline" href={`${base}/workflow-runs/${row.id}`}>{row.id}</Link></Td><Td>{row.status}</Td><Td mono>{row.pipeline_count}</Td></tr>)}</tbody></Table>
  </div>;
}
function Metric({ label, value }: { label: string; value: number }) { return <div className="rounded bg-paper-raised p-6"><p className="text-eyebrow uppercase text-ink-muted">{label}</p><p className="mt-2 font-mono text-title">{value}</p></div>; }
