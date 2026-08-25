"use client";

import { ErrorState } from "@/app/components/ui/ErrorState";
import { Skeleton } from "@/app/components/ui/Skeleton";
import { PageIntro, WorkspaceShell } from "@/app/components/workspace/PageIntro";
import { useLabTasks } from "@/lib/application";
import Link from "next/link";

export default function LabTasksPage() {
  const query = useLabTasks();
  if (query.isPending) {
    return (
      <WorkspaceShell>
        <Skeleton className="h-64" />
      </WorkspaceShell>
    );
  }
  if (query.isError) {
    return (
      <WorkspaceShell>
        <ErrorState body="Could not load tasks." onRetry={() => void query.refetch()} />
      </WorkspaceShell>
    );
  }
  return (
    <WorkspaceShell>
      <PageIntro
        eyebrow="Experimentation lab"
        title="Tasks"
        actions={
          <Link className="font-body text-body font-semibold text-brand underline-offset-2 hover:underline" href="/lab/tasks/create">
            Create from YAML
          </Link>
        }
      />
      <div className="mt-8 grid gap-4">
        {(query.data ?? []).map((task) => (
          <article key={task.id} className="rounded-2xl bg-white p-6 shadow-sm ring-1 ring-hairline">
            <p className="font-mono text-data text-ink-muted">{task.slug}</p>
            <h2 className="mt-1 font-display text-section text-ink">{task.name}</h2>
            <p className="mt-2 font-body text-body text-ink-muted">{task.description}</p>
            <p className="mt-2 font-mono text-data text-ink">{task.task_type}</p>
          </article>
        ))}
      </div>
    </WorkspaceShell>
  );
}
