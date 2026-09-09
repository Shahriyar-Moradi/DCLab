"use client";

import { ExplorerLoadState } from "@/app/components/explorer/ExplorerLoadState";
import { WorkflowRunExplorer } from "@/app/components/explorer/WorkflowRunExplorer";
import { explorerBase, explorerRoot } from "@/app/components/explorer/paths";
import { usePlatformWorkflowRun } from "@/lib/application";
import { useParams, usePathname } from "next/navigation";

export default function WorkflowRunPage() {
  const { businessId, runId } = useParams<{ businessId: string; runId: string }>();
  const businessMode = usePathname().startsWith("/business/");
  const query = usePlatformWorkflowRun(businessId, runId, businessMode);
  const root = explorerRoot(businessMode);
  if (query.isPending || query.isError || !query.data) {
    return (
      <ExplorerLoadState
        breadcrumbs={[
          { label: root.label, href: root.href },
          { label: "Workspace", href: explorerBase(businessId, businessMode) },
          { label: "Workflow run" },
        ]}
        title="Workflow run"
        pending={query.isPending}
        error={query.isError || !query.data}
        errorBody="Workflow run not found."
        onRetry={() => void query.refetch()}
      />
    );
  }
  return <WorkflowRunExplorer run={query.data} businessId={businessId} businessMode={businessMode} />;
}
