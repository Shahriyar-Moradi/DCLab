"use client";

import { ExplorerLoadState } from "@/app/components/explorer/ExplorerLoadState";
import { WorkspaceExplorer } from "@/app/components/explorer/WorkspaceExplorer";
import { explorerRoot } from "@/app/components/explorer/paths";
import { usePlatformBusiness } from "@/lib/application";
import { useParams, usePathname } from "next/navigation";

export default function BusinessPage() {
  const { businessId } = useParams<{ businessId: string }>();
  const businessMode = usePathname().startsWith("/business/");
  const query = usePlatformBusiness(businessId, businessMode);
  const root = explorerRoot(businessMode);
  if (query.isPending || query.isError || !query.data) {
    return (
      <ExplorerLoadState
        breadcrumbs={[{ label: root.label, href: root.href }, { label: "Workspace" }]}
        title="Workspace"
        pending={query.isPending}
        error={query.isError || !query.data}
        errorBody="Business not found."
        onRetry={() => void query.refetch()}
      />
    );
  }
  return <WorkspaceExplorer business={query.data} businessMode={businessMode} />;
}
