"use client";

import { Badge } from "@/app/components/ui/Badge";
import { Button } from "@/app/components/ui/Button";
import { ErrorState } from "@/app/components/ui/ErrorState";
import { Skeleton } from "@/app/components/ui/Skeleton";
import { Table, Td, Th } from "@/app/components/ui/Table";
import { downloadAdminRunPredictions, useAdminClientUpload } from "@/lib/application";
import { formatTimestamp, type AdminMlRun, type SignalTone } from "@/lib/domain";
import Link from "next/link";
import { useParams } from "next/navigation";
import { useState, type ReactNode } from "react";

const SOURCE_TONE: Record<string, SignalTone> = {
  explicit: "green",
  rule: "amber",
  llm: "green",
  fallback: "oxblood",
};

const STATUS_TONE: Record<string, SignalTone> = {
  completed: "green",
  failed: "oxblood",
  skipped: "amber",
  queued: "amber",
  running: "amber",
  ingesting: "amber",
  analyzing: "amber",
  cleaning: "amber",
  feature_engineering: "amber",
  preprocessing: "amber",
  splitting: "amber",
  cross_validation: "amber",
  training: "amber",
  evaluating: "amber",
  predicting: "amber",
  not_applicable: "amber",
};

function formatFill(value: unknown): string {
  if (value === null || value === undefined) return "";
  return ` fill ${JSON.stringify(value)}`;
}

function formatDuration(seconds: number | null | undefined): string {
  if (seconds == null || Number.isNaN(seconds)) return "—";
  if (seconds < 60) return `${seconds.toFixed(1)}s`;
  const minutes = Math.floor(seconds / 60);
  const rest = seconds - minutes * 60;
  return `${minutes}m ${rest.toFixed(0)}s`;
}

function formatScore(value: number | null | undefined): string {
  if (value == null || Number.isNaN(value)) return "—";
  return value.toFixed(3);
}

function numericMetrics(metrics: Record<string, unknown> | null | undefined): [string, number][] {
  if (!metrics) return [];
  return Object.entries(metrics).filter((entry): entry is [string, number] => typeof entry[1] === "number");
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

function Card({ label, value }: { label: string; value: string }) {
  return (
    <div className="rounded bg-paper-raised p-6">
      <p className="font-body text-eyebrow uppercase tracking-[0.06em] text-ink-muted">{label}</p>
      <p className="mt-2 break-all font-mono text-data text-ink">{value}</p>
    </div>
  );
}

function Section({ title, children }: { title: string; children: ReactNode }) {
  return (
    <section className="mt-10">
      <h2 className="font-display text-section text-ink">{title}</h2>
      <div className="mt-4">{children}</div>
    </section>
  );
}

function Flag({ done, label, detail }: { done: boolean; label: string; detail?: string | null }) {
  return (
    <li className="flex items-baseline gap-3 font-body text-body text-ink">
      <span className="w-4 font-mono text-data text-ink-muted" aria-hidden>
        {done ? "✓" : "○"}
      </span>
      <span>
        {label}
        {detail ? <span className="ml-2 font-mono text-data text-ink-muted">{detail}</span> : null}
      </span>
    </li>
  );
}

export default function ClientUploadAutoTrainPage() {
  const params = useParams<{ id: string }>();
  const query = useAdminClientUpload(params.id);

  if (query.isPending) return <Skeleton className="h-96" />;
  if (query.isError || !query.data) {
    return <ErrorState body="Client upload not found." onRetry={() => void query.refetch()} />;
  }

  const upload = query.data;
  const run = upload.ml_run ?? null;
  const summary = run?.processing_summary;

  return (
    <div>
      <p className="font-body text-eyebrow uppercase tracking-[0.06em] text-ink-muted">
        DCLab Admin · Run overview
      </p>
      <h1 className="mt-2 font-display text-title text-ink">{upload.original_filename}</h1>
      <p className="mt-2 font-mono text-data text-ink-muted">
        {upload.category} · {upload.kind} · {formatTimestamp(upload.created_at)}
      </p>
      <p className="mt-3 font-body text-body">
        <Link className="text-navy underline-offset-2 hover:underline" href={`/lab/runs/${upload.id}`}>
          Open client run page
        </Link>
        {upload.experiment_id ? (
          <>
            {" · "}
            <Link
              className="text-navy underline-offset-2 hover:underline"
              href={`/admin/lab/experiments/${upload.experiment_id}`}
            >
              Open experiment record
            </Link>
          </>
        ) : null}
      </p>

      <Section title="Run Overview">
        <div className="grid gap-4 md:grid-cols-3">
          <Card label="Dataset" value={run?.dataset ?? upload.original_filename} />
          <Card label="Rows" value={run?.analysis.rows != null ? String(run.analysis.rows) : String(upload.record_count)} />
          <Card label="Features" value={featureCount(run?.analysis)} />
          <Card label="Target" value={run?.target ?? "—"} />
          <Card label="Task" value={taskLabel(run?.task_type)} />
          <Card label="Target source" value={run?.target_source ?? "—"} />
          <Card
            label="Target confidence"
            value={run?.target_confidence != null ? run.target_confidence.toFixed(2) : "—"}
          />
          <div className="rounded bg-paper-raised p-6">
            <p className="font-body text-eyebrow uppercase tracking-[0.06em] text-ink-muted">Status</p>
            <p className="mt-2">
              <Badge tone={STATUS_TONE[upload.pipeline_status] ?? "amber"}>{upload.pipeline_status}</Badge>
            </p>
          </div>
          <Card label="Duration" value={formatDuration(run?.duration_seconds)} />
        </div>
        {run?.target_reason ? (
          <p className="mt-4 rounded bg-paper-raised p-4 font-body text-body text-ink">
            <span className="font-medium">Target reasoning:</span> {run.target_reason}
          </p>
        ) : null}
      </Section>

      <Section title="Data Quality">
        <div className="grid gap-4 md:grid-cols-3">
          <Card label="Missing values" value={run?.analysis.missing_values != null ? String(run.analysis.missing_values) : "—"} />
          <Card label="Duplicate rows" value={run?.analysis.duplicates != null ? String(run.analysis.duplicates) : "—"} />
          <Card label="Numerical features" value={run ? String(run.analysis.numerical_columns.length) : "—"} />
          <Card label="Categorical features" value={run ? String(run.analysis.categorical_columns.length) : "—"} />
          <Card
            label="Constant columns"
            value={run?.analysis.constant_columns.length ? String(run.analysis.constant_columns.length) : "None"}
          />
          <Card
            label="High-cardinality columns"
            value={run?.analysis.high_cardinality_columns.length ? String(run.analysis.high_cardinality_columns.length) : "None"}
          />
        </div>
      </Section>

      <Section title="Processing Summary">
        {summary ? (
          <ul className="space-y-2 rounded bg-paper-raised p-6">
            <Flag done={summary.cleaning_completed} label="Cleaning completed" />
            <Flag done={summary.feature_engineering_completed} label="Feature engineering completed" />
            <Flag done={summary.preprocessing_completed} label="Preprocessing completed" />
            <Flag
              done={Boolean(summary.train_test_split)}
              label="Train/test split"
              detail={summary.train_test_split}
            />
            <Flag
              done={Boolean(summary.cross_validation)}
              label="Cross-validation"
              detail={summary.cross_validation}
            />
            <Flag done={summary.training_completed} label="Training completed" />
            <Flag done={summary.evaluation_completed} label="Evaluation completed" />
            <Flag done={summary.predictions_completed} label="Predictions completed" />
          </ul>
        ) : (
          <p className="font-body text-body text-ink-muted">No processing summary yet.</p>
        )}
      </Section>

      <ModelComparisonSection rows={run?.model_comparison ?? []} />
      <FinalModelSection model={run?.final_model ?? null} />
      <EvaluationSection run={run} />
      <PredictionsSection runId={upload.id} predictions={run?.predictions} />

      <details className="mt-10">
        <summary className="cursor-pointer font-display text-section text-ink">Technical detail</summary>
        <div className="mt-4 space-y-8">
          <CleaningSection steps={run?.cleaning ?? []} />
          <FeatureEngineeringSection fe={run?.feature_engineering} />
          {upload.decision_records.length > 0 ? (
            <div>
              <h3 className="font-display text-section text-ink">Missing-value audit</h3>
              <ul className="mt-3 space-y-3">
                {upload.decision_records.map((row) => (
                  <li key={row.id} className="rounded bg-paper-raised p-4">
                    <div className="flex flex-wrap items-center justify-between gap-2">
                      <span className="font-mono text-data text-ink">{row.column}</span>
                      <Badge tone={SOURCE_TONE[row.source] ?? "amber"}>{row.source}</Badge>
                    </div>
                    <p className="mt-2 font-body text-body text-ink">
                      {row.rule_decision}
                      {row.final_decision !== row.rule_decision ? ` → ${row.final_decision}` : ""}
                      {formatFill(row.fill_value)} · {row.validator_verdict}
                    </p>
                  </li>
                ))}
              </ul>
            </div>
          ) : null}
          <div>
            <h3 className="font-display text-section text-ink">Raw pipeline log</h3>
            <pre className="mt-3 max-h-[40vh] overflow-auto rounded bg-paper-raised p-4 font-mono text-data text-ink">
              {JSON.stringify(upload.pipeline_log, null, 2) ?? "No log yet."}
            </pre>
          </div>
        </div>
      </details>
    </div>
  );
}

function CleaningSection({ steps }: { steps: AdminMlRun["cleaning"] }) {
  return (
    <div>
      <h3 className="font-display text-section text-ink">Cleaning</h3>
      {steps.length === 0 ? (
        <p className="mt-3 font-body text-body text-ink-muted">No cleaning transformations recorded yet.</p>
      ) : (
        <div className="mt-3">
          <Table>
            <thead>
              <tr>
                <Th>Column</Th>
                <Th>Problem</Th>
                <Th>Action</Th>
                <Th>Result</Th>
              </tr>
            </thead>
            <tbody>
              {steps.map((step, index) => (
                <tr key={`${step.column}-${step.action}-${index}`}>
                  <Td mono>{step.column}</Td>
                  <Td>{step.problem}</Td>
                  <Td>{step.action}</Td>
                  <Td>{step.result}</Td>
                </tr>
              ))}
            </tbody>
          </Table>
        </div>
      )}
    </div>
  );
}

function FeatureEngineeringSection({ fe }: { fe: AdminMlRun["feature_engineering"] | undefined }) {
  return (
    <div>
      <h3 className="font-display text-section text-ink">Feature engineering</h3>
      <div className="mt-3 grid gap-4 md:grid-cols-2">
        <Card label="Original features" value={fe?.original_features.length ? String(fe.original_features.length) : "—"} />
        <Card label="Generated features" value={fe?.generated_features.length ? String(fe.generated_features.length) : "—"} />
        <Card label="Removed features" value={fe?.removed_features.length ? String(fe.removed_features.length) : "—"} />
        <Card
          label="Transformations"
          value={
            fe?.transformations.length
              ? fe.transformations.map((item) => String(item.step ?? JSON.stringify(item))).join(", ")
              : "—"
          }
        />
      </div>
    </div>
  );
}

function ModelComparisonSection({ rows }: { rows: AdminMlRun["model_comparison"] }) {
  const scores = rows
    .map((row) => ({ name: row.name, value: cvScore(row), selected: row.selected }))
    .filter((row): row is { name: string; value: number; selected: boolean } => row.value != null);
  const max = Math.max(...scores.map((row) => row.value), 0.0001);

  return (
    <Section title="Model Comparison">
      {rows.length === 0 ? (
        <p className="font-body text-body text-ink-muted">No candidate metrics persisted yet.</p>
      ) : (
        <>
          {scores.length > 0 ? (
            <ul className="mb-6 space-y-3 rounded bg-paper-raised p-6">
              {scores.map((row) => (
                <li key={row.name}>
                  <div className="flex justify-between gap-3 font-body text-body text-ink">
                    <span>
                      {row.name}
                      {row.selected ? (
                        <span className="ml-2 font-body text-eyebrow uppercase tracking-[0.06em] text-ink-muted">
                          selected
                        </span>
                      ) : null}
                    </span>
                    <span className="font-mono text-data">{formatScore(row.value)}</span>
                  </div>
                  <div className="mt-1 h-2 overflow-hidden rounded bg-paper">
                    <div className="h-2 rounded bg-ink" style={{ width: `${(row.value / max) * 100}%` }} />
                  </div>
                </li>
              ))}
            </ul>
          ) : null}
          <Table>
            <thead>
              <tr>
                <Th>Model</Th>
                <Th>CV</Th>
                <Th>Test</Th>
              </tr>
            </thead>
            <tbody>
              {rows.map((row) => (
                <tr key={row.model_family}>
                  <Td>
                    {row.name}
                    {row.selected ? (
                      <span className="ml-2 font-body text-eyebrow uppercase tracking-[0.06em] text-ink-muted">
                        selected
                      </span>
                    ) : null}
                  </Td>
                  <Td mono>{formatScore(cvScore(row))}</Td>
                  <Td mono>{formatScore(testScore(row))}</Td>
                </tr>
              ))}
            </tbody>
          </Table>
        </>
      )}
    </Section>
  );
}

function FinalModelSection({ model }: { model: AdminMlRun["final_model"] }) {
  if (!model) {
    return (
      <Section title="Final Model">
        <p className="font-body text-body text-ink-muted">No model has been locked yet.</p>
      </Section>
    );
  }
  return (
    <Section title="Final Model">
      <div className="grid gap-4 md:grid-cols-3">
        <Card label="Selected model" value={model.selected_model ?? model.model_family ?? "—"} />
        <div className="rounded bg-paper-raised p-6">
          <p className="font-body text-eyebrow uppercase tracking-[0.06em] text-ink-muted">CV performance</p>
          <MetricList entries={numericMetrics(model.cv_metrics)} />
        </div>
        <div className="rounded bg-paper-raised p-6">
          <p className="font-body text-eyebrow uppercase tracking-[0.06em] text-ink-muted">Test performance</p>
          <MetricList entries={numericMetrics(model.test_metrics)} />
        </div>
      </div>
    </Section>
  );
}

function EvaluationSection({ run }: { run: AdminMlRun | null }) {
  const selected = run?.model_comparison.find((row) => row.selected) ?? null;
  const test = numericMetrics(run?.final_model?.test_metrics ?? selected?.test_metrics);
  if (!run) {
    return (
      <Section title="Evaluation">
        <p className="font-body text-body text-ink-muted">No evaluation yet.</p>
      </Section>
    );
  }
  return (
    <Section title="Evaluation">
      {test.length === 0 ? (
        <p className="font-body text-body text-ink-muted">No test metrics persisted yet.</p>
      ) : (
        <div className="rounded bg-paper-raised p-6">
          <MetricList entries={test} />
        </div>
      )}
    </Section>
  );
}

function MetricList({ entries }: { entries: [string, number][] }) {
  if (!entries.length) {
    return <p className="mt-2 font-mono text-data text-ink">—</p>;
  }
  return (
    <ul className="mt-2 space-y-1">
      {entries.map(([name, value]) => (
        <li key={name} className="flex justify-between gap-4 font-mono text-data text-ink">
          <span className="text-ink-muted">{name}</span>
          <span>{Number.isInteger(value) ? String(value) : value.toFixed(4)}</span>
        </li>
      ))}
    </ul>
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
    <Section title="Predictions">
      <div className="grid gap-4 md:grid-cols-2">
        <Card label="Prediction count" value={predictions ? String(predictions.count) : "—"} />
        <div className="rounded bg-paper-raised p-6">
          <p className="font-body text-eyebrow uppercase tracking-[0.06em] text-ink-muted">
            Prediction distribution
          </p>
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
        {error ? <p className="mt-2 font-body text-body text-ink-muted">{error}</p> : null}
      </div>
    </Section>
  );
}
