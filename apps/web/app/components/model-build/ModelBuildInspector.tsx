"use client";

import { Badge } from "@/app/components/ui/Badge";
import { Button } from "@/app/components/ui/Button";
import { ErrorState } from "@/app/components/ui/ErrorState";
import { GlassPanel } from "@/app/components/ui/GlassPanel";
import { LoadingState } from "@/app/components/ui/LoadingState";
import { StatusBadge, statusTone } from "@/app/components/ui/StatusBadge";
import { downloadModelBuildReproduction, useModelBuild } from "@/lib/application";
import { cn } from "@/lib/cn";
import { useEffect, useMemo, useState } from "react";
import { ModelBuildGeneratedCodePanel } from "./ModelBuildGeneratedCodePanel";
import { ModelBuildStagePanel } from "./ModelBuildStagePanels";
import {
  defaultStageKey,
  formatDurationMs,
  overallProgress,
  stageVisual,
} from "./format";

export function ModelBuildInspector({
  workspaceId,
  pipelineRunId,
}: {
  workspaceId?: string;
  pipelineRunId?: string;
}) {
  const query = useModelBuild(workspaceId, pipelineRunId);
  const [selectedKey, setSelectedKey] = useState<string | undefined>(undefined);
  const [touched, setTouched] = useState(false);
  const [downloadError, setDownloadError] = useState<string | null>(null);
  const [downloadBusy, setDownloadBusy] = useState<"notebook" | "script" | null>(null);

  const stages = query.data?.stages ?? [];
  const fallbackKey = useMemo(() => defaultStageKey(stages), [stages]);

  useEffect(() => {
    if (touched && selectedKey && stages.some((stage) => stage.key === selectedKey)) return;
    setSelectedKey(fallbackKey);
  }, [fallbackKey, selectedKey, stages, touched]);

  if (!workspaceId || !pipelineRunId) return null;
  if (query.isPending) {
    return (
      <GlassPanel title="Model build" description="Canonical stages for this pipeline run.">
        <LoadingState label="Loading model build" />
      </GlassPanel>
    );
  }
  if (query.isError || !query.data) {
    return (
      <GlassPanel title="Model build">
        <ErrorState
          title="Model build is unavailable"
          body="This workspace-scoped timeline could not be loaded."
          onRetry={() => void query.refetch()}
        />
      </GlassPanel>
    );
  }

  const build = query.data;
  const selected = stages.find((stage) => stage.key === selectedKey) ?? stages[0];
  const progress = overallProgress(stages);
  const live = stages.some((stage) => stageVisual(stage.status) === "running");
  const totalDuration = stages.reduce((sum, stage) => sum + (stage.duration_ms ?? 0), 0);

  async function onDownload(kind: "notebook" | "script") {
    if (!workspaceId || !pipelineRunId) return;
    setDownloadError(null);
    setDownloadBusy(kind);
    try {
      await downloadModelBuildReproduction(workspaceId, pipelineRunId, kind);
    } catch {
      setDownloadError("Could not download the reproduction file.");
    } finally {
      setDownloadBusy(null);
    }
  }

  return (
    <GlassPanel
      title="Model build"
      description="Canonical persisted stages and the generated Python for the selected step."
    >
      <div className="mb-4 flex flex-wrap items-center gap-3">
        <Badge tone={statusTone(build.pipeline_run_status)} emphasis="soft">
          {build.pipeline_run_status}
        </Badge>
        {build.generator_version ? (
          <Badge tone="neutral" emphasis="soft">
            {build.generator_version}
          </Badge>
        ) : null}
        <p className="text-body text-ink" aria-live="polite">
          {progress.completed} of {progress.total} complete
          {totalDuration ? ` · ${formatDurationMs(totalDuration)}` : ""}
          {live ? " · live" : ""}
        </p>
        {build.reproduction_spec_digest ? (
          <p className="font-mono text-data text-ink-muted">
            spec {build.reproduction_spec_digest.slice(0, 12)}
          </p>
        ) : null}
        {build.reproduction_notebook || build.reproduction_script ? (
          <div className="ml-auto flex flex-wrap items-center gap-2">
            {build.reproduction_notebook ? (
              <Button
                type="button"
                variant="secondary"
                size="sm"
                loading={downloadBusy === "notebook"}
                disabled={downloadBusy !== null}
                onClick={() => void onDownload("notebook")}
              >
                Download notebook
              </Button>
            ) : null}
            {build.reproduction_script ? (
              <Button
                type="button"
                variant="secondary"
                size="sm"
                loading={downloadBusy === "script"}
                disabled={downloadBusy !== null}
                onClick={() => void onDownload("script")}
              >
                Download script
              </Button>
            ) : null}
          </div>
        ) : null}
      </div>
      {downloadError ? (
        <p className="mb-4 text-helper text-oxblood" role="alert">
          {downloadError}
        </p>
      ) : null}
      <div className="model-build-progress" role="progressbar" aria-valuemin={0} aria-valuemax={100} aria-valuenow={progress.percent}>
        <div className="model-build-progress-fill" style={{ width: `${progress.percent}%` }} />
      </div>

      <ol className="model-build-rail mt-5">
        {stages.map((stage) => {
          const visual = stageVisual(stage.status);
          const duration = formatDurationMs(stage.duration_ms);
          return (
            <li key={stage.key} className="min-w-0">
              <button
                type="button"
                onClick={() => {
                  setTouched(true);
                  setSelectedKey(stage.key);
                }}
                className={cn(
                  "model-build-step",
                  `model-build-step-${visual}`,
                  selected?.key === stage.key && "model-build-step-selected",
                )}
                aria-current={selected?.key === stage.key ? "step" : undefined}
              >
                <span className="flex items-center gap-2">
                  <span className="model-build-step-marker" aria-hidden />
                  <span className="font-mono text-data">{String(stage.sequence).padStart(2, "0")}</span>
                </span>
                <span className="line-clamp-2 text-helper">{stage.title}</span>
                {duration ? <span className="font-mono text-data">{duration}</span> : null}
              </button>
            </li>
          );
        })}
      </ol>

      {selected ? (
        <div className="mt-5 rounded-xl border border-hairline bg-paper-raised p-4 sm:p-5">
          <div className="mb-4 flex flex-wrap items-start justify-between gap-3">
            <div className="min-w-0">
              <p className="product-eyebrow">Stage {selected.sequence}</p>
              <h3 className="mt-1 font-sans text-section text-ink">{selected.title}</h3>
            </div>
            <div className="flex flex-wrap items-center gap-2">
              <StatusBadge status={selected.status} />
              {formatDurationMs(selected.duration_ms) ? (
                <span className="font-mono text-data text-ink-muted">{formatDurationMs(selected.duration_ms)}</span>
              ) : null}
            </div>
          </div>
          <ModelBuildStagePanel build={build} stage={selected} />
          <ModelBuildGeneratedCodePanel stage={selected} />
        </div>
      ) : null}
    </GlassPanel>
  );
}
