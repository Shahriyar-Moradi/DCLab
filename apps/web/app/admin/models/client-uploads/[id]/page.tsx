"use client";

import { Badge } from "@/app/components/ui/Badge";
import { ErrorState } from "@/app/components/ui/ErrorState";
import { Skeleton } from "@/app/components/ui/Skeleton";
import { useAdminClientUpload } from "@/lib/application";
import type { SignalTone } from "@/lib/domain";
import Link from "next/link";
import { useParams } from "next/navigation";

type MissingColumnDecision = {
  column: string;
  missing_count: number;
  missing_fraction: number;
  action: string;
};

type PipelineLog = {
  reason?: string;
  eda?: { row_count?: number; column_count?: number; duplicate_rows?: number };
  quality?: { issue_count?: number; issues?: Array<Record<string, unknown>> };
  target?: { column?: string; reason?: string };
  missing_value_decisions?: {
    dropped_columns?: string[];
    rows_with_missing?: number;
    row_missing_fraction?: number;
    drop_rows_recommended?: boolean;
    column_decisions?: MissingColumnDecision[];
  };
  numerical_cols?: string[];
  categorical_cols?: string[];
  boost_family_used?: string;
  experiment_status?: string;
};

const STATUS_TONE: Record<string, SignalTone> = {
  completed: "green",
  running: "amber",
  queued: "amber",
  skipped: "amber",
  failed: "oxblood",
  not_applicable: "amber",
};

function Card({ label, value }: { label: string; value: string }) {
  return (
    <div className="rounded bg-paper-raised p-6">
      <p className="font-body text-eyebrow uppercase tracking-[0.06em] text-ink-muted">{label}</p>
      <p className="mt-2 font-mono text-data text-ink">{value}</p>
    </div>
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
  const log = (upload.pipeline_log ?? {}) as PipelineLog;
  const missing = log.missing_value_decisions;

  return (
    <div>
      <p className="font-body text-eyebrow uppercase tracking-[0.06em] text-ink-muted">
        DCLab Admin · Labs custom-box upload · auto-train
      </p>
      <h1 className="mt-2 font-display text-title text-ink">{upload.original_filename}</h1>
      <p className="mt-2 font-mono text-data text-ink-muted">
        {upload.category} · {upload.kind} · {upload.record_count} rows noticed ·{" "}
        {new Date(upload.created_at).toLocaleString()}
      </p>
      <p className="mt-4 max-w-2xl font-body text-body text-ink-muted">
        Simple-case auto-train: after the client saved this file, an automatic job ran EDA, decided how to
        handle missing values, built a scikit-learn ColumnTransformer, and trained RandomForest/
        {log.boost_family_used === "gradient_boosting" ? "GradientBoosting (XGBoost unavailable)" : "XGBoost"}{" "}
        candidates with K-fold cross-validation. The client only ever saw that the file was saved.
      </p>

      <div className="mt-8 grid gap-4 md:grid-cols-3">
        <div className="rounded bg-paper-raised p-6">
          <p className="font-body text-eyebrow uppercase tracking-[0.06em] text-ink-muted">Pipeline status</p>
          <p className="mt-2">
            <Badge tone={STATUS_TONE[upload.pipeline_status] ?? "amber"}>{upload.pipeline_status}</Badge>
          </p>
        </div>
        <Card label="Target chosen" value={log.target?.column ?? "—"} />
        <Card
          label="Experiment"
          value={upload.experiment_id ? "open in Lab experiments →" : "—"}
        />
      </div>

      {upload.experiment_id ? (
        <p className="mt-4">
          <Link
            className="font-body text-body text-navy underline-offset-2 hover:underline"
            href={`/admin/lab/experiments/${upload.experiment_id}`}
          >
            Open full experiment detail (candidates, funnel, test metrics) →
          </Link>
        </p>
      ) : null}

      {log.reason ? (
        <div className="mt-8 rounded bg-paper-raised p-6">
          <p className="font-body text-eyebrow uppercase tracking-[0.06em] text-ink-muted">Why</p>
          <p className="mt-2 font-body text-body text-ink">{log.reason}</p>
        </div>
      ) : null}

      {log.eda ? (
        <>
          <h2 className="mt-10 font-display text-section text-ink">EDA</h2>
          <div className="mt-3 grid gap-4 md:grid-cols-3">
            <Card label="Rows" value={String(log.eda.row_count ?? "—")} />
            <Card label="Columns" value={String(log.eda.column_count ?? "—")} />
            <Card label="Duplicate rows" value={String(log.eda.duplicate_rows ?? "—")} />
          </div>
        </>
      ) : null}

      {log.target ? (
        <>
          <h2 className="mt-10 font-display text-section text-ink">Target (heuristic)</h2>
          <p className="mt-3 rounded bg-paper-raised p-4 font-body text-body text-ink">
            <span className="font-mono text-data">{log.target.column}</span> — {log.target.reason}
          </p>
        </>
      ) : null}

      {missing ? (
        <>
          <h2 className="mt-10 font-display text-section text-ink">Missing-value decisions</h2>
          <p className="mt-3 font-body text-body text-ink-muted">
            {missing.rows_with_missing ?? 0} of the rows had at least one missing feature (
            {((missing.row_missing_fraction ?? 0) * 100).toFixed(1)}%) —{" "}
            {missing.drop_rows_recommended
              ? "few enough that a drop_sparse_rows candidate was trained alongside impute_all."
              : "imputed rather than dropped, plus a competing drop_sparse_rows candidate for comparison."}
          </p>
          {missing.dropped_columns && missing.dropped_columns.length > 0 ? (
            <p className="mt-2 font-mono text-data text-ink-muted">
              Columns dropped (&gt;50% missing): {missing.dropped_columns.join(", ")}
            </p>
          ) : null}
          <ul className="mt-3 divide-y divide-hairline rounded bg-paper-raised">
            {(missing.column_decisions ?? []).map((row) => (
              <li key={row.column} className="flex items-center justify-between px-4 py-2">
                <span className="font-mono text-data text-ink">{row.column}</span>
                <span className="font-body text-body text-ink-muted">
                  {row.missing_count} missing ({(row.missing_fraction * 100).toFixed(1)}%) → {row.action}
                </span>
              </li>
            ))}
          </ul>
        </>
      ) : null}

      {(log.numerical_cols || log.categorical_cols) && (log.numerical_cols?.length || log.categorical_cols?.length) ? (
        <>
          <h2 className="mt-10 font-display text-section text-ink">Column roles</h2>
          <div className="mt-3 grid gap-4 md:grid-cols-2">
            <div className="rounded bg-paper-raised p-4">
              <p className="font-body text-eyebrow uppercase tracking-[0.06em] text-ink-muted">
                Numerical (mean-impute + scale)
              </p>
              <p className="mt-2 font-mono text-data text-ink">{(log.numerical_cols ?? []).join(", ") || "—"}</p>
            </div>
            <div className="rounded bg-paper-raised p-4">
              <p className="font-body text-eyebrow uppercase tracking-[0.06em] text-ink-muted">
                Categorical (most-frequent-impute + one-hot)
              </p>
              <p className="mt-2 font-mono text-data text-ink">{(log.categorical_cols ?? []).join(", ") || "—"}</p>
            </div>
          </div>
        </>
      ) : null}

      <h2 className="mt-10 font-display text-section text-ink">Raw pipeline log</h2>
      <pre className="mt-3 max-h-[60vh] overflow-auto rounded bg-paper-raised p-4 font-mono text-data text-ink">
        {JSON.stringify(upload.pipeline_log, null, 2) ?? "No log yet — still queued or running."}
      </pre>
    </div>
  );
}
