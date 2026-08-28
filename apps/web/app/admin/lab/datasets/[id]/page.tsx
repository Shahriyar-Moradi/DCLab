"use client";

import { Button } from "@/app/components/ui/Button";
import { ErrorState } from "@/app/components/ui/ErrorState";
import { Skeleton } from "@/app/components/ui/Skeleton";
import { apiGet } from "@/lib/infrastructure";
import { LabDatasetSchema } from "@/lib/domain";
import { useLabUseCasePlan, useTrainLabUseCase } from "@/lib/application";
import { useQuery } from "@tanstack/react-query";
import { z } from "zod";
import Link from "next/link";
import { useParams } from "next/navigation";
import { useState } from "react";

export default function DatasetDetailPage() {
  const params = useParams<{ id: string }>();
  const datasetId = params.id;
  const [activeSlug, setActiveSlug] = useState<string | null>(null);
  const [batchMessage, setBatchMessage] = useState<string | null>(null);
  const dataset = useQuery({
    queryKey: ["lab", "dataset", datasetId],
    queryFn: () => apiGet(`/admin/datasets/${datasetId}`, LabDatasetSchema),
  });
  const profile = useQuery({
    queryKey: ["lab", "profile", datasetId],
    queryFn: () => apiGet(`/admin/datasets/${datasetId}/profile`, z.object({ id: z.string(), stats: z.unknown() })),
    retry: 0,
  });
  const plan = useLabUseCasePlan(datasetId);
  const train = useTrainLabUseCase(datasetId);

  if (dataset.isPending) return <Skeleton className="h-64" />;
  if (dataset.isError || !dataset.data) {
    return <ErrorState body="Dataset not found." onRetry={() => void dataset.refetch()} />;
  }
  const stats = profile.data?.stats as
    | { columns?: Array<{ name: string; missing_pct: number; dtype: string }>; row_count?: number }
    | undefined;
  const trainable = (plan.data?.use_cases ?? []).filter((item) => item.trainable);

  async function trainAll() {
    setBatchMessage(null);
    for (const item of trainable) {
      setActiveSlug(item.slug);
      try {
        await train.mutateAsync(item.slug);
      } catch (error) {
        setBatchMessage(error instanceof Error ? error.message : "Training failed.");
        setActiveSlug(null);
        return;
      }
    }
    setActiveSlug(null);
    setBatchMessage(`Trained ${trainable.length} use case${trainable.length === 1 ? "" : "s"}.`);
  }

  return (
    <div>
      <p className="font-body text-eyebrow uppercase tracking-[0.06em] text-ink-muted">
        <Link className="text-navy underline-offset-2 hover:underline" href="/admin/lab/datasets">
          Datasets
        </Link>
      </p>
      <h1 className="mt-2 font-display text-title text-ink">{dataset.data.name}</h1>
      <p className="mt-2 font-mono text-data text-ink-muted">
        {dataset.data.row_count} rows · {dataset.data.column_count} columns · {dataset.data.version}
      </p>
      <h2 className="mt-10 font-display text-section text-ink">Use cases</h2>
      <p className="mt-2 max-w-2xl font-body text-body text-ink-muted">
        Five models per use case: a baseline, a linear model on all features, a random forest on all features, and two
        tree models on different feature-group combinations.
      </p>
      {plan.isPending ? <Skeleton className="mt-4 h-40" /> : null}
      {plan.isError ? (
        <p className="mt-4 font-body text-body text-oxblood">Could not plan use cases for this file.</p>
      ) : null}
      <div className="mt-4 grid gap-4">
        {(plan.data?.use_cases ?? []).map((item) => (
          <article key={item.slug} className="rounded bg-paper-raised p-6">
            <div className="flex flex-wrap items-start justify-between gap-4">
              <div>
                <h3 className="font-display text-section text-ink">{item.name}</h3>
                <p className="mt-1 font-body text-body text-ink-muted">{item.description}</p>
                {item.trainable ? (
                  <p className="mt-2 font-mono text-data text-ink">
                    label {item.target_column} · {item.task_type} · {item.model_families.length} families
                  </p>
                ) : (
                  <p className="mt-2 font-body text-body text-oxblood">{item.skip_reason}</p>
                )}
                {item.trainable ? (
                  <ul className="mt-3 font-mono text-data text-ink-muted">
                    {Object.entries(item.feature_groups).map(([group, cols]) => (
                      <li key={group}>
                        {group}: {cols.join(", ")}
                      </li>
                    ))}
                  </ul>
                ) : null}
                {item.latest_experiment_id ? (
                  <p className="mt-3 font-body text-body">
                    Last run {item.latest_status} ·{" "}
                    <Link
                      className="text-navy underline-offset-2 hover:underline"
                      href={`/admin/lab/experiments/${item.latest_experiment_id}`}
                    >
                      Open report
                    </Link>
                  </p>
                ) : null}
              </div>
              <Button
                disabled={!item.trainable || train.isPending}
                onClick={() => {
                  setActiveSlug(item.slug);
                  setBatchMessage(null);
                  train.mutate(item.slug, { onSettled: () => setActiveSlug(null) });
                }}
              >
                {train.isPending && activeSlug === item.slug ? "Training…" : "Train 5 models"}
              </Button>
            </div>
          </article>
        ))}
      </div>
      <p className="mt-6">
        <Button disabled={trainable.length === 0 || train.isPending} onClick={() => void trainAll()}>
          {train.isPending && activeSlug ? `Training ${activeSlug}…` : `Train all ready use cases (${trainable.length})`}
        </Button>
      </p>
      {train.isError ? <p className="mt-3 font-body text-body text-oxblood">{train.error.message}</p> : null}
      {batchMessage ? <p className="mt-3 font-body text-body text-ink">{batchMessage}</p> : null}
      <h2 className="mt-10 font-display text-section text-ink">Profile</h2>
      {profile.isError ? (
        <p className="mt-2 font-body text-body text-ink-muted">No profile yet.</p>
      ) : (
        <ul className="mt-4 rounded bg-paper-raised p-4">
          {(stats?.columns ?? []).slice(0, 40).map((col) => (
            <li key={col.name} className="border-t border-hairline py-2 font-mono text-data text-ink">
              {col.name} · {col.dtype} · missing {Math.round((col.missing_pct ?? 0) * 100)}%
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
