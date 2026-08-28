"use client";

import { ErrorState } from "@/app/components/ui/ErrorState";
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
      <p className="font-body text-eyebrow uppercase tracking-[0.06em] text-ink-muted">
        DCLab Admin · Client-triggered trial
      </p>
      <h1 className="mt-2 font-display text-title text-ink">{audit.use_case}</h1>
      <p className="mt-2 font-mono text-data text-ink-muted">
        client_lab_run_id: {audit.client_lab_run_id} · {new Date(audit.created_at).toLocaleString()}
      </p>
      <p className="mt-4 max-w-2xl font-body text-body text-ink-muted">
        Full, unrestricted output of the training run this client&rsquo;s trial request triggered. The
        client only ever saw the translated insights derived from this data.
      </p>
      <h2 className="mt-8 font-display text-section text-ink">Raw payload</h2>
      <pre className="mt-4 max-h-[70vh] overflow-auto rounded bg-paper-raised p-4 font-mono text-data text-ink">
        {JSON.stringify(audit.payload, null, 2)}
      </pre>
    </div>
  );
}
