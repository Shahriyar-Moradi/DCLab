"use client";

import { Badge } from "@/app/components/ui/Badge";
import { ErrorState } from "@/app/components/ui/ErrorState";
import { Skeleton } from "@/app/components/ui/Skeleton";
import { Table, Td, Th } from "@/app/components/ui/Table";
import { usePlatformWorkflowRun } from "@/lib/application";
import Link from "next/link";
import { useParams, usePathname } from "next/navigation";

export default function WorkflowRunPage() {
  const { businessId, runId } = useParams<{ businessId: string; runId: string }>();
  const businessMode = usePathname().startsWith("/business/");
  const query = usePlatformWorkflowRun(businessId, runId, businessMode);
  if (query.isPending) return <Skeleton className="h-80" />;
  if (query.isError || !query.data) return <ErrorState body="Workflow run not found." onRetry={() => void query.refetch()} />;
  const run = query.data;
  const root = businessMode ? "/business" : "/admin/businesses";
  const base = businessMode ? `/business/workspaces/${businessId}` : `/admin/businesses/${businessId}`;
  const canMonitor = !businessMode || ("capabilities" in run && run.capabilities.pipeline_monitor === true);
  return <div>
    <p className="text-eyebrow uppercase tracking-[0.08em] text-ink-muted"><Link href={root}>{businessMode ? "Business administration" : "Businesses"}</Link> → <Link href={base}>{run.business_name}</Link> → {run.domain_name} → {run.workflow_name} → Workflow Run</p>
    <div className="mt-3 flex flex-wrap items-center gap-3"><h1 className="font-display text-title">Workflow Run</h1><Badge tone={run.status === "completed" ? "green" : run.status === "failed" ? "oxblood" : "amber"}>{run.status}</Badge></div>
    <p className="mt-2 font-mono text-data text-ink-muted">{run.id}</p>
    <div className="mt-8 grid gap-4 md:grid-cols-4"><Metric label="Source" value={run.source_filename ?? run.source_type} /><Metric label="Target" value={run.resolved_target ?? run.explicit_target ?? "—"} /><Metric label="Task" value={run.task_type ?? "—"} /><Metric label="Pipelines" value={String(run.pipeline_count)} /></div>
    {run.failure_reason ? <p className="mt-6 rounded border border-oxblood/30 bg-oxblood/5 p-4 text-body text-oxblood">{run.failure_reason}</p> : null}
    <h2 className="mb-2 mt-12 font-display text-section">Pipeline Runs</h2><p className="mb-4 text-body text-ink-muted">Each pipeline is an independent technical run. All pipelines are rendered; none are collapsed into the workflow invocation.</p>
    <Table><thead><tr><Th>#</Th><Th>Pipeline</Th><Th>Purpose</Th><Th>Status</Th><Th>Candidates</Th><Th>Selected model</Th><Th>Monitor</Th></tr></thead><tbody>{run.pipelines.map((pipeline) => <tr key={pipeline.id}><Td mono>{pipeline.pipeline_index + 1}</Td><Td><span className="font-semibold">{pipeline.pipeline_name}</span><p className="font-mono text-data text-ink-muted">{pipeline.id}</p></Td><Td>{pipeline.pipeline_purpose}</Td><Td>{pipeline.status}</Td><Td mono>{pipeline.candidate_count}</Td><Td>{pipeline.model_asset_id ? <Link className="text-navy hover:underline" href={`${base}/models/${pipeline.model_asset_id}`}>{pipeline.model_name} {pipeline.model_version}</Link> : "No successful version"}</Td><Td>{canMonitor ? <Link className="font-semibold text-navy hover:underline" href={businessMode ? `${base}/pipeline-runs/${pipeline.id}/monitor` : `/admin/pipeline-runs/${pipeline.id}/monitor`}>Open monitor</Link> : <span className="text-ink-muted">Not enabled</span>}</Td></tr>)}</tbody></Table>
  </div>;
}
function Metric({ label, value }: { label: string; value: string }) { return <div className="rounded bg-paper-raised p-5"><p className="text-eyebrow uppercase text-ink-muted">{label}</p><p className="mt-2 break-all font-mono text-data">{value}</p></div>; }
