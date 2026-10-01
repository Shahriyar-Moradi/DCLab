"use client";

import { Panel } from "@/app/components/ui/Card";
import { numericMetricEntries } from "@/app/components/admin/format";
import type { AdminMlRun } from "@/lib/domain";
import { Award } from "lucide-react";

function MetricGrid({ title, entries }: { title: string; entries: [string, number][] }) {
  return (
    <div className="min-w-0">
      <h3 className="border-b border-hairline pb-1.5 font-sans text-body font-medium text-ink">{title}</h3>
      {entries.length === 0 ? (
        <p className="mt-2 text-body text-ink-muted">No metrics recorded.</p>
      ) : (
        <dl className="mt-1 grid grid-cols-2 gap-x-4 gap-y-0">
          {entries.map(([name, value]) => (
            <div key={name} className="flex items-center justify-between gap-2 border-b border-hairline/50 py-1">
              <dt className="font-mono text-data text-ink-muted">{name}</dt>
              <dd className="font-mono text-data text-ink">
                {Number.isInteger(value) ? String(value) : value.toFixed(4)}
              </dd>
            </div>
          ))}
        </dl>
      )}
    </div>
  );
}

export function FinalModel({ model }: { model: AdminMlRun["final_model"] }) {
  if (!model) {
    return (
      <Panel className="mt-6">
        <div className="flex items-center gap-3 border-b border-hairline pb-3">
          <span className="flex h-8 w-8 shrink-0 items-center justify-center rounded-lg bg-navy-soft text-navy">
            <Award className="h-4 w-4" />
          </span>
          <h2 className="font-sans text-section text-ink">Final Model</h2>
        </div>
        <p className="mt-3 text-body text-ink-muted">No model has been locked yet.</p>
      </Panel>
    );
  }

  const modelName = model.selected_model ?? model.model_family ?? "—";
  const cvEntries = numericMetricEntries(model.cv_metrics);
  const testEntries = numericMetricEntries(model.test_metrics);

  return (
    <Panel className="mt-6">
      <div className="flex items-center gap-3 border-b border-hairline pb-3">
        <span className="flex h-8 w-8 shrink-0 items-center justify-center rounded-lg bg-navy-soft text-navy">
          <Award className="h-4 w-4" />
        </span>
        <div className="min-w-0 flex-1">
          <h2 className="font-sans text-section text-ink">Final Model</h2>
        </div>
        <span className="shrink-0 rounded-full bg-green/15 px-3 py-1 text-eyebrow uppercase tracking-[0.06em] text-green">
          {modelName}
        </span>
      </div>

      <div className="mt-3 grid gap-6 md:grid-cols-2">
        <MetricGrid title="CV performance" entries={cvEntries} />
        <MetricGrid title="Test performance" entries={testEntries} />
      </div>
    </Panel>
  );
}
