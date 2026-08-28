"use client";

import { ErrorState } from "@/app/components/ui/ErrorState";
import { Skeleton } from "@/app/components/ui/Skeleton";
import { useLabTasks } from "@/lib/application";
import Link from "next/link";

export default function LabTasksPage() {
  const query = useLabTasks();
  if (query.isPending) return <Skeleton className="h-64" />;
  if (query.isError) return <ErrorState body="Could not load tasks." onRetry={() => void query.refetch()} />;
  return (
    <div>
      <h1 className="font-display text-title text-ink">Tasks</h1>
      <p className="mt-2">
        <Link className="font-body text-body text-navy underline-offset-2 hover:underline" href="/admin/lab/tasks/create">
          Create from YAML
        </Link>
      </p>
      <div className="mt-8 grid gap-4">
        {(query.data ?? []).map((task) => (
          <article key={task.id} className="rounded bg-paper-raised p-6">
            <p className="font-mono text-data text-ink-muted">{task.slug}</p>
            <h2 className="mt-1 font-display text-section text-ink">{task.name}</h2>
            <p className="mt-2 font-body text-body text-ink-muted">{task.description}</p>
            <p className="mt-2 font-mono text-data text-ink">{task.task_type}</p>
          </article>
        ))}
      </div>
    </div>
  );
}
