"use client";

import { ActionChart } from "@/app/components/overview/ActionChart";
import { DecisionLedgerEntry } from "@/app/components/decisions/DecisionLedgerEntry";
import { EmptyState } from "@/app/components/ui/EmptyState";
import { ErrorState } from "@/app/components/ui/ErrorState";
import { Skeleton } from "@/app/components/ui/Skeleton";
import { useOverviewSnapshot } from "@/lib/application";
import { decisionToView } from "@/lib/domain";

export default function OverviewPage() {
  const snapshot = useOverviewSnapshot();

  if (snapshot.isPending) {
    return (
      <div>
        <h1 className="font-display text-title text-ink">Overview</h1>
        <div className="mt-8 grid gap-4 md:grid-cols-3">
          <Skeleton className="h-24" />
          <Skeleton className="h-24" />
          <Skeleton className="h-24" />
        </div>
        <Skeleton className="mt-8 h-64" />
      </div>
    );
  }

  if (snapshot.isError) {
    return (
      <ErrorState
        body="Could not load overview numbers from the backend. Check that the API is running."
        onRetry={() => void snapshot.refetch()}
      />
    );
  }

  const data = snapshot.data;
  if (!data || (data.opportunityTotal === 0 && data.decisionTotal === 0)) {
    return (
      <EmptyState
        title="No opportunities yet"
        body="Upload a CSV of historical sales opportunities to score them and see recommended actions."
        actionLabel="Upload opportunities"
        actionHref="/opportunities/upload"
      />
    );
  }

  const counts: Record<string, number> = {};
  for (const row of data.decisions) {
    counts[row.recommended_action] = (counts[row.recommended_action] ?? 0) + 1;
  }
  const topAction =
    Object.entries(counts).sort((left, right) => right[1] - left[1])[0]?.[0]?.replaceAll("_", " ") ?? "—";
  const recent = data.decisions.slice(0, 5);

  return (
    <div>
      <h1 className="font-display text-title text-ink">Overview</h1>
      <p className="mt-2 font-body text-body text-ink-muted">
        What the decision layer has scored, and which actions it is recommending.
      </p>
      <div className="mt-8 grid gap-4 md:grid-cols-3">
        <StatCard label="Opportunities" value={String(data.opportunityTotal)} />
        <StatCard label="Decisions generated" value={String(data.decisionTotal)} />
        <StatCard label="Most common action" value={topAction} />
      </div>
      {data.truncated ? (
        <p className="mt-4 font-body text-body text-ink-muted">
          Action breakdown uses the 500 most recent decisions (API page size is 100).
        </p>
      ) : null}
      <h2 className="mt-12 font-display text-section text-ink">Recommended actions</h2>
      <div className="mt-4 h-64 rounded bg-paper-raised p-4">
        <ActionChart counts={counts} />
      </div>
      <h2 className="mt-12 font-display text-section text-ink">Recent decisions</h2>
      <div className="mt-4 grid gap-4">
        {recent.map((row) => (
          <DecisionLedgerEntry key={row.id} decision={decisionToView(row)} variant="compact" />
        ))}
      </div>
    </div>
  );
}

function StatCard({ label, value }: { label: string; value: string }) {
  return (
    <div className="rounded bg-paper-raised p-6">
      <p className="font-body text-eyebrow uppercase tracking-[0.06em] text-ink-muted">{label}</p>
      <p className="mt-2 font-mono text-title text-ink">{value}</p>
    </div>
  );
}
