"use client";

import { DomainExplorer } from "@/app/components/explorer/DomainExplorer";
import { ExplorerLoadState } from "@/app/components/explorer/ExplorerLoadState";
import { explorerBase, explorerRoot } from "@/app/components/explorer/paths";
import { usePlatformDomain } from "@/lib/application";
import { useParams, usePathname } from "next/navigation";

export default function DomainPage() {
  const { businessId, domainId } = useParams<{ businessId: string; domainId: string }>();
  const businessMode = usePathname().startsWith("/business/");
  const query = usePlatformDomain(businessId, domainId, businessMode);
  const root = explorerRoot(businessMode);
  if (query.isPending || query.isError || !query.data) {
    return (
      <ExplorerLoadState
        breadcrumbs={[
          { label: root.label, href: root.href },
          { label: "Workspace", href: explorerBase(businessId, businessMode) },
          { label: "Domain" },
        ]}
        title="Domain"
        pending={query.isPending}
        error={query.isError || !query.data}
        errorBody="Domain not found."
        onRetry={() => void query.refetch()}
      />
    );
  }
  return <DomainExplorer domain={query.data} businessId={businessId} businessMode={businessMode} />;
}
