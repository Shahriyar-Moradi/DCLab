"use client";

import { ErrorState } from "@/app/components/ui/ErrorState";
import { MetricCard, ProductPageHeader } from "@/app/components/product/ProductPrimitives";
import { Skeleton } from "@/app/components/ui/Skeleton";
import { Table, Td, Th } from "@/app/components/ui/Table";
import { usePlatformModel } from "@/lib/application";
import Link from "next/link";
import { useParams, usePathname } from "next/navigation";

export default function ModelPage() {
  const { businessId, modelId } = useParams<{ businessId: string; modelId: string }>();
  const businessMode = usePathname().startsWith("/business/");
  const query = usePlatformModel(businessId, modelId, businessMode);
  if (query.isPending) return <Skeleton className="h-80" />;
  if (query.isError || !query.data) return <ErrorState body="Model not found." onRetry={() => void query.refetch()} />;
  const model = query.data;
  const root = businessMode ? "/business" : "/admin/businesses";
  const base = businessMode ? `/business/workspaces/${businessId}` : `/admin/businesses/${businessId}`;
  const canMonitor = !businessMode || ("capabilities" in model && model.capabilities.pipeline_monitor === true);
  return <div>
    <ProductPageHeader
      breadcrumbs={[{ label: businessMode ? "Business administration" : "Businesses", href: root }, { label: model.business_name, href: base }, { label: model.domain_name }, { label: model.workflow_name }, { label: "Model" }]}
      title={model.name}
      description={`${model.slug} · ${model.status}`}
    />
    <div className="mb-8 grid gap-3 sm:grid-cols-3"><MetricCard label="Status" value={model.status} /><MetricCard label="Versions" value={String(model.versions.length)} /><MetricCard label="Pipeline monitor" value={canMonitor ? "Available" : "Not enabled"} /></div>
    <h2 className="mb-4 mt-12 font-display text-section">Immutable selected versions</h2>
    <Table><thead><tr><Th>Version</Th><Th>Selected candidate</Th><Th>Workflow run</Th><Th>Pipeline</Th><Th>Created</Th></tr></thead><tbody>{model.versions.map((version) => <tr key={version.id}><Td mono>{version.version}</Td><Td mono>{version.selected_candidate_id}</Td><Td><Link className="font-mono text-data text-navy hover:underline" href={`${base}/workflow-runs/${version.workflow_run_id}`}>{version.workflow_run_id}</Link></Td><Td>{canMonitor ? <Link className="font-semibold text-navy hover:underline" href={businessMode ? `${base}/pipeline-runs/${version.pipeline_run_id}/monitor` : `/admin/pipeline-runs/${version.pipeline_run_id}/monitor`}>Pipeline Monitor</Link> : <span className="text-ink-muted">Not enabled</span>}</Td><Td mono>{new Date(version.created_at).toLocaleString()}</Td></tr>)}</tbody></Table>
  </div>;
}
