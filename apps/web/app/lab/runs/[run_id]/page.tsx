"use client";

import { Button } from "@/app/components/ui/Button";
import { GlassPanel, MetricCard, ProductPageHeader, RunProgress } from "@/app/components/product/ProductPrimitives";
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

function ProcessingChecklist({
  milestone,
  steps,
}: {
  milestone: string;
  steps: LabRunStep[];
}) {
  return (
    <GlassPanel className="mt-6" title={milestone || "Processing run"} description={PROCESSING_HINT}>
      {steps.length > 0 ? <RunProgress items={steps.map((step) => ({ id: step.id, label: step.label, state: step.state }))} /> : null}
    </GlassPanel>
  );
}

function formatChance(value: number | null): string {
  if (value === null || Number.isNaN(value)) return "—";
  return `${(value * 100).toFixed(1)}%`;
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
      <GlassPanel title="Run overview">
        <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-5">
          <MetricCard label="Dataset" value={outcome.dataset_name} />
          <MetricCard label="Records" value={outcome.record_count.toLocaleString()} />
          <MetricCard label="Features" value={outcome.feature_count.toLocaleString()} />
          <MetricCard label="Target" value={outcome.target_label} />
          <MetricCard label="Status" value={STATUS_LABEL[status]} />
        </div>
      </GlassPanel>

      <GlassPanel title="Result">
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
      </GlassPanel>

      <GlassPanel title="Predictions">
        <div className="flex flex-wrap items-end justify-between gap-3">
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
      </GlassPanel>

      <GlassPanel title="Summary">
        <p className="mt-4 max-w-2xl font-body text-body text-ink">{outcome.summary}</p>
      </GlassPanel>
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
      <ProductPageHeader eyebrow="ML workspace · Labs" title={run.filename} status={{ label: STATUS_LABEL[run.status] }} description={run.headline || run.message} />

      {inProgress ? <ProcessingChecklist milestone={run.milestone || run.headline} steps={run.steps} /> : null}

      {run.status === "failed" ? (
        <GlassPanel className="mt-6" title="Could not finish this analysis."><p className="text-body text-ink">{run.message}</p></GlassPanel>
      ) : null}

      {run.status === "completed" && run.outcome ? (
        <CompletedOutcome outcome={run.outcome} status={run.status} runId={run.run_id} />
      ) : null}

      {run.status === "completed" && !run.outcome ? (
        <GlassPanel className="mt-6"><p className="text-body text-ink">{run.message}</p></GlassPanel>
      ) : null}

      <div className="mt-8 flex flex-wrap gap-3">
        <Link href="/app/labs" className="inline-flex items-center justify-center rounded border border-hairline bg-paper-raised px-4 py-2 font-body text-body font-medium text-ink transition-colors hover:bg-navy-soft">
          Back to Labs
        </Link>
        {isPlatformMember ? (
          <Link href={`/admin/models/client-uploads/${run.id}`} className="inline-flex items-center justify-center rounded border border-hairline bg-paper-raised px-4 py-2 font-body text-body font-medium text-ink transition-colors hover:bg-navy-soft">
            Admin record
          </Link>
        ) : null}
      </div>
    </div>
  );
}
