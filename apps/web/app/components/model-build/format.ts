import type { ModelBuildStage, PipelineModelBuild } from "@/lib/domain";

export type StageVisual = "completed" | "running" | "failed" | "upcoming" | "skipped";

export function stageVisual(status: string): StageVisual {
  const value = status.toLowerCase();
  if (["complete", "completed", "succeeded", "success"].includes(value)) return "completed";
  if (["running", "started", "in_progress"].includes(value)) return "running";
  if (["failed", "error"].includes(value)) return "failed";
  if (["skipped", "cancelled", "canceled"].includes(value)) return "skipped";
  return "upcoming";
}

export function formatDurationMs(value: number | null | undefined): string {
  if (value == null || Number.isNaN(value) || value < 0) return "";
  if (value < 1000) return `${Math.round(value)}ms`;
  const seconds = value / 1000;
  if (seconds < 60) return `${seconds < 10 ? seconds.toFixed(1) : seconds.toFixed(0)}s`;
  const minutes = Math.floor(seconds / 60);
  const rest = Math.round(seconds - minutes * 60);
  return rest ? `${minutes}m ${rest}s` : `${minutes}m`;
}

export function asRecord(value: unknown): Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value)
    ? (value as Record<string, unknown>)
    : {};
}

export function asList(value: unknown): unknown[] {
  return Array.isArray(value) ? value : [];
}

export function asString(value: unknown, fallback = "—"): string {
  if (value === null || value === undefined || value === "") return fallback;
  if (typeof value === "string" || typeof value === "number" || typeof value === "boolean") {
    return String(value);
  }
  try {
    return JSON.stringify(value);
  } catch {
    return fallback;
  }
}

export function asNumber(value: unknown): number | null {
  return typeof value === "number" && !Number.isNaN(value) ? value : null;
}

export function asStringRecord(value: unknown): Record<string, string> {
  const row = asRecord(value);
  const out: Record<string, string> = {};
  for (const [key, item] of Object.entries(row)) {
    if (item === null || item === undefined) continue;
    if (typeof item === "object") {
      out[key] = asString(item);
      continue;
    }
    out[key] = String(item);
  }
  return out;
}

export function asNumberRecord(value: unknown): Record<string, number> {
  const row = asRecord(value);
  const out: Record<string, number> = {};
  for (const [key, item] of Object.entries(row)) {
    if (typeof item === "number" && !Number.isNaN(item)) out[key] = item;
  }
  return out;
}

export function percentLabel(value: unknown): string {
  const number = asNumber(value);
  if (number == null) return "—";
  const ratio = number <= 1 ? number : number / 100;
  return `${Math.round(ratio * 1000) / 10}%`;
}

export function stageByKey(build: PipelineModelBuild, key: string): ModelBuildStage | undefined {
  return build.stages.find((stage) => stage.key === key);
}

export function defaultStageKey(stages: ModelBuildStage[]): string | undefined {
  const running = stages.find((stage) => stageVisual(stage.status) === "running");
  if (running) return running.key;
  const failed = stages.find((stage) => stageVisual(stage.status) === "failed");
  if (failed) return failed.key;
  const completed = [...stages].reverse().find((stage) => stageVisual(stage.status) === "completed");
  return completed?.key ?? stages[0]?.key;
}

export function overallProgress(stages: ModelBuildStage[]): { completed: number; total: number; percent: number } {
  const total = stages.length || 1;
  const completed = stages.filter((stage) => stageVisual(stage.status) === "completed").length;
  const running = stages.filter((stage) => stageVisual(stage.status) === "running").length;
  const percent = Math.round(((completed + running * 0.45) / total) * 100);
  return { completed, total: stages.length, percent: Math.min(100, percent) };
}

export function formatMetric(value: number): string {
  if (Number.isInteger(value)) return String(value);
  const abs = Math.abs(value);
  if (abs >= 100) return value.toFixed(1);
  if (abs >= 1) return value.toFixed(3);
  return value.toFixed(4);
}
