"use client";

import { ExplorerLoadState } from "@/app/components/explorer/ExplorerLoadState";
import { TenantWorkspaceList } from "@/app/components/explorer/WorkspaceList";
import { useBusinessWorkspaces } from "@/lib/application";

export default function BusinessAdministrationPage() {
  const query = useBusinessWorkspaces();
  const loading = (
    <ExplorerLoadState
      breadcrumbs={[{ label: "Business administration" }]}
      title="Business administration"
      pending={query.isPending}
      error={query.isError}
      errorBody="Could not load your authorized businesses."
      onRetry={() => void query.refetch()}
    />
  );
  if (query.isPending || query.isError) return loading;
  return <TenantWorkspaceList rows={query.data ?? []} />;
}
