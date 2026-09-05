"use client";

import { OpenDatasetPanel } from "@/app/components/labs/OpenDatasetPanel";
import { ProblemWorkspace } from "@/app/components/labs/ProblemWorkspace";
import { CATEGORY_META, CATEGORY_ORDER } from "@/app/components/insights/categoryMeta";
import { ErrorState } from "@/app/components/ui/ErrorState";
import { FilterBar } from "@/app/components/ui/FilterBar";
import { PageHeader } from "@/app/components/ui/PageHeader";
import { SectionHeader } from "@/app/components/ui/SectionHeader";
import { Skeleton } from "@/app/components/ui/Skeleton";
import { useLabProblems } from "@/lib/application";
import { type ClientLabProblem, type InsightCategoryValue } from "@/lib/domain";
import { useMemo, useState } from "react";

export default function ClientLabsPage() {
  const problems = useLabProblems();
  const [category, setCategory] = useState<InsightCategoryValue>(CATEGORY_ORDER[0]);

  const byCategory = useMemo(() => {
    const grouped = new Map<InsightCategoryValue, ClientLabProblem[]>();
    for (const problem of problems.data ?? []) {
      const list = grouped.get(problem.category) ?? [];
      list.push(problem);
      grouped.set(problem.category, list);
    }
    return grouped;
  }, [problems.data]);

  if (problems.isPending) {
    return (
      <div>
        <PageHeader eyebrow="ML workspace" title="Labs" description="Loading available ML workflows and saved datasets." />
        <Skeleton className="h-48" />
        <Skeleton className="mt-4 h-64" />
      </div>
    );
  }

  if (problems.isError) {
    return (
      <div>
        <PageHeader eyebrow="ML workspace" title="Labs" description="Upload data or run a bounded business problem trial." />
        <ErrorState
          body="Could not load the trial problems from the backend. Check that the API is running."
          onRetry={() => void problems.refetch()}
        />
      </div>
    );
  }

  const meta = CATEGORY_META[category];
  const items = byCategory.get(category) ?? [];

  return (
    <div>
      <PageHeader
        eyebrow="ML workspace"
        title="Labs"
        description="Upload a dataset for automatic analysis, or run a bounded problem trial on sample data or a matching CSV."
      />
      <FilterBar
        ariaLabel="Business area"
        value={category}
        onChange={(id) => setCategory(id as InsightCategoryValue)}
        options={CATEGORY_ORDER.map((item) => ({ id: item, label: item }))}
      />
      <SectionHeader className="mt-8" title={category} description={meta.blurb} />
      <div className="mt-5 grid gap-8">
        <OpenDatasetPanel key={category} category={category} />
        <ProblemWorkspace key={category} problems={items} />
      </div>
    </div>
  );
}
