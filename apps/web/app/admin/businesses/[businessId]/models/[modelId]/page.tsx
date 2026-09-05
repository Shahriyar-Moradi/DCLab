"use client";

import { ExplorerLoadState } from "@/app/components/explorer/ExplorerLoadState";
import { ModelExplorer } from "@/app/components/explorer/ModelExplorer";
import { explorerBase, explorerRoot } from "@/app/components/explorer/paths";
import { usePlatformModel } from "@/lib/application";
import { useParams, usePathname } from "next/navigation";

export default function ModelPage() {
  const { businessId, modelId } = useParams<{ businessId: string; modelId: string }>();
  const businessMode = usePathname().startsWith("/business/");
  const query = usePlatformModel(businessId, modelId, businessMode);
  const root = explorerRoot(businessMode);
  if (query.isPending || query.isError || !query.data) {
    return (
      <ExplorerLoadState
        breadcrumbs={[
          { label: root.label, href: root.href },
          { label: "Workspace", href: explorerBase(businessId, businessMode) },
          { label: "Model" },
        ]}
        title="Model"
        pending={query.isPending}
        error={query.isError || !query.data}
        errorBody="Model not found."
        onRetry={() => void query.refetch()}
      />
    );
  }
  return <ModelExplorer model={query.data} businessId={businessId} businessMode={businessMode} />;
}
