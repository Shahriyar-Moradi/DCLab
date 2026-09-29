"use client";

import { Panel } from "@/app/components/ui/Card";
import { DataTable } from "@/app/components/ui/DataTable";
import type { AdminMlRun } from "@/lib/domain";
import { cn } from "@/lib/cn";

function formatScore(value: number | null | undefined): string {
  if (value == null || Number.isNaN(value)) return "—";
  return value.toFixed(3);
}

function cvScore(row: AdminMlRun["model_comparison"][number]): number | null {
  if (row.cv_auc != null) return row.cv_auc;
  const r2 = row.cv_metrics.r2;
  return typeof r2 === "number" ? r2 : null;
}

function testScore(row: AdminMlRun["model_comparison"][number]): number | null {
  if (row.test_auc != null) return row.test_auc;
  const r2 = row.test_metrics?.r2;
  return typeof r2 === "number" ? r2 : null;
}

function initials(name: string): string {
  const words = name.trim().split(/\s+/);
  if (words.length >= 2) return (words[0][0] + words[1][0]).toUpperCase();
  return name.slice(0, 2).toUpperCase();
}

export function ModelComparison({ rows }: { rows: AdminMlRun["model_comparison"] }) {
  if (rows.length === 0) {
    return (
      <Panel className="mt-6" title="Model Comparison">
        <p className="text-body text-ink-muted">No candidate metrics persisted yet.</p>
      </Panel>
    );
  }

  const scores = rows
    .map((row) => ({ name: row.name, value: cvScore(row), selected: row.selected }))
    .filter((row): row is { name: string; value: number; selected: boolean } => row.value != null);
  const max = Math.max(...scores.map((row) => row.value), 0.0001);

  return (
    <Panel className="mt-6" title="Model Comparison">
      {scores.length > 0 && (
        <div className="mb-6">
          <p className="mb-3 text-body text-ink-muted">
            {rows.length} {rows.length === 1 ? "model" : "models"} evaluated
          </p>
          <ul className="space-y-2">
            {scores.map((row) => (
              <li
                key={row.name}
                className={cn(
                  "flex items-center gap-3 rounded-lg px-3 py-2.5 transition-ui",
                  row.selected ? "bg-green/10 ring-1 ring-green/20" : "hover:bg-navy-soft/40",
                )}
              >
                {/* Initials circle */}
                <span
                  className={cn(
                    "flex h-8 w-8 shrink-0 items-center justify-center rounded-full text-label font-semibold",
                    row.selected ? "bg-green text-paper-raised" : "bg-navy-soft text-ink-muted",
                  )}
                >
                  {initials(row.name)}
                </span>

                {/* Name + selected badge */}
                <div className="flex min-w-0 flex-1 items-center gap-2">
                  <span className="truncate text-body text-ink">{row.name}</span>
                  {row.selected && (
                    <span className="shrink-0 rounded-full bg-green/15 px-2 py-0.5 text-eyebrow uppercase tracking-[0.06em] text-green">
                      selected
                    </span>
                  )}
                </div>

                {/* Score */}
                <span className="shrink-0 font-mono text-data text-ink">{formatScore(row.value)}</span>
              </li>
            ))}
          </ul>
        </div>
      )}

      <DataTable
        columns={[
          {
            id: "model",
            header: "Model",
            cell: (row) => (
              <span>
                {row.name}
                {row.selected ? (
                  <span className="ml-2 text-eyebrow uppercase tracking-[0.06em] text-ink-muted">selected</span>
                ) : null}
              </span>
            ),
          },
          { id: "cv", header: "CV", mono: true, cell: (row) => formatScore(cvScore(row)) },
          { id: "test", header: "Test", mono: true, cell: (row) => formatScore(testScore(row)) },
        ]}
        rows={rows}
        rowKey={(row) => row.model_family}
      />
    </Panel>
  );
}
