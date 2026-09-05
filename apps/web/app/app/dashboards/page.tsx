"use client";

import { ActionChart } from "@/app/components/overview/ActionChart";
import { Badge } from "@/app/components/ui/Badge";
import { Button } from "@/app/components/ui/Button";
import { Card, Panel } from "@/app/components/ui/Card";
import { ConfidenceBar } from "@/app/components/ui/ConfidenceBar";
import { DataTable } from "@/app/components/ui/DataTable";
import { EmptyState } from "@/app/components/ui/EmptyState";
import { ErrorState } from "@/app/components/ui/ErrorState";
import { GlassPanel } from "@/app/components/ui/GlassPanel";
import { MetricCard } from "@/app/components/ui/MetricCard";
import { PageHeader } from "@/app/components/ui/PageHeader";
import { SectionHeader } from "@/app/components/ui/SectionHeader";
import { Skeleton } from "@/app/components/ui/Skeleton";
import { useOverviewSnapshot } from "@/lib/application";
import {
  actionLabel,
  actionTone,
  decisionToView,
  formatMoney,
  formatPercent,
  formatTimestamp,
  toneFromConfidenceBand,
  type Decision,
} from "@/lib/domain";
import { AlertTriangle, CircleDollarSign, Gauge, Heart, Target, Users } from "lucide-react";
import Link from "next/link";

const CONFIDENCE_BANDS = ["High", "Medium", "Low"] as const;

function summarizeDecisions(decisions: Decision[]) {
  const counts: Record<string, number> = {};
  const bands = { High: 0, Medium: 0, Low: 0 };
  let expectedSum = 0;
  for (const row of decisions) {
    counts[row.recommended_action] = (counts[row.recommended_action] ?? 0) + 1;
    bands[row.confidence_band] += 1;
    expectedSum += row.expected_revenue;
  }
  const topAction = Object.entries(counts).sort((left, right) => right[1] - left[1])[0]?.[0];
  return {
    counts,
    bands,
    expectedSum,
    topAction: topAction ? actionLabel(topAction) : "—",
    denom: Math.max(decisions.length, 1),
    highConf: bands.High,
  };
}

function DashboardHeader({
  fetching = false,
  onRefresh,
}: {
  fetching?: boolean;
  onRefresh?: () => void;
}) {
  return (
    <PageHeader
      eyebrow="Workspace"
      title="Dashboard"
      description="Opportunity volume, recommended actions, and decision confidence from this workspace."
      actions={
        <Button variant="secondary" disabled={!onRefresh || fetching} onClick={onRefresh}>
          {fetching ? "Refreshing…" : "Refresh"}
        </Button>
      }
    />
  );
}

export default function DashboardsPage() {
  const snapshot = useOverviewSnapshot();

  if (snapshot.isPending) {
    return (
      <div>
        <DashboardHeader />
        <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-3">
          <Skeleton className="h-24" />
          <Skeleton className="h-24" />
          <Skeleton className="h-24" />
          <Skeleton className="h-24" />
          <Skeleton className="h-24" />
          <Skeleton className="h-24" />
        </div>
        <div className="mt-6 grid min-w-0 gap-5 lg:grid-cols-3">
          <Skeleton className="h-72 lg:col-span-2" />
          <Skeleton className="h-72" />
        </div>
      </div>
    );
  }

  if (snapshot.isError) {
    return (
      <div>
        <DashboardHeader fetching={snapshot.isFetching} onRefresh={() => void snapshot.refetch()} />
        <ErrorState
          body="Could not load overview numbers from the backend. Check that the API is running."
          onRetry={() => void snapshot.refetch()}
        />
      </div>
    );
  }

  const data = snapshot.data;
  if (!data || (data.opportunityTotal === 0 && data.decisionTotal === 0)) {
    return (
      <div>
        <DashboardHeader fetching={snapshot.isFetching} onRefresh={() => void snapshot.refetch()} />
        <EmptyState
          title="No opportunities yet"
          body="Upload a CSV of historical sales opportunities to score them and see recommended actions."
          actionLabel="Upload opportunities"
          actionHref="/app/opportunities/upload"
        />
      </div>
    );
  }

  const { counts, bands, expectedSum, topAction, denom, highConf } = summarizeDecisions(data.decisions);
  const rankedActions = Object.entries(counts).sort((left, right) => right[1] - left[1]);
  const recent = data.decisions.slice(0, 5);

  return (
    <div>
      <DashboardHeader fetching={snapshot.isFetching} onRefresh={() => void snapshot.refetch()} />

      <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-3">
        <Link href="/app/opportunities" className="block min-w-0 rounded-xl focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-ring">
          <MetricCard icon={<CircleDollarSign size={17} />} label="Opportunities" value={String(data.opportunityTotal)} hint="In the pipeline" />
        </Link>
        <Link href="/app/decisions" className="block min-w-0 rounded-xl focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-ring">
          <MetricCard icon={<Gauge size={17} />} label="Decisions" value={String(data.decisionTotal)} hint="Generated" tone="brand" />
        </Link>
        <MetricCard icon={<Target size={17} />} label="Top action" value={topAction} hint="Most recommended" />
        <MetricCard
          icon={<Heart size={17} />}
          label="High confidence"
          value={formatPercent(highConf / denom)}
          hint={`${highConf} of ${data.decisions.length}`}
          tone="brand"
        />
        <MetricCard icon={<Users size={17} />} label="Expected value" value={formatMoney(expectedSum)} hint="From loaded decisions" />
        <MetricCard icon={<AlertTriangle size={17} />} label="Needs review" value={String(bands.Low)} hint="Low confidence" tone="warning" />
      </div>

      <div className="mt-6 grid min-w-0 gap-5 lg:grid-cols-3">
        <GlassPanel
          title="Recommended actions"
          description="Current decision mix from the translated workspace ledger."
          className="min-w-0 lg:col-span-2"
        >
          <div className="h-64 min-w-0 overflow-hidden">
            <ActionChart counts={counts} />
          </div>
          {data.truncated ? (
            <p className="mt-3 text-helper text-ink-muted">
              Action breakdown uses the 500 most recent decisions (API page size is 100).
            </p>
          ) : null}
        </GlassPanel>

        <div className="grid gap-5">
          <Panel title="Top recommendations" description="Highest-frequency actions in the current decision set.">
            {rankedActions.length ? (
              <ul className="space-y-3">
                {rankedActions.slice(0, 3).map(([action, count], index) => (
                  <li key={action} className="flex items-start justify-between gap-3 text-body text-ink">
                    <span className="flex min-w-0 items-start gap-2">
                      <span
                        className="mt-1.5 h-1.5 w-1.5 shrink-0 rounded-full"
                        style={{ background: index === 0 ? "var(--color-navy)" : "var(--color-cyan)" }}
                      />
                      <span className="min-w-0">{actionLabel(action)}</span>
                    </span>
                    <span className="shrink-0 font-mono text-data text-ink-muted">{count}×</span>
                  </li>
                ))}
              </ul>
            ) : (
              <p className="text-body text-ink-muted">No recommended actions yet.</p>
            )}
          </Panel>
          <Panel title="Decision confidence">
            {CONFIDENCE_BANDS.map((band) => (
              <div key={band} className="mt-3 first:mt-0">
                <p className="mb-1.5 text-helper text-ink-muted">{band}</p>
                <ConfidenceBar value={bands[band] / denom} tone={toneFromConfidenceBand(band)} />
              </div>
            ))}
          </Panel>
        </div>
      </div>

      <SectionHeader
        className="mt-8"
        title="Recent decisions"
        description="Most recent translated decisions in this workspace."
        actions={
          <Link href="/app/decisions" className="text-helper font-medium text-navy underline-offset-2 hover:underline">
            View all
          </Link>
        }
      />
      <div className="mt-4">
        {recent.length ? (
          <DataTable
            columns={[
              {
                id: "opportunity",
                header: "Opportunity",
                mono: true,
                cell: (row) => decisionToView(row).opportunityExternalId,
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
                header: "Expected value",
                mono: true,
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
                id: "generated",
                header: "Generated",
                mono: true,
                cell: (row) => formatTimestamp(row.created_at) || "—",
              },
              {
                id: "open",
                header: "Detail",
                cell: (row) => (
                  <Link href={`/app/decisions/${row.id}`} className="font-medium text-navy underline-offset-2 hover:underline">
                    Open
                  </Link>
                ),
              },
            ]}
            rows={recent}
            rowKey={(row) => row.id}
          />
        ) : (
          <Card className="px-6 py-8">
            <p className="text-body text-ink-muted">No decisions generated yet. Open an opportunity to create one.</p>
          </Card>
        )}
      </div>
    </div>
  );
}
