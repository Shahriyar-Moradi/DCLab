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
      <ul className="mt-8 divide-y divide-hairline rounded bg-paper-raised">
        {(query.data ?? []).map((row) => (
          <li key={row.id} className="px-4 py-3">
            <Link className="font-mono text-data text-navy underline-offset-2 hover:underline" href={`/lab/experiments/${row.id}`}>
              {row.id}
            </Link>
            <p className="font-body text-body text-ink-muted">{row.status}</p>
          </li>
        ))}
      </ul>
    </div>
  );
}
