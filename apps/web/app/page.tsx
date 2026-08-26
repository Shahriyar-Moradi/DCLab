"use client";

import { CaseStudySection, GetStartedCTA, WhyUsSection } from "@/app/components/marketing/sections";
import { useOverviewSnapshot } from "@/lib/application";
import { formatMoney, formatPercent } from "@/lib/domain";
import { ArrowRight, Beaker, ListChecks, ScanSearch, Sparkles, TrendingUp, Upload, Wallet } from "lucide-react";
import Link from "next/link";

const BOOK_A_DEMO_HREF = "mailto:hello@decision.ai?subject=Book%20a%20demo";

const STEPS = [
  {
    icon: Upload,
    title: "Upload opportunities",
    body: "Drop in a CSV of historical sales opportunities — external ID, amount, stage, source, owner.",
    href: "/opportunities/upload",
  },
  {
    icon: ScanSearch,
    title: "Score & decide",
    body: "The decision engine scores each row and returns a recommended action with confidence and reasoning.",
    href: "/decisions",
  },
  {
    icon: Beaker,
    title: "Experiment in the Lab",
    body: "Profile new datasets, define prediction tasks, and run a budgeted candidate search to beat the baseline.",
    href: "/lab",
  },
];

export default function HomePage() {
  return (
    <div>
      <Hero />
      <HowItWorks />
      <WhyUsSection />
      <CaseStudySection />
      <GetStartedCTA />
    </div>
  );
}

function Hero() {
  const snapshot = useOverviewSnapshot();
  const data = snapshot.data;
  const topAction = (() => {
    if (!data) return "—";
    const counts: Record<string, number> = {};
    for (const row of data.decisions) counts[row.recommended_action] = (counts[row.recommended_action] ?? 0) + 1;
    return Object.entries(counts).sort((a, b) => b[1] - a[1])[0]?.[0]?.replaceAll("_", " ") ?? "—";
  })();
  const avgConfidence = data && data.decisions.length > 0 ? data.decisions.reduce((sum, row) => sum + row.confidence, 0) / data.decisions.length : 0;
  const expectedSum = data ? data.decisions.reduce((sum, row) => sum + row.expected_revenue, 0) : 0;

  return (
    <section className="relative overflow-hidden bg-paper">
      <div className="mx-auto grid max-w-7xl gap-10 px-5 py-20 lg:grid-cols-2 lg:items-center lg:px-8 lg:py-28">
        <div>
          <p className="inline-flex items-center gap-1.5 text-eyebrow uppercase text-brand">
            <Sparkles size={14} /> The AI Decision Intelligence Company
          </p>
          <h1 className="mt-4 text-4xl font-bold leading-tight tracking-tight text-ink lg:text-5xl">
            We Build AI That <span className="text-brand-gradient">Grows Businesses.</span>
          </h1>
          <p className="mt-5 max-w-xl text-base leading-7 text-ink-muted">
            We build an autonomous decision layer that continuously scores opportunities, recommends the next
            action, and keeps searching for a model that beats the one you have.
          </p>
          <p className="mt-2 max-w-xl text-sm leading-6 text-ink-muted/80">
            Instead of replacing your team, our AI becomes a decision-making partner that learns from your business
            every day.
          </p>
          <div className="mt-8 flex flex-wrap gap-3">
            <Link
              href={BOOK_A_DEMO_HREF}
              className="bg-brand-gradient shadow-brand inline-flex items-center gap-2 rounded-full px-6 py-3 text-sm font-semibold text-white"
            >
              Book a Demo <ArrowRight size={16} />
            </Link>
            <Link
              href="/platform"
              className="inline-flex items-center gap-2 rounded-full border border-hairline bg-paper-raised px-6 py-3 text-sm font-semibold text-ink shadow-sm"
            >
              See Platform
            </Link>
          </div>
        </div>
        <div className="relative">
          <div className="rounded-3xl bg-paper-raised p-6 shadow-brand ring-1 ring-hairline">
            <div className="flex items-center justify-between">
              <p className="flex items-center gap-2 text-sm font-semibold text-ink">
                <ScanSearch size={16} className="text-brand" /> AI Decision Engine
              </p>
              <span className="flex items-center gap-1.5 text-xs font-medium text-green">
                <span className="h-1.5 w-1.5 rounded-full bg-green" /> Live
              </span>
            </div>
            <div className="mt-5 grid grid-cols-2 gap-3">
              <MetricTile icon={Wallet} label="Opportunities" value={snapshot.isPending ? "…" : String(data?.opportunityTotal ?? 0)} />
              <MetricTile icon={ListChecks} label="Decisions" value={snapshot.isPending ? "…" : String(data?.decisionTotal ?? 0)} />
            </div>
            <div className="mt-3 flex items-center justify-between rounded-xl bg-navy-soft px-4 py-3 text-sm">
              <span className="flex items-center gap-2 font-medium text-ink">
                <Sparkles size={14} className="text-brand" /> Top action
              </span>
              <span className="font-semibold text-ink">{snapshot.isPending ? "…" : topAction}</span>
            </div>
            <div className="mt-2 flex items-center justify-between rounded-xl bg-navy-soft px-4 py-3 text-sm">
              <span className="flex items-center gap-2 font-medium text-ink">
                <TrendingUp size={14} className="text-brand" /> Prediction model
              </span>
              <span className="font-semibold text-ink">{snapshot.isPending ? "…" : formatPercent(avgConfidence)} confidence</span>
            </div>
            <div className="mt-4 rounded-2xl bg-brand-gradient p-4">
              <p className="text-sm font-semibold text-white">Expected value in view</p>
              <p className="mt-1 text-2xl font-bold text-white">
                {snapshot.isPending ? "…" : snapshot.isError ? "API offline" : formatMoney(expectedSum)}
              </p>
              <p className="mt-1 text-xs text-white/80">From decisions currently in the ledger</p>
            </div>
          </div>
        </div>
      </div>
    </section>
  );
}

function MetricTile({ icon: Icon, label, value }: { icon: typeof Wallet; label: string; value: string }) {
  return (
    <div className="rounded-xl bg-navy-soft p-3">
      <Icon size={14} className="text-brand" />
      <p className="mt-2 text-[0.65rem] uppercase tracking-wide text-ink-muted">{label}</p>
      <p className="mt-1 truncate text-lg font-bold text-ink">{value}</p>
    </div>
  );
}

function HowItWorks() {
  return (
    <section className="mx-auto max-w-7xl px-5 py-20 lg:px-8">
      <p className="text-center text-eyebrow uppercase text-brand">How it works</p>
      <h2 className="mt-4 text-center text-3xl font-bold text-ink lg:text-4xl">From Upload to Decision to Proof</h2>
      <div className="mt-12 grid gap-6 lg:grid-cols-3">
        {STEPS.map((step, index) => (
          <Link
            key={step.title}
            href={step.href}
            className="group rounded-2xl bg-paper-raised p-6 shadow-sm ring-1 ring-hairline transition hover:shadow-md hover:ring-navy/30"
          >
            <span className="font-mono text-xs text-ink-muted">Step {index + 1}</span>
            <div className="mt-3 flex h-10 w-10 items-center justify-center rounded-xl bg-navy-soft text-brand">
              <step.icon size={20} strokeWidth={1.75} />
            </div>
            <h3 className="mt-4 text-base font-semibold text-ink">{step.title}</h3>
            <p className="mt-2 text-sm leading-6 text-ink-muted">{step.body}</p>
            <span className="mt-4 inline-flex items-center gap-1 text-sm font-semibold text-brand opacity-0 transition group-hover:opacity-100">
              Open <ArrowRight size={14} />
            </span>
          </Link>
        ))}
      </div>
    </section>
  );
}
