"use client";

import { Button } from "@/app/components/ui/Button";
import { ErrorState } from "@/app/components/ui/ErrorState";
import { Skeleton } from "@/app/components/ui/Skeleton";
import { Table, Td, Th } from "@/app/components/ui/Table";
import { downloadLabPredictions, useLabUpload, useSession } from "@/lib/application";
import type { ClientLabUpload, LabRunOutcome, LabRunStatus, LabRunStep } from "@/lib/domain";
import { isPlatformRole } from "@/lib/infrastructure/session";
import Link from "next/link";
import { useParams } from "next/navigation";
import { useState } from "react";

const PROCESSING_HINT = "This may take a while.";

const STATUS_LABEL: Record<LabRunStatus, string> = {
  queued: "Queued",
  processing: "In progress",
  completed: "Completed",
  failed: "Could not finish",
};

function stepMarker(state: LabRunStep["state"]): string {
  if (state === "done") return "✓";
  if (state === "current") return "●";
  return "○";
}

function ProcessingChecklist({
  milestone,
  steps,
}: {
  milestone: string;
  steps: LabRunStep[];
}) {
  return (
    <div className="mt-8 rounded bg-paper-raised p-8">
      {milestone ? <p className="font-display text-section text-ink">{milestone}</p> : null}
      <p className="mt-3 font-body text-body text-ink-muted">{PROCESSING_HINT}</p>
      {steps.length > 0 ? (
        <ol className="mt-6 space-y-3">
          {steps.map((step) => (
            <li
              key={step.id}
              className="flex items-baseline gap-3 font-body text-body"
              aria-current={step.state === "current" ? "step" : undefined}
            >
              <span className="w-6 shrink-0 text-center font-mono text-data text-ink-muted" aria-hidden>
                {stepMarker(step.state)}
              </span>
              <span className={step.state === "upcoming" ? "text-ink-muted" : "text-ink"}>
                {step.label}
              </span>
            </li>
          ))}
        </ol>
      ) : null}
    </div>
  );
}

function formatChance(value: number | null): string {
  if (value === null || Number.isNaN(value)) return "—";
  return `${(value * 100).toFixed(1)}%`;
}

function Fact({ label, value }: { label: string; value: string }) {
  return (
    <div>
      <dt className="font-body text-eyebrow uppercase tracking-[0.06em] text-ink-muted">{label}</dt>
      <dd className="mt-2 font-body text-body text-ink">{value}</dd>
    </div>
  );
}

function CompletedOutcome({
  outcome,
  status,
  runId,
}: {
  outcome: LabRunOutcome;
  status: LabRunStatus;
  runId: string;
}) {
  const [busy, setBusy] = useState(false);
  const [downloadError, setDownloadError] = useState<string | null>(null);

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
      <section className="rounded bg-paper-raised p-6">
        <h2 className="font-display text-section text-ink">Overview</h2>
        <dl className="mt-6 grid gap-6 sm:grid-cols-2 lg:grid-cols-5">
          <Fact label="Dataset" value={outcome.dataset_name} />
          <Fact label="Records" value={outcome.record_count.toLocaleString()} />
          <Fact label="Features" value={outcome.feature_count.toLocaleString()} />
          <Fact label="Target" value={outcome.target_label} />
          <Fact label="Status" value={STATUS_LABEL[status]} />
        </dl>
      </section>

      <section className="rounded bg-paper-raised p-6">
        <h2 className="font-display text-section text-ink">Result</h2>
        <dl className="mt-6 grid gap-6 sm:grid-cols-3">
          <div>
            <dt className="font-body text-eyebrow uppercase tracking-[0.06em] text-ink-muted">Metric</dt>
            <dd className="mt-2 font-display text-title text-ink">{outcome.performance_percent.toFixed(1)}%</dd>
          </div>
          <div>
            <dt className="font-body text-eyebrow uppercase tracking-[0.06em] text-ink-muted">Test performance</dt>
            <dd className="mt-2 font-body text-body text-ink">{outcome.performance_summary}</dd>
          </div>
          <div>
            <dt className="font-body text-eyebrow uppercase tracking-[0.06em] text-ink-muted">Predictions</dt>
            <dd className="mt-2 font-display text-title text-ink">{outcome.prediction_count.toLocaleString()}</dd>
          </div>
        </dl>
      </section>

      <section className="rounded bg-paper-raised p-6">
        <div className="flex flex-wrap items-end justify-between gap-3">
          <h2 className="font-display text-section text-ink">Predictions</h2>
          {outcome.download_available ? (
            <Button variant="secondary" onClick={() => void onDownload()} disabled={busy}>
              {busy ? "Preparing…" : "Download results"}
            </Button>
          ) : null}
        </div>
        {downloadError ? <p className="mt-3 font-body text-body text-oxblood">{downloadError}</p> : null}
        {outcome.predictions.length > 0 ? (
          <div className="mt-6 max-h-[28rem] overflow-auto">
            <Table>
              <thead className="sticky top-0 bg-paper-raised">
                <tr>
                  <Th>Record</Th>
                  <Th>Prediction</Th>
                  <Th>Probability</Th>
                </tr>
              </thead>
              <tbody>
                {outcome.predictions.map((row, index) => (
                  <tr key={`${row.record_id}-${index}`}>
                    <Td mono>{row.record_id}</Td>
                    <Td>{row.prediction}</Td>
                    <Td mono>{formatChance(row.probability)}</Td>
                  </tr>
                ))}
              </tbody>
            </Table>
          </div>
        ) : null}
      </section>

      <section className="rounded bg-paper-raised p-6">
        <h2 className="font-display text-section text-ink">Summary</h2>
        <p className="mt-4 max-w-2xl font-body text-body text-ink">{outcome.summary}</p>
      </section>
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

  const run: ClientLabUpload = query.data;
  const inProgress = run.status === "queued" || run.status === "processing";
  const isPlatformMember = user ? isPlatformRole(user.role) : false;

  return (
    <div>
      <p className="font-body text-eyebrow uppercase tracking-[0.06em] text-ink-muted">Labs</p>
      <h1 className="mt-2 font-display text-title text-ink">{run.filename}</h1>

      {inProgress ? <ProcessingChecklist milestone={run.milestone || run.headline} steps={run.steps} /> : null}

      {run.status === "failed" ? (
        <div className="mt-8 rounded bg-paper-raised p-8">
          <p className="font-display text-section text-ink">Could not finish this analysis.</p>
          <p className="mt-3 font-body text-body text-ink">{run.message}</p>
        </div>
      ) : null}

      {run.status === "completed" && run.outcome ? (
        <CompletedOutcome outcome={run.outcome} status={run.status} runId={run.run_id} />
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
        {isPlatformMember ? (
          <Link href={`/admin/models/client-uploads/${run.id}`}>
            <Button variant="secondary">Admin record</Button>
          </Link>
        ) : null}
      </div>
    </div>
  );
}
