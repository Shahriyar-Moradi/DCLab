"use client";

import { ErrorState } from "@/app/components/ui/ErrorState";
import { Skeleton } from "@/app/components/ui/Skeleton";
import { useLabCandidates, useLabComparison, useLabExperiment, useLabReport } from "@/lib/application";
import { useParams } from "next/navigation";

export default function LabExperimentDetailPage() {
  const params = useParams<{ id: string }>();
  const experiment = useLabExperiment(params.id);
  const report = useLabReport(params.id);
  const candidates = useLabCandidates(params.id);
  const comparison = useLabComparison(params.id);
  if (experiment.isPending) return <Skeleton className="h-96" />;
  if (experiment.isError || !experiment.data) {
    return <ErrorState body="Experiment not found." onRetry={() => void experiment.refetch()} />;
  }
  const result = (experiment.data.result ?? {}) as {
    funnel?: Record<string, number>;
    fusion?: string;
    test_metrics?: Record<string, unknown>;
    best_single?: { model_family?: string; score?: number };
    feature_group_scores?: Record<string, number>;
    combination_table?: Array<{ groups: string[]; best_score: number }>;
    leakage?: { risk?: string };
  };
  return (
    <div>
      <p className="font-body text-eyebrow uppercase tracking-[0.06em] text-ink-muted">{experiment.data.status}</p>
      <h1 className="mt-2 font-mono text-title text-ink">{experiment.data.id}</h1>
      <div className="mt-8 grid gap-4 md:grid-cols-3">
        <Card label="Fusion" value={String(result.fusion ?? "—")} />
        <Card label="Best family" value={String(result.best_single?.model_family ?? "—")} />
        <Card label="Leakage" value={String(result.leakage?.risk ?? "—")} />
      </div>
      <h2 className="mt-10 font-display text-section text-ink">Funnel</h2>
      <pre className="mt-3 overflow-auto rounded bg-paper-raised p-4 font-mono text-data text-ink">
        {JSON.stringify(result.funnel, null, 2)}
      </pre>
      <h2 className="mt-10 font-display text-section text-ink">Candidates</h2>
      <ul className="mt-3 rounded bg-paper-raised p-4">
        {(candidates.data ?? []).map((row) => (
          <li key={String(row.candidate_id)} className="border-t border-hairline py-2 font-mono text-data text-ink">
            {row.model_family} · {(row.feature_groups ?? []).join("+")} · {row.status}
            {typeof row.score === "number" ? ` · ${row.score.toFixed(4)}` : ""}
          </li>
        ))}
      </ul>
      <h2 className="mt-10 font-display text-section text-ink">Comparison</h2>
      <pre className="mt-3 overflow-auto rounded bg-paper-raised p-4 font-mono text-data text-ink">
        {JSON.stringify(comparison.data ?? {}, null, 2)}
      </pre>
      <h2 className="mt-10 font-display text-section text-ink">Test metrics</h2>
      <pre className="mt-3 overflow-auto rounded bg-paper-raised p-4 font-mono text-data text-ink">
        {JSON.stringify(result.test_metrics, null, 2)}
      </pre>
      <h2 className="mt-10 font-display text-section text-ink">Feature groups</h2>
      <ul className="mt-3 rounded bg-paper-raised p-4">
        {Object.entries(result.feature_group_scores ?? {}).map(([name, score]) => (
          <li key={name} className="border-t border-hairline py-2 font-mono text-data text-ink">
            {name}: {Number(score).toFixed(4)}
          </li>
        ))}
      </ul>
      <h2 className="mt-10 font-display text-section text-ink">Combinations</h2>
      <ul className="mt-3 rounded bg-paper-raised p-4">
        {(result.combination_table ?? []).map((row) => (
          <li key={row.groups.join("+")} className="border-t border-hairline py-2 font-mono text-data text-ink">
            {row.groups.join(" + ")}: {row.best_score.toFixed(4)}
          </li>
        ))}
      </ul>
      <h2 className="mt-10 font-display text-section text-ink">Ensemble</h2>
      <p className="mt-3 font-body text-body text-ink">
        Fusion <span className="font-mono text-data">{String(result.fusion ?? "—")}</span> is kept only when it beats
        the best single model on validation. Test is scored once.
      </p>
      <h2 className="mt-10 font-display text-section text-ink">Report</h2>
      <pre className="mt-3 overflow-auto whitespace-pre-wrap rounded bg-paper-raised p-4 font-body text-body text-ink">
        {report.data?.markdown ?? "No report yet."}
      </pre>
    </div>
  );
}

function Card({ label, value }: { label: string; value: string }) {
  return (
    <div className="rounded bg-paper-raised p-6">
      <p className="font-body text-eyebrow uppercase tracking-[0.06em] text-ink-muted">{label}</p>
      <p className="mt-2 font-mono text-data text-ink">{value}</p>
    </div>
  );
}
