"use client";

import { ErrorState } from "@/app/components/ui/ErrorState";
import { Skeleton } from "@/app/components/ui/Skeleton";
import { WorkspaceShell } from "@/app/components/workspace/PageIntro";
import { apiGet } from "@/lib/infrastructure";
import { LabDatasetSchema } from "@/lib/domain";
import { useQuery } from "@tanstack/react-query";
import { z } from "zod";
import { useParams } from "next/navigation";

export default function DatasetDetailPage() {
  const params = useParams<{ id: string }>();
  const dataset = useQuery({
    queryKey: ["lab", "dataset", params.id],
    queryFn: () => apiGet(`/lab/datasets/${params.id}`, LabDatasetSchema),
  });
  const profile = useQuery({
    queryKey: ["lab", "profile", params.id],
    queryFn: () => apiGet(`/lab/datasets/${params.id}/profile`, z.object({ id: z.string(), stats: z.unknown() })),
    retry: 0,
  });
  if (dataset.isPending) {
    return (
      <WorkspaceShell>
        <Skeleton className="h-64" />
      </WorkspaceShell>
    );
  }
  if (dataset.isError || !dataset.data) {
    return (
      <WorkspaceShell>
        <ErrorState body="Dataset not found." onRetry={() => void dataset.refetch()} />
      </WorkspaceShell>
    );
  }
  const stats = profile.data?.stats as { columns?: Array<{ name: string; missing_pct: number; dtype: string }>; row_count?: number } | undefined;
  return (
    <WorkspaceShell>
      <h1 className="font-display text-title text-ink">{dataset.data.name}</h1>
      <p className="mt-2 font-mono text-data text-ink-muted">
        {dataset.data.row_count} rows · {dataset.data.column_count} columns · {dataset.data.version}
      </p>
      <h2 className="mt-8 font-display text-section text-ink">Profile</h2>
      {profile.isError ? (
        <p className="mt-2 font-body text-body text-ink-muted">No profile yet. Run `dclab dataset profile`.</p>
      ) : (
        <ul className="mt-4 rounded-2xl bg-white p-4 shadow-sm ring-1 ring-hairline">
          {(stats?.columns ?? []).slice(0, 40).map((col) => (
            <li key={col.name} className="border-t border-hairline py-2 font-mono text-data text-ink">
              {col.name} · {col.dtype} · missing {Math.round((col.missing_pct ?? 0) * 100)}%
            </li>
          ))}
        </ul>
      )}
    </WorkspaceShell>
  );
}
