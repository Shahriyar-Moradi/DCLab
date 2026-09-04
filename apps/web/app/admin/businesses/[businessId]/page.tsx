"use client";

import { Badge } from "@/app/components/ui/Badge";
import { ErrorState } from "@/app/components/ui/ErrorState";
import { Skeleton } from "@/app/components/ui/Skeleton";
import { Table, Td, Th } from "@/app/components/ui/Table";
import { usePlatformBusiness } from "@/lib/application";
import Link from "next/link";
import { useParams, usePathname } from "next/navigation";

const platformTabs = ["overview", "domains", "workflows", "models", "runs", "memberships"];
const businessTabs = ["overview", "domains", "workflows", "models", "runs"];

export default function BusinessPage() {
  const { businessId } = useParams<{ businessId: string }>();
  const businessMode = usePathname().startsWith("/business/");
  const query = usePlatformBusiness(businessId, businessMode);
  if (query.isPending) return <Skeleton className="h-96" />;
  if (query.isError || !query.data) return <ErrorState body="Business not found." onRetry={() => void query.refetch()} />;
  const business = query.data;
  const root = businessMode ? "/business" : "/admin/businesses";
  const base = businessMode ? `/business/workspaces/${business.id}` : `/admin/businesses/${business.id}`;
  const hideModels = businessMode && "capabilities" in business && business.capabilities.model_management !== true;
  const tabs = (businessMode ? businessTabs : platformTabs).filter((tab) => tab !== "models" || !hideModels);
  return <div>
    <p className="text-eyebrow uppercase tracking-[0.08em] text-ink-muted"><Link href={root}>{businessMode ? "Business administration" : "Businesses"}</Link> → {business.name}</p>
    <h1 className="mt-3 font-display text-title">{business.name}</h1>
    <p className="mt-2 font-mono text-data text-ink-muted">{business.slug} · {business.legal_name ?? "No legal name"} · {business.industry ?? "Industry not set"}</p>
    <nav className="mt-6 flex flex-wrap gap-2" aria-label="Business profile sections">{tabs.map((tab) => <a key={tab} href={`#${tab}`} className="rounded border border-hairline px-3 py-2 text-body capitalize hover:bg-paper-raised">{tab}</a>)}</nav>
    <section id="overview" className="mt-10"><Heading>Overview</Heading><div className="grid gap-3 sm:grid-cols-3 lg:grid-cols-6"><Stat label="Domains" value={business.domain_count} /><Stat label="Workflows" value={business.workflow_count} /><Stat label="Runs" value={business.run_count} /><Stat label="Pipelines" value={business.pipeline_count} /><Stat label="Models" value={business.model_count} /><Stat label="Members" value={business.membership_count} /></div></section>
    {businessMode && "capabilities" in business ? <section className="mt-8 rounded bg-paper-raised p-5"><p className="text-eyebrow uppercase text-ink-muted">Technical capabilities</p><div className="mt-3 flex flex-wrap gap-2">{Object.entries(business.capabilities).map(([key, enabled]) => <Badge key={key} tone={enabled ? "green" : "amber"}>{key}: {enabled ? "enabled" : "disabled"}</Badge>)}</div>{!business.can_write ? <p className="mt-4 text-body text-ink-muted">Read-only business access. Side-effecting actions are disabled and rejected by the API.</p> : null}</section> : null}
    <section id="domains" className="mt-12"><Heading>Domains</Heading><div className="grid gap-4 md:grid-cols-2 xl:grid-cols-3">{business.domains.map((domain) => <Link key={domain.id} href={`${base}/domains/${domain.id}`} className="rounded bg-paper-raised p-5 hover:ring-1 hover:ring-brand"><div className="flex justify-between"><h3 className="font-display text-section">{domain.name}</h3><Badge tone={domain.enabled ? "green" : "amber"}>{domain.enabled ? "Enabled" : "Disabled"}</Badge></div><p className="mt-2 text-body text-ink-muted">{domain.description || "Configurable business domain"}</p><p className="mt-4 font-mono text-data">{domain.workflow_count} workflows · {domain.run_count} runs</p></Link>)}</div></section>
    <section id="workflows" className="mt-12"><Heading>Workflows</Heading><Table><thead><tr><Th>Workflow</Th><Th>Domain</Th><Th>Status</Th><Th>Runs</Th><Th>Models</Th></tr></thead><tbody>{business.workflows.map((row) => <tr key={row.id}><Td><Link className="font-semibold text-navy hover:underline" href={`${base}/workflows/${row.id}`}>{row.name}</Link></Td><Td>{row.domain_name}</Td><Td>{row.status}</Td><Td mono>{row.run_count}</Td><Td mono>{row.model_count}</Td></tr>)}</tbody></Table></section>
    {businessMode && "capabilities" in business && business.capabilities.model_management !== true ? <section id="models" className="mt-12 rounded border border-hairline bg-paper-raised p-5"><p className="font-mono text-data text-ink-muted">model_management is not enabled for this workspace.</p></section> : <section id="models" className="mt-12"><Heading>Models</Heading><Table><thead><tr><Th>Model</Th><Th>Workflow</Th><Th>Status</Th><Th>Versions</Th></tr></thead><tbody>{business.models.map((row) => <tr key={row.id}><Td><Link className="font-semibold text-navy hover:underline" href={`${base}/models/${row.id}`}>{row.name}</Link></Td><Td>{row.workflow_name}</Td><Td>{row.status}</Td><Td mono>{row.versions.length}</Td></tr>)}</tbody></Table></section>}
    <section id="runs" className="mt-12"><Heading>Runs</Heading><Runs base={base} rows={business.runs} /></section>
    {!businessMode ? <section id="memberships" className="mt-12"><Heading>Users / Memberships</Heading><Table><thead><tr><Th>User</Th><Th>Name</Th><Th>Role</Th><Th>Status</Th></tr></thead><tbody>{business.memberships.map((row) => <tr key={row.id}><Td mono>{row.email}</Td><Td>{row.full_name || "—"}</Td><Td>{row.role}</Td><Td>{row.is_active ? "Active" : "Disabled"}</Td></tr>)}</tbody></Table></section> : null}
  </div>;
}

function Runs({ base, rows }: { base: string; rows: Array<{ id: string; workflow_name: string; domain_name: string; status: string; pipeline_count: number; created_at: string }> }) {
  return <Table><thead><tr><Th>Workflow run</Th><Th>Domain</Th><Th>Status</Th><Th>Pipelines</Th><Th>Created</Th></tr></thead><tbody>{rows.map((row) => <tr key={row.id}><Td><Link className="font-semibold text-navy hover:underline" href={`${base}/workflow-runs/${row.id}`}>{row.workflow_name}</Link><p className="font-mono text-data text-ink-muted">{row.id}</p></Td><Td>{row.domain_name}</Td><Td>{row.status}</Td><Td mono>{row.pipeline_count}</Td><Td mono>{new Date(row.created_at).toLocaleString()}</Td></tr>)}</tbody></Table>;
}

function Heading({ children }: { children: React.ReactNode }) { return <h2 className="mb-4 font-display text-section">{children}</h2>; }
function Stat({ label, value }: { label: string; value: number }) { return <div className="rounded bg-paper-raised p-5"><p className="text-eyebrow uppercase text-ink-muted">{label}</p><p className="mt-2 font-mono text-title">{value}</p></div>; }
