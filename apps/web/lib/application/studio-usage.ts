/**
 * P8.4-UI: Settings -> Usage & cost (read only). Pure view model for GET /v1/workspaces/{id}/usage:
 * plain units (hours/minutes, GB), percent and warning/limit states, the 31-day series and the honest
 * notes. Every number comes from the read; nothing is invented, and a failed read is never shown as 0.
 */
import { plainText } from "./command-search.ts";

export type UsageQuota = {
  key: string;
  limit: number | null;
  over: boolean;
  percent: number | null;
  period: string;
  resets_at: string | null;
  used: number;
  warning: boolean;
  enforced: boolean;
  unit: string;
};
export type UsageAiSpend = { currency: string; hard_stop: boolean; limit_micros: number; over: boolean; percent: number | null; period_start: string; reserved_micros: number; spent_micros: number; warning: boolean };
export type UsageDay = { day: string; compute_seconds: number };
export type UsageReadLike = {
  plan: string;
  quotas: UsageQuota[];
  ai_spend: UsageAiSpend | null;
  daily: UsageDay[];
  disclaimer: string;
  notes: string[];
  resets_at: string;
  quotas_enforced: boolean;
  metering_enabled: boolean;
  measured_at: string | null;
};

export type UsageScreenState = "loading" | "no_workspace" | "forbidden" | "error" | "empty" | "ready";

export const USAGE_DISCLAIMER_FALLBACK = "Measured from DCLab's own records: an estimate, not an invoice.";
export const USAGE_FORBIDDEN_TEXT = "Usage and limits are shown to owners, administrators and data scientists. Ask an administrator.";
/**
 * "Never blocks reading, approving or rolling back" is true by construction: the limit check is called only from
 * the admission points (pinned by apps/api/tests/test_usage_metering.py::test_only_admission_points_call_the_limit_check).
 */
export const USAGE_HONEST_NOTES: string[] = [
  "Compute time counts training and scoring runs only, and only the last attempt of each.",
  "Stored data is a snapshot of what the workspace holds now, not a monthly total.",
  "A limit refuses new work only. It never stops work that is already running, and never blocks reading, approving or rolling back.",
  "Adding a person can be refused when the plan's member limit is reached.",
  "Plans are set by DCLab staff. There is nothing to change on this page.",
  "The limit bars are measured live. The chart and table come from hourly totals, so they can be up to an hour behind, and work that is still running is not counted in them yet.",
];

const UNAVAILABLE = "unavailable";
/** A usable amount, or null: a bad value is never shown as a real-looking 0. */
const amount = (value: unknown): number | null => (typeof value === "number" && Number.isFinite(value) && value >= 0 ? value : null);
const num = (value: unknown): number => amount(value) ?? 0;

/** Which screen to show. A refused read (403) is its own state; a failed read is never "empty". */
export function usageScreenState(args: { loaded: boolean; hasWorkspace?: boolean; pending: boolean; status: number | null; failed: boolean; data: UsageReadLike | undefined }): UsageScreenState {
  if (!args.loaded) return "loading";
  if (args.hasWorkspace === false) return "no_workspace";
  if (args.pending) return "loading";
  if (args.status === 403) return "forbidden";
  if (args.failed || !args.data) return "error";
  return args.data.quotas.length === 0 && args.data.daily.length === 0 ? "empty" : "ready";
}

/** Compute time in plain words: seconds, minutes, or hours and minutes. */
export function formatDuration(seconds: unknown): string {
  const raw = amount(seconds);
  if (raw === null) return UNAVAILABLE;
  const total = Math.round(raw);
  if (total < 60) return `${total} ${total === 1 ? "second" : "seconds"}`;
  const minutes = Math.round(total / 60);
  if (minutes < 60) return `${minutes} ${minutes === 1 ? "minute" : "minutes"}`;
  const hours = Math.floor(minutes / 60);
  const rest = minutes % 60;
  const h = `${hours.toLocaleString("en-US")} ${hours === 1 ? "hour" : "hours"}`;
  return rest === 0 ? h : `${h} ${rest} min`;
}

/** Stored data: MB below 0.1 GB, else GB. Both are binary units (GiB/MiB: 1 GB = 1,024 MB), as the server counts them. */
export function formatGb(bytes: unknown): string {
  const raw = amount(bytes);
  if (raw === null) return UNAVAILABLE;
  const gb = raw / 1024 ** 3;
  if (gb < 0.1 && raw > 0) return `${(raw / 1024 ** 2).toLocaleString("en-US", { minimumFractionDigits: 1, maximumFractionDigits: 1 })} MB`;
  return `${gb.toLocaleString("en-US", { minimumFractionDigits: 1, maximumFractionDigits: 1 })} GB`;
}

export function formatMoney(micros: unknown, currency: unknown): string {
  const raw = amount(micros);
  if (raw === null) return UNAVAILABLE;
  const code = plainText(currency, 8) || "(billing currency)";
  return `${(raw / 1e6).toLocaleString("en-US", { minimumFractionDigits: 2, maximumFractionDigits: 2 })} ${code}`;
}

export function formatUtcDateTime(value: unknown): string {
  const date = new Date(String(value ?? ""));
  return Number.isNaN(date.getTime()) ? "time unknown" : `${date.toLocaleString("en-US", { year: "numeric", month: "short", day: "numeric", hour: "2-digit", minute: "2-digit", hour12: false, timeZone: "UTC" })} (UTC)`;
}

export function formatUtcDate(value: unknown): string {
  const date = new Date(String(value ?? ""));
  return Number.isNaN(date.getTime()) ? "date unknown" : `${date.toLocaleDateString("en-US", { year: "numeric", month: "short", day: "numeric", timeZone: "UTC" })} (UTC)`;
}

const QUOTA_LABEL: Record<string, string> = {
  compute_seconds_month: "Compute time this month",
  storage_bytes: "Stored data",
  runs_per_day: "Runs today",
  members: "People",
};

function formatAmount(key: string, value: number): string {
  if (key === "compute_seconds_month") return formatDuration(value);
  if (key === "storage_bytes") return formatGb(value);
  if (amount(value) === null) return UNAVAILABLE;
  const n = Math.round(num(value));
  return key === "runs_per_day" ? `${n.toLocaleString("en-US")} ${n === 1 ? "run" : "runs"}` : n.toLocaleString("en-US");
}

export type QuotaBar = {
  key: string;
  label: string;
  state: "ok" | "warning" | "over" | "unlimited";
  stateText: string;
  usedText: string;
  limitText: string;
  noAllowance: boolean;
  percentText: string;
  width: number;
  resetText: string;
  enforcedNote: string | null;
};

const SHOWN_QUOTAS = ["compute_seconds_month", "storage_bytes", "runs_per_day", "members"];
export const NO_ALLOWANCE_TEXT = "No allowance on this plan";
export const NO_AI_BUDGET_TEXT = "No AI budget is set";

/** One bar per shown limit. Over beats warning; no limit means unlimited and no percent is invented. */
export function quotaBar(q: UsageQuota): QuotaBar {
  const key = Object.hasOwn(QUOTA_LABEL, q.key) ? q.key : "other";
  const unlimited = q.limit === null || q.limit === undefined;
  const state: QuotaBar["state"] = q.over === true ? "over" : q.warning === true ? "warning" : unlimited ? "unlimited" : "ok";
  const noAllowance = q.limit === 0;
  const percent = unlimited || noAllowance ? null : typeof q.percent === "number" && Number.isFinite(q.percent) ? Math.max(0, q.percent) : q.limit ? (num(q.used) / q.limit) * 100 : 0;
  return {
    key: q.key,
    label: key === "other" ? plainText(q.key, 40) || "Limit" : QUOTA_LABEL[key],
    state,
    stateText: state === "over" ? "Limit reached" : state === "warning" ? "Warning: at 80% or more" : state === "unlimited" ? "No limit" : "Within the limit",
    usedText: formatAmount(q.key, q.used),
    limitText: unlimited ? "no limit" : noAllowance ? NO_ALLOWANCE_TEXT : formatAmount(q.key, q.limit as number),
    noAllowance,
    percentText: percent === null ? "" : `${(Math.round(percent * 10) / 10).toLocaleString("en-US")}%`,
    width: noAllowance ? (q.over ? 100 : 0) : percent === null ? 0 : Math.min(100, Math.max(0, percent)),
    resetText: q.resets_at ? `Starts again ${formatUtcDate(q.resets_at)}` : q.period === "none" ? "Not reset by a date" : "",
    enforcedNote: q.enforced === false ? "Shown only: this limit does not refuse work right now." : null,
  };
}

export function quotaBars(quotas: UsageQuota[]): QuotaBar[] {
  // The people limit is shown only when the read returns it and it is enforced.
  return SHOWN_QUOTAS.flatMap((key) => quotas.filter((q) => q.key === key && (key !== "members" || q.enforced === true))).map(quotaBar);
}

export type AiSpendView = { state: QuotaBar["state"] | "none"; stateText: string; spentText: string; limitText: string; reservedText: string; percentText: string; width: number; hardStopText: string };

/** The read does not carry the policy's alert fraction, so the AI card never states "80%". A zero budget is "no budget", not 0%. */
export function aiSpendView(spend: UsageAiSpend | null): AiSpendView | null {
  if (!spend) return null;
  const none = spend.limit_micros === 0;
  const state: AiSpendView["state"] = none ? "none" : spend.over ? "over" : spend.warning ? "warning" : "ok";
  const percent = typeof spend.percent === "number" && Number.isFinite(spend.percent) ? Math.max(0, spend.percent) : 0;
  return {
    state,
    stateText: state === "none" ? NO_AI_BUDGET_TEXT : state === "over" ? "AI budget reached" : state === "warning" ? "Warning: close to the AI budget" : "Within the budget",
    spentText: formatMoney(spend.spent_micros, spend.currency),
    limitText: none ? NO_AI_BUDGET_TEXT : formatMoney(spend.limit_micros, spend.currency),
    reservedText: formatMoney(spend.reserved_micros, spend.currency),
    percentText: none ? "" : `${(Math.round(percent * 10) / 10).toLocaleString("en-US")}%`,
    width: none ? 0 : Math.min(100, percent),
    hardStopText: spend.hard_stop ? "At the budget, new AI calls are refused." : "At the budget, AI features are only flagged.",
  };
}

export type DayBar = { day: string; label: string; seconds: number; text: string; height: number };

/** The 31-day series, scaled to its own maximum; an all-zero series draws no bars (and says so). */
export function dailySeries(days: UsageDay[]): { bars: DayBar[]; max: number; allZero: boolean; summary: string } {
  const clean = days.map((d) => ({ day: plainText(d.day, 10), seconds: num(d.compute_seconds) }));
  const max = clean.reduce((m, d) => Math.max(m, d.seconds), 0);
  const total = clean.reduce((s, d) => s + d.seconds, 0);
  const bars = clean.map((d) => ({
    day: d.day,
    label: d.day.slice(5),
    seconds: d.seconds,
    text: formatDuration(d.seconds),
    height: max === 0 ? 0 : Math.max(d.seconds > 0 ? 2 : 0, (d.seconds / max) * 100),
  }));
  return {
    bars,
    max,
    allZero: max === 0,
    summary: clean.length === 0 ? "No days to show." : `Compute time per day over the last ${clean.length} days (UTC). Total ${formatDuration(total)}; busiest day ${formatDuration(max)}.`,
  };
}

/** Server notes as plain text, minus any that repeat a fixed note above. */
export function noteTexts(notes: unknown): string[] {
  const own = new Set(USAGE_HONEST_NOTES.map((t) => t.toLowerCase()));
  const seen = new Set<string>();
  return (Array.isArray(notes) ? notes : []).map((n) => plainText(n, 300)).filter((t) => {
    const key = t.toLowerCase();
    if (!t || own.has(key) || seen.has(key)) return false;
    seen.add(key);
    return true;
  });
}

export function freshnessText(measuredAt: unknown): string {
  return measuredAt ? `Daily totals last updated ${formatUtcDateTime(measuredAt)}.` : "Daily totals have not been recorded yet.";
}
