"use client";

import { Panel } from "@/app/components/ui/Card";
import { numericMetricEntries } from "@/app/components/admin/format";
import type { AdminMlRun } from "@/lib/domain";
import { ClipboardCheck } from "lucide-react";

export function Evaluation({ run }: { run: AdminMlRun | null }) {
  if (!run) {
    return (
      <Panel className="mt-6">
        <div className="flex items-center gap-3 border-b border-hairline pb-3">
          <span className="flex h-8 w-8 shrink-0 items-center justify-center rounded-lg bg-navy-soft text-navy">
            <ClipboardCheck className="h-4 w-4" />
          </span>
          <h2 className="font-sans text-section text-ink">Evaluation</h2>
        </div>
        <p className="mt-3 text-body text-ink-muted">No evaluation yet.</p>
      </Panel>
    );
  }

  const selected = run.model_comparison.find((row) => row.selected) ?? null;
  const entries = numericMetricEntries(run.final_model?.test_metrics ?? selected?.test_metrics);

  return (
    <Panel className="mt-6">
      <div className="flex items-center gap-3 border-b border-hairline pb-3">
        <span className="flex h-8 w-8 shrink-0 items-center justify-center rounded-lg bg-navy-soft text-navy">
          <ClipboardCheck className="h-4 w-4" />
        </span>
        <h2 className="font-sans text-section text-ink">Evaluation</h2>
      </div>

      {entries.length === 0 ? (
        <p className="mt-3 text-body text-ink-muted">No test metrics persisted yet.</p>
      ) : (
        <dl className="mt-2 grid grid-cols-2 gap-x-4 gap-y-0 md:grid-cols-3">
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
    </Panel>
  );
}
