import { formatTimestamp } from "@/lib/domain";
import type {
  BusinessModelDetail,
  BusinessWorkflowRunDetail,
  BusinessWorkspaceDetail,
  PlatformBusinessDetail,
  PlatformModelDetail,
  PlatformWorkflowRunDetail,
} from "@/lib/domain";

export type ExplorerFact = { label: string; value: string; mono?: boolean };

export function nonempty(value: string | null | undefined): string | undefined {
  const trimmed = value?.trim();
  return trimmed ? trimmed : undefined;
}

export function fact(label: string, value: string | null | undefined, mono?: boolean): ExplorerFact | null {
  const text = nonempty(value);
  return text ? { label, value: text, mono } : null;
}

export function factsOf(items: Array<ExplorerFact | null | undefined>): ExplorerFact[] {
  return items.filter((item): item is ExplorerFact => Boolean(item && nonempty(item.value)));
}

export function recordHasKeys(value: Record<string, unknown> | null | undefined): boolean {
  return Boolean(value && Object.keys(value).length > 0);
}

export function formatMetricMap(metrics: Record<string, unknown>): string {
  return Object.entries(metrics)
    .flatMap(([key, value]) => {
      if (value == null || value === "") return [];
      if (typeof value === "number") {
        return [`${key} ${Number.isInteger(value) ? String(value) : value.toFixed(4)}`];
      }
      if (typeof value === "string" || typeof value === "boolean") {
        return [`${key} ${String(value)}`];
      }
      return [`${key} ${JSON.stringify(value)}`];
    })
    .join(" · ");
}

export function formatWhen(value: string | null | undefined): string | undefined {
  if (!value) return undefined;
  return nonempty(formatTimestamp(value)) ?? nonempty(value);
}

export function isWorkspaceDetail(
  business: PlatformBusinessDetail | BusinessWorkspaceDetail,
): business is BusinessWorkspaceDetail {
  return "capabilities" in business;
}

export function isBusinessRun(
  run: PlatformWorkflowRunDetail | BusinessWorkflowRunDetail,
): run is BusinessWorkflowRunDetail {
  return "capabilities" in run;
}

export function isBusinessModel(
  model: PlatformModelDetail | BusinessModelDetail,
): model is BusinessModelDetail {
  return "capabilities" in model;
}

export function canOpenMonitor(
  businessMode: boolean,
  capabilities: Record<string, boolean> | undefined,
): boolean {
  return !businessMode || capabilities?.pipeline_monitor === true;
}
