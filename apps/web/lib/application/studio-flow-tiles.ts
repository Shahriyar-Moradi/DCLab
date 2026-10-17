/**
 * Project flow bar as eight tiles (V7 UI-B, demo "flow-home"): each step with a value and a short note, built only from
 * `GET /v1/projects/{id}/flow` (typed facts; the web writes the words). Pure: no React, no fetch. Scores are
 * cross-validation only (the read never carries a final test set value); the file name and goal texts are untrusted
 * and are capped plain text. A state the read does not give is "unknown" (shown plain), never guessed.
 */
import { plainText } from "./command-search.ts";
import { utcDay } from "./format-time.ts";
import { cvScoreText } from "./studio-home.ts";
import { lookup } from "./studio-inbox.ts";
import { modelName } from "./studio-names.ts";
import { familyLabel } from "./studio-runs.ts";
import { stepForPath, stepScreenForPath, stepScreenHref, type FlowInput, type StepScreenId } from "./studio-flow.ts";

export type TileState = "done" | "in_progress" | "alert" | "todo" | "unknown";
export type FlowTile = { id: StepScreenId; number: number; label: string; value: string; note: string; state: TileState; stateWords: string; current: boolean; href: string | null };

type Step = { state: string };
export type ProjectFlowLike = {
  data: Step & { file_name?: string | null; row_count?: number | null; newer_file?: boolean };
  goal: Step & { goal?: { target_column?: string | null; objective?: string | null } | null; split_plan_id?: string | null; validation_folds?: number | null };
  experiments: Step & { run_count?: number; running_count?: number; best_run_number?: number | null; best_cv_score?: number | null; selection_metric?: string | null };
  improve: Step & { branch_run_count?: number };
  model: Step & { model_version_id?: string | null; version?: string | null; model_family?: string | null; algorithm?: string | null; decision_threshold?: number | null; run_number?: number | null };
  predictions: Step & { completed_scorings?: number; last_rows_scored?: number | null; last_scored_at?: string | null; schedule_enabled?: boolean | null };
  monitoring: Step & { status?: string | null; window_start?: string | null };
  history: Step & { decision_count?: number; last_decision_at?: string | null };
};

/** The eight steps in flow order; the numbers are fixed (1-8) so they match the sidebar even when a step is not listed. */
export const FLOW_TILE_ORDER: ReadonlyArray<{ id: StepScreenId; label: string }> = [
  { id: "data", label: "Data" }, { id: "goal", label: "Goal & test design" }, { id: "experiments", label: "Experiments" },
  { id: "improve", label: "Improve" }, { id: "models", label: "Model" }, { id: "predictions", label: "Predictions" },
  { id: "monitoring", label: "Monitoring" }, { id: "history", label: "History" },
];

const STATE_WORDS: Record<Exclude<TileState, "unknown">, string> = { done: "done", in_progress: "in progress", alert: "needs a look", todo: "not started" };

/** Newest monitored week of the model in use, in words (drift means "the inputs look different", never "the model is wrong"). */
const MONITORING_WORDS: Record<string, string> = {
  ok: "Inputs look like training", watch: "Worth a look", alert: "Inputs look different", not_enough_data: "Too few rows yet",
};

const count = (n: number | null | undefined): number => (typeof n === "number" && Number.isFinite(n) && n > 0 ? Math.floor(n) : 0);
const plural = (n: number, one: string, many: string): string => `${n.toLocaleString("en-US")} ${n === 1 ? one : many}`;
const isNumber = (n: unknown): n is number => typeof n === "number" && Number.isFinite(n);

/** Value and note of one step from its own facts. Words avoid the other steps' names (each tile is named by its step). */
function words(id: StepScreenId, flow: ProjectFlowLike): { value: string; note: string } {
  switch (id) {
    case "data": {
      const s = flow.data;
      if (s.state === "todo") return { value: "No file yet", note: "Upload a training file" };
      const rows = isNumber(s.row_count) ? plural(Math.max(0, Math.floor(s.row_count)), "row", "rows") : "rows not counted";
      return { value: plainText(s.file_name, 40) || "Training file", note: s.newer_file ? `${rows} · a newer file is uploaded` : rows };
    }
    case "goal": {
      const s = flow.goal;
      const target = plainText(s.goal?.target_column, 40);
      const value = target ? `Predict ${target}` : s.goal ? "Set, no column yet" : "Not set yet";
      if (s.state === "done") return { value, note: count(s.validation_folds) ? `${count(s.validation_folds)}-fold cross-validation` : "test design set" };
      return { value, note: s.goal ? "no test design in use yet" : "set when you start a run" };
    }
    case "experiments": {
      const s = flow.experiments;
      const runs = count(s.run_count);
      if (!runs) return { value: "No runs yet", note: "start the first run" };
      const running = count(s.running_count);
      const score = cvScoreText(s.selection_metric, s.best_cv_score);
      // The read ranks the most recent finished runs on the current test design (a bounded window): "best of recent runs".
      const best = score ? (Number.isInteger(s.best_run_number) ? `best of recent runs: Run ${s.best_run_number}, CV ${score}` : `best of recent runs: CV ${score}`) : "no finished run yet";
      return { value: plural(runs, "run", "runs"), note: running ? `${running} running · ${best}` : best };
    }
    case "improve": {
      const tries = count(flow.improve.branch_run_count);
      return tries ? { value: plural(tries, "try", "tries"), note: "runs branched from another run" } : { value: "Not tried yet", note: "try one change at a time" };
    }
    case "models": {
      const s = flow.model;
      if (s.state === "todo") return { value: "None in use", note: "approve one for scoring" };
      const family = familyLabel(s.model_family) ?? familyLabel(s.algorithm);
      const parts = [family && s.version ? modelName(plainText(s.version, 20)) : null, isNumber(s.decision_threshold) ? `threshold ${s.decision_threshold.toFixed(2)}` : null];
      return { value: family ?? modelName(plainText(s.version, 20)), note: parts.filter(Boolean).join(" · ") || "in use" };
    }
    case "predictions": {
      const s = flow.predictions;
      const weekly = s.schedule_enabled === true ? "weekly scoring on" : null;
      if (s.state === "in_progress") return { value: "Scoring now", note: weekly ?? "the first file is being scored" };
      const done = count(s.completed_scorings);
      if (!done) return { value: "Not scored yet", note: weekly ?? "score a new file" };
      const value = isNumber(s.last_rows_scored) ? plural(Math.max(0, Math.floor(s.last_rows_scored)), "row scored", "rows scored") : plural(done, "scoring", "scorings");
      return { value, note: weekly ?? (s.last_scored_at ? `last on ${utcDay(s.last_scored_at)}` : plural(done, "scoring", "scorings")) };
    }
    case "monitoring": {
      const s = flow.monitoring;
      if (s.state === "todo" || !s.status) return { value: "Nothing yet", note: "no week checked yet" };
      return { value: lookup(MONITORING_WORDS, s.status, "Checked"), note: s.window_start ? `week of ${utcDay(s.window_start)}` : "newest week" };
    }
    case "history": {
      const s = flow.history;
      const n = count(s.decision_count);
      return n ? { value: plural(n, "decision", "decisions"), note: s.last_decision_at ? `last on ${utcDay(s.last_decision_at)}` : "who, what and when" } : { value: "Nothing yet", note: "decisions appear here" };
    }
  }
}

const READ_KEY: Record<StepScreenId, keyof ProjectFlowLike> = {
  data: "data", goal: "goal", experiments: "experiments", improve: "improve", models: "model", predictions: "predictions", monitoring: "monitoring", history: "history",
};

export type FlowTilesInput = Omit<FlowInput, "refKinds" | "runStatuses">;

/** The tiles for the listed steps (`available`: step ids whose screen exists, "history" included). */
export function flowTiles(flow: ProjectFlowLike, input: FlowTilesInput): FlowTile[] {
  const here = stepForPath(input.pathname, input.projectId, input.tab) ?? (stepScreenForPath(input.pathname, input.projectId) === "history" ? "history" : null);
  return FLOW_TILE_ORDER.flatMap((step, index) => {
    if (!input.available.has(step.id)) return [];
    const raw = flow[READ_KEY[step.id]].state;
    const state: TileState = Object.hasOwn(STATE_WORDS, raw) ? (raw as TileState) : "unknown";
    return [{
      id: step.id, number: index + 1, label: step.label, ...words(step.id, flow), state,
      stateWords: state === "unknown" ? "" : STATE_WORDS[state as Exclude<TileState, "unknown">],
      current: step.id === here, href: stepScreenHref(step.id, input.projectId, input.targets),
    }];
  });
}

/** Ids the tiles link to, from the flow read itself (Home has no refs read): the test design and the model in use. */
export function flowTargets(flow: { goal: { split_plan_id?: string | null }; model: { model_version_id?: string | null } }): FlowInput["targets"] {
  return { split_plan: flow.goal.split_plan_id ?? null, project_model: flow.model.model_version_id ?? null };
}
