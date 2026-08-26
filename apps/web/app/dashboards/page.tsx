"use client";

import { ActionChart } from "@/app/components/overview/ActionChart";
import { DecisionLedgerEntry } from "@/app/components/decisions/DecisionLedgerEntry";
import { EmptyState } from "@/app/components/ui/EmptyState";
import { ErrorState } from "@/app/components/ui/ErrorState";
import { Skeleton } from "@/app/components/ui/Skeleton";
import { useOverviewSnapshot } from "@/lib/application";
import { confidenceBand, decisionToView, formatMoney, formatPercent } from "@/lib/domain";
import {
  AlertTriangle,
  CircleDollarSign,
  Gauge,
  Heart,
  Sparkles,
  Target,
  Users,
} from "lucide-react";

export default function DashboardsPage() {
  const snapshot = useOverviewSnapshot();

  if (snapshot.isPending) {
    return (
      <div>
        <div className="bg-midnight">
          <DashboardHero />
        </div>
        <div className="mx-auto max-w-7xl px-5 py-12 lg:px-8">
          <div className="grid gap-4 md:grid-cols-3">
            <Skeleton className="h-24" />
            <Skeleton className="h-24" />
            <Skeleton className="h-24" />
          </div>
        </div>
      </div>
    );
  }

  if (snapshot.isError) {
    return (
      <div>
        <div className="bg-midnight">
          <DashboardHero />
        </div>
        <div className="mx-auto max-w-3xl px-5 py-12">
          <ErrorState
            body="Could not load overview numbers from the backend. Check that the API is running."
            onRetry={() => void snapshot.refetch()}
          />
        </div>
      </div>
    );
  }

  const data = snapshot.data;
  if (!data || (data.opportunityTotal === 0 && data.decisionTotal === 0)) {
    return (
      <div>
        <div className="bg-midnight">
          <DashboardHero />
        </div>
        <div className="mx-auto max-w-3xl px-5 py-12">
          <EmptyState
            title="No opportunities yet"
            body="Upload a CSV of historical sales opportunities to score them and see recommended actions."
            actionLabel="Upload opportunities"
            actionHref="/opportunities/upload"
          />
        </div>
      </div>
    );
  }

  const counts: Record<string, number> = {};
  for (const row of data.decisions) {
    counts[row.recommended_action] = (counts[row.recommended_action] ?? 0) + 1;
  }
  const topAction =
    Object.entries(counts).sort((left, right) => right[1] - left[1])[0]?.[0]?.replaceAll("_", " ") ?? "—";
  const recent = data.decisions.slice(0, 5);
  const avgConfidence =
    data.decisions.length > 0
      ? data.decisions.reduce((sum, row) => sum + row.confidence, 0) / data.decisions.length
      : 0;
  const expectedSum = data.decisions.reduce((sum, row) => sum + row.expected_revenue, 0);
  const highConf = data.decisions.filter((row) => row.confidence >= 0.75).length;
  const bands = { High: 0, Medium: 0, Low: 0 };
  for (const row of data.decisions) {
    bands[confidenceBand(row.confidence)] += 1;
  }
  const denom = Math.max(data.decisions.length, 1);

  return (
    <div>
      <div className="bg-midnight">
        <DashboardHero />
        <div className="mx-auto max-w-7xl px-5 pb-16 lg:px-8">
          <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-3 xl:grid-cols-6">
            <Metric icon={CircleDollarSign} label="Opportunities" value={String(data.opportunityTotal)} hint="In the pipeline" />
            <Metric icon={Gauge} label="Decisions" value={String(data.decisionTotal)} hint="Generated" />
            <Metric icon={Target} label="Top action" value={topAction} hint="Most recommended" />
            <Metric icon={Heart} label="Avg confidence" value={formatPercent(avgConfidence)} hint={confidenceBand(avgConfidence)} />
            <Metric icon={Users} label="Expected value" value={formatMoney(expectedSum)} hint="From loaded decisions" />
            <Metric
              icon={AlertTriangle}
              label="High confidence"
              value={String(highConf)}
              hint={`${highConf} of ${data.decisions.length}`}
              warn
            />
          </div>

          {data.truncated ? (
            <p className="mt-4 text-sm text-white/50">Action breakdown uses the 500 most recent decisions (API page size is 100).</p>
          ) : null}

          <div className="mt-8 grid gap-6 lg:grid-cols-3">
            <div className="rounded-2xl bg-white/5 p-5 ring-1 ring-white/10 lg:col-span-2">
              <div className="flex items-center justify-between">
                <h2 className="font-semibold text-white">Recommended actions</h2>
                <p className="flex items-center gap-1.5 text-xs font-semibold text-cyan">
                  <Sparkles size={14} /> AI Predicted
                </p>
              </div>
              <div className="mt-4 h-64">
                <ActionChart counts={counts} />
              </div>
            </div>
            <div className="space-y-6">
              <div className="rounded-2xl bg-white/5 p-5 ring-1 ring-white/10">
                <h2 className="font-semibold text-white">AI Recommendations</h2>
                <ul className="mt-4 space-y-3 text-sm text-white/70">
                  {Object.entries(counts)
                    .sort((a, b) => b[1] - a[1])
                    .slice(0, 3)
                    .map(([action, count]) => (
                      <li key={action} className="flex gap-2">
                        <span className="mt-1.5 h-1.5 w-1.5 shrink-0 rounded-full bg-cyan" />
                        {count}× {action.replaceAll("_", " ")}
                      </li>
                    ))}
                </ul>
              </div>
              <div className="rounded-2xl bg-white/5 p-5 ring-1 ring-white/10">
                <h2 className="font-semibold text-white">Decision Confidence</h2>
                {(["High", "Medium", "Low"] as const).map((band) => (
                  <div key={band} className="mt-3">
                    <div className="flex justify-between text-xs text-white/60">
                      <span>{band}</span>
                      <span>{Math.round((bands[band] / denom) * 100)}%</span>
                    </div>
                    <div className="mt-1 h-2 rounded-full bg-white/10">
                      <div
                        className="h-2 rounded-full bg-gradient-to-r from-brand to-cyan"
                        style={{ width: `${(bands[band] / denom) * 100}%` }}
                      />
                    </div>
                  </div>
                ))}
              </div>
            </div>
          </div>
        </div>
      </div>

      <div className="mx-auto max-w-7xl px-5 py-12 lg:px-8 lg:py-16">
        <h2 className="text-2xl font-bold text-ink">Recent decisions</h2>
        <div className="mt-4 grid gap-4">
          {recent.map((row) => (
            <DecisionLedgerEntry key={row.id} decision={decisionToView(row)} variant="compact" />
          ))}
        </div>
      </div>
    </div>
  );
}

function DashboardHero() {
  return (
    <section className="px-5 pb-10 pt-16 text-center lg:px-8 lg:pt-20">
      <p className="text-eyebrow uppercase text-cyan">Dashboard</p>
      <h1 className="mt-4 text-4xl font-bold text-white lg:text-5xl">Your Business, in Real Time.</h1>
      <p className="mx-auto mt-3 max-w-2xl text-white/65">
        Every metric that matters, from the opportunities and decisions in this workspace.
      </p>
    </section>
  );
}

function Metric({
  icon: Icon,
  label,
  value,
  hint,
  warn,
}: {
  icon: typeof CircleDollarSign;
  label: string;
  value: string;
  hint: string;
  warn?: boolean;
}) {
  return (
    <div className="rounded-2xl bg-white/5 p-4 ring-1 ring-white/10">
      <Icon size={16} className={warn ? "text-amber" : "text-cyan"} />
      <p className="mt-3 text-xs text-white/50">{label}</p>
      <p className="mt-1 truncate text-xl font-bold text-white">{value}</p>
      <p className={`mt-1 text-xs ${warn ? "text-amber" : "text-cyan"}`}>{hint}</p>
    </div>
  );
}
