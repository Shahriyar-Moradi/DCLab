/**
 * Home view models (P4.15-UI). Pure: no React, no fetch. Every value comes from an API field; links are built
 * only from UUID-validated ids through `safeInternalHref`.
 */
import { safeInternalHref } from "../../components/studio/safe-href.ts";
import { isUuid, plainText } from "./command-search.ts";
import { lowerIsBetter } from "./studio-compare.ts";
import { inboxQuestion, inboxTitle, lookup, questionHref, suggestedBy } from "./studio-inbox.ts";
import { metricInfo } from "./studio-goal.ts";
import { familyLabel, stageWords, statusWords } from "./studio-runs.ts";
import { utcWhen } from "./format-time.ts";
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

const RETRAIN_REQUESTED: Record<string, string> = {
  person: "Started a retrain on newest data", auto: "Started a retrain on newest data automatically (the retrain policy is Automatic)", ask: "Asked in the Inbox: retrain on newest data?",
};
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
  // The fact only (no score): the run's final test set was used, once.
  if (item.kind === "final_test_run") return `${name} was checked once on its final test set`;
  if (item.kind === "agent_run_started") return "An AI agent run started";
  if (item.kind === "agent_run_finished") return `An AI agent run ${item.status ? statusWords(item.status).toLowerCase() : "ended"}`;
  // D5c: retrain events (the status is the origin, or the comparison's outcome); no figures.
  if (item.kind === "retrain_requested") return lookup(RETRAIN_REQUESTED, item.status ?? "", "Retrain on newest data");
  if (item.kind === "retrain_compared") return item.status === "compared" ? "The retrained model was put beside the model in use (same kind of test, latest months)" : "The retrained model could not be compared with the model in use";
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
    running_count?: number;
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

/** "PR-AUC 0.70" for a cross-validation score; error metrics are shown as positive errors in their own units. */
export function cvScoreText(metric: string | null | undefined, raw: number | null | undefined): string | null {
  if (!metric || typeof raw !== "number" || !Number.isFinite(raw)) return null;
  const value = lowerIsBetter(metric) ? Math.abs(raw) : raw;
  const label = metricInfo(metric)?.label ?? plainText(metric, 40);
  return `${label} ${Math.abs(value) >= 100 ? value.toFixed(1) : value.toFixed(2)}`;
}

/** "PR-AUC 0.70" for the score column. */
export function championScoreText(project: ProjectLike): string | null {
  const metric = championMetric(project);
  return metric ? cvScoreText(metric.name, metric.value) : null;
}

/** "CatBoost · CV PR-AUC 0.68" for a finished run whose evidence is locked (cross-validation only, never the final test set). */
export function runResultText(result: { model_family?: string | null; algorithm?: string | null; metric: string; cv_score: number; metric_scope?: string } | null | undefined): string | null {
  if (!result || result.metric_scope !== "cv_aggregate") return null;
  const score = cvScoreText(result.metric, result.cv_score);
  const family = familyLabel(result.model_family) ?? familyLabel(result.algorithm);
  return [family, score ? `CV ${score}` : null].filter(Boolean).join(" · ") || null;
}

/** The sentence under the Home title: what needs you, then what is running. Unknown counts are left out, never "0". */
export function homeSubtitle(waiting: number | undefined, running: RunningCount | undefined): string {
  const parts: string[] = [];
  if (waiting !== undefined) parts.push(waiting === 0 ? "Nothing needs you right now." : `${waiting} ${waiting === 1 ? "thing needs" : "things need"} you.`);
  if (running) parts.push(runningSentence(running));
  return parts.join(" ");
}

/**
 * How much is running. `all`: every unfinished run of every project is counted (the summaries carry `running_count`).
 * `latest`: only each project's latest run is known, so nothing is said about older runs.
 */
export type RunningCount = { kind: "all"; runs: number } | { kind: "latest"; projects: number };

export function runningCount(projects: ReadonlyArray<ProjectLike>): RunningCount {
  const counts = projects.map((p) => p.summary?.running_count);
  if (counts.every((n): n is number => typeof n === "number" && Number.isFinite(n))) return { kind: "all", runs: counts.reduce((a, b) => a + Math.max(0, Math.floor(b)), 0) };
  return { kind: "latest", projects: runsInProgress([...projects]) };
}

export function runningSentence(running: RunningCount): string {
  if (running.kind === "all") return running.runs === 0 ? "Nothing is running." : `${running.runs} ${running.runs === 1 ? "run is" : "runs are"} not finished yet.`;
  return running.projects === 0 ? "No project's latest run is in progress." : `Runs are in progress in ${running.projects} ${running.projects === 1 ? "project" : "projects"}.`;
}

// --- what needs you -----------------------------------------------------------------------

type NeedsYouItem = {
  kind: string; project_id?: string | null; summary: string; status: string; can_act: boolean;
  proposed_by?: string | null; decision_type?: string | null; proposal_type?: string | null;
  subject: { kind: string; id?: string | null };
};
export type NeedsYouRow = { title: string; sentence: string; action: { label: string; href: string } };

/**
 * One row of "N things need you": the Inbox title, one plain sentence from the item's own fields (who raised it, which
 * project, what to do) and one button. The server's summary is only matched, never shown, except as the cleaned title of
 * a kind this page does not know.
 */
export function needsYouRow(item: NeedsYouItem, context: { projectName: string | null; runName: string | null }): NeedsYouRow {
  const title = inboxTitle(item, context.runName);
  const where = context.projectName ? ` in ${context.projectName}` : "";
  const project = isUuid(item.project_id) ? item.project_id : null;
  if (item.kind === "decision_proposal" || item.kind === "agent_proposal") {
    const who = suggestedBy(item.proposed_by, item.kind) ?? "Raised";
    const what = item.can_act ? "Approve or reject it in the Inbox." : "You can read it; someone with the right role decides.";
    return { title, sentence: `${who}${where}. ${what}`, action: { label: item.can_act ? "Decide" : "Review", href: "/inbox" } };
  }
  if (item.kind === "question") {
    return { title, sentence: `A run${where} is waiting for your answer before it can go on.`, action: { label: "Answer", href: questionHref(item) ?? "/inbox" } };
  }
  if (item.kind === "drift_alert" || item.kind === "retrain_ask" || item.kind === "challenger_ready") {
    const href = project ? safeInternalHref(`/projects/${project}/monitoring`) : null;
    const sentence = item.kind === "drift_alert"
      ? `The weekly check${where} marked the newest week as alert: drift, or real outcomes below a floor. Monitoring says which.`
      : item.kind === "retrain_ask"
        ? `A newer labelled file exists${where} and the newest week is an alert. Retrain on it from Monitoring, or leave it.`
        : `A retrained model${where} is beside the model in use. Switch to it or keep the model in use.`;
    return { title, sentence, action: { label: "Open Monitoring", href: href ?? "/inbox" } };
  }
  if (item.kind === "run_finished") {
    const run = item.subject.kind === "experiment" && project && isUuid(item.subject.id) ? safeInternalHref(`/projects/${project}/experiments/${item.subject.id}`) : null;
    return { title, sentence: `Open the run${where} to see what it found.`, action: { label: "Open", href: run ?? "/inbox" } };
  }
  return { title, sentence: `Waiting in the Inbox${where}.`, action: { label: "Open", href: "/inbox" } };
}

// --- running now --------------------------------------------------------------------------

type ProgressLike = { stage?: string | null; model_family?: string | null; fold_number?: number | null; folds_planned?: number | null; candidates_finished?: number; fold_completed?: boolean };
type LiveRunLike = { status: string; created_at: string; run_number?: number | null; progress?: ProgressLike | null };

/** Words for the engine stage of a run's latest event (the event stream's own stage names); others fall back to the step words. */
const PROGRESS_STAGE: Record<string, string> = {
  cross_validation: "Training the models, fold by fold", candidate_generation: "Listing the models to try", preprocessing: "Preparing the columns",
  final_refit: "Refitting the chosen model", deterministic_verification: "Running the trust checks",
};

/** "started 4 minutes ago" from the run's start; a start more than a day old (or unreadable) is a UTC date. */
export function startedAgo(iso: string, now: Date): string {
  const at = new Date(iso);
  if (Number.isNaN(at.getTime())) return "start time unknown";
  const minutes = Math.floor((now.getTime() - at.getTime()) / 60_000);
  if (minutes < 1) return "started just now";
  if (minutes < 60) return `started ${minutes} ${minutes === 1 ? "minute" : "minutes"} ago`;
  const hours = Math.floor(minutes / 60);
  return hours < 24 ? `started ${hours} ${hours === 1 ? "hour" : "hours"} ago` : `started ${utcWhen(iso)}`;
}

/**
 * The cross-validation folds of the model being trained now (each model goes through every fold), only when the fold and
 * the planned folds are both recorded. `value` is the folds done: fold k is done once its `cv_fold_completed` is the
 * latest event, else k - 1 are done and fold k is in progress.
 */
export function foldMeter(progress: ProgressLike | null | undefined): { value: number; max: number; step: string; text: string } | null {
  const k = progress?.fold_number, n = progress?.folds_planned;
  if (!Number.isInteger(k) || !Number.isInteger(n) || (n as number) < 1 || (k as number) < 1 || (k as number) > (n as number)) return null;
  const done = progress?.fold_completed === true;
  return {
    value: done ? (k as number) : (k as number) - 1, max: n as number,
    step: `Model being trained: fold ${k} of ${n}${done ? " done" : ""}`,
    text: done ? `Fold ${k} of ${n} done` : `Fold ${k} of ${n} in progress, ${(k as number) - 1} done`,
  };
}

/** A run in progress as a title ("Run 6 · CatBoost") and one line from its latest recorded event; nothing is estimated. Without `now` (first render) the start is left out. */
export function liveRunText(run: LiveRunLike, now: Date | null): { title: string; detail: string } {
  const family = familyLabel(run.progress?.model_family);
  const title = [Number.isInteger(run.run_number) ? `Run ${run.run_number}` : "Latest run", family].filter(Boolean).join(" · ");
  const progress = run.progress;
  const meter = foldMeter(progress);
  let step: string;
  if (!progress) step = run.status === "queued" ? "Waiting to start" : `${statusWords(run.status)}, no step recorded yet`;
  else if (meter) step = meter.step;
  else if (progress.stage) step = lookup(PROGRESS_STAGE, progress.stage, stageWords(progress.stage, progress.stage.replaceAll("_", " ")));
  else step = statusWords(run.status);
  if (run.status === "cancelling") step = progress ? `Stopping · ${step}` : "Stopping";
  const done = progress?.candidates_finished ?? 0;
  const finished = done > 0 ? `${done} ${done === 1 ? "model" : "models"} finished` : null;
  return { title, detail: [step, finished, now ? startedAgo(run.created_at, now) : null].filter(Boolean).join(" · ") };
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

type LatestRun<T extends ProjectLike> = NonNullable<NonNullable<T["summary"]>["latest_run"]>;

/**
 * Projects with something running: the latest run when it is queued, running or stopping (its progress is known), and
 * `others` = the project's other unfinished runs (from `running_count`; their progress is not read).
 */
export function liveRuns<T extends ProjectLike>(projects: T[]): Array<{ project: T; run: LatestRun<T> | null; status: string | null; experimentId: string | null; others: number }> {
  return projects.flatMap((project) => {
    const latest = project.summary?.latest_run;
    const run = latest && LIVE_RUN_STATUSES.has(latest.status) ? (latest as LatestRun<T>) : null;
    const total = project.summary?.running_count;
    const others = typeof total === "number" && Number.isFinite(total) ? Math.max(0, Math.floor(total) - (run ? 1 : 0)) : 0;
    if (!run && !others) return [];
    return [{ project, run, status: run?.status ?? null, experimentId: run && isUuid(run.experiment_id) ? run.experiment_id! : null, others }];
  });
}

/** The line for a project's other unfinished runs (older runs, or runs waiting for an answer). */
export const otherRunsText = (others: number, withLatest: boolean): string =>
  `${others} ${withLatest ? "more " : ""}${others === 1 ? "run is" : "runs are"} not finished yet`;
