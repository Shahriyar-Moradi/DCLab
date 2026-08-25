"use client";

import { DecisionLedgerEntry } from "@/app/components/decisions/DecisionLedgerEntry";
import { PageHero } from "@/app/components/marketing/primitives";
import { RevenueForecastChart } from "@/app/components/overview/RevenueForecastChart";
import { EmptyState } from "@/app/components/ui/EmptyState";
import { ErrorState } from "@/app/components/ui/ErrorState";
import { WorkspaceShell } from "@/app/components/workspace/PageIntro";
import { useOverviewSnapshot } from "@/lib/application";
import { decisionToView } from "@/lib/domain";
import {
  Activity,
  AlertTriangle,
  DollarSign,
  Gauge,
  Heart,
  Sparkles,
  Target,
  Users,
  type LucideIcon,
} from "lucide-react";
import Link from "next/link";

const KPIS: { icon: LucideIcon; label: string; value: string; hint: string; tone: "blue" | "green" | "amber" }[] = [
  { icon: DollarSign, label: "Revenue Forecast", value: "+$2.4M", hint: "Confidence 96%", tone: "blue" },
  { icon: Gauge, label: "Campaign ROI", value: "8.9x", hint: "Above target", tone: "green" },
  { icon: Target, label: "Lead Score", value: "91%", hint: "High priority", tone: "blue" },
  { icon: Heart, label: "Customer Health", value: "87%", hint: "Stable", tone: "green" },
  { icon: Users, label: "Pipeline Value", value: "$4.8M", hint: "+12% WoW", tone: "blue" },
  { icon: AlertTriangle, label: "Churn Risk", value: "11%", hint: "2 accounts", tone: "amber" },
];

const RECS = [
  "Shift 15% budget to top-performing channels",
  "3 leads ready for sales outreach",
  "Adjust pricing on premium tier",
];

const MODELS = [
  { name: "Revenue Model", value: 94 },
  { name: "Churn Model", value: 88 },
  { name: "Conversion Model", value: 91 },
];

export default function DashboardsPage() {
  const snapshot = useOverviewSnapshot();
  const data = snapshot.data;
  const recent = data?.decisions.slice(0, 3) ?? [];
  const empty = !data || (data.opportunityTotal === 0 && data.decisionTotal === 0);

  return (
    <div>
      <PageHero
        eyebrow="Dashboard"
        title="Your Business, in Real Time."
        subtitle="Every metric that matters, predicted before it happens."
      />
      <WorkspaceShell>
        <div className="bg-midnight-glow rounded-3xl p-5 text-white ring-1 ring-hairline lg:p-8">
          <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-3 xl:grid-cols-6">
            {KPIS.map((item) => (
              <Metric key={item.label} {...item} />
            ))}
          </div>
          <div className="mt-8 grid gap-6 lg:grid-cols-3">
            <div className="rounded-2xl bg-black/30 p-5 lg:col-span-2">
              <div className="flex items-center justify-between">
                <h2 className="font-semibold text-white">Revenue Forecast</h2>
                <p className="flex items-center gap-1.5 text-xs font-semibold text-cyan">
                  <Sparkles size={14} strokeWidth={1.75} /> AI Predicted
                </p>
              </div>
              <div className="mt-4 h-64">
                <RevenueForecastChart />
              </div>
            </div>
            <div className="space-y-6">
              <div className="rounded-2xl bg-black/30 p-5">
                <h2 className="flex items-center gap-2 font-semibold text-white">
                  <Activity size={16} className="text-cyan" strokeWidth={1.75} />
                  AI Recommendations
                </h2>
                <ul className="mt-4 space-y-3 text-sm text-white/80">
                  {RECS.map((line) => (
                    <li key={line} className="flex gap-2">
                      <span className="mt-1.5 h-1.5 w-1.5 shrink-0 rounded-full bg-cyan" />
                      {line}
                    </li>
                  ))}
                </ul>
              </div>
              <div className="rounded-2xl bg-black/30 p-5">
                <h2 className="font-semibold text-white">Model Accuracy</h2>
                {MODELS.map((model) => (
                  <div key={model.name} className="mt-3">
                    <div className="flex justify-between text-xs text-white/60">
                      <span>{model.name}</span>
                      <span>{model.value}%</span>
                    </div>
                    <div className="mt-1 h-2 rounded-full bg-white/10">
                      <div className="h-2 rounded-full bg-brand-gradient" style={{ width: `${model.value}%` }} />
                    </div>
                  </div>
                ))}
              </div>
            </div>
          </div>
        </div>

        <div className="mt-12">
          <div className="flex flex-wrap items-end justify-between gap-3">
            <div>
              <p className="text-eyebrow uppercase text-brand">Workspace</p>
              <h2 className="mt-1 text-2xl font-bold text-ink">Live pipeline</h2>
            </div>
            <Link href="/opportunities" className="text-sm font-semibold text-brand">
              Open opportunities →
            </Link>
          </div>
          {snapshot.isError ? (
            <div className="mt-4">
              <ErrorState
                body="Could not load overview numbers from the backend. Check that the API is running."
                onRetry={() => void snapshot.refetch()}
              />
            </div>
          ) : empty && !snapshot.isPending ? (
            <div className="mt-4">
              <EmptyState
                title="No opportunities yet"
                body="Upload a CSV of historical sales opportunities to score them and see recommended actions."
                actionLabel="Upload opportunities"
                actionHref="/opportunities/upload"
              />
            </div>
          ) : (
            <>
              <div className="mt-4 grid gap-3 sm:grid-cols-2">
                <div className="rounded-2xl bg-white p-4 shadow-sm ring-1 ring-hairline">
                  <p className="text-xs text-ink-muted">Opportunities</p>
                  <p className="mt-1 text-2xl font-bold text-ink">{data?.opportunityTotal ?? "—"}</p>
                </div>
                <div className="rounded-2xl bg-white p-4 shadow-sm ring-1 ring-hairline">
                  <p className="text-xs text-ink-muted">Decisions generated</p>
                  <p className="mt-1 text-2xl font-bold text-ink">{data?.decisionTotal ?? "—"}</p>
                </div>
              </div>
              <div className="mt-4 grid gap-4">
                {recent.map((row) => (
                  <DecisionLedgerEntry key={row.id} decision={decisionToView(row)} variant="compact" />
                ))}
              </div>
            </>
          )}
        </div>
      </WorkspaceShell>
    </div>
  );
}

function Metric({
  icon: Icon,
  label,
  value,
  hint,
  tone,
}: {
  icon: LucideIcon;
  label: string;
  value: string;
  hint: string;
  tone: "blue" | "green" | "amber";
}) {
  const color = tone === "green" ? "text-green" : tone === "amber" ? "text-amber" : "text-cyan";
  return (
    <div className="rounded-2xl bg-black/30 p-4 ring-1 ring-white/5">
      <Icon size={16} className={color} strokeWidth={1.75} />
      <p className="mt-3 text-xs text-white/50">{label}</p>
      <p className="mt-1 truncate text-xl font-bold text-white">{value}</p>
      <p className={`mt-1 text-xs ${color}`}>{hint}</p>
    </div>
  );
}
