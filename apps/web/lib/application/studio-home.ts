/**
 * Home view models (P4.15-UI). Pure: no React, no fetch. Every value comes from an API field; links are built
 * only from UUID-validated ids through `safeInternalHref`.
 */
import { safeInternalHref } from "../../components/studio/safe-href.ts";
import { isUuid, plainText } from "./command-search.ts";
import { lowerIsBetter } from "./studio-compare.ts";
import { lookup, inboxQuestion } from "./studio-inbox.ts";
import { metricInfo } from "./studio-goal.ts";
import { statusWords } from "./studio-runs.ts";
import { whatWords } from "./studio-history.ts";

export type PillToneName = "ok" | "warn" | "crit" | "ai" | "det" | "gray";

export const LIVE_RUN_STATUSES = new Set(["queued", "running", "cancelling"]);

type GovernanceLike = { ai_enabled_setting: boolean; policy_unavailable?: string | null; switches: { platform_ai_blocking: string | null } };

/** AI is off when the workspace setting, the platform or a missing policy says so. Only "off" is ever stated; "on" just means nothing says off. */
export function aiIsOff(governance: GovernanceLike): boolean {
  return !!governance.policy_unavailable || !!governance.switches.platform_ai_blocking || !governance.ai_enabled_setting;
}

type ActivityLike = { kind: string; project_id?: string | null; link: { kind: string; id: string } };

/** Where an activity row goes: a History entry or an experiment inside its project. Agent runs have no page yet. */
export function activityHref(item: ActivityLike): string | null {
  if (!isUuid(item.project_id) || !isUuid(item.link.id)) return null;
  if (item.link.kind === "decision_record") return safeInternalHref(`/projects/${item.project_id}/decisions?record=${item.link.id}`);
  if (item.link.kind === "experiment") return safeInternalHref(`/projects/${item.project_id}/experiments/${item.link.id}`);
  return null;
}

/** Who did it, honestly: access-token tools are never "the assistant", and an unknown agent kind is not guessed. */
export function actorLabel(actor: { kind: string; rule?: string | null; agent_key?: string | null; agent_run_id?: string | null; is_you?: boolean }): { text: string; tone: PillToneName } {
  if (actor.kind === "rule") return { text: "The rules", tone: "det" };
  // The read model does not say who started an agent run (a person or an access token), so it is never named here.
  if (actor.kind === "agent") return actor.agent_run_id ? { text: "An AI agent run", tone: "ai" } : { text: "A connected tool (access token) or an AI agent", tone: "ai" };
  if (actor.kind === "person") return { text: actor.is_you ? "You" : "A person", tone: "gray" };
  return { text: "Someone else", tone: "gray" };
}

const RUN_END: Record<string, string> = { completed: "finished", failed: "failed", cancelled: "was cancelled", skipped: "was skipped", ended: "ended" };
const withoutQuestionMark = (text: string): string => text.replace(/\?$/, "");

type ActivityText = ActivityLike & { summary: string; status?: string | null; decision_type?: string | null; subject?: { kind: string; id?: string | null } };

/** An activity row as a sentence (without the actor). Run names come from the caller; the server's fixed summaries are only matched. */
export function activitySentence(item: ActivityText, runName: string | null): string {
  if (item.kind === "decision") {
    const type = item.decision_type ?? "";
    const question = withoutQuestionMark(inboxQuestion(type));
    if (item.status === "proposed") return `Waiting for your decision: ${question}`;
    if (item.status === "rejected") return `Rejected: ${question}`;
    return item.status === "superseded" ? `${whatWords(type)} (since corrected)` : whatWords(type);
  }
  const name = runName ?? "A run";
  if (item.kind === "run_queued") return `${name} was queued`;
  if (item.kind === "run_finished") {
    const end = /\s(completed|failed|cancelled|skipped|ended)$/.exec(item.summary)?.[1];
    return `${name} ${end ? lookup(RUN_END, end, "ended") : "ended"}`;
  }
  if (item.kind === "agent_run_started") return "An AI agent run started";
  if (item.kind === "agent_run_finished") return `An AI agent run ${item.status ? statusWords(item.status).toLowerCase() : "ended"}`;
  return plainText(item.summary, 120);
}

/** True for rows about AI agent runs: hidden while AI is known to be off. */
export const isAssistantRow = (item: { kind: string }): boolean => item.kind === "agent_run_started" || item.kind === "agent_run_finished";

export function greeting(hour: number): string {
  return hour < 12 ? "Good morning" : hour < 18 ? "Good afternoon" : "Good evening";
}

type ProjectLike = {
  id: string; name: string; updated_at: string;
  summary?: {
    goal?: { target_column?: string | null; primary_metric?: string | null; objective?: string | null } | null;
    champion?: { version: string; selection_metric?: string | null; metric_scope?: string; cv_metrics?: Record<string, number> } | null;
    latest_run?: { status: string; experiment_id?: string } | null;
  };
};

/** The model in use's cross-validation score in the metric's own units (error metrics are stored negated; they are shown as positive errors). */
export function championMetric(project: ProjectLike): { name: string; value: number } | null {
  const champion = project.summary?.champion;
  // Only a cross-validation aggregate is ever shown under that label.
  if (!champion?.cv_metrics || champion.metric_scope !== "cv_aggregate") return null;
  const preferred = champion.selection_metric;
  const name = preferred && Object.hasOwn(champion.cv_metrics, preferred) ? preferred : Object.keys(champion.cv_metrics).sort()[0];
  if (name === undefined) return null;
  const raw = champion.cv_metrics[name];
  return Number.isFinite(raw) ? { name, value: lowerIsBetter(name) ? Math.abs(raw) : raw } : null;
}

/** "PR-AUC 0.70" for the score column. */
export function championScoreText(project: ProjectLike): string | null {
  const metric = championMetric(project);
  if (!metric) return null;
  const label = metricInfo(metric.name)?.label ?? metric.name;
  return `${label} ${Math.abs(metric.value) >= 100 ? metric.value.toFixed(1) : metric.value.toFixed(2)}`;
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

/** Projects whose latest run is queued, running or stopping (name and run link come from the project summary). */
export function liveRuns<T extends ProjectLike>(projects: T[]): Array<{ project: T; status: string; experimentId: string | null }> {
  return projects.flatMap((project) => {
    const run = project.summary?.latest_run;
    return run && LIVE_RUN_STATUSES.has(run.status) ? [{ project, status: run.status, experimentId: isUuid(run.experiment_id) ? run.experiment_id! : null }] : [];
  });
}
