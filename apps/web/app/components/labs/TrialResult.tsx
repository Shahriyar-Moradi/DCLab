"use client";

import { InsightCard } from "@/app/components/insights/InsightCard";
import { Card } from "@/app/components/ui/Card";
import type { ClientLabRun } from "@/lib/domain";

export function TrialResult({ run }: { run: ClientLabRun }) {
  if (run.status === "failed") {
    return (
      <Card className="mt-5 px-5 py-4" role="alert">
        <p className="product-eyebrow">Failed</p>
        <p className="mt-2 text-body text-ink">{run.failure_reason ?? "This run could not be completed."}</p>
      </Card>
    );
  }
  return (
    <div className="mt-5 space-y-3">
      <p className="product-eyebrow">
        Results from {run.data_source === "sample" ? "sample data" : "your file"} · {run.row_count.toLocaleString()} rows
      </p>
      <div className="grid gap-3">
        {run.insights.map((insight) => (
          <InsightCard key={`${insight.subject_id}-${insight.headline}`} insight={insight} />
        ))}
      </div>
    </div>
  );
}
