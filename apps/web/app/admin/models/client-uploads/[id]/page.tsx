"use client";

import { Evaluation } from "@/app/components/admin/Evaluation";
import { FinalModel } from "@/app/components/admin/FinalModel";
import { ModelComparison } from "@/app/components/admin/ModelComparison";
import { ProcessingSummary } from "@/app/components/admin/ProcessingSummary";
import { formatDurationSeconds, formatWhen } from "@/app/components/admin/format";
import { Badge } from "@/app/components/ui/Badge";
import { buttonClassName } from "@/app/components/ui/Button";
import { Button } from "@/app/components/ui/Button";
import { MetricCard } from "@/app/components/ui/MetricCard";
import { Panel } from "@/app/components/ui/Card";
import { DataTable } from "@/app/components/ui/DataTable";
import { ErrorState } from "@/app/components/ui/ErrorState";
import { PageHeader } from "@/app/components/ui/PageHeader";
import { Skeleton } from "@/app/components/ui/Skeleton";
import { StatusBadge, statusTone } from "@/app/components/ui/StatusBadge";
import { downloadAdminRunPredictions, useAdminClientUpload } from "@/lib/application";
import { type AdminMlRun, type SignalTone } from "@/lib/domain";
import { ApiError } from "@/lib/infrastructure/api-client";
import Link from "next/link";
import { useParams } from "next/navigation";
import { useState } from "react";

const SOURCE_TONE: Record<string, SignalTone> = {
  explicit: "green",
  rule: "amber",
  llm: "green",
  fallback: "oxblood",
};

function formatFill(value: unknown): string {
  if (value === null || value === undefined) return "";
  return ` fill ${JSON.stringify(value)}`;
}

function taskLabel(taskType: string | null | undefined): string {
  if (!taskType) return "—";
  if (taskType === "binary" || taskType === "multiclass") return "Classification";
  if (taskType === "regression") return "Regression";
  return taskType;
}

function featureCount(analysis: AdminMlRun["analysis"] | undefined): string {
  if (!analysis) return "—";
  const counted = analysis.numerical_columns.length + analysis.categorical_columns.length;
  if (counted > 0) return String(counted);
  if (analysis.columns != null) return String(analysis.columns);
  return "—";
}

export default function ClientUploadAutoTrainPage() {
  const params = useParams<{ id: string }>();
  const query = useAdminClientUpload(params.id);

  if (query.isPending) return <Skeleton className="h-96" />;
  if (query.isError || !query.data) {
    const notFound = query.error instanceof ApiError && query.error.status === 404;
    return (
      <ErrorState
        title={notFound ? "Client upload not found" : "Could not load client upload"}
        body={notFound ? "This client upload does not exist." : query.error?.message || "The upload could not be loaded."}
        onRetry={() => void query.refetch()}
      />
    );
  }

  const upload = query.data;
  const run = upload.ml_run ?? null;
  const summary = run?.processing_summary;

  return (
    <div>
      <PageHeader
        breadcrumbs={[
          { label: "Labs", href: "/admin/lab" },
          { label: "Model registry", href: "/admin/models" },
          { label: upload.original_filename },
        ]}
        eyebrow="Run overview"
        title={upload.original_filename}
        identifier={upload.id}
        description={`${upload.category} · ${upload.kind} · ${formatWhen(upload.created_at)}`}
        status={{ label: upload.pipeline_status, tone: statusTone(upload.pipeline_status) }}
        actions={
          <>
            <Link href={`/lab/runs/${upload.id}`} className={buttonClassName({ variant: "secondary" })}>
              Open client run
            </Link>
            {upload.experiment_id ? (
              <Link
                href={`/admin/pipeline-runs/${upload.experiment_id}/monitor`}
                className={buttonClassName({ variant: "secondary" })}
              >
                Pipeline Monitor
              </Link>
            ) : null}
            {upload.workflow_run_id ? (
              <Link
                href={`/admin/businesses/${upload.workspace_id}/workflow-runs/${upload.workflow_run_id}`}
                className={buttonClassName({ variant: "secondary" })}
              >
                Workflow Run
              </Link>
            ) : null}
          </>
        }
      />

      <Panel className="mt-8" title="Run Overview">
        <div className="grid gap-4 md:grid-cols-3">
          <MetricCard label="Dataset" value={run?.dataset ?? upload.original_filename} />
          <MetricCard
            label="Rows"
            value={run?.analysis.rows != null ? String(run.analysis.rows) : String(upload.record_count)}
          />
          <MetricCard label="Features" value={featureCount(run?.analysis)} />
          <MetricCard label="Target" value={run?.target ?? "—"} />
          <MetricCard label="Task" value={taskLabel(run?.task_type)} />
          <MetricCard label="Target source" value={run?.target_source ?? "—"} />
          <MetricCard
            label="Target confidence"
            value={run?.target_confidence != null ? run.target_confidence.toFixed(2) : "—"}
          />
          <div className="product-metric-card product-metric-card-default">
            <p className="product-eyebrow">Status</p>
            <p className="mt-3">
              <StatusBadge status={upload.pipeline_status} />
            </p>
          </div>
          <MetricCard label="Duration" value={formatDurationSeconds(run?.duration_seconds) || "—"} />
        </div>
        {run?.target_reason ? (
          <p className="mt-4 rounded-lg bg-navy-soft p-4 text-body text-ink">
            <span className="font-medium">Target reasoning:</span> {run.target_reason}
          </p>
        ) : null}
        {run?.failure_reason ? (
          <p className="mt-4 rounded-lg bg-navy-soft p-4 text-body text-ink">
            <span className="font-medium">Failure reason:</span> {run.failure_reason}
          </p>
        ) : null}
      </Panel>

      <Panel className="mt-6" title="Data Quality">
        <div className="grid gap-4 md:grid-cols-3">
          <MetricCard
            label="Missing values"
            value={run?.analysis.missing_values != null ? String(run.analysis.missing_values) : "—"}
          />
          <MetricCard
            label="Duplicate rows"
            value={run?.analysis.duplicates != null ? String(run.analysis.duplicates) : "—"}
          />
          <MetricCard label="Numerical features" value={run ? String(run.analysis.numerical_columns.length) : "—"} />
          <MetricCard label="Categorical features" value={run ? String(run.analysis.categorical_columns.length) : "—"} />
          <MetricCard
            label="Constant columns"
            value={run?.analysis.constant_columns.length ? String(run.analysis.constant_columns.length) : "None"}
          />
          <MetricCard
            label="High-cardinality columns"
            value={
              run?.analysis.high_cardinality_columns.length
                ? String(run.analysis.high_cardinality_columns.length)
                : "None"
            }
          />
        </div>
      </Panel>

      <ProcessingSummary summary={summary} />

      <ModelComparison rows={run?.model_comparison ?? []} />
      <FinalModel model={run?.final_model ?? null} />
      <Evaluation run={run} />
      <PredictionsSection runId={upload.id} predictions={run?.predictions} />

      <details className="mt-10">
        <summary className="cursor-pointer font-sans text-section text-ink">Technical detail</summary>
        <div className="mt-4 space-y-8">
          <CleaningSection steps={run?.cleaning ?? []} />
          <FeatureEngineeringSection fe={run?.feature_engineering} />
          {upload.decision_records.length > 0 ? (
            <Panel title="Missing-value audit">
              <ul className="space-y-3">
                {upload.decision_records.map((row) => (
                  <li key={row.id} className="rounded-lg border border-hairline p-4">
                    <div className="flex flex-wrap items-center justify-between gap-2">
                      <span className="font-mono text-data text-ink">{row.column}</span>
                      <Badge tone={SOURCE_TONE[row.source] ?? "amber"}>{row.source}</Badge>
                    </div>
                    <p className="mt-2 text-body text-ink">
                      {row.rule_decision}
                      {row.final_decision !== row.rule_decision ? ` → ${row.final_decision}` : ""}
                      {formatFill(row.fill_value)} · {row.validator_verdict}
                    </p>
                  </li>
                ))}
              </ul>
            </Panel>
          ) : null}
          <Panel title="Raw pipeline log">
            <pre className="max-h-[40vh] overflow-auto font-mono text-data text-ink">
              {JSON.stringify(upload.pipeline_log, null, 2) ?? "No log yet."}
            </pre>
          </Panel>
        </div>
      </details>
    </div>
  );
}

function CleaningSection({ steps }: { steps: AdminMlRun["cleaning"] }) {
  return (
    <Panel title="Cleaning">
      <DataTable
        columns={[
          { id: "column", header: "Column", mono: true, cell: (step) => step.column },
          { id: "problem", header: "Problem", cell: (step) => step.problem },
          { id: "action", header: "Action", cell: (step) => step.action },
          { id: "result", header: "Result", cell: (step) => step.result },
        ]}
        rows={steps}
        rowKey={(step) => `${step.column}-${step.action}-${step.problem}-${step.result}`}
        emptyTitle="No cleaning transformations recorded yet."
        emptyBody="Cleaning steps appear after the run records column-level transformations."
      />
    </Panel>
  );
}

function FeatureEngineeringSection({ fe }: { fe: AdminMlRun["feature_engineering"] | undefined }) {
  return (
    <Panel title="Feature engineering">
      <div className="grid gap-4 md:grid-cols-2">
        <MetricCard label="Original features" value={fe?.original_features.length ? String(fe.original_features.length) : "—"} />
        <MetricCard label="Generated features" value={fe?.generated_features.length ? String(fe.generated_features.length) : "—"} />
        <MetricCard label="Removed features" value={fe?.removed_features.length ? String(fe.removed_features.length) : "—"} />
        <MetricCard
          label="Transformations"
          value={
            fe?.transformations.length
              ? fe.transformations.map((item) => String(item.step ?? JSON.stringify(item))).join(", ")
              : "—"
          }
        />
      </div>
    </Panel>
  );
}

function PredictionsSection({
  runId,
  predictions,
}: {
  runId: string;
  predictions: AdminMlRun["predictions"] | undefined;
}) {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const distribution = Object.entries(predictions?.distribution ?? {});

  async function onDownload() {
    setError(null);
    setBusy(true);
    try {
      await downloadAdminRunPredictions(runId);
    } catch {
      setError("Could not download the prediction dataset.");
    } finally {
      setBusy(false);
    }
  }

  return (
    <Panel className="mt-6" title="Predictions">
      <div className="grid gap-4 md:grid-cols-2">
        <MetricCard label="Prediction count" value={predictions ? String(predictions.count) : "—"} />
        <div className="product-metric-card product-metric-card-default">
          <p className="product-eyebrow">Prediction distribution</p>
          {distribution.length === 0 ? (
            <p className="mt-2 font-mono text-data text-ink">—</p>
          ) : (
            <ul className="mt-2 space-y-1">
              {distribution.map(([label, count]) => (
                <li key={label} className="flex justify-between font-mono text-data text-ink">
                  <span>y_pred={label}</span>
                  <span>{count}</span>
                </li>
              ))}
            </ul>
          )}
        </div>
      </div>
      <div className="mt-4">
        <Button type="button" variant="secondary" disabled={!predictions?.download_available || busy} onClick={() => void onDownload()}>
          {busy ? "Downloading…" : "Download predictions"}
        </Button>
        {error ? <p className="mt-2 text-body text-ink-muted">{error}</p> : null}
      </div>
    </Panel>
  );
}
