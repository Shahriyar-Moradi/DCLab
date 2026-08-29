"use client";

import { Button } from "@/app/components/ui/Button";
import { ErrorState } from "@/app/components/ui/ErrorState";
import { Skeleton } from "@/app/components/ui/Skeleton";
import { downloadLabPredictions, useLabUpload, useSession } from "@/lib/application";
import type { LabRunOutcome } from "@/lib/domain";
import Link from "next/link";
import { useParams } from "next/navigation";
import { useState } from "react";

const PROCESSING_TITLE = "Analyzing your data...";
const PROCESSING_HINT = "This may take a while.";

function formatChance(value: number | null): string {
  if (value === null || Number.isNaN(value)) return "—";
  return `${(value * 100).toFixed(1)}%`;
}

function CompletedOutcome({
  outcome,
  runId,
}: {
  outcome: LabRunOutcome;
  runId: string;
}) {
  const [busy, setBusy] = useState(false);
  const [downloadError, setDownloadError] = useState<string | null>(null);
  const [showPredictions, setShowPredictions] = useState(false);

  async function onDownload() {
    setDownloadError(null);
    setBusy(true);
    try {
      await downloadLabPredictions(runId);
    } catch {
      setDownloadError("Could not download the results.");
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="mt-8 space-y-8">
      <div>
        <h2 className="font-display text-section text-ink">{outcome.title}</h2>
        <p className="mt-4 max-w-2xl font-body text-body text-ink">{outcome.summary}</p>
      </div>

      <div className="rounded bg-paper-raised p-6">
        <p className="font-body text-eyebrow uppercase tracking-[0.06em] text-ink-muted">How it did</p>
        <p className="mt-2 font-display text-title text-ink">{outcome.performance_percent.toFixed(1)}%</p>
        <p className="mt-1 font-body text-body text-ink-muted">{outcome.performance_summary}</p>
        <p className="mt-6 font-body text-eyebrow uppercase tracking-[0.06em] text-ink-muted">
          Predictions generated
        </p>
        <p className="mt-2 font-display text-title text-ink">{outcome.prediction_count.toLocaleString()}</p>
      </div>

      <div className="flex flex-wrap gap-3">
        {outcome.predictions.length > 0 ? (
          <Button variant="secondary" onClick={() => setShowPredictions((open) => !open)}>
            {showPredictions ? "Hide predictions" : "View predictions"}
          </Button>
        ) : null}
        {outcome.download_available ? (
          <Button variant="secondary" onClick={() => void onDownload()} disabled={busy}>
            {busy ? "Preparing…" : "Download results"}
          </Button>
        ) : null}
      </div>
      {downloadError ? <p className="font-body text-body text-oxblood">{downloadError}</p> : null}

      {showPredictions && outcome.predictions.length > 0 ? (
        <div className="max-h-[28rem] overflow-auto rounded bg-paper-raised">
          <table className="w-full text-left">
            <thead className="sticky top-0 bg-paper-raised">
              <tr>
                <th className="px-4 py-3 font-body text-eyebrow uppercase tracking-[0.06em] text-ink-muted">
                  Prediction
                </th>
                <th className="px-4 py-3 font-body text-eyebrow uppercase tracking-[0.06em] text-ink-muted">
                  Probability
                </th>
              </tr>
            </thead>
            <tbody>
              {outcome.predictions.map((row, index) => (
                <tr key={`${row.prediction}-${index}`} className="border-t border-hairline">
                  <td className="px-4 py-2 font-body text-body text-ink">{row.prediction}</td>
                  <td className="px-4 py-2 font-mono text-data text-ink">{formatChance(row.probability)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      ) : null}
    </div>
  );
}

export default function LabRunPage() {
  const params = useParams<{ run_id: string }>();
  const query = useLabUpload(params.run_id);
  const { user } = useSession();

  if (query.isPending) return <Skeleton className="h-80" />;
  if (query.isError || !query.data) {
    return (
      <ErrorState
        title="Run not found"
        body="That run is not in this workspace."
        onRetry={() => void query.refetch()}
      />
    );
  }

  const run = query.data;
  const inProgress = run.status === "queued" || run.status === "processing";
  const isAdmin = user?.role === "dclab_admin";

  return (
    <div>
      <p className="font-body text-eyebrow uppercase tracking-[0.06em] text-ink-muted">Labs</p>
      <h1 className="mt-2 font-display text-title text-ink">{run.filename}</h1>

      {inProgress ? (
        <div className="mt-8 rounded bg-paper-raised p-8">
          <p className="font-display text-section text-ink">{PROCESSING_TITLE}</p>
          <p className="mt-3 font-body text-body text-ink-muted">{PROCESSING_HINT}</p>
        </div>
      ) : null}

      {run.status === "failed" ? (
        <div className="mt-8 rounded bg-paper-raised p-8">
          <p className="font-display text-section text-ink">Could not finish this analysis.</p>
          <p className="mt-3 font-body text-body text-ink">{run.message}</p>
        </div>
      ) : null}

      {run.status === "completed" && run.outcome ? (
        <CompletedOutcome outcome={run.outcome} runId={run.run_id} />
      ) : null}

      {run.status === "completed" && !run.outcome ? (
        <div className="mt-8 rounded bg-paper-raised p-8">
          <p className="font-body text-body text-ink">{run.message}</p>
        </div>
      ) : null}

      <div className="mt-8 flex flex-wrap gap-3">
        <Link href="/app/labs">
          <Button variant="secondary">Back to Labs</Button>
        </Link>
        {isAdmin ? (
          <Link href={`/admin/models/client-uploads/${run.id}`}>
            <Button variant="secondary">Admin record</Button>
          </Link>
        ) : null}
      </div>
    </div>
  );
}
