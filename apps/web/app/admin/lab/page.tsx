"use client";

import { Badge } from "@/app/components/ui/Badge";
import { GlassPanel, MetricCard, ProductPageHeader } from "@/app/components/product/ProductPrimitives";
import { ErrorState } from "@/app/components/ui/ErrorState";
import { Skeleton } from "@/app/components/ui/Skeleton";
import {
  useAdminClientUploads,
  useLabDatasets,
  useLabEnvironments,
  useLabExperiments,
  useLabTasks,
} from "@/lib/application";
import type { SignalTone } from "@/lib/domain";
import Link from "next/link";

const UPLOAD_STATUS_TONE: Record<string, SignalTone> = {
  completed: "green",
  running: "amber",
  queued: "amber",
  skipped: "amber",
  failed: "oxblood",
  not_applicable: "amber",
};

export default function LabDashboard() {
  const env = useLabEnvironments();
  const datasets = useLabDatasets();
  const tasks = useLabTasks();
  const experiments = useLabExperiments();
  const clientUploads = useAdminClientUploads();

  if (env.isError || datasets.isError || tasks.isError || experiments.isError || clientUploads.isError) {
    return (
      <ErrorState
        body="Could not load the Lab. Is the API running?"
        onRetry={() => {
          void env.refetch();
          void datasets.refetch();
          void tasks.refetch();
          void experiments.refetch();
          void clientUploads.refetch();
        }}
      />
    );
  }
  if (env.isPending || datasets.isPending || tasks.isPending || experiments.isPending || clientUploads.isPending) {
    return <Skeleton className="h-64" />;
  }

  return (
    <div>
      <ProductPageHeader eyebrow="DCLab internal ML workspace" title="Labs" description="Upload datasets, inspect use cases, and trace experiments through their candidate models and results." />
      <GlassPanel title="Lab workflow" description="The internal experimentation path maps eligible columns onto supported use cases before training.">
      <ol className="max-w-2xl list-decimal space-y-1 pl-5 font-body text-body text-ink">
        <li>Upload a spreadsheet (or load the sample workbook with all five labels).</li>
        <li>Confirm which use cases have a label column in that file.</li>
        <li>Train. Each use case fits five models across feature groups, then keeps the useful ones.</li>
        <li>Open the experiment report for metrics, candidates, and the ensemble.</li>
      </ol>
      </GlassPanel>
      <div className="mt-8 grid gap-4 md:grid-cols-3">
        <Stat label="Datasets" value={String(datasets.data?.length ?? 0)} href="/admin/lab/datasets" />
        <Stat label="Experiments" value={String(experiments.data?.length ?? 0)} href="/admin/lab/experiments" />
        <Stat label="Tasks" value={String(tasks.data?.length ?? 0)} href="/admin/lab/tasks" />
      </div>
      <p className="mt-8">
        <Link
          className="inline-flex rounded bg-navy px-4 py-2 font-body text-body font-medium text-paper-raised"
          href="/admin/lab/datasets"
        >
          Open datasets
        </Link>
      </p>
      <h2 className="mt-12 font-display text-section text-ink">Recent experiments</h2>
      <ul className="mt-4 divide-y divide-hairline rounded-xl border border-hairline bg-paper-raised">
        {(experiments.data ?? []).slice(0, 8).map((row) => (
          <li key={row.id} className="px-4 py-3">
            <Link className="font-body text-body text-navy underline-offset-2 hover:underline" href={`/admin/lab/experiments/${row.id}`}>
              {row.task_name ?? row.use_case ?? row.id}
            </Link>
            <p className="font-mono text-data text-ink-muted">
              {row.status}
              {row.dataset_name ? ` · ${row.dataset_name}` : ""}
            </p>
          </li>
        ))}
      </ul>
      <h2 className="mt-12 font-display text-section text-ink">Open ingest jobs</h2>
      <p className="mt-2 max-w-2xl font-body text-body text-ink-muted">
        Simple-case auto-train runs behind every Labs custom-box upload (spreadsheet/JSON/table file, named
        columns, 40+ rows). See docs/LABS_DATA_UNDERSTANDING.md.
      </p>
      <ul className="mt-4 divide-y divide-hairline rounded-xl border border-hairline bg-paper-raised">
        {(clientUploads.data ?? []).slice(0, 10).map((row) => (
          <li key={row.id} className="flex items-center justify-between px-4 py-3">
            <div>
              <Link
                className="font-body text-body text-navy underline-offset-2 hover:underline"
                href={`/admin/models/client-uploads/${row.id}`}
              >
                {row.original_filename}
              </Link>
              <p className="font-mono text-data text-ink-muted">
                {row.category} · {row.kind} · {row.record_count} rows
              </p>
            </div>
            <Badge tone={UPLOAD_STATUS_TONE[row.pipeline_status] ?? "amber"}>{row.pipeline_status}</Badge>
          </li>
        ))}
        {(clientUploads.data ?? []).length === 0 ? (
          <li className="px-4 py-3 font-body text-body text-ink-muted">No custom-box uploads yet.</li>
        ) : null}
      </ul>
    </div>
  );
}

function Stat({ label, value, href }: { label: string; value: string; href: string }) {
  return (
    <Link href={href}><MetricCard label={label} value={value} /></Link>
  );
}
