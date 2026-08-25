"use client";

import { DecisionLedgerEntry } from "@/app/components/decisions/DecisionLedgerEntry";
import { EmptyState } from "@/app/components/ui/EmptyState";
import { ErrorState } from "@/app/components/ui/ErrorState";
import { Skeleton } from "@/app/components/ui/Skeleton";
import { fieldControlClass, PageIntro, Pager, WorkspaceShell } from "@/app/components/workspace/PageIntro";
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
      <WorkspaceShell>
        <PageIntro eyebrow="Workspace" title="Decisions" />
        <Skeleton className="mt-8 h-40" />
        <Skeleton className="mt-4 h-40" />
      </WorkspaceShell>
    );
  }
  if (query.isError) {
    return (
      <WorkspaceShell>
        <ErrorState body="Could not load decisions." onRetry={() => void query.refetch()} />
      </WorkspaceShell>
    );
  }
  const payload = query.data;
  if (!payload || (payload.total === 0 && !action && !status)) {
    return (
      <WorkspaceShell>
        <EmptyState
          title="No decisions yet"
          body="Open an opportunity and generate a decision. The ledger will show the recommended action, expected value, and why."
          actionLabel="Go to opportunities"
          actionHref="/opportunities"
        />
      </WorkspaceShell>
    );
  }

  return (
    <WorkspaceShell>
      <PageIntro
        eyebrow="Workspace"
        title="Decisions"
        subtitle="Audit trail of recommended actions."
        actions={
          <div className="flex flex-wrap gap-4">
            <label className="font-body text-body text-ink">
              Action
              <select
                className={fieldControlClass}
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
                className={fieldControlClass}
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
        }
      />
      <div className="mt-8 grid gap-4">
        {payload.items.map((row) => (
          <DecisionLedgerEntry key={row.id} decision={decisionToView(row)} variant="compact" />
        ))}
      </div>
      <Pager
        offset={offset}
        pageSize={PAGE_SIZE}
        total={payload.total}
        onPrev={() => setOffset((value) => Math.max(0, value - PAGE_SIZE))}
        onNext={() => setOffset((value) => value + PAGE_SIZE)}
      />
    </WorkspaceShell>
  );
}
