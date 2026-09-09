"use client";

import { ExplorerLoadState } from "@/app/components/explorer/ExplorerLoadState";
import { PipelineMonitorView } from "@/app/components/explorer/PipelineMonitorView";
import { explorerRoot } from "@/app/components/explorer/paths";
import { useBusinessDeepAudit, usePipelineMonitor, useSession } from "@/lib/application";
import { useParams } from "next/navigation";

export default function PipelineMonitorPage() {
  const { pipelineId, businessId } = useParams<{ pipelineId: string; businessId?: string }>();
  const query = usePipelineMonitor(pipelineId, businessId);
  const deepAudit = useBusinessDeepAudit();
  const { user } = useSession();
  const root = explorerRoot(Boolean(businessId));
  if (query.isPending || query.isError || !query.data) {
    return (
      <ExplorerLoadState
        breadcrumbs={[{ label: root.label, href: root.href }, { label: "Pipeline Monitor" }]}
        title="Pipeline Monitor"
        pending={query.isPending}
        error={query.isError || !query.data}
        errorBody="Pipeline Monitor could not be loaded."
        onRetry={() => void query.refetch()}
      />
    );
  }
  return (
    <PipelineMonitorView
      monitor={query.data}
      businessId={businessId}
      user={user}
      deepAuditPending={deepAudit.isPending}
      deepAuditError={deepAudit.isError}
      onDeepAudit={(runId) =>
        deepAudit.mutate({ businessId, runId }, { onSuccess: () => void query.refetch() })
      }
    />
  );
}
