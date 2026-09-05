"use client";

import { InsightCard } from "@/app/components/insights/InsightCard";
import { CATEGORY_META, CATEGORY_ORDER } from "@/app/components/insights/categoryMeta";
import { Button } from "@/app/components/ui/Button";
import { EmptyState } from "@/app/components/ui/EmptyState";
import { ErrorState } from "@/app/components/ui/ErrorState";
import { FilterBar } from "@/app/components/ui/FilterBar";
import { PageHeader } from "@/app/components/ui/PageHeader";
import { SectionHeader } from "@/app/components/ui/SectionHeader";
import { Skeleton } from "@/app/components/ui/Skeleton";
import { useInsights } from "@/lib/application";
import { type ClientInsight, type InsightCategoryValue } from "@/lib/domain";
import { useState } from "react";

export default function InsightsPage() {
  const insights = useInsights();
  const [category, setCategory] = useState("");

  if (insights.isPending) {
    return (
      <div>
        <InsightsHeader />
        <div className="grid gap-4 md:grid-cols-3">
          <Skeleton className="h-40" />
          <Skeleton className="h-40" />
          <Skeleton className="h-40" />
        </div>
      </div>
    );
  }

  if (insights.isError) {
    return (
      <div>
        <InsightsHeader fetching={insights.isFetching} onRefresh={() => void insights.refetch()} />
        <ErrorState
          body="Could not load insights from the backend. Check that the API is running."
          onRetry={() => void insights.refetch()}
        />
      </div>
    );
  }

  const categories = insights.data?.categories ?? [];
  const totalInsights = categories.reduce((sum, group) => sum + group.insights.length, 0);
  const byCategory = new Map(categories.map((group) => [group.category, group.insights]));

  return (
    <div>
      <InsightsHeader fetching={insights.isFetching} onRefresh={() => void insights.refetch()} />
      {totalInsights === 0 ? (
        <EmptyState
          title="No insights yet"
          body="Insights appear here once your workspace has recommendations for a business area — ask your DCLab team to prepare one, or try the trial prototypes in Labs."
        />
      ) : (
        <div className="space-y-10">
          <FilterBar
            ariaLabel="Business area"
            value={category}
            onChange={setCategory}
            options={[
              { id: "", label: "All areas" },
              ...CATEGORY_ORDER.map((item) => ({ id: item, label: item })),
            ]}
          />
          {(category ? [category as InsightCategoryValue] : CATEGORY_ORDER).map((item) => (
            <InsightSection key={item} category={item} insights={byCategory.get(item) ?? []} />
          ))}
        </div>
      )}
    </div>
  );
}

function InsightsHeader({
  fetching = false,
  onRefresh,
}: {
  fetching?: boolean;
  onRefresh?: () => void;
}) {
  return (
    <PageHeader
      eyebrow="Workspace"
      title="Insights"
      description="Recommendations grouped by business area, using the latest translated results for this workspace."
      actions={
        <Button variant="secondary" disabled={!onRefresh || fetching} onClick={onRefresh}>
          {fetching ? "Refreshing…" : "Refresh"}
        </Button>
      }
    />
  );
}

function InsightSection({ category, insights }: { category: InsightCategoryValue; insights: ClientInsight[] }) {
  const meta = CATEGORY_META[category];
  const Icon = meta.icon;
  return (
    <section>
      <SectionHeader
        title={category}
        description={meta.blurb}
        actions={
          <span className="flex h-9 w-9 items-center justify-center rounded-full bg-navy-soft text-navy">
            <Icon size={18} aria-hidden />
          </span>
        }
      />
      {insights.length === 0 ? (
        <p className="mt-4 rounded-xl border border-hairline bg-paper-raised px-6 py-8 text-center text-body text-ink-muted">
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
