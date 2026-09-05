"use client";

import { buttonClassName } from "@/app/components/ui/Button";
import { DataTable } from "@/app/components/ui/DataTable";
import { EmptyState } from "@/app/components/ui/EmptyState";
import { ErrorState } from "@/app/components/ui/ErrorState";
import { FilterBar } from "@/app/components/ui/FilterBar";
import { PageHeader } from "@/app/components/ui/PageHeader";
import { Pagination } from "@/app/components/ui/Pagination";
import { SearchInput } from "@/app/components/ui/SearchInput";
import { Skeleton } from "@/app/components/ui/Skeleton";
import { StatusBadge } from "@/app/components/ui/StatusBadge";
import { useOpportunities } from "@/lib/application";
import { formatMoney, formatTimestamp, type SignalTone } from "@/lib/domain";
import Link from "next/link";
import { useState } from "react";

const PAGE_SIZE = 20;
const STAGES = ["prospecting", "qualification", "proposal", "negotiation", "closed_won", "closed_lost"] as const;

function stageTone(stage: string): SignalTone {
  if (stage === "closed_won") return "green";
  if (stage === "closed_lost") return "oxblood";
  return "amber";
}

function humanize(value: string) {
  return value.replaceAll("_", " ");
}

export default function OpportunitiesPage() {
  const [offset, setOffset] = useState(0);
  const [stage, setStage] = useState("");
  const [sort, setSort] = useState<"created_at" | "amount">("created_at");
  const [order, setOrder] = useState<"asc" | "desc">("desc");
  const [queryText, setQueryText] = useState("");
  const query = useOpportunities({ limit: PAGE_SIZE, offset, stage: stage || undefined, sort, order });

  function toggleSort(column: "created_at" | "amount") {
    if (sort === column) setOrder((current) => (current === "asc" ? "desc" : "asc"));
    else {
      setSort(column);
      setOrder("desc");
    }
    setOffset(0);
  }

  const header = <OpportunitiesHeader total={query.data?.total} />;

  if (query.isPending) {
    return (
      <div>
        {header}
        <Skeleton className="h-96" />
      </div>
    );
  }
  if (query.isError) {
    return (
      <div>
        {header}
        <ErrorState body="Could not load opportunities." onRetry={() => void query.refetch()} />
      </div>
    );
  }
  const payload = query.data;
  if (!payload || (payload.total === 0 && !stage)) {
    return (
      <div>
        {header}
        <EmptyState
          title="No opportunities yet"
          body="Upload a CSV to get started. The decision engine scores each row and recommends an action."
          actionLabel="Upload opportunities"
          actionHref="/app/opportunities/upload"
        />
      </div>
    );
  }

  const needle = queryText.trim().toLowerCase();
  const rows = needle
    ? payload.items.filter((row) =>
        [row.external_id, row.customer_id, row.source, row.owner_id, row.stage]
          .join(" ")
          .toLowerCase()
          .includes(needle),
      )
    : payload.items;
  const page = Math.floor(offset / PAGE_SIZE) + 1;
  const pageCount = Math.max(1, Math.ceil(payload.total / PAGE_SIZE));

  return (
    <div>
      {header}
      <FilterBar
        ariaLabel="Opportunity stage"
        value={stage}
        onChange={(id) => {
          setStage(id);
          setOffset(0);
        }}
        options={[
          { id: "", label: "All" },
          ...STAGES.map((item) => ({ id: item, label: humanize(item) })),
        ]}
        trailing={
          <SearchInput
            value={queryText}
            onChange={setQueryText}
            placeholder="Filter this page by ID or customer"
          />
        }
      />
      <div className="mt-4">
        <DataTable
          columns={[
            {
              id: "external_id",
              header: "ID",
              mono: true,
              cell: (row) => (
                <Link className="font-medium text-navy underline-offset-2 hover:underline" href={`/app/opportunities/${row.external_id}`}>
                  {row.external_id}
                </Link>
              ),
            },
            { id: "customer", header: "Customer", mono: true, cell: (row) => row.customer_id },
            {
              id: "amount",
              header: `Amount${sort === "amount" ? (order === "desc" ? " ↓" : " ↑") : ""}`,
              mono: true,
              sortable: true,
              cell: (row) => formatMoney(row.amount, row.currency),
            },
            {
              id: "stage",
              header: "Stage",
              cell: (row) => <StatusBadge status={humanize(row.stage)} tone={stageTone(row.stage)} />,
            },
            { id: "source", header: "Source", cell: (row) => row.source },
            {
              id: "created_at",
              header: `Created${sort === "created_at" ? (order === "desc" ? " ↓" : " ↑") : ""}`,
              mono: true,
              sortable: true,
              cell: (row) => formatTimestamp(row.created_at) || "—",
            },
          ]}
          rows={rows}
          rowKey={(row) => row.id}
          sortId={sort}
          sortDir={order}
          onSort={(id) => {
            if (id === "amount" || id === "created_at") toggleSort(id);
          }}
          emptyTitle="No matching opportunities"
          emptyBody={needle ? "Nothing on this page matches that filter." : "No opportunities in this stage."}
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

function OpportunitiesHeader({ total }: { total?: number }) {
  return (
    <PageHeader
      eyebrow="Workspace"
      title="Opportunities"
      description={
        typeof total === "number"
          ? `${total} in the pipeline.`
          : "Pipeline records this workspace can score."
      }
      actions={
        <Link href="/app/opportunities/upload" className={buttonClassName()}>
          Upload
        </Link>
      }
    />
  );
}
