"use client";

import { InsightCard } from "@/app/components/insights/InsightCard";
import { KIND_LABELS, LAB_RUN_STATUS_LABEL } from "@/app/components/labs/status";
import { adminClientUploadHref } from "@/app/components/admin/paths";
import { RunProgress } from "@/app/components/product/ProductPrimitives";
import { Button, buttonClassName } from "@/app/components/ui/Button";
import { Fact, FactGrid, Panel } from "@/app/components/ui/Card";
import { DataTable } from "@/app/components/ui/DataTable";
import { ErrorState } from "@/app/components/ui/ErrorState";
import { GlassPanel } from "@/app/components/ui/GlassPanel";
import { MetricCard } from "@/app/components/ui/MetricCard";
import { PageHeader } from "@/app/components/ui/PageHeader";
import { SectionHeader } from "@/app/components/ui/SectionHeader";
import { Skeleton } from "@/app/components/ui/Skeleton";
import { downloadLabPredictions, useLabUpload, useSession } from "@/lib/application";
import {
  formatTimestamp,
  type ClientLabUpload,
  type LabRunOutcome,
  type LabRunStatus,
  type LabRunStep,
  type SignalTone,
} from "@/lib/domain";
import { isPlatformRole } from "@/lib/infrastructure/session";
import Link from "next/link";
import { useParams } from "next/navigation";
import { useState, type ReactNode } from "react";

type DetailFact = { label: string; value: string; mono?: boolean };

function statusTone(status: LabRunStatus): SignalTone {
  if (status === "completed") return "green";
  if (status === "failed") return "oxblood";
  return "amber";
}

function formatChance(value: number | null): string {
  if (value === null || Number.isNaN(value)) return "—";
  return `${(value * 100).toFixed(1)}%`;
}

function nonempty(value: string | null | undefined): string | undefined {
  const trimmed = value?.trim();
  return trimmed ? trimmed : undefined;
}

function hasLiveSteps(steps: LabRunStep[]): boolean {
  return steps.some((step) => step.state === "done" || step.state === "current");
}

function yesNo(value: boolean): string {
  return value ? "Yes" : "No";
}

function NavActions({
  uploadId,
  isPlatformMember,
  extra,
}: {
  uploadId?: string;
  isPlatformMember: boolean;
  extra?: ReactNode;
}) {
  return (
    <>
      {extra}
      <Link href="/app/labs" className={buttonClassName({ variant: "secondary" })}>
        Back to Labs
      </Link>
      {isPlatformMember && uploadId ? (
        <Link href={adminClientUploadHref(uploadId)} className={buttonClassName({ variant: "secondary" })}>
          Admin record
        </Link>
      ) : null}
    </>
  );
}

function factsOf(items: Array<DetailFact | null>): DetailFact[] {
  return items.filter((item): item is DetailFact => item != null);
}

function FactBlock({ facts }: { facts: DetailFact[] }) {
  if (facts.length === 0) return null;
  return (
    <FactGrid>
      {facts.map((fact) => (
        <Fact key={fact.label} label={fact.label} value={fact.value} mono={fact.mono} />
      ))}
    </FactGrid>
  );
}

function ExecutionProgress({ run }: { run: ClientLabUpload }) {
  if (!hasLiveSteps(run.steps)) return null;
  const title = nonempty(run.milestone) ?? run.steps.find((step) => step.state === "current")?.label;
  return (
    <GlassPanel title={title} description="This may take a while.">
      <RunProgress items={run.steps.map((step) => ({ id: step.id, label: step.label, state: step.state }))} />
    </GlassPanel>
  );
}

function OutcomeMetrics({ outcome }: { outcome: LabRunOutcome }) {
  return (
    <Panel title="Metrics" description={nonempty(outcome.performance_summary)}>
      <div className="mb-5 grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
        {nonempty(outcome.dataset_name) ? <MetricCard label="Dataset" value={outcome.dataset_name} /> : null}
        <MetricCard label="Records" value={outcome.record_count.toLocaleString()} />
        <MetricCard label="Features" value={outcome.feature_count.toLocaleString()} />
        {nonempty(outcome.target_label) ? <MetricCard label="Target" value={outcome.target_label} /> : null}
      </div>
      <FactBlock
        facts={factsOf([
          { label: "Metric", value: `${outcome.performance_percent.toFixed(1)}%`, mono: true },
          nonempty(outcome.task_kind) ? { label: "Task", value: outcome.task_kind } : null,
          nonempty(outcome.method_label) ? { label: "Method", value: outcome.method_label } : null,
          { label: "Predictions", value: outcome.prediction_count.toLocaleString(), mono: true },
        ])}
      />
    </Panel>
  );
}

export default function LabRunPage() {
  const params = useParams<{ run_id: string }>();
  const query = useLabUpload(params.run_id);
  const { user } = useSession();
  const [busy, setBusy] = useState(false);
  const [downloadError, setDownloadError] = useState<string | null>(null);
  const isPlatformMember = user ? isPlatformRole(user.role) : false;

  async function onDownload(runId: string) {
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

  if (query.isPending) {
    return (
      <div>
        <PageHeader
          breadcrumbs={[{ label: "Labs", href: "/app/labs" }, { label: "Run" }]}
          title="Run"
          actions={<NavActions isPlatformMember={false} />}
        />
        <Skeleton className="h-80" />
      </div>
    );
  }
  if (query.isError || !query.data) {
    return (
      <div>
        <PageHeader
          breadcrumbs={[{ label: "Labs", href: "/app/labs" }, { label: "Run" }]}
          title="Run"
          actions={<NavActions isPlatformMember={false} />}
        />
        <ErrorState title="Run not found" body="That run is not in this workspace." onRetry={() => void query.refetch()} />
      </div>
    );
  }

  const run: ClientLabUpload = query.data;
  const outcome = run.outcome;
  const inProgress = run.status === "queued" || run.status === "processing";
  const downloadAvailable = Boolean(outcome?.download_available);
  const created = nonempty(formatTimestamp(run.created_at)) ?? nonempty(run.created_at);
  const headerDescription =
    nonempty(run.headline) ?? (run.status === "failed" ? undefined : nonempty(run.message));

  const contextFacts = factsOf([
    nonempty(run.category) ? { label: "Category", value: run.category } : null,
    nonempty(run.kind) ? { label: "Kind", value: KIND_LABELS[run.kind] ?? run.kind } : null,
    nonempty(run.filename) ? { label: "File", value: run.filename } : null,
    { label: "Records", value: run.record_count.toLocaleString(), mono: true },
    { label: "Named fields", value: yesNo(run.has_named_fields) },
    { label: "Structured", value: yesNo(run.structured) },
    created ? { label: "Created", value: created, mono: true } : null,
  ]);

  const summaryTitle = nonempty(outcome?.title);
  const summaryBody = nonempty(outcome?.summary) ?? (run.status === "completed" && !outcome ? nonempty(run.message) : undefined);
  const recordsLine = nonempty(outcome?.records_line);
  const targetLine = nonempty(outcome?.target_line);
  const showSummary = Boolean(summaryTitle || summaryBody || recordsLine || targetLine);

  const technicalFacts = factsOf([
    { label: "Run ID", value: run.run_id, mono: true },
    run.id !== run.run_id ? { label: "Upload ID", value: run.id, mono: true } : null,
    run.dataset_id ? { label: "Dataset ID", value: run.dataset_id, mono: true } : null,
    { label: "Progress", value: run.progress, mono: true },
    { label: "Pipeline status", value: run.pipeline_status, mono: true },
    run.stage !== run.status && run.stage !== run.pipeline_status
      ? { label: "Stage", value: run.stage, mono: true }
      : null,
  ]);

  const showArtifacts = downloadAvailable || Boolean(run.dataset_id);

  return (
    <div>
      <PageHeader
        eyebrow="ML workspace · Labs"
        title={run.filename}
        identifier={run.run_id}
        description={headerDescription}
        breadcrumbs={[{ label: "Labs", href: "/app/labs" }, { label: run.filename }]}
        status={{ label: LAB_RUN_STATUS_LABEL[run.status], tone: statusTone(run.status) }}
        actions={
          <NavActions
            uploadId={run.id}
            isPlatformMember={isPlatformMember}
            extra={
              downloadAvailable ? (
                <Button onClick={() => void onDownload(run.run_id)} disabled={busy}>
                  {busy ? "Preparing…" : "Download results"}
                </Button>
              ) : null
            }
          />
        }
      />

      {downloadError ? (
        <p className="mb-5 text-body text-oxblood" role="alert">
          {downloadError}
        </p>
      ) : null}

      <div className="space-y-5">
        <Panel title="Context">
          <FactBlock facts={contextFacts} />
          {run.fields_noticed.length > 0 ? (
            <div className={contextFacts.length ? "mt-5" : undefined}>
              <p className="product-eyebrow">Fields noticed</p>
              <p className="mt-1 break-words font-mono text-data text-ink">{run.fields_noticed.join(", ")}</p>
            </div>
          ) : null}
        </Panel>

        {inProgress ? <ExecutionProgress run={run} /> : null}

        {showSummary ? (
          <Panel title="Summary">
            {summaryTitle ? <p className="font-sans text-section text-ink">{summaryTitle}</p> : null}
            {summaryBody ? (
              <p className={`max-w-2xl text-body text-ink ${summaryTitle ? "mt-2" : ""}`}>{summaryBody}</p>
            ) : null}
            {recordsLine ? <p className="mt-3 text-body text-ink-muted">{recordsLine}</p> : null}
            {targetLine ? <p className="mt-1 text-body text-ink-muted">{targetLine}</p> : null}
          </Panel>
        ) : null}

        {outcome && outcome.predictions.length > 0 ? (
          <Panel title="Results">
            <div className="max-h-[28rem] overflow-auto">
              <DataTable
                columns={[
                  { id: "record", header: "Record", mono: true, cell: (row) => row.record_id },
                  { id: "prediction", header: "Prediction", cell: (row) => row.prediction },
                  {
                    id: "probability",
                    header: "Probability",
                    mono: true,
                    cell: (row) => formatChance(row.probability),
                  },
                ]}
                rows={outcome.predictions.map((row, index) => ({ ...row, rowId: `${row.record_id}-${index}` }))}
                rowKey={(row) => row.rowId}
              />
            </div>
          </Panel>
        ) : null}

        {run.insights.length > 0 ? (
          <section>
            <SectionHeader title="Insights" />
            <div className="mt-4 grid gap-4 md:grid-cols-2">
              {run.insights.map((insight) => (
                <InsightCard key={`${insight.subject_id}-${insight.headline}`} insight={insight} />
              ))}
            </div>
          </section>
        ) : null}

        {outcome ? <OutcomeMetrics outcome={outcome} /> : null}

        {showArtifacts ? (
          <Panel title="Artifacts">
            <FactBlock
              facts={factsOf([
                downloadAvailable ? { label: "Predictions", value: "Available for download" } : null,
                run.dataset_id ? { label: "Dataset ID", value: run.dataset_id, mono: true } : null,
              ])}
            />
          </Panel>
        ) : null}

        {run.status === "failed" ? (
          <Panel title="Errors">
            <p className="text-body text-ink" role="alert">
              {run.message}
            </p>
          </Panel>
        ) : null}

        <Panel title="Technical information">
          <FactBlock facts={technicalFacts} />
        </Panel>
      </div>
    </div>
  );
}
