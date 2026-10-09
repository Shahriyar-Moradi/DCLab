/**
 * Home view models (P4.15-UI). Pure: no React, no fetch. Every value comes from an API field; links are built
 * only from UUID-validated ids through `safeInternalHref`.
 */
import { safeInternalHref } from "../../components/studio/safe-href.ts";
import { isUuid } from "./command-search.ts";

export type PillToneName = "ok" | "warn" | "crit" | "ai" | "det" | "gray";

export const LIVE_RUN_STATUSES = new Set(["queued", "running", "cancelling"]);

type GovernanceLike = {
  ai_enabled_setting: boolean;
  policy_unavailable?: string | null;
  spend?: { currency: string; workspace: Array<{ currency: string; limit_micros: number; period: string; scope: string; spent_micros: number; hard_stop: boolean }> } | null;
  open_incidents: Array<unknown>;
  switches: { platform_ai_blocking: string | null; workspace: Array<{ state: string }> };
};

export type AiHealth = { state: "off" | "attention" | "on" | "unavailable"; label: string; detail: string };

/** A failed governance read says nothing about whether AI is on or off. */
export function aiHealthUnavailable(error: unknown): AiHealth {
  const status = (error as { status?: unknown } | null)?.status;
  if (status === 403) return { state: "unavailable", label: "AI health unavailable", detail: "Your role cannot read the AI settings, so AI health is not shown here." };
  return { state: "unavailable", label: "AI health unavailable", detail: "AI health could not be loaded." };
}

/** "AI is off" is explicit; the product works with it off. */
export function aiHealth(governance: GovernanceLike): AiHealth {
  if (governance.policy_unavailable) return { state: "off", label: "AI is off", detail: "The AI policy is not available, so AI requests are refused. Every step still works with rules." };
  if (governance.switches.platform_ai_blocking) return { state: "off", label: "AI is off", detail: "AI is switched off for the whole platform. Every step still works with rules." };
  if (!governance.ai_enabled_setting) return { state: "off", label: "AI is off", detail: "AI is off for this workspace. Every step still works with rules." };
  const incidents = governance.open_incidents.length;
  const switchedOff = governance.switches.workspace.filter((s) => s.state === "off").length;
  if (incidents > 0) return { state: "attention", label: `${incidents} open problem report${incidents === 1 ? "" : "s"}`, detail: "An AI feature was held after a problem. Ask a workspace admin to review it." };
  if (switchedOff > 0) return { state: "attention", label: `${switchedOff} AI switch${switchedOff === 1 ? "" : "es"} off`, detail: "Part of the AI is switched off in this workspace." };
  return { state: "on", label: "AI is on", detail: "No open incidents." };
}

const MICROS = 1_000_000;
export function formatMicros(micros: number, currency: string): string {
  const amount = micros / MICROS;
  try {
    return new Intl.NumberFormat("en-US", { style: "currency", currency, maximumFractionDigits: amount < 100 ? 2 : 0 }).format(amount);
  } catch {
    return `${amount.toFixed(2)} ${currency}`;
  }
}

/** AI spend against the workspace budget; the monthly workspace counter when there is one, else the first. */
export function spendSummary(governance: GovernanceLike): { value: string; hint: string; fraction: number | null } | null {
  const periods = governance.spend?.workspace ?? [];
  const period = periods.find((p) => p.scope === "workspace" && p.period === "month") ?? periods[0];
  if (!period) return null;
  const spent = formatMicros(period.spent_micros, period.currency);
  if (period.limit_micros <= 0) return { value: spent, hint: `${period.period} · no budget limit set`, fraction: null };
  return {
    value: spent,
    hint: `of ${formatMicros(period.limit_micros, period.currency)} ${period.period}${period.hard_stop ? " (hard stop)" : ""}`,
    fraction: Math.min(1, period.spent_micros / period.limit_micros),
  };
}

type ActivityLike = { kind: string; project_id?: string | null; link: { kind: string; id: string } };

/** Where an activity row goes: a History entry or an experiment inside its project. Agent runs have no page yet. */
export function activityHref(item: ActivityLike): string | null {
  if (!isUuid(item.project_id) || !isUuid(item.link.id)) return null;
  if (item.link.kind === "decision_record") return safeInternalHref(`/projects/${item.project_id}/decisions?record=${item.link.id}`);
  if (item.link.kind === "experiment") return safeInternalHref(`/projects/${item.project_id}/experiments/${item.link.id}`);
  return null;
}

export function actorLabel(actor: { kind: string; rule?: string | null; agent_key?: string | null; is_you?: boolean }): { text: string; tone: PillToneName } {
  if (actor.kind === "rule") return { text: "rules", tone: "det" };
  if (actor.kind === "agent") return { text: "connected tool", tone: "ai" };
  return { text: actor.is_you ? "you" : "person", tone: "gray" };
}

type ProjectLike = {
  id: string; name: string; updated_at: string;
  summary?: {
    goal?: { target_column?: string | null; primary_metric?: string | null; objective?: string | null } | null;
    champion?: { version: string; selection_metric?: string | null; metric_scope?: string; cv_metrics?: Record<string, number> } | null;
    latest_run?: { status: string } | null;
  };
};

/** The champion's CV metric (labelled as cross-validation by the caller); null until the evidence is locked. */
export function championMetric(project: ProjectLike): { name: string; value: number } | null {
  const champion = project.summary?.champion;
  // Only a cross-validation aggregate is ever shown under that label.
  if (!champion?.cv_metrics || champion.metric_scope !== "cv_aggregate") return null;
  const preferred = champion.selection_metric;
  const name = preferred && preferred in champion.cv_metrics ? preferred : Object.keys(champion.cv_metrics).sort()[0];
  return name === undefined ? null : { name, value: champion.cv_metrics[name] };
}

export function projectHomeHref(project: { id: string }): string | null {
  return isUuid(project.id) ? safeInternalHref(`/projects/${project.id}/experiments`) : null;
}

export function runsInProgress(projects: ProjectLike[]): number {
  return projects.filter((p) => LIVE_RUN_STATUSES.has(p.summary?.latest_run?.status ?? "")).length;
}

export function recentProjects<T extends { updated_at: string }>(projects: T[], limit: number): T[] {
  return [...projects].sort((a, b) => b.updated_at.localeCompare(a.updated_at)).slice(0, limit);
}
