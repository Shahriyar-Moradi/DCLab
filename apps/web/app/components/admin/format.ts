import { formatTimestamp, type SignalTone } from "@/lib/domain";

export function numericMetricEntries(metrics: Record<string, unknown> | null | undefined): [string, number][] {
  if (!metrics) return [];
  return Object.entries(metrics).filter((entry): entry is [string, number] => typeof entry[1] === "number");
}

export function formatNumericMetrics(metrics: Record<string, unknown> | null | undefined): string {
  const entries = numericMetricEntries(metrics);
  if (!entries.length) return "";
  return entries
    .map(([key, value]) => `${key}=${Number.isInteger(value) ? String(value) : value.toFixed(3)}`)
    .join(" · ");
}

export function formatWhen(value: string | null | undefined): string {
  if (!value) return "";
  return formatTimestamp(value) || value;
}

export function recordOf(value: unknown): Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value) ? (value as Record<string, unknown>) : {};
}

export function sourceTone(source: string): SignalTone {
  if (source === "experiment") return "green";
  if (source === "simulation") return "amber";
  return "oxblood";
}

export function datasetHealthTone(status: string): SignalTone {
  if (status === "healthy") return "green";
  if (status === "not_profiled") return "amber";
  return "oxblood";
}

export function formatDurationSeconds(seconds: number | null | undefined): string {
  if (seconds == null || Number.isNaN(seconds)) return "";
  if (seconds < 60) return `${seconds.toFixed(1)}s`;
  const minutes = Math.floor(seconds / 60);
  const rest = seconds - minutes * 60;
  return `${minutes}m ${rest.toFixed(0)}s`;
}

export function formatElapsed(startedAt: string | null | undefined, endedAt: string | null | undefined): string {
  if (!startedAt || !endedAt) return "";
  const start = Date.parse(startedAt);
  const end = Date.parse(endedAt);
  if (Number.isNaN(start) || Number.isNaN(end)) return "";
  return formatDurationSeconds(Math.max(0, (end - start) / 1000));
}

export function stringifyUnknown(value: unknown): string {
  if (value === null || value === undefined || value === "") return "";
  if (typeof value === "string") return value;
  if (typeof value === "number" || typeof value === "boolean") return String(value);
  try {
    return JSON.stringify(value);
  } catch {
    return String(value);
  }
}
