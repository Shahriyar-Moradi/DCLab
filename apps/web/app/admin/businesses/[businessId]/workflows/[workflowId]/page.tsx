"use client";

import { ExplorerLoadState } from "@/app/components/explorer/ExplorerLoadState";
import { WorkflowExplorer } from "@/app/components/explorer/WorkflowExplorer";
import { explorerBase, explorerRoot } from "@/app/components/explorer/paths";
import { usePlatformWorkflow } from "@/lib/application";
import { useParams, usePathname } from "next/navigation";

export default function WorkflowPage() {
  const { businessId, workflowId } = useParams<{ businessId: string; workflowId: string }>();
  const businessMode = usePathname().startsWith("/business/");
  const query = usePlatformWorkflow(businessId, workflowId, businessMode);
  const root = explorerRoot(businessMode);
  if (query.isPending || query.isError || !query.data) {
    return (
      <ExplorerLoadState
        breadcrumbs={[
          { label: root.label, href: root.href },
          { label: "Workspace", href: explorerBase(businessId, businessMode) },
          { label: "Workflow" },
        ]}
        title="Workflow"
        pending={query.isPending}
        error={query.isError || !query.data}
        errorBody="Workflow not found."
        onRetry={() => void query.refetch()}
      />
    );
  }
  return <WorkflowExplorer workflow={query.data} businessId={businessId} businessMode={businessMode} />;
}
