"use client";

import { Badge } from "@/app/components/ui/Badge";
import { DataTable } from "@/app/components/ui/DataTable";
import { EmptyState } from "@/app/components/ui/EmptyState";
import { ErrorState } from "@/app/components/ui/ErrorState";
import { FilterBar } from "@/app/components/ui/FilterBar";
import { MetricCard } from "@/app/components/ui/MetricCard";
import { PageHeader } from "@/app/components/ui/PageHeader";
import { Pagination } from "@/app/components/ui/Pagination";
import { SearchInput } from "@/app/components/ui/SearchInput";
import { Select } from "@/app/components/ui/Select";
import { Skeleton } from "@/app/components/ui/Skeleton";
import { StatusBadge } from "@/app/components/ui/StatusBadge";
import { filterByText, nextLocalSort, sortByValue, sortMarker } from "@/app/components/ui/localCollection";
import { useDecisions } from "@/lib/application";
import { actionLabel, actionTone, decisionToView, formatMoney, formatTimestamp, toneFromConfidenceBand } from "@/lib/domain";
import Link from "next/link";
import { useState } from "react";

const PAGE_SIZE = 20;
const ACTIONS = ["CONTACT_TODAY", "SCHEDULE_FOLLOWUP", "SEND_EMAIL", "NO_ACTION"];

export default function DecisionsPage() {
  const [offset, setOffset] = useState(0);
  const [action, setAction] = useState("");
  const [status, setStatus] = useState("");
  const [queryText, setQueryText] = useState("");
  const [sortId, setSortId] = useState<"value" | "generated">("generated");
  const [sortDir, setSortDir] = useState<"asc" | "desc">("desc");
  const query = useDecisions({
    limit: PAGE_SIZE,
    offset,
    action: action || undefined,
    status: status || undefined,
  });

  const header = (
    <PageHeader
      eyebrow="Workspace"
      title="Decisions"
      description="Recommended actions, confidence, and expected value from this workspace."
    />
  );

  if (query.isPending) {
    return (
      <div>
        {header}
        <Skeleton className="mt-2 h-40" />
        <Skeleton className="mt-4 h-40" />
      </div>
    );
  }
  if (query.isError) {
    return (
      <div>
        {header}
        <ErrorState body="Could not load decisions." onRetry={() => void query.refetch()} />
      </div>
    );
  }
  const payload = query.data;
  if (!payload || (payload.total === 0 && !action && !status)) {
    return (
      <div>
        {header}
        <EmptyState
          title="No decisions yet"
          body="Open an opportunity and generate a decision. The ledger will show the recommended action, expected value, and why."
          actionLabel="Go to opportunities"
          actionHref="/app/opportunities"
        />
      </div>
    );
  }

  const filtered = filterByText(payload.items, queryText, (row) => {
    const view = decisionToView(row);
    return [view.opportunityExternalId, view.recommendedAction, row.status, row.confidence_band];
  });
  const rows = sortByValue(filtered, sortId, sortDir, (row, id) =>
    id === "value" ? row.expected_revenue : row.created_at,
  );
  const actionCounts: Record<string, number> = {};
  let high = 0;
  let expected = 0;
  for (const row of payload.items) {
    actionCounts[row.recommended_action] = (actionCounts[row.recommended_action] ?? 0) + 1;
    if (row.confidence_band === "High") high += 1;
    expected += row.expected_revenue;
  }
  const topAction = Object.entries(actionCounts).sort((left, right) => right[1] - left[1])[0]?.[0];
  const pageSummary = {
    high,
    expected,
    top: topAction ? actionLabel(topAction) : "—",
  };
  const page = Math.floor(offset / PAGE_SIZE) + 1;
  const pageCount = Math.max(1, Math.ceil(payload.total / PAGE_SIZE));
  const needle = queryText.trim();

  return (
    <div>
      {header}
      <FilterBar
        ariaLabel="Recommended action"
        value={action}
        onChange={(id) => {
          setAction(id);
          setOffset(0);
        }}
        options={[
          { id: "", label: "All actions" },
          ...ACTIONS.map((item) => ({ id: item, label: actionLabel(item) })),
        ]}
        trailing={
          <div className="grid gap-2 sm:grid-cols-2">
            <Select
              aria-label="Status"
              value={status}
              onChange={(event) => {
                setStatus(event.target.value);
                setOffset(0);
              }}
            >
              <option value="">All statuses</option>
              <option value="pending_review">pending review</option>
            </Select>
            <SearchInput
              value={queryText}
              onChange={setQueryText}
              placeholder="Filter this page"
            />
          </div>
        }
      />
      <div className="mt-4 grid gap-3 sm:grid-cols-2 xl:grid-cols-4">
        <MetricCard label="Loaded on this page" value={String(payload.items.length)} hint={`${payload.total} in the ledger`} />
        <MetricCard label="High confidence" value={String(pageSummary.high)} hint="This page only" />
        <MetricCard label="Top action" value={pageSummary.top} hint="This page only" />
        <MetricCard label="Expected value" value={formatMoney(pageSummary.expected)} hint="Sum of this page" />
      </div>
      <div className="mt-4">
        <DataTable
          columns={[
            {
              id: "opportunity",
              header: "Opportunity",
              mono: true,
              cell: (row) => {
                const view = decisionToView(row);
                return (
                  <Link
                    className="font-medium text-navy underline-offset-2 hover:underline"
                    href={`/app/opportunities/${view.opportunityExternalId}`}
                  >
                    {view.opportunityExternalId}
                  </Link>
                );
              },
            },
            {
              id: "action",
              header: "Action",
              cell: (row) => (
                <Badge tone={actionTone(row.recommended_action)} emphasis="soft">
                  {actionLabel(row.recommended_action)}
                </Badge>
              ),
            },
            {
              id: "value",
              header: `Expected value${sortMarker(sortId, "value", sortDir)}`,
              mono: true,
              sortable: true,
              cell: (row) => formatMoney(row.expected_revenue),
            },
            {
              id: "confidence",
              header: "Confidence",
              cell: (row) => (
                <Badge tone={toneFromConfidenceBand(row.confidence_band)} emphasis="soft">
                  {row.confidence_band}
                </Badge>
              ),
            },
            {
              id: "status",
              header: "Status",
              cell: (row) => <StatusBadge status={row.status.replaceAll("_", " ")} />,
            },
            {
              id: "generated",
              header: `Generated${sortMarker(sortId, "generated", sortDir)}`,
              mono: true,
              sortable: true,
              cell: (row) => formatTimestamp(row.created_at) || "—",
            },
            {
              id: "open",
              header: "Detail",
              cell: (row) => (
                <Link className="font-medium text-navy underline-offset-2 hover:underline" href={`/app/decisions/${row.id}`}>
                  Open
                </Link>
              ),
            },
          ]}
          rows={rows}
          rowKey={(row) => row.id}
          sortId={sortId}
          sortDir={sortDir}
          onSort={(id) => {
            if (id !== "value" && id !== "generated") return;
            const next = nextLocalSort(sortId, sortDir, id);
            setSortId(next.id as "value" | "generated");
            setSortDir(next.dir);
          }}
          emptyTitle="No matching decisions"
          emptyBody={needle ? "Nothing on this page matches that filter." : "No decisions for this action or status."}
        />
      </div>
      <div className="mt-4 flex flex-wrap items-center justify-between gap-3">
        <p className="font-mono text-data text-ink-muted">
          {payload.total === 0
            ? "0"
            : `${offset + 1}–${Math.min(offset + PAGE_SIZE, payload.total)} of ${payload.total}`}
        </p>
        <Pagination
          page={page}
          pageCount={pageCount}
          disabled={query.isFetching}
          onPageChange={(next) => setOffset((next - 1) * PAGE_SIZE)}
        />
      </div>
    </div>
  );
}
