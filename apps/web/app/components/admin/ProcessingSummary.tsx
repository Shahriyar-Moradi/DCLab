"use client";

import { Panel } from "@/app/components/ui/Card";
import { CircleCheck, CircleDashed, GitBranch } from "lucide-react";
import type { AdminMlRun } from "@/lib/domain";

type Step = { done: boolean; label: string; detail?: string | null };

function stepsFromSummary(summary: NonNullable<AdminMlRun["processing_summary"]>): Step[] {
  return [
    { done: summary.cleaning_completed, label: "Cleaning completed" },
    { done: summary.feature_engineering_completed, label: "Feature engineering completed" },
    { done: summary.preprocessing_completed, label: "Preprocessing completed" },
    { done: Boolean(summary.train_test_split), label: "Train/test split", detail: summary.train_test_split },
    { done: Boolean(summary.cross_validation), label: "Cross-validation", detail: summary.cross_validation },
    { done: summary.training_completed, label: "Training completed" },
    { done: summary.evaluation_completed, label: "Evaluation completed" },
    { done: summary.predictions_completed, label: "Predictions completed" },
  ];
}

export function ProcessingSummary({ summary }: { summary: AdminMlRun["processing_summary"] }) {
  if (!summary) {
    return (
      <Panel className="mt-6" title="Processing Summary">
        <p className="text-body text-ink-muted">No processing summary yet.</p>
      </Panel>
    );
  }

  const steps = stepsFromSummary(summary);
  const doneCount = steps.filter((s) => s.done).length;

  return (
    <Panel className="mt-6">
      {/* Header */}
      <div className="flex items-center gap-3 border-b border-hairline pb-4">
        <span className="flex h-9 w-9 shrink-0 items-center justify-center rounded-lg bg-navy-soft text-navy">
          <GitBranch className="h-5 w-5" />
        </span>
        <div className="min-w-0">
          <h2 className="font-sans text-section text-ink">
            Processing Summary · {doneCount === steps.length ? "All checks passed" : `${doneCount} / ${steps.length} checks passed`}
          </h2>
        </div>
      </div>

      {/* Checks table */}
      <div className="mt-1">
        <div className="grid grid-cols-[2rem_1fr] gap-x-3 px-1 pb-2 pt-3 text-eyebrow uppercase tracking-[0.06em] text-ink-muted">
          <span>Status</span>
          <span>Job</span>
        </div>
        <ul>
          {steps.map((step, i) => (
            <li
              key={step.label}
              className={`grid grid-cols-[2rem_1fr] items-start gap-x-3 px-1 py-2.5 ${i > 0 ? "border-t border-hairline" : ""}`}
            >
              {/* Status icon */}
              <span className="flex h-5 w-5 items-center justify-center">
                {step.done ? (
                  <CircleCheck className="h-5 w-5 text-green" />
                ) : (
                  <CircleDashed className="h-5 w-5 text-ink-muted/40" />
                )}
              </span>

              {/* Job name + detail */}
              <div className="min-w-0">
                <p className={`text-body ${step.done ? "text-ink" : "text-ink-muted"}`}>
                  {step.label}
                </p>
                {step.detail ? (
                  <p className="mt-0.5 font-mono text-data text-ink-muted">{step.detail}</p>
                ) : null}
              </div>
            </li>
          ))}
        </ul>
      </div>
    </Panel>
  );
}
