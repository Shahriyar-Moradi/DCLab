"use client";

import { BOOK_A_DEMO_HREF } from "@/app/components/marketing/links";
import { Eyebrow, MarketingButton, MarketingSection } from "@/app/components/marketing/primitives";
import { GetStartedCTA, ProductPathSection, WhyUsSection } from "@/app/components/marketing/sections";
import { MetricCard } from "@/app/components/ui/MetricCard";
import { useOverviewSnapshot, useSession } from "@/lib/application";
import { formatMoney, formatPercent } from "@/lib/domain";
import { ArrowRight, Sparkles } from "lucide-react";

export default function HomePage() {
  return (
    <div>
      <Hero />
      <ProductPathSection />
      <WhyUsSection />
      <GetStartedCTA />
    </div>
  );
}

function Hero() {
  const { user, loaded } = useSession();
  const snapshot = useOverviewSnapshot(loaded && Boolean(user));
  const data = snapshot.data;
  const showWorkspace = Boolean(user && snapshot.isSuccess && data);

  const topAction = (() => {
    if (!data) return "";
    const counts: Record<string, number> = {};
    for (const row of data.decisions) counts[row.recommended_action] = (counts[row.recommended_action] ?? 0) + 1;
    return Object.entries(counts).sort((a, b) => b[1] - a[1])[0]?.[0]?.replaceAll("_", " ") ?? "";
  })();
  const highConfidenceShare =
    data && data.decisions.length > 0
      ? data.decisions.filter((row) => row.confidence_band === "High").length / data.decisions.length
      : 0;
  const expectedSum = data ? data.decisions.reduce((sum, row) => sum + row.expected_revenue, 0) : 0;

  return (
    <MarketingSection>
      <div className="grid gap-12 lg:grid-cols-2 lg:items-center">
        <div>
          <Eyebrow className="inline-flex items-center gap-1.5">
            <Sparkles size={14} /> The DCLab decision platform
          </Eyebrow>
          <h1 className="mt-4 text-display text-ink">
            We Build AI That <span className="text-brand-gradient">Grows Businesses.</span>
          </h1>
          <p className="mt-5 max-w-xl text-base leading-7 text-ink-muted">
            We build an autonomous decision layer that continuously scores opportunities, recommends the next
            action, and keeps searching for a model that beats the one you have.
          </p>
          <p className="mt-2 max-w-xl text-sm leading-6 text-ink-muted">
            Instead of replacing your team, our AI becomes a decision-making partner that learns from your business
            every day.
          </p>
          <div className="mt-8 flex w-full flex-col gap-3 sm:flex-row sm:flex-wrap">
            <MarketingButton href={BOOK_A_DEMO_HREF}>
              Book a Demo <ArrowRight size={16} />
            </MarketingButton>
            <MarketingButton href="/platform" variant="secondary">
              See Platform
            </MarketingButton>
          </div>
        </div>
        <div className="surface-glass rounded-2xl p-5 sm:p-6">
          {showWorkspace && data ? (
            <>
              <p className="text-eyebrow uppercase tracking-[0.18em] text-ink-muted">Your workspace</p>
              <div className="mt-5 grid grid-cols-2 gap-3">
                <MetricCard label="Opportunities" value={String(data.opportunityTotal)} />
                <MetricCard label="Decisions" value={String(data.decisionTotal)} />
              </div>
              {topAction ? (
                <p className="mt-4 text-body text-ink">
                  Top action <span className="font-medium">{topAction}</span>
                </p>
              ) : null}
              {data.decisions.length > 0 ? (
                <p className="mt-2 text-helper text-ink-muted">
                  High-confidence share {formatPercent(highConfidenceShare)} · expected value in view{" "}
                  {formatMoney(expectedSum)}
                </p>
              ) : null}
            </>
          ) : (
            <>
              <p className="text-eyebrow uppercase tracking-[0.18em] text-ink-muted">Product surfaces</p>
              <h2 className="mt-2 text-section text-ink">Opportunity ledger, decisions, and Labs</h2>
              <ul className="mt-5 space-y-3 text-body text-ink-muted">
                <li>Upload historical opportunities as CSV</li>
                <li>Record an audited recommended action</li>
                <li>Compare models in the Experimentation Lab</li>
              </ul>
              <p className="mt-6 text-helper text-ink-muted">
                Sign in to see live totals from your workspace. This panel does not invent customer metrics.
              </p>
            </>
          )}
        </div>
      </div>
    </MarketingSection>
  );
}
