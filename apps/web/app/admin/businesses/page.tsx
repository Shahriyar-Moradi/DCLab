"use client";

import { ExplorerLoadState } from "@/app/components/explorer/ExplorerLoadState";
import { PlatformBusinessList } from "@/app/components/explorer/WorkspaceList";
import { usePlatformBusinesses } from "@/lib/application";

export default function BusinessesPage() {
  const query = usePlatformBusinesses();
  const loading = (
    <ExplorerLoadState
      breadcrumbs={[{ label: "Businesses" }]}
      title="Businesses"
      pending={query.isPending}
      error={query.isError}
      errorBody="Could not load businesses."
      onRetry={() => void query.refetch()}
    />
  );
  if (query.isPending || query.isError) return loading;
  return <PlatformBusinessList rows={query.data ?? []} />;
}
