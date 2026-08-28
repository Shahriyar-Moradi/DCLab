"use client";

import { InsightCard } from "@/app/components/insights/InsightCard";
import { CATEGORY_META, CATEGORY_ORDER } from "@/app/components/insights/categoryMeta";
import { EmptyState } from "@/app/components/ui/EmptyState";
import { ErrorState } from "@/app/components/ui/ErrorState";
import { Skeleton } from "@/app/components/ui/Skeleton";
import { useInsights } from "@/lib/application";
import { type ClientInsight, type InsightCategoryValue } from "@/lib/domain";

export default function InsightsPage() {
  const insights = useInsights();

  if (insights.isPending) {
    return (
      <div>
        <InsightsHero />
        <div className="mx-auto max-w-7xl px-5 py-12 lg:px-8">
          <div className="grid gap-4 md:grid-cols-3">
            <Skeleton className="h-40" />
            <Skeleton className="h-40" />
            <Skeleton className="h-40" />
          </div>
        </div>
      </div>
    );
  }

  if (insights.isError) {
    return (
      <div>
        <InsightsHero />
        <div className="mx-auto max-w-3xl px-5 py-12">
          <ErrorState
            body="Could not load insights from the backend. Check that the API is running."
            onRetry={() => void insights.refetch()}
          />
        </div>
      </div>
    );
  }

  const categories = insights.data?.categories ?? [];
  const totalInsights = categories.reduce((sum, group) => sum + group.insights.length, 0);
  const byCategory = new Map(categories.map((group) => [group.category, group.insights]));

  return (
    <div>
      <InsightsHero />
      <div className="mx-auto max-w-7xl px-5 pb-16 lg:px-8">
        {totalInsights === 0 ? (
          <EmptyState
            title="No insights yet"
            body="Insights appear here once your workspace has recommendations for a business area — ask your DCLab team to prepare one, or try the trial prototypes in Labs."
          />
        ) : (
          <div className="space-y-12">
            {CATEGORY_ORDER.map((category) => (
              <InsightSection key={category} category={category} insights={byCategory.get(category) ?? []} />
            ))}
          </div>
        )}
      </div>
    </div>
  );
}

function InsightsHero() {
  return (
    <section className="bg-midnight px-5 pb-10 pt-16 text-center lg:px-8 lg:pt-20">
      <p className="text-eyebrow uppercase text-cyan">Insights</p>
      <h1 className="mt-4 text-4xl font-bold text-white lg:text-5xl">What Matters, By Business Area.</h1>
      <p className="mx-auto mt-3 max-w-2xl text-white/65">
        Every recommendation in plain language — organized the way you run the business, not the way it was built.
      </p>
    </section>
  );
}

function InsightSection({ category, insights }: { category: InsightCategoryValue; insights: ClientInsight[] }) {
  const meta = CATEGORY_META[category];
  const Icon = meta.icon;
  return (
    <section>
      <div className="flex items-center gap-3">
        <span className="flex h-9 w-9 items-center justify-center rounded-full bg-navy-soft/60 text-navy">
          <Icon size={18} />
        </span>
        <div>
          <h2 className="font-display text-section text-ink">{category}</h2>
          <p className="font-body text-body text-ink-muted">{meta.blurb}</p>
        </div>
      </div>
      {insights.length === 0 ? (
        <p className="mt-4 rounded bg-paper-raised px-6 py-8 text-center font-body text-body text-ink-muted">
          No {category.toLowerCase()} insights yet.
        </p>
      ) : (
        <div className="mt-4 grid gap-4 md:grid-cols-2 xl:grid-cols-3">
          {insights.map((insight) => (
            <InsightCard key={`${category}-${insight.subject_id}-${insight.headline}`} insight={insight} />
          ))}
        </div>
      )}
    </section>
  );
}
