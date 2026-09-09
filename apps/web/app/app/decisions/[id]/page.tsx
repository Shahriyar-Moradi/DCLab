"use client";

import { DecisionLedgerEntry } from "@/app/components/decisions/DecisionLedgerEntry";
import { ErrorState } from "@/app/components/ui/ErrorState";
import { PageHeader } from "@/app/components/ui/PageHeader";
import { Skeleton } from "@/app/components/ui/Skeleton";
import { useDecision } from "@/lib/application";
import { actionLabel, decisionToView, toneFromConfidenceBand } from "@/lib/domain";
import Link from "next/link";
import { useParams } from "next/navigation";

export default function DecisionDetailPage() {
  const params = useParams<{ id: string }>();
  const query = useDecision(params.id);

  if (query.isPending) {
    return (
      <div>
        <PageHeader
          breadcrumbs={[{ label: "Decisions", href: "/app/decisions" }, { label: "Decision" }]}
          title="Decision"
        />
        <Skeleton className="h-96" />
      </div>
    );
  }
  if (query.isError || !query.data) {
    return (
      <div>
        <PageHeader
          breadcrumbs={[{ label: "Decisions", href: "/app/decisions" }, { label: "Decision" }]}
          title="Decision"
        />
        <ErrorState title="Decision not found" body="That ledger entry is not in the database." onRetry={() => void query.refetch()} />
      </div>
    );
  }
  const view = decisionToView(query.data);

  return (
    <div>
      <PageHeader
        eyebrow="Decision"
        title={actionLabel(view.recommendedAction)}
        description={`Opportunity ${view.opportunityExternalId}`}
        breadcrumbs={[
          { label: "Decisions", href: "/app/decisions" },
          { label: view.opportunityExternalId },
        ]}
        status={{ label: `${view.confidenceBand} confidence`, tone: toneFromConfidenceBand(view.confidenceBand) }}
      />
      <DecisionLedgerEntry decision={view} variant="full" />
      <p className="mt-6 text-body">
        <Link className="text-navy underline-offset-2 hover:underline" href={`/app/opportunities/${view.opportunityExternalId}`}>
          Source opportunity {view.opportunityExternalId}
        </Link>
      </p>
    </div>
  );
}
