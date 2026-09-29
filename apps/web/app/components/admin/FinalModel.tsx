"use client";

import { Panel } from "@/app/components/ui/Card";
import { numericMetricEntries } from "@/app/components/admin/format";
import type { AdminMlRun } from "@/lib/domain";
import { Award } from "lucide-react";

function MetricTable({ title, entries }: { title: string; entries: [string, number][] }) {
  return (
    <div className="min-w-0">
      <div className="border-b border-hairline pb-2">
        <h3 className="font-sans text-body font-medium text-ink">{title}</h3>
      </div>
      {entries.length === 0 ? (
        <p className="mt-3 text-body text-ink-muted">No metrics recorded.</p>
      ) : (
        <ul className="mt-1">
          {entries.map(([name, value], i) => (
            <li
              key={name}
              className={`flex items-center justify-between gap-4 px-1 py-2 ${i > 0 ? "border-t border-hairline" : ""}`}
            >
              <span className="font-mono text-data text-ink-muted">{name}</span>
              <span className="font-mono text-data text-ink">
                {Number.isInteger(value) ? String(value) : value.toFixed(4)}
              </span>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}

export function FinalModel({ model }: { model: AdminMlRun["final_model"] }) {
  if (!model) {
    return (
      <Panel className="mt-6">
        <div className="flex items-center gap-3 border-b border-hairline pb-4">
          <span className="flex h-9 w-9 shrink-0 items-center justify-center rounded-lg bg-navy-soft text-navy">
            <Award className="h-5 w-5" />
          </span>
          <h2 className="font-sans text-section text-ink">Final Model</h2>
        </div>
        <p className="mt-4 text-body text-ink-muted">No model has been locked yet.</p>
      </Panel>
    );
  }

  const modelName = model.selected_model ?? model.model_family ?? "—";
  const cvEntries = numericMetricEntries(model.cv_metrics);
  const testEntries = numericMetricEntries(model.test_metrics);

  return (
    <Panel className="mt-6">
      {/* Header */}
      <div className="flex items-center gap-3 border-b border-hairline pb-4">
        <span className="flex h-9 w-9 shrink-0 items-center justify-center rounded-lg bg-navy-soft text-navy">
          <Award className="h-5 w-5" />
        </span>
        <div className="min-w-0 flex-1">
          <h2 className="font-sans text-section text-ink">Final Model</h2>
        </div>
        <span className="shrink-0 rounded-full bg-green/15 px-3 py-1 text-eyebrow uppercase tracking-[0.06em] text-green">
          {modelName}
        </span>
      </div>

      {/* Metric tables */}
      <div className="mt-4 grid gap-6 md:grid-cols-2">
        <MetricTable title="CV performance" entries={cvEntries} />
        <MetricTable title="Test performance" entries={testEntries} />
      </div>
    </Panel>
  );
}
