"use client";

import { ErrorState } from "@/app/components/ui/ErrorState";
import { Skeleton } from "@/app/components/ui/Skeleton";
import { useLabDatasets, useLabEnvironments, useLabExperiments, useLabTasks } from "@/lib/application";
import Link from "next/link";

export default function LabDashboard() {
  const env = useLabEnvironments();
  const datasets = useLabDatasets();
  const tasks = useLabTasks();
  const experiments = useLabExperiments();

  if (env.isError || datasets.isError) {
    return <ErrorState body="Could not load the Lab. Is the API running?" onRetry={() => void env.refetch()} />;
  }
  if (env.isPending || datasets.isPending || tasks.isPending || experiments.isPending) {
    return <Skeleton className="h-64" />;
  }

  return (
    <div>
      <p className="font-body text-eyebrow uppercase tracking-[0.06em] text-ink-muted">DCLab Internal Dogfood</p>
      <h1 className="mt-2 font-display text-title text-ink">Experimentation lab</h1>
      <p className="mt-2 max-w-2xl font-body text-body text-ink-muted">
        Profile a dataset, define a prediction task, run a controlled candidate search, and read the report.
        This is the same workflow a customer prototype environment will use later.
      </p>
      <div className="mt-8 grid gap-4 md:grid-cols-3">
        <Stat label="Environments" value={String(env.data?.length ?? 0)} href="/lab" />
        <Stat label="Datasets" value={String(datasets.data?.length ?? 0)} href="/lab/datasets" />
        <Stat label="Experiments" value={String(experiments.data?.length ?? 0)} href="/lab/experiments" />
      </div>
      <h2 className="mt-12 font-display text-section text-ink">Recent experiments</h2>
      <ul className="mt-4 divide-y divide-hairline rounded bg-paper-raised">
        {(experiments.data ?? []).slice(0, 8).map((row) => (
          <li key={row.id} className="px-4 py-3">
            <Link className="font-mono text-data text-navy underline-offset-2 hover:underline" href={`/lab/experiments/${row.id}`}>
              {row.id}
            </Link>
            <p className="font-body text-body text-ink-muted">{row.status}</p>
          </li>
        ))}
      </ul>
      <p className="mt-8 font-body text-body">
        <Link className="text-navy underline-offset-2 hover:underline" href="/lab/datasets">
          Datasets
        </Link>
        {" · "}
        <Link className="text-navy underline-offset-2 hover:underline" href="/lab/tasks">
          Tasks
        </Link>
      </p>
    </div>
  );
}

function Stat({ label, value, href }: { label: string; value: string; href: string }) {
  return (
    <Link href={href} className="rounded bg-paper-raised p-6">
      <p className="font-body text-eyebrow uppercase tracking-[0.06em] text-ink-muted">{label}</p>
      <p className="mt-2 font-mono text-title text-ink">{value}</p>
    </Link>
  );
}
