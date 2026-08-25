"use client";

import { DecisionLedgerEntry } from "@/app/components/decisions/DecisionLedgerEntry";
import { ErrorState } from "@/app/components/ui/ErrorState";
import { Skeleton } from "@/app/components/ui/Skeleton";
import { WorkspaceShell } from "@/app/components/workspace/PageIntro";
import { useDecision } from "@/lib/application";
import { decisionToView } from "@/lib/domain";
import Link from "next/link";
import { useParams } from "next/navigation";

export default function DecisionDetailPage() {
  const params = useParams<{ id: string }>();
  const query = useDecision(params.id);

  if (query.isPending) {
    return (
      <WorkspaceShell>
        <Skeleton className="h-96" />
      </WorkspaceShell>
    );
  }
  if (query.isError || !query.data) {
    return (
      <WorkspaceShell>
        <ErrorState title="Decision not found" body="That ledger entry is not in the database." onRetry={() => void query.refetch()} />
      </WorkspaceShell>
    );
  }
  const view = decisionToView(query.data);

  return (
    <WorkspaceShell>
      <div className="mx-auto max-w-xl">
      <p className="font-body text-eyebrow uppercase tracking-[0.06em] text-brand">Decision detail</p>
      <h1 className="mt-2 font-display text-title text-ink">Why this action</h1>
      <div className="mt-8">
        <DecisionLedgerEntry decision={view} variant="full" />
      </div>
      <p className="mt-6 font-body text-body">
        <Link className="text-brand underline-offset-2 hover:underline" href={`/opportunities/${view.opportunityExternalId}`}>
          Source opportunity {view.opportunityExternalId}
        </Link>
      </p>
      </div>
    </WorkspaceShell>
  );
}
