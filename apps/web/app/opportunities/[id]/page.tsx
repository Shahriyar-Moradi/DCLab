"use client";

import { DecisionLedgerEntry } from "@/app/components/decisions/DecisionLedgerEntry";
import { Button } from "@/app/components/ui/Button";
import { ErrorState } from "@/app/components/ui/ErrorState";
import { Skeleton } from "@/app/components/ui/Skeleton";
import { WorkspaceShell } from "@/app/components/workspace/PageIntro";
import { useDecisions, useGenerateDecision, useOpportunity } from "@/lib/application";
import { decisionToView, formatMoney, formatTimestamp, generateToView } from "@/lib/domain";
import Link from "next/link";
import { useParams } from "next/navigation";
import { useState } from "react";

export default function OpportunityDetailPage() {
  const params = useParams<{ id: string }>();
  const id = params.id;
  const opportunity = useOpportunity(id);
  const existing = useDecisions({ opportunity_id: id, limit: 1 });
  const generate = useGenerateDecision();
  const [fresh, setFresh] = useState(false);

  if (opportunity.isPending) {
    return (
      <WorkspaceShell>
        <Skeleton className="h-80" />
      </WorkspaceShell>
    );
  }
  if (opportunity.isError || !opportunity.data) {
    return (
      <WorkspaceShell>
        <ErrorState
          title="Opportunity not found"
          body="That ID is not in the database. Check the opportunities list."
          onRetry={() => void opportunity.refetch()}
        />
      </WorkspaceShell>
    );
  }
  const row = opportunity.data;
  const current = generate.data
    ? generateToView(generate.data)
    : existing.data?.items[0]
      ? decisionToView(existing.data.items[0])
      : null;

  return (
    <WorkspaceShell>
      <p className="font-body text-eyebrow uppercase tracking-[0.06em] text-brand">Opportunity</p>
      <h1 className="mt-2 font-mono text-title text-ink">{row.external_id}</h1>
      <div className="mt-6 grid gap-4 md:grid-cols-3">
        <Field label="Amount" value={formatMoney(row.amount, row.currency)} mono />
        <Field label="Stage" value={row.stage} />
        <Field label="Source" value={row.source} />
        <Field label="Customer" value={row.customer_id} mono />
        <Field label="Created" value={formatTimestamp(row.created_at)} mono />
        <Field label="Owner" value={row.owner_id} mono />
      </div>
      <div className="mt-12">
        <h2 className="font-display text-section text-ink">Decision</h2>
        {current ? (
          <div className="mt-4 max-w-xl">
            <DecisionLedgerEntry decision={current} variant="compact" animate={fresh} />
            <Button
              className="mt-4"
              variant="secondary"
              disabled={generate.isPending}
              onClick={() =>
                generate.mutate(row.external_id, {
                  onSuccess: () => setFresh(true),
                })
              }
            >
              {generate.isPending ? "Scoring…" : "Regenerate"}
            </Button>
            {generate.isError ? (
              <p className="mt-3 font-body text-body text-oxblood">{generate.error.message}</p>
            ) : null}
          </div>
        ) : (
          <div className="mt-4 rounded-2xl bg-white p-6 shadow-sm ring-1 ring-hairline">
            <p className="font-body text-body text-ink-muted">No decision yet for this opportunity.</p>
            <Button
              className="mt-4"
              disabled={generate.isPending}
              onClick={() =>
                generate.mutate(row.external_id, {
                  onSuccess: () => setFresh(true),
                })
              }
            >
              {generate.isPending ? "Scoring…" : "Generate decision"}
            </Button>
            {generate.isError ? (
              <p className="mt-3 font-body text-body text-oxblood">{generate.error.message}</p>
            ) : null}
          </div>
        )}
      </div>
      <p className="mt-8">
        <Link className="font-body text-body text-brand underline-offset-2 hover:underline" href="/opportunities">
          Back to opportunities
        </Link>
      </p>
    </WorkspaceShell>
  );
}

function Field({ label, value, mono }: { label: string; value: string; mono?: boolean }) {
  return (
    <div className="rounded-2xl bg-white p-5 shadow-sm ring-1 ring-hairline">
      <p className="font-body text-eyebrow uppercase tracking-[0.06em] text-ink-muted">{label}</p>
      <p className={mono ? "mt-1 font-mono text-data text-ink" : "mt-1 font-body text-body text-ink"}>{value}</p>
    </div>
  );
}
