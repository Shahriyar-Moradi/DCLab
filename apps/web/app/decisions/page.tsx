"use client";

import { DecisionLedgerEntry } from "@/app/components/decisions/DecisionLedgerEntry";
import { EmptyState } from "@/app/components/ui/EmptyState";
import { ErrorState } from "@/app/components/ui/ErrorState";
import { Skeleton } from "@/app/components/ui/Skeleton";
import { useDecisions } from "@/lib/application";
import { decisionToView } from "@/lib/domain";
import { useState } from "react";

const PAGE_SIZE = 20;
const ACTIONS = ["CONTACT_TODAY", "SCHEDULE_FOLLOWUP", "SEND_EMAIL", "NO_ACTION"];

export default function DecisionsPage() {
  const [offset, setOffset] = useState(0);
  const [action, setAction] = useState("");
  const [status, setStatus] = useState("");
  const query = useDecisions({
    limit: PAGE_SIZE,
    offset,
    action: action || undefined,
    status: status || undefined,
  });

  if (query.isPending) {
    return (
      <div>
        <h1 className="font-display text-title text-ink">Decisions</h1>
        <Skeleton className="mt-8 h-40" />
        <Skeleton className="mt-4 h-40" />
      </div>
    );
  }
  if (query.isError) {
    return <ErrorState body="Could not load decisions." onRetry={() => void query.refetch()} />;
  }
  const payload = query.data;
  if (!payload || (payload.total === 0 && !action && !status)) {
    return (
      <EmptyState
        title="No decisions yet"
        body="Open an opportunity and generate a decision. The ledger will show the recommended action, expected value, and why."
        actionLabel="Go to opportunities"
        actionHref="/opportunities"
      />
    );
  }

  return (
    <div>
      <h1 className="font-display text-title text-ink">Decisions</h1>
      <p className="mt-2 font-body text-body text-ink-muted">Audit trail of recommended actions.</p>
      <div className="mt-6 flex flex-wrap gap-4">
        <label className="font-body text-body text-ink">
          Action
          <select
            className="ml-2 rounded border border-hairline bg-paper-raised px-3 py-2"
            value={action}
            onChange={(event) => {
              setAction(event.target.value);
              setOffset(0);
            }}
          >
            <option value="">All</option>
            {ACTIONS.map((item) => (
              <option key={item} value={item}>
                {item.replaceAll("_", " ")}
              </option>
            ))}
          </select>
        </label>
        <label className="font-body text-body text-ink">
          Status
          <select
            className="ml-2 rounded border border-hairline bg-paper-raised px-3 py-2"
            value={status}
            onChange={(event) => {
              setStatus(event.target.value);
              setOffset(0);
            }}
          >
            <option value="">All</option>
            <option value="pending_review">pending_review</option>
          </select>
        </label>
      </div>
      <div className="mt-8 grid gap-4">
        {payload.items.map((row) => (
          <DecisionLedgerEntry key={row.id} decision={decisionToView(row)} variant="compact" />
        ))}
      </div>
      <div className="mt-6 flex items-center justify-between font-body text-body text-ink">
        <button
          type="button"
          className="rounded px-3 py-2 hover:bg-navy-soft disabled:opacity-40"
          disabled={offset === 0}
          onClick={() => setOffset((value) => Math.max(0, value - PAGE_SIZE))}
        >
          Previous
        </button>
        <p className="font-mono text-data">
          {payload.total === 0 ? "0" : `${offset + 1}–${Math.min(offset + PAGE_SIZE, payload.total)}`} of {payload.total}
        </p>
        <button
          type="button"
          className="rounded px-3 py-2 hover:bg-navy-soft disabled:opacity-40"
          disabled={offset + PAGE_SIZE >= payload.total}
          onClick={() => setOffset((value) => value + PAGE_SIZE)}
        >
          Next
        </button>
      </div>
    </div>
  );
}
