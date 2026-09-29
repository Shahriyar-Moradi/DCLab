"use client";

import { Panel } from "@/app/components/ui/Card";
import { numericMetricEntries } from "@/app/components/admin/format";
import type { AdminMlRun } from "@/lib/domain";
import { ClipboardCheck } from "lucide-react";

export function Evaluation({ run }: { run: AdminMlRun | null }) {
  if (!run) {
    return (
      <Panel className="mt-6">
        <div className="flex items-center gap-3 border-b border-hairline pb-4">
          <span className="flex h-9 w-9 shrink-0 items-center justify-center rounded-lg bg-navy-soft text-navy">
            <ClipboardCheck className="h-5 w-5" />
          </span>
          <h2 className="font-sans text-section text-ink">Evaluation</h2>
        </div>
        <p className="mt-4 text-body text-ink-muted">No evaluation yet.</p>
      </Panel>
    );
  }

  const selected = run.model_comparison.find((row) => row.selected) ?? null;
  const entries = numericMetricEntries(run.final_model?.test_metrics ?? selected?.test_metrics);

  return (
    <Panel className="mt-6">
      {/* Header */}
      <div className="flex items-center gap-3 border-b border-hairline pb-4">
        <span className="flex h-9 w-9 shrink-0 items-center justify-center rounded-lg bg-navy-soft text-navy">
          <ClipboardCheck className="h-5 w-5" />
        </span>
        <h2 className="font-sans text-section text-ink">Evaluation</h2>
      </div>

      {entries.length === 0 ? (
        <p className="mt-4 text-body text-ink-muted">No test metrics persisted yet.</p>
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
    </Panel>
  );
}
