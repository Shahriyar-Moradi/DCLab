"use client";

import { ErrorState } from "@/app/components/ui/ErrorState";
import { Skeleton } from "@/app/components/ui/Skeleton";
import { PageIntro, WorkspaceShell } from "@/app/components/workspace/PageIntro";
import { useLabDatasets, useLabEnvironments, useLabExperiments, useLabTasks } from "@/lib/application";
import Link from "next/link";

export default function LabDashboard() {
  const env = useLabEnvironments();
  const datasets = useLabDatasets();
  const tasks = useLabTasks();
  const experiments = useLabExperiments();

  if (env.isError || datasets.isError) {
    return (
      <WorkspaceShell>
        <ErrorState body="Could not load the Lab. Is the API running?" onRetry={() => void env.refetch()} />
      </WorkspaceShell>
    );
  }
  if (env.isPending || datasets.isPending || tasks.isPending || experiments.isPending) {
    return (
      <WorkspaceShell>
        <Skeleton className="h-64" />
      </WorkspaceShell>
    );
  }

  return (
    <WorkspaceShell>
      <PageIntro
        eyebrow="DCLab Internal Dogfood"
        title="Experimentation lab"
        subtitle="Profile a dataset, define a prediction task, run a controlled candidate search, and read the report. This is the same workflow a customer prototype environment will use later."
      />
      <div className="mt-8 grid gap-4 md:grid-cols-3">
        <Stat label="Environments" value={String(env.data?.length ?? 0)} href="/lab" />
        <Stat label="Datasets" value={String(datasets.data?.length ?? 0)} href="/lab/datasets" />
        <Stat label="Experiments" value={String(experiments.data?.length ?? 0)} href="/lab/experiments" />
      </div>
      <h2 className="mt-12 font-display text-section text-ink">Recent experiments</h2>
      <ul className="mt-4 divide-y divide-hairline rounded-2xl bg-white shadow-sm ring-1 ring-hairline">
        {(experiments.data ?? []).slice(0, 8).map((row) => (
          <li key={row.id} className="px-4 py-3">
            <Link className="font-mono text-data text-brand underline-offset-2 hover:underline" href={`/lab/experiments/${row.id}`}>
              {row.id}
            </Link>
            <p className="font-body text-body text-ink-muted">{row.status}</p>
          </li>
        ))}
      </ul>
      <p className="mt-8 font-body text-body">
        <Link className="text-brand underline-offset-2 hover:underline" href="/lab/datasets">
          Datasets
        </Link>
        {" · "}
        <Link className="text-brand underline-offset-2 hover:underline" href="/lab/tasks">
          Tasks
        </Link>
      </p>
    </WorkspaceShell>
  );
}

function Stat({ label, value, href }: { label: string; value: string; href: string }) {
  return (
    <Link href={href} className="rounded-2xl bg-white p-6 shadow-sm ring-1 ring-hairline">
      <p className="font-body text-eyebrow uppercase tracking-[0.06em] text-ink-muted">{label}</p>
      <p className="mt-2 font-mono text-title text-ink">{value}</p>
    </Link>
  );
}
