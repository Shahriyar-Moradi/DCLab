"use client";

import { formatWhen } from "@/app/components/admin/format";
import { Fact, FactGrid, Panel } from "@/app/components/ui/Card";
import { ErrorState } from "@/app/components/ui/ErrorState";
import { PageHeader } from "@/app/components/ui/PageHeader";
import { Skeleton } from "@/app/components/ui/Skeleton";
import { useAdminClientTrialAudit } from "@/lib/application";
import { useParams } from "next/navigation";

export default function ClientTrialAuditPage() {
  const params = useParams<{ id: string }>();
  const query = useAdminClientTrialAudit(params.id);

  if (query.isPending) return <Skeleton className="h-64" />;
  if (query.isError || !query.data) {
    return <ErrorState body="Client trial audit not found." onRetry={() => void query.refetch()} />;
  }

  const audit = query.data;

  return (
    <div>
      <PageHeader
        breadcrumbs={[
          { label: "Model registry", href: "/admin/models" },
          { label: audit.use_case },
        ]}
        eyebrow="Client-triggered trial"
        title={audit.use_case}
        identifier={audit.id}
        description="Full, unrestricted output of the training run this client’s trial request triggered. The client only ever saw the translated insights derived from this data."
      />
      <Panel className="mt-8">
        <FactGrid>
          <Fact label="Use case" value={audit.use_case} />
          <Fact label="Client lab run" value={audit.client_lab_run_id} mono />
          <Fact label="Created" value={formatWhen(audit.created_at) || audit.created_at} mono />
        </FactGrid>
      </Panel>
      <Panel className="mt-6" title="Raw payload">
        <pre className="max-h-[70vh] overflow-auto font-mono text-data text-ink">
          {JSON.stringify(audit.payload, null, 2)}
        </pre>
      </Panel>
    </div>
  );
}
