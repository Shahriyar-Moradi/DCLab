"use client";

import { ErrorState } from "@/app/components/ui/ErrorState";
import { Skeleton } from "@/app/components/ui/Skeleton";
import { Table, Td, Th } from "@/app/components/ui/Table";
import { usePlatformWorkflow } from "@/lib/application";
import Link from "next/link";
import { useParams, usePathname } from "next/navigation";

export default function WorkflowPage() {
  const { businessId, workflowId } = useParams<{ businessId: string; workflowId: string }>();
  const businessMode = usePathname().startsWith("/business/");
  const query = usePlatformWorkflow(businessId, workflowId, businessMode);
  if (query.isPending) return <Skeleton className="h-80" />;
  if (query.isError || !query.data) return <ErrorState body="Workflow not found." onRetry={() => void query.refetch()} />;
  const workflow = query.data;
  const root = businessMode ? "/business" : "/admin/businesses";
  const base = businessMode ? `/business/workspaces/${businessId}` : `/admin/businesses/${businessId}`;
  return <div>
    <p className="text-eyebrow uppercase tracking-[0.08em] text-ink-muted"><Link href={root}>{businessMode ? "Business administration" : "Businesses"}</Link> → <Link href={base}>{workflow.business_name}</Link> → {workflow.domain_name} → Workflow</p>
    <h1 className="mt-3 font-display text-title">{workflow.name}</h1>
    <p className="mt-2 max-w-3xl text-body text-ink-muted">{workflow.business_objective || workflow.description || "No objective recorded."}</p>
    <div className="mt-8 grid gap-4 md:grid-cols-3"><Metric label="Domain" value={workflow.domain_name} /><Metric label="Status" value={workflow.status} /><Metric label="Runs / models" value={`${workflow.run_count} / ${workflow.model_count}`} /></div>
    <h2 className="mb-4 mt-12 font-display text-section">Workflow runs</h2>
    <Table><thead><tr><Th>Invocation</Th><Th>Source</Th><Th>Target / task</Th><Th>Status</Th><Th>Pipelines</Th></tr></thead><tbody>{workflow.runs.map((run) => <tr key={run.id}><Td><Link className="font-mono text-data text-navy hover:underline" href={`${base}/workflow-runs/${run.id}`}>{run.id}</Link></Td><Td>{run.source_filename ?? run.source_type}</Td><Td>{run.resolved_target ?? run.explicit_target ?? "—"} · {run.task_type ?? "—"}</Td><Td>{run.status}</Td><Td mono>{run.pipeline_count}</Td></tr>)}</tbody></Table>
  </div>;
}
function Metric({ label, value }: { label: string; value: string }) { return <div className="rounded bg-paper-raised p-6"><p className="text-eyebrow uppercase text-ink-muted">{label}</p><p className="mt-2 font-mono text-data">{value}</p></div>; }
