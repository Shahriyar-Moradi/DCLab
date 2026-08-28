"use client";

import { EmptyState } from "@/app/components/ui/EmptyState";
import { ErrorState } from "@/app/components/ui/ErrorState";
import { Skeleton } from "@/app/components/ui/Skeleton";
import { Table, Td, Th } from "@/app/components/ui/Table";
import { useOpportunities } from "@/lib/application";
import { formatMoney, formatTimestamp } from "@/lib/domain";
import Link from "next/link";
import { useMemo, useState } from "react";

const PAGE_SIZE = 20;

export default function OpportunitiesPage() {
  const [offset, setOffset] = useState(0);
  const [stage, setStage] = useState("");
  const [sort, setSort] = useState<"created_at" | "amount">("created_at");
  const [order, setOrder] = useState<"asc" | "desc">("desc");
  const query = useOpportunities({ limit: PAGE_SIZE, offset, stage: stage || undefined, sort, order });

  const stages = useMemo(() => ["prospecting", "qualification", "proposal", "negotiation", "closed_won", "closed_lost"], []);

  function toggleSort(column: "created_at" | "amount") {
    if (sort === column) setOrder((current) => (current === "asc" ? "desc" : "asc"));
    else {
      setSort(column);
      setOrder("desc");
    }
    setOffset(0);
  }

  if (query.isPending) {
    return (
      <div>
        <h1 className="font-display text-title text-ink">Opportunities</h1>
        <Skeleton className="mt-8 h-96" />
      </div>
    );
  }
  if (query.isError) {
    return <ErrorState body="Could not load opportunities." onRetry={() => void query.refetch()} />;
  }
  const payload = query.data;
  if (!payload || (payload.total === 0 && !stage)) {
    return (
      <EmptyState
        title="No opportunities yet"
        body="Upload a CSV to get started. The decision engine scores each row and recommends an action."
        actionLabel="Upload opportunities"
        actionHref="/app/opportunities/upload"
      />
    );
  }

  return (
    <div>
      <div className="flex flex-wrap items-end justify-between gap-4">
        <div>
          <h1 className="font-display text-title text-ink">Opportunities</h1>
          <p className="mt-2 font-body text-body text-ink-muted">{payload.total} in the pipeline</p>
        </div>
        <label className="font-body text-body text-ink">
          Stage
          <select
            className="ml-2 rounded border border-hairline bg-paper-raised px-3 py-2"
            value={stage}
            onChange={(event) => {
              setStage(event.target.value);
              setOffset(0);
            }}
          >
            <option value="">All</option>
            {stages.map((item) => (
              <option key={item} value={item}>
                {item}
              </option>
            ))}
          </select>
        </label>
      </div>
      <div className="mt-8 rounded bg-paper-raised p-2 md:p-4">
        <Table>
          <thead>
            <tr>
              <Th>ID</Th>
              <Th>Customer</Th>
              <Th sortable onSort={() => toggleSort("amount")}>
                Amount {sort === "amount" ? (order === "desc" ? "↓" : "↑") : ""}
              </Th>
              <Th>Stage</Th>
              <Th>Source</Th>
              <Th sortable onSort={() => toggleSort("created_at")}>
                Created {sort === "created_at" ? (order === "desc" ? "↓" : "↑") : ""}
              </Th>
            </tr>
          </thead>
          <tbody>
            {payload.items.map((row) => (
              <tr key={row.id} className="hover:bg-navy-soft/60">
                <Td mono>
                  <Link className="text-navy underline-offset-2 hover:underline" href={`/app/opportunities/${row.external_id}`}>
                    {row.external_id}
                  </Link>
                </Td>
                <Td mono>{row.customer_id}</Td>
                <Td mono>
                  {formatMoney(row.amount, row.currency)}
                </Td>
                <Td>{row.stage}</Td>
                <Td>{row.source}</Td>
                <Td mono>{formatTimestamp(row.created_at)}</Td>
              </tr>
            ))}
          </tbody>
        </Table>
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
          {offset + 1}–{Math.min(offset + PAGE_SIZE, payload.total)} of {payload.total}
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
