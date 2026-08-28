"use client";

import { ErrorState } from "@/app/components/ui/ErrorState";
import { Skeleton } from "@/app/components/ui/Skeleton";
import { useLabExperiments } from "@/lib/application";
import Link from "next/link";

export default function LabExperimentsPage() {
  const query = useLabExperiments();
  if (query.isPending) return <Skeleton className="h-64" />;
  if (query.isError) return <ErrorState body="Could not load experiments." onRetry={() => void query.refetch()} />;
  return (
    <div>
      <h1 className="font-display text-title text-ink">Experiments</h1>
      <p className="mt-2 font-body text-body text-ink-muted">Each run is one use case on one dataset.</p>
      <ul className="mt-8 divide-y divide-hairline rounded bg-paper-raised">
        {(query.data ?? []).map((row) => (
          <li key={row.id} className="px-4 py-3">
            <Link className="font-body text-body text-navy underline-offset-2 hover:underline" href={`/admin/lab/experiments/${row.id}`}>
              {row.task_name ?? row.use_case ?? row.id}
            </Link>
            <p className="font-mono text-data text-ink-muted">
              {row.status}
              {row.dataset_name ? ` · ${row.dataset_name}` : ""}
              {row.use_case ? ` · ${row.use_case}` : ""}
            </p>
          </li>
        ))}
      </ul>
    </div>
  );
}
