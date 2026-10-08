/**
 * Experiments in plain words (V7-A3), pure so it runs under `npm run test:components`.
 * Every value comes from fields the API already returns (the run list, the run read with its cross-validation winner
 * and baseline comparison, the trust checks and the model-build stages). A value the API did not return is left out,
 * never guessed. No final test set value is read here. Lookups by server-controlled keys use own-key checks.
 */
import { plainText } from "./command-search.ts";
import { metricInfo } from "./studio-goal.ts";
import { CHANGE_KINDS, lowerIsBetter, naturalScore } from "./studio-compare.ts";
import { durationLabel, stageTone, type PointRow } from "./studio-pipeline.ts";

type Rec = Record<string, unknown>;
const rec = (value: unknown): Rec => (typeof value === "object" && value !== null && !Array.isArray(value) ? (value as Rec) : {});
const num = (value: unknown): number | null => (typeof value === "number" && Number.isFinite(value) ? value : null);
const str = (value: unknown): string | null => (typeof value === "string" && value.trim() ? value.trim() : null);
const own = <T>(table: Record<string, T>, key: string): T | undefined => (Object.hasOwn(table, key) ? table[key] : undefined);

// --- model families ----------------------------------------------------------------------

const FAMILY: Record<string, string> = {
  majority: "Baseline (most common answer)", mean: "Baseline (average)", median: "Baseline (middle value)",
  logistic_regression: "Logistic regression", linear_regression: "Linear regression", ridge: "Ridge regression", lasso: "Lasso regression", elasticnet: "Elastic net",
  random_forest: "Random forest", random_forest_regressor: "Random forest", extra_trees: "Extra trees", extra_trees_regressor: "Extra trees",
  lightgbm: "LightGBM", xgboost: "XGBoost", catboost: "CatBoost",
  gradient_boosting: "Gradient boosting", hist_gradient_boosting: "Gradient boosting", decision_tree: "Decision tree", knn: "Nearest neighbours",
};
const DUMMY = new Set(["majority", "mean", "median"]);

/** A model family in words; an unknown family is shown as given (cleaned), never hidden. */
export function familyLabel(family: string | null | undefined): string | null {
  const key = (family ?? "").trim();
  if (!key) return null;
  return own(FAMILY, key) ?? plainText(key.replaceAll("_", " "), 40);
}
export const isBaselineFamily = (family: string | null | undefined): boolean => DUMMY.has((family ?? "").trim());

// --- what changed ------------------------------------------------------------------------

export const TRANSFORM_WORDS: Record<string, string> = {
  drop_column: "leave it out", keep: "keep it as it is", impute_median: "fill missing values with the median",
  impute_most_frequent: "fill missing values with the most common value", datetime_extract: "split the date into parts",
};

/** The plain-words name of each change kind (one source: the branch form's list). */
export const CHANGE_WORDS: Record<string, string> = Object.fromEntries(CHANGE_KINDS.map((k) => [k.kind, k.label]));

const txt = (value: unknown, max = 60) => plainText(value, max);
/** A setting value as short text; an object or list is never printed as "[object Object]". */
const scalar = (value: unknown, max = 30): string => (value !== null && typeof value === "object" ? "(a list or group of values)" : txt(value, max));
const LIST_CAP = 8;
const pairsText = (table: Rec, cap = LIST_CAP): string => {
  const all = Object.entries(table);
  const shown = all.slice(0, cap).map(([k, v]) => `${txt(k, 30)} = ${scalar(v)}`).join(", ");
  return all.length > cap ? `${shown} and ${all.length - cap} more` : shown;
};

/** One sentence per change of a stored change set. Names and values are untrusted text (capped, plain). */
export function changeSentences(changeSet: Rec | null | undefined): string[] {
  const changes = Array.isArray(rec(changeSet).changes) ? (rec(changeSet).changes as unknown[]) : [];
  return changes.map(rec).map((c) => {
    const kind = str(c.kind) ?? "change";
    const family = familyLabel(str(c.family));
    const params = rec(c.parameters);
    const withParams = Object.keys(params).length ? ` (${pairsText(params)})` : "";
    switch (kind) {
      case "class_weighting": {
        const mode = str(c.mode);
        if (mode === "custom") return `Custom class weights: ${pairsText(rec(c.weights)) || "none given"}`;
        return mode === "balanced" ? "Balanced class weights" : mode === "none" ? "No class weights" : "Changed class weights";
      }
      case "family_include": return family ? `Added the ${family} model family` : "Added a model family";
      case "family_exclude": return family ? `Dropped the ${family} model family` : "Dropped a model family";
      case "hyperparameter_override": return `Fixed ${family ?? "model"} settings${Object.keys(params).length ? `: ${pairsText(params, 32)}` : ""}`;
      case "feature_transform_add": return `Column ${txt(c.column)}: ${own(TRANSFORM_WORDS, str(c.transform) ?? "") ?? txt(c.transform, 30)}${withParams}`;
      case "feature_transform_remove": return `Removed the treatment of column ${txt(c.column)}: ${own(TRANSFORM_WORDS, str(c.transform) ?? "") ?? txt(c.transform, 30)}${withParams}`;
      case "metric_override": return `Ranked models on ${metricInfo(str(c.primary_metric))?.label ?? (str(c.primary_metric) ? txt(c.primary_metric, 40) : "another score")}${str(c.reason) ? ` (${txt(c.reason, 120)})` : ""}`;
      case "threshold_objective": {
        const rules = (Array.isArray(c.constraints) ? c.constraints : []).map(rec).slice(0, LIST_CAP)
          .map((r) => `${metricInfo(str(r.metric))?.label ?? txt(r.metric, 30)} ${txt(r.op, 4)} ${scalar(r.value)}`);
        const costs = [num(c.cost_false_positive) !== null ? `a wrong alarm costs ${num(c.cost_false_positive)}` : null, num(c.cost_false_negative) !== null ? `a miss costs ${num(c.cost_false_negative)}` : null].filter(Boolean);
        const parts = [rules.length ? `rule: ${rules.join(" and ")}` : null, costs.length ? costs.join(", ") : null].filter(Boolean);
        return `Changed the threshold rule${parts.length ? ` (${parts.join("; ")})` : ""}`;
      }
      default: return own(CHANGE_WORDS, kind) ?? txt(kind.replaceAll("_", " "), 40);
    }
  });
}

// --- score, baseline, trust --------------------------------------------------------------

export type RunFacts = {
  family: string | null; baseline: boolean; metric: string | null; metricKey: string | null; score: number | null; spread: number | null;
  beats: "yes" | "no" | "unknown"; margin: number | null; clear: boolean | null;
};
type DetailLike = { metrics?: { family?: string | null; selection_metric?: string | null; selected_score?: number | null; cv?: Rec | null; baseline_comparison?: Rec | null } | null } | null | undefined;

/** The selection score in the metric's own units: the cross-validation mean when present, else the stored "larger is better" score flipped back. */
export function selectionScore(metricKey: string | null | undefined, cv: unknown, selected: number | null | undefined): number | null {
  const fromCv = metricKey ? num(rec(cv)[metricKey]) : null;
  if (fromCv !== null) return fromCv;
  return typeof selected === "number" && Number.isFinite(selected) ? naturalScore(metricKey, selected) : null;
}

/** The cross-validation numbers of one run. The spread is the winner's fold-to-fold spread on the same metric, or absent. */
export function runFacts(detail: DetailLike): RunFacts {
  const m = detail?.metrics ?? null;
  const base = rec(m?.baseline_comparison);
  const metricKey = str(m?.selection_metric);
  const sameMetric = str(base.metric) === metricKey;
  const beats = typeof base.beats_baseline === "boolean" && sameMetric ? (base.beats_baseline ? "yes" : "no") : "unknown";
  const margin = sameMetric ? num(base.margin) : null;
  // The cross-validation mean is stored in the metric's own units; selected_score is "larger is better" (errors negated).
  return {
    family: str(m?.family), baseline: isBaselineFamily(m?.family), metricKey, metric: metricInfo(metricKey)?.label ?? (metricKey ? plainText(metricKey.replaceAll("_", " "), 40) : null),
    score: selectionScore(metricKey, m?.cv, m?.selected_score) ?? (sameMetric ? selectionScore(metricKey, null, num(base.winner_cv_score)) : null),
    spread: sameMetric ? num(base.winner_cv_std) : null,
    beats, margin: beats === "yes" && margin !== null ? Math.abs(margin) : null, clear: typeof base.clear_margin === "boolean" ? base.clear_margin : null,
  };
}

const fixed = (n: number) => (Math.abs(n) >= 100 ? n.toFixed(1) : n.toFixed(2));
export function scoreText(facts: RunFacts): string | null {
  if (facts.score === null) return null;
  return facts.spread !== null ? `${fixed(facts.score)} ± ${fixed(facts.spread)}` : fixed(facts.score);
}

/** "Yes, +0.44" / "Yes, by a small margin" / "No" / "Not recorded"; the baseline run itself is not compared with itself. */
export function beatsText(facts: RunFacts): string {
  if (facts.baseline) return "This is the baseline";
  if (facts.beats === "unknown") return "Not recorded";
  if (facts.beats === "no") return "No";
  const by = facts.margin !== null ? `, +${fixed(facts.margin)}` : "";
  return facts.clear === false ? `Yes${by}, but within the spread` : `Yes${by}`;
}

export type TrustState = "pending" | "not_checked" | "ready";
export type TrustCounts = { state: TrustState; pass: number; warn: number; fail: number; notChecked: number; total: number };
type FindingsRead = { investigated: boolean; checks?: Array<{ status: string }> } | null | undefined;

/** ✓ / ⚠ counts from the real checks. "Not checked" (no recorded checks, or a check that was not evaluated) is never counted as passed. */
export function trustCounts(findings: FindingsRead): TrustCounts {
  if (!findings) return { state: "pending", pass: 0, warn: 0, fail: 0, notChecked: 0, total: 0 };
  const checks = findings.checks ?? [];
  if (!findings.investigated || !checks.length) return { state: "not_checked", pass: 0, warn: 0, fail: 0, notChecked: 0, total: 0 };
  const count = (s: string) => checks.filter((c) => c.status === s).length;
  const pass = count("pass");
  const warn = count("warning");
  const fail = count("fail");
  return { state: "ready", pass, warn, fail, notChecked: checks.length - pass - warn - fail, total: checks.length };
}

/** "13 passed · 2 to review · 1 not checked", plain words, only the non-zero parts after the first. */
export function trustSentence(c: TrustCounts): string {
  if (c.state === "pending") return "Loading the trust checks…";
  if (c.state === "not_checked") return "Not checked yet";
  const parts = [`${c.pass} passed`];
  if (c.warn) parts.push(`${c.warn} to review`);
  if (c.fail) parts.push(`${c.fail} failed`);
  if (c.notChecked) parts.push(`${c.notChecked} not checked`);
  return parts.join(" · ");
}

// --- best on cross-validation ------------------------------------------------------------

export type Scored = { id: string; completed: boolean; splitPlanId: string | null | undefined; metricKey: string | null; score: number | null; spread?: number | null; baseline: boolean };

/**
 * The run(s) with the best cross-validation score (natural units: larger is better except for error scores). Only runs
 * with a recorded test design, on the same test design and the same score, are compared; the baseline is never "best".
 * Fewer than two comparable scored runs: nothing is marked. `withinSpread` is true when the runner-up is closer than the
 * best run's own fold-to-fold spread, so the lead may be noise.
 */
export function bestRunIds(rows: Scored[]): { ids: Set<string>; withinSpread: boolean } {
  const none = { ids: new Set<string>(), withinSpread: false };
  const scored = rows.filter((r) => r.completed && r.score !== null && r.metricKey && r.splitPlanId && !r.baseline);
  if (scored.length < 2) return none;
  const first = scored[0];
  if (!scored.every((r) => r.metricKey === first.metricKey && r.splitPlanId === first.splitPlanId)) return none;
  const low = lowerIsBetter(first.metricKey!);
  const sign = low ? -1 : 1;
  const best = Math.max(...scored.map((r) => sign * r.score!));
  const top = scored.filter((r) => sign * r.score! === best);
  const rest = scored.filter((r) => sign * r.score! !== best).map((r) => sign * r.score!);
  const second = rest.length ? Math.max(...rest) : null;
  const spread = Math.max(...top.map((r) => r.spread ?? 0));
  return { ids: new Set(top.map((r) => r.id)), withinSpread: top.length > 1 || (second !== null && spread > 0 && best - second < spread) };
}

// --- the steps of a run ------------------------------------------------------------------

type StageTimes = { key: string; status: string; duration_ms?: number | null };
// Order matters: the final test set is set aside BEFORE anything is profiled, so profiling only looked at training rows.
const STEP_GROUPS: Array<{ id: string; label: string; keys: string[] }> = [
  { id: "read", label: "Read the file and set the goal", keys: ["ingestion", "profiling_eda", "target_task", "structural_cleaning"] },
  { id: "split", label: "Set the final test set aside", keys: ["final_holdout_plan", "holdout_lock"] },
  { id: "profile", label: "Profile the training rows and plan the folds", keys: ["problem_profile", "validation_plan", "metric_plan", "leakage_audit", "missing_value_decisions"] },
  { id: "features", label: "Build features", keys: ["feature_engineering", "preprocessing"] },
  { id: "train", label: "Train the models, fold by fold", keys: ["candidate_generation", "cv_training"] },
  { id: "choose", label: "Choose the best on cross-validation", keys: ["candidate_comparison", "winner_lock"] },
  { id: "final", label: "Final test, once in this run", keys: ["final_refit", "final_holdout"] },
  { id: "report", label: "Save the model and run the trust checks", keys: ["artifact_reproducibility_persistence", "deterministic_verification"] },
];
const GROUP_OF = new Map(STEP_GROUPS.flatMap((g) => g.keys.map((k) => [k, g.id] as const)));

export type StepState = "done" | "partly" | "running" | "failed" | "skipped" | "stopped" | "waiting";
export type RunStep = { id: string; label: string; state: StepState; ms: number | null; time: string };

const SKIPPED = new Set(["skipped", "not_applicable"]);
const STOPPED = new Set(["cancelled", "canceled", "cancelling"]);
function stepState(statuses: string[]): StepState {
  const tones = statuses.map((s) => stageTone(s));
  const low = statuses.map((s) => s.toLowerCase());
  if (tones.includes("crit")) return "failed";
  if (tones.includes("ai")) return "running";
  if (low.some((s) => STOPPED.has(s))) return "stopped";
  if (low.every((s) => SKIPPED.has(s))) return "skipped";
  const okCount = tones.filter((t) => t === "ok").length + low.filter((s) => SKIPPED.has(s)).length;
  if (okCount === statuses.length) return "done";
  return okCount > 0 ? "partly" : "waiting";
}

/** The run's recorded stages grouped into plain steps, in run order, with the time each recorded. A step with no recorded stage is left out. */
export function runSteps(stages: StageTimes[]): RunStep[] {
  const out: RunStep[] = [];
  for (const group of [...STEP_GROUPS, { id: "other", label: "Other steps", keys: [] as string[] }]) {
    const own = stages.filter((s) => (GROUP_OF.get(s.key) ?? "other") === group.id);
    if (!own.length) continue;
    const timed = own.map((s) => s.duration_ms).filter((v): v is number => typeof v === "number" && Number.isFinite(v) && v >= 0);
    const ms = timed.length ? timed.reduce((a, b) => a + b, 0) : null;
    out.push({ id: group.id, label: group.label, state: stepState(own.map((s) => s.status)), ms, time: durationLabel(ms) });
  }
  return out;
}
export const STEP_STATE_WORDS: Record<StepState, string> = { done: "done", partly: "partly done", running: "running", failed: "failed", skipped: "skipped", stopped: "stopped", waiting: "not started" };

// --- decision points in words ------------------------------------------------------------

const POINT_WORDS: Record<string, string> = {
  "target.column": "Which column to predict", "spec.objective": "How to score the models", "column.is_identifier": "Which columns are IDs",
  "column.semantic_role": "What each column means", "column.missing_value_action": "How to fill missing values", "feature.leakage_suspect": "Columns that may give away the answer",
  "split.strategy": "How to set aside the final test set", "training.families_budget": "Which model families to try", "experiment.review": "A second look at the finished run",
};
export function pointLabel(key: string): string {
  return own(POINT_WORDS, key) ?? plainText(key.replaceAll(".", " ").replaceAll("_", " "), 60);
}
/** True when at least one point had an AI answer (otherwise the AI columns are hidden: the rules decided everything). */
export const anyAiAnswer = (rows: PointRow[]): boolean => rows.some((r) => !r.aiOff);

// --- status ------------------------------------------------------------------------------

const STATUS_WORDS: Record<string, string> = {
  completed: "Finished", running: "Running", queued: "Waiting to start", failed: "Failed", cancelled: "Stopped", canceled: "Stopped",
  cancelling: "Stopping", needs_input: "Waiting for your input", skipped: "Skipped",
};
/** A run status in words; an unknown status is shown as given (cleaned). */
export function statusWords(status: string): string {
  return own(STATUS_WORDS, status) ?? plainText(status.replaceAll("_", " "), 30);
}
export const STATUS_FILTERS = [
  { id: "all", label: "All runs" }, { id: "completed", label: "Finished" }, { id: "live", label: "In progress" }, { id: "failed", label: "Failed or stopped" },
] as const;
export type StatusFilter = (typeof STATUS_FILTERS)[number]["id"];
export function matchesStatusFilter(status: string, filter: StatusFilter): boolean {
  if (filter === "all") return true;
  if (filter === "completed") return status === "completed";
  if (filter === "live") return ["running", "queued", "cancelling", "needs_input"].includes(status);
  return ["failed", "cancelled", "canceled"].includes(status);
}

// --- engine steps in words ---------------------------------------------------------------

const STAGE_WORDS: Record<string, string> = {
  target_task: "Goal: the column to predict and the kind of answer", structural_cleaning: "Clean the table", final_holdout_plan: "Plan the final test set",
  holdout_lock: "Set the final test set aside", problem_profile: "Profile the training rows", validation_plan: "Plan the cross-validation folds", metric_plan: "Choose the score",
  leakage_audit: "Look for columns that give away the answer", missing_value_decisions: "Decide how to fill missing values", feature_engineering: "Build features",
  preprocessing: "Prepare the columns", candidate_generation: "List the models to try", cv_training: "Train the models, fold by fold",
  candidate_comparison: "Compare the models on cross-validation", winner_lock: "Choose the best model", final_refit: "Refit the chosen model",
  final_holdout: "Final test set (used once)", artifact_reproducibility_persistence: "Save the model and how to reproduce it", deterministic_verification: "Run the trust checks",
};
/** A step of the engine in words; a step this app does not know keeps its recorded title (cleaned). */
export function stageWords(key: string, title: string): string {
  return own(STAGE_WORDS, key) ?? plainText(title, 80);
}
/** A step state in one word, from the recorded status. */
export function stageStateWords(status: string): string {
  const tone = stageTone(status);
  if (SKIPPED.has(status.toLowerCase())) return "skipped";
  if (STOPPED.has(status.toLowerCase())) return "stopped";
  return tone === "ok" ? "done" : tone === "crit" ? "failed" : tone === "ai" ? "running" : plainText(status.replaceAll("_", " "), 24);
}
