/**
 * The improvement loop in plain words (D4e = P5.5-A, ADR 0007 option A), pure so it runs under `npm run test:components`.
 *
 * Everything here restates fields of the loop read (`GET /v1/improve-runs/{id}`, cross-validation only) or of the start form.
 * A value that was not returned is left out, never guessed. Lookups by server-controlled keys use own-key checks, unknown codes get
 * a generic sentence (never the raw code), and server messages are never shown. Honesty rules (ADR 0007 section 12): a try is only
 * called better when its verdict is "beats the noise"; "within the noise" is the common, correct outcome and is said so; only an
 * accepted try is re-run once and scored once on the final test set.
 */
import { isUuid, projectHref } from "./command-search.ts";
import { lowerIsBetter } from "./studio-compare.ts";
import { metricInfo } from "./studio-goal.ts";
import { fmtNum, fmtScore, fmtSigned } from "./studio-improve.ts";
import { familyLabel } from "./studio-runs.ts";
import { envelope, type PlainError } from "./studio-wizard.ts";

const own = <T>(table: Record<string, T>, key: unknown): T | undefined => (typeof key === "string" && Object.hasOwn(table, key) ? table[key] : undefined);
const num = (value: unknown): number | null => (typeof value === "number" && Number.isFinite(value) ? value : null);
const rec = (value: unknown): Record<string, unknown> | null => (value && typeof value === "object" && !Array.isArray(value) ? (value as Record<string, unknown>) : null);

// --- shapes (the loop read, structurally) ---------------------------------------------------

export type LoopIteration = {
  seq: number; kind: string; status: string; reason?: string | null; action: Record<string, unknown>;
  experiment_id?: string | null; incumbent_experiment_id?: string | null; comparison?: Record<string, unknown> | null; training_seconds?: number | null;
  created_at: string; finished_at?: string | null;
};
export type LoopActions = { accept: boolean; keep_current: boolean; cancel: boolean };
export type LoopRead = {
  id: string; project_id: string; start_experiment_id: string; final_experiment_id?: string | null;
  status: string; outcome?: string | null; stop_reason?: string | null; stop_detail?: string | null;
  goal: Record<string, unknown>; budget: Record<string, unknown>; validation_folds: number; looks_before: number;
  sealed_set_label: string; sealed_set_consulted: boolean; final_attempts: number; accepted_iteration_seq?: number | null;
  started_by: string; ai: string; training_seconds_spent: number; llm_micros_spent: number;
  waiting_user_expires_at?: string | null; created_at: string; started_at?: string | null; finished_at?: string | null;
  iterations: LoopIteration[]; actions: LoopActions;
};

// --- links ------------------------------------------------------------------------------------

/** `/projects/{project}/improve/{loop}` from UUIDs only; null otherwise. Inbox notices of kind `improve_run` link here. */
export function improveLoopHref(projectId: unknown, loopId: unknown): string | null {
  return isUuid(loopId) ? projectHref(projectId, "improve", loopId) : null;
}

/** The loop view for an Inbox item `{kind: "improve_run", project_id, source: {kind: "improve_run", id}}`; null for anything else. */
export function improveNoticeHref(item: { kind?: unknown; project_id?: unknown; source?: { kind?: unknown; id?: unknown } | null } | null | undefined): string | null {
  if (!item || item.kind !== "improve_run" || item.source?.kind !== "improve_run") return null;
  return improveLoopHref(item.project_id, item.source.id);
}

// --- states -----------------------------------------------------------------------------------

/** States in which the loop changes on its own: the page polls while one of these holds. */
export const POLLED_STATES: ReadonlySet<string> = new Set(["queued", "running", "finalizing"]);
export const isPolled = (status: string | null | undefined): boolean => !!status && POLLED_STATES.has(status);

type Tone = "ok" | "warn" | "crit" | "ai" | "det" | "gray";
const STATUS: Record<string, { label: string; tone: Tone }> = {
  queued: { label: "Waiting to start", tone: "gray" },
  running: { label: "Trying changes", tone: "ai" },
  waiting_user: { label: "Waiting for you", tone: "warn" },
  finalizing: { label: "Final run in progress", tone: "ai" },
  completed: { label: "Finished", tone: "ok" },
  failed: { label: "Failed", tone: "crit" },
  cancelled: { label: "Stopped", tone: "gray" },
};
export function loopStatus(status: string | null | undefined): { label: string; tone: Tone } {
  return own(STATUS, status) ?? { label: "Not recognised", tone: "gray" };
}

/** ADR 0007 section 12: the usual result, said plainly wherever a loop's tries are shown. */
export const WITHIN_NOISE_NOTE = "Most tries end within the noise, and most loops end with the current model kept. That is the correct answer, not a failure: five folds can only show large gains.";
export const SEALED_NOTE = "Each try is a sealed run: it is judged on cross-validation only and never looks at the final test set.";
export const ONE_LOOK_NOTE = "Only a try a person accepts is re-run (retried at most twice if a re-run stops before the final test); the final test set is scored once. That one number is shown after it finishes.";
export const FRESH_SPLIT_NOTE = "The loop uses this run's own test design (the same folds and the same final test set). A loop on a fresh split is not available yet.";

const OUTCOME: Record<string, string> = {
  improved: "A try beat the noise and a person accepted it. Its final run scored the final test set once.",
  kept_current: "No change beyond noise; current model kept.",
  goal_met: "The current model already meets the goal on cross-validation (met on cross-validation, which can be optimistic). Nothing was changed.",
  blocked: "A trust check of the run blocks the loop (a target leak or a score too good to be true). Fix that first; nothing was changed.",
  nothing_comparable: "No try could be compared fairly with the current model, so nothing can be said about a gain. The current model is kept.",
  failed: "The loop failed before it could finish. The current model is kept.",
  cancelled: "The loop was stopped. The best so far is kept; nothing was put in use.",
};
const DETAIL: Record<string, string> = {
  kept_by_person: "A person chose to keep the current model, so the final test set was not used.",
  waiting_user_expired: "Nobody decided within 14 days, so the current model was kept and the final test set was not used.",
  cancelled_by_person: "A person stopped it.",
  cancelled_by_token: "A connected tool that started it stopped it.",
  authority_revoked: "The access it ran under was withdrawn, so it stopped.",
  step_error: "Its steps kept failing.",
  step_job_failed: "Its background step failed.",
  final_run_missing: "The final run could not be found.",
  final_run_cancelled_after_look: "The final run used the final test set once but was stopped before a number was recorded; that look still counts.",
  final_run_failed_after_look: "The final run used the final test set once but failed before a number was recorded; that look still counts.",
  final_run_cancelled_before_look: "The final run was stopped before the final test, so the final test set was not used.",
  final_run_failed_before_look: "The final run failed before the final test, so the final test set was not used.",
  nondeterministic_rerun: "The final run did not reproduce the accepted try, so it stopped before the final test.",
  engine_or_data_drift: "Models shared with the current run did not score the same again, so no later try could be compared fairly.",
};

/** One sentence for a finished loop; a live loop gets none. Kept-current wording depends on why it was kept. */
export function outcomeSentence(run: Pick<LoopRead, "status" | "outcome" | "stop_detail" | "iterations">): string | null {
  if (!run.outcome) return null;
  if (run.outcome === "kept_current" && (run.stop_detail === "kept_by_person" || run.stop_detail === "waiting_user_expired")) return own(DETAIL, run.stop_detail)!;
  const compared = run.iterations.some((it) => it.kind === "family_include" && (it.status === "accepted" || it.status === "rejected"));
  if (run.outcome === "kept_current" && !compared) return "No try was compared, so nothing can be said about a gain; the current model is kept.";
  return own(OUTCOME, run.outcome) ?? "The loop ended with a result this page does not recognise.";
}
export function stopDetailText(detail: string | null | undefined): string | null {
  if (!detail) return null;
  return own(DETAIL, detail) ?? null;
}

/** Each ADR 0007 section 4 stop rule in plain words. */
const STOP: Record<string, string> = {
  cancelled: "Stopped: it was cancelled.",
  failed: "Stopped: the same try failed twice, or the loop's steps kept failing.",
  blocking_finding: "Stopped: a trust check blocks the current model.",
  goal_met: "Stopped: the goal is met on cross-validation.",
  no_improvement: "Stopped: several tries in a row did not beat the noise (the patience ran out).",
  unstable_evidence: "Stopped: the current model's scores swing too much from fold to fold to judge more tries.",
  comparison_bound: "Stopped: this test design has been used for as many comparisons as it safely allows.",
  budget_iterations: "Stopped: the number of tries in the budget was used up.",
  budget_wall: "Stopped: the next try would not finish within the time budget.",
  budget_compute: "Stopped: the training time in the budget was used up.",
  proposer_exhausted: "Stopped: there is nothing left to try.",
};
export const STOP_REASON_CODES = Object.keys(STOP);
export function stopReasonText(reason: string | null | undefined): string | null {
  if (!reason) return null;
  return own(STOP, reason) ?? "Stopped for a reason this page does not recognise.";
}

const SEALED_SET: Record<string, string> = {
  clean: "The final test set of this test design has not been looked at yet.",
  reported_before: "The final test set of this test design was looked at once before (the starting run's own number).",
  reused: "The final test set of this test design has been looked at several times before, so a new final-test number is a less fresh estimate.",
  carried_over: "A fresh test design that carried over the starting run's choices.",
};
export const sealedSetText = (label: string | null | undefined): string => own(SEALED_SET, label) ?? "How often the final test set was looked at before is not recorded.";

// --- goal -------------------------------------------------------------------------------------

export type GoalBody = { plain: { metric: string } } | { objective: { maximize: string; constraints: Array<{ metric: string; op: ">=" | "<="; value: number }> } };
const OPS: Record<string, string> = { ">=": "at least", "<=": "at most" };
const metricLabel = (metric: unknown): string => metricInfo(typeof metric === "string" ? metric : null)?.label ?? "the score";

function constraintWords(items: unknown): string[] {
  return (Array.isArray(items) ? items : []).flatMap((c) => {
    const item = rec(c);
    const op = own(OPS, item?.op);
    const value = num(item?.value);
    return item && op && value !== null ? [`${metricLabel(item.metric)} ${op} ${fmtScore(value)}`] : [];
  });
}

/** The goal (as stored on a loop or as the form will send it) in words. */
export function goalWords(goal: Record<string, unknown> | null | undefined): string {
  const plain = rec(goal?.plain);
  const objective = rec(goal?.objective);
  const target = rec(goal?.target);
  const tail = target && num(target.value) !== null ? `; stop once it reaches ${fmtScore(num(target.value)!)} on cross-validation` : "";
  if (plain) return `Best ${metricLabel(plain.metric)} on cross-validation${tail}`;
  if (objective) {
    const rules = constraintWords(objective.constraints);
    return `Best ${metricLabel(objective.maximize)}${rules.length ? ` while ${rules.join(" and ")}` : ""}, on cross-validation${tail}`;
  }
  return "Goal not recorded";
}

/** The goal metric of a stored goal (the plain metric or the maximised one). */
export function goalMetric(goal: Record<string, unknown> | null | undefined): string | null {
  const plain = rec(goal?.plain);
  const objective = rec(goal?.objective);
  const metric = plain?.metric ?? objective?.maximize;
  return typeof metric === "string" && metric ? metric : null;
}

export type GoalChoice = { goal: GoalBody; source: "operating_point" | "ranking_metric"; words: string } | { goal: null; words: string };

/**
 * The goal the form sends, read from the project's current state: the operating point chosen on the starting run when it was chosen
 * as an objective (maximise one score under rules), else the run's ranking score. The server checks it and refuses one it cannot judge.
 */
export function goalFromState(input: {
  chosen?: { method?: string | null; objective?: { goal?: string | null; constraints?: Array<{ metric: string; op: string; value: number }> | null } | null } | null;
  selectionMetric?: string | null;
}): GoalChoice {
  const objective = input.chosen?.method === "objective" ? input.chosen.objective : null;
  if (objective && typeof objective.goal === "string" && objective.goal) {
    const constraints = (objective.constraints ?? []).filter((c) => (c.op === ">=" || c.op === "<=") && num(c.value) !== null)
      .map((c) => ({ metric: c.metric, op: c.op as ">=" | "<=", value: c.value }));
    const goal: GoalBody = { objective: { maximize: objective.goal, constraints } };
    return { goal, source: "operating_point", words: goalWords(goal as unknown as Record<string, unknown>) };
  }
  const metric = (input.selectionMetric ?? "").trim();
  if (!metric) return { goal: null, words: "The starting run's ranking score is not recorded, so no goal can be set." };
  const goal: GoalBody = { plain: { metric } };
  return { goal, source: "ranking_metric", words: goalWords(goal as unknown as Record<string, unknown>) };
}

// --- budget -----------------------------------------------------------------------------------

/** Server defaults and ceilings of `ImproveBudget` (the OpenAPI schema; a unit test keeps them equal). Time is entered in minutes. */
export const BUDGET_LIMITS = {
  tries: { default: 6, min: 1, max: 20 },
  minutes: { default: 120, min: 1, max: 720 },
  patience: { default: 3, min: 1, max: 20 },
} as const;
export type BudgetInput = { tries: string; minutes: string; patience: string };
export type BudgetBody = { max_iterations: number; max_wall_seconds: number; patience: number };
export const DEFAULT_BUDGET_INPUT: BudgetInput = {
  tries: String(BUDGET_LIMITS.tries.default), minutes: String(BUDGET_LIMITS.minutes.default), patience: String(BUDGET_LIMITS.patience.default),
};

const whole = (text: string): number | null => (/^\s*\d{1,6}\s*$/.test(text) ? Number(text.trim()) : null);

/** Whole numbers inside the server's ceilings; patience at most the tries. Errors are per field, in words. */
export function validateBudget(input: BudgetInput): { ok: true; budget: BudgetBody } | { ok: false; errors: Partial<Record<keyof BudgetInput, string>> } {
  const errors: Partial<Record<keyof BudgetInput, string>> = {};
  const read = (field: keyof BudgetInput, what: string) => {
    const limits = BUDGET_LIMITS[field];
    const value = whole(input[field]);
    if (value === null) errors[field] = `Enter ${what} as a whole number.`;
    else if (value < limits.min || value > limits.max) errors[field] = `Enter ${what} from ${limits.min} to ${limits.max}.`;
    return value;
  };
  const tries = read("tries", "the most tries");
  const minutes = read("minutes", "the most minutes");
  const patience = read("patience", "the patience");
  if (!errors.tries && !errors.patience && tries !== null && patience !== null && patience > tries) errors.patience = "The patience cannot be more than the most tries.";
  if (Object.keys(errors).length || tries === null || minutes === null || patience === null) return { ok: false, errors };
  return { ok: true, budget: { max_iterations: tries, max_wall_seconds: minutes * 60, patience } };
}

/** The whole start body. `fresh_split` is never sent: it is not available yet. */
export function startBody(fromExperimentId: string, goal: GoalBody, budget: BudgetBody) {
  return { start: { from_experiment_id: fromExperimentId }, goal, budget };
}

// --- tries ------------------------------------------------------------------------------------

export type TryVerdictKey = "beats_noise" | "within_noise" | "worse" | "not_comparable" | "suspicious" | "pending" | "did_not_finish" | "stopped" | "threshold" | "unknown";
const TRY_BADGE: Record<TryVerdictKey, { label: string; tone: Tone }> = {
  beats_noise: { label: "Beats noise", tone: "ok" },
  within_noise: { label: "Within noise", tone: "gray" },
  worse: { label: "Worse", tone: "warn" },
  not_comparable: { label: "Not comparable", tone: "gray" },
  suspicious: { label: "Too good to trust, not counted", tone: "warn" },
  pending: { label: "Training", tone: "ai" },
  did_not_finish: { label: "Did not finish", tone: "crit" },
  stopped: { label: "Stopped", tone: "gray" },
  threshold: { label: "Threshold, no retraining", tone: "det" },
  unknown: { label: "Not recognised", tone: "gray" },
};
const WITHIN_REASONS = new Set(["inside_margin", "below_minimum_effect", "no_change", "try_misses_rule"]);

/** The verdict of a try from its status and reason. Only `accepted` (the server's "beats the noise" that passed its trust checks) is a gain. */
export function tryVerdict(it: Pick<LoopIteration, "kind" | "status" | "reason">): TryVerdictKey {
  if (it.kind === "operating_point") return "threshold";
  switch (it.status) {
    case "accepted": return "beats_noise";
    case "rejected":
      if (it.reason === "worse") return "worse";
      if (it.reason === "rejected_suspicious") return "suspicious";
      return it.reason && WITHIN_REASONS.has(it.reason) ? "within_noise" : "unknown";
    case "not_comparable": return "not_comparable";
    case "planned": case "running": return "pending";
    case "failed": return "did_not_finish";
    case "cancelled": return "stopped";
    default: return "unknown";
  }
}
export const tryBadge = (key: TryVerdictKey) => TRY_BADGE[key];

const NOT_COMPARABLE: Record<string, string> = {
  truncated: "a model was skipped or cut short for time",
  superset_violated: "it did not keep every model of the current run and add a new one",
  engine_or_data_drift: "models shared with the current run did not score the same again",
  unplanned_candidates: "it trained models the plan had not counted",
  missing_locked_folds: "a saved fold score is missing",
  no_goal_curve: "a threshold curve for the goal is missing",
  evidence_unreadable: "the saved results could not be read",
  different_metric: "the two runs rank models by different scores",
  goal_not_per_fold: "the goal cannot be read fold by fold",
  no_folds: "there are no folds to compare",
  fold_counts_differ: "the folds of the two runs differ",
  fold_below_min_denominator: "a fold has too few rows for this goal",
  too_few_folds: "there are fewer than 5 folds",
  different_folds: "the two runs were not scored on the same folds",
  constant_difference: "every fold moved by exactly the same amount, so the noise cannot be measured",
  time_ordered_folds: "the test design is ordered by time",
};
const NOT_FINISHED: Record<string, string> = {
  probe_failed: "The try's run failed; it counts toward the tries but not toward the patience.",
  probe_cancelled: "The try's run was stopped.",
  loop_stopped: "The loop stopped while this try was training.",
};

/**
 * What a try was compared with: the current model (the starting run) until a try is accepted; after that, later tries are compared
 * with the best try so far ("the best try so far (Try N)").
 */
export function againstName(it: Pick<LoopIteration, "incumbent_experiment_id">, run: Pick<LoopRead, "start_experiment_id" | "iterations">): string {
  const against = it.incumbent_experiment_id;
  if (!against || against === run.start_experiment_id) return "the current model";
  const best = run.iterations.find((other) => other.kind === "family_include" && other.experiment_id === against);
  return best ? `the best try so far (Try ${best.seq})` : "the best try so far";
}
export const capitalize = (text: string): string => (text ? text[0].toUpperCase() + text.slice(1) : text);

/** One plain sentence for a try (never the raw reason code); `against` names what it was compared with. */
export function trySentence(it: Pick<LoopIteration, "kind" | "status" | "reason" | "comparison">, against = "the current model"): string {
  const key = tryVerdict(it);
  const c = it.comparison ?? null;
  switch (key) {
    case "threshold": {
      const status = c?.status;
      const threshold = num(c?.threshold);
      if (status === "optimal") return `The goal can be met by the current model at the threshold ${threshold === null ? "found" : fmtScore(threshold)}, measured on cross-validation. Nothing was retrained.`;
      if (status === "not_evaluated") return "No usable threshold curve is stored for the starting run, so the threshold step was skipped.";
      return "No threshold of the current model meets the goal's rules on cross-validation; the model itself must change.";
    }
    case "beats_noise": return `${differenceLead(c, against)}more than the noise margin, so it is unlikely to be luck. It is evidence, not proof, and it is decided on cross-validation only.`;
    case "within_noise":
      if (it.reason === "no_change") return `Same score as ${against} on every fold: no change.`;
      if (it.reason === "try_misses_rule") return `This try misses the goal's rules where ${against} meets them, so it cannot beat it.`;
      return `${differenceLead(c, against)}inside the noise margin, so it could be luck. ${capitalize(against)} stays.`;
    case "worse": return `${differenceLead(c, against)}a drop larger than the noise margin.`;
    case "suspicious": return "It beat the noise, but its own trust checks flagged a result too good to be true, so it is not counted as a gain.";
    case "not_comparable": return `Not comparable: ${own(NOT_COMPARABLE, it.reason) ?? "the two runs cannot be compared fairly"}.`;
    case "pending": return it.status === "planned" ? "Waiting for its turn to train." : "Training now on the same folds. The verdict appears when it finishes.";
    case "did_not_finish": case "stopped": return own(NOT_FINISHED, it.reason) ?? (key === "stopped" ? "The try's run was stopped." : "The try did not finish.");
    default: return "This result was not recognised, so no conclusion is drawn.";
  }
}

function higherBetter(c: Record<string, unknown> | null): boolean {
  if (c?.direction === "lower_is_better") return false;
  if (c?.direction === "higher_is_better") return true;
  const metric = c?.goal_metric;
  return !(typeof metric === "string" && lowerIsBetter(metric));
}

function differenceLead(c: Record<string, unknown> | null, against: string): string {
  const diff = num(c?.mean_difference);
  const margin = num(c?.margin);
  const folds = num(c?.folds);
  if (diff === null || margin === null || folds === null) return "On the same folds the difference was ";
  const shortfall = c?.scored_by === "constraint_shortfall";
  const what = shortfall ? "the shortfall from the goal's rules" : metricLabel(c?.goal_metric);
  const better = diff === 0 ? null : (diff > 0) === (shortfall ? false : higherBetter(c));
  const moved = diff === 0 ? "unchanged" : `${fmtNum(diff)} ${diff > 0 ? "higher" : "lower"} (${better ? "better" : "worse"})`;
  return `Across the same ${folds} folds, ${what} was ${moved} on average than ${against}, against a noise margin of ${fmtNum(margin)}: `;
}

/** What a try changed, in words. */
export function tryChange(it: Pick<LoopIteration, "kind" | "action">): string {
  if (it.kind === "operating_point") return "Set the threshold for the goal (no retraining)";
  if (it.kind === "family_include") {
    const family = familyLabel(typeof it.action?.family === "string" ? it.action.family : null);
    return family ? `Add the ${family} model family` : "Add a model family";
  }
  return "A change this page does not recognise";
}

export type FoldSummary = { tryMean: number; currentMean: number; tryLow: number; tryHigh: number; folds: number; byShortfall: boolean };
/** Means and the try's fold range from the frozen per-fold comparison; null when fewer than two finite folds were returned. */
export function foldSummary(c: Record<string, unknown> | null | undefined): FoldSummary | null {
  const rows = (Array.isArray(c?.fold_scores) ? (c!.fold_scores as unknown[]) : []).map(rec)
    .filter((r): r is Record<string, unknown> => !!r && num(r.probe) !== null && num(r.incumbent) !== null);
  if (rows.length < 2) return null;
  const probe = rows.map((r) => r.probe as number);
  const current = rows.map((r) => r.incumbent as number);
  const mean = (xs: number[]) => xs.reduce((a, b) => a + b, 0) / xs.length;
  return { tryMean: mean(probe), currentMean: mean(current), tryLow: Math.min(...probe), tryHigh: Math.max(...probe), folds: rows.length, byShortfall: c?.scored_by === "constraint_shortfall" };
}

export type FoldRow = { fold: number; current: string; tried: string; difference: string };
export function foldRows(c: Record<string, unknown> | null | undefined): FoldRow[] {
  return (Array.isArray(c?.fold_scores) ? (c!.fold_scores as unknown[]) : []).map(rec).flatMap((r) => {
    const fold = num(r?.fold_number);
    const probe = num(r?.probe);
    const current = num(r?.incumbent);
    const diff = num(r?.difference);
    return fold === null || probe === null || current === null ? [] : [{ fold, current: fmtScore(current), tried: fmtScore(probe), difference: diff === null ? "—" : fmtSigned(diff) }];
  });
}

export type ChartRow = { name: string; tried: number; current: number; against: string };
/** Goal metric per compared try with the current model's mean beside it (tries scored by the rules' shortfall are left out: another unit). */
export function chartRows(run: Pick<LoopRead, "start_experiment_id" | "iterations">): { rows: ChartRow[]; leftOut: number } {
  const rows: ChartRow[] = [];
  let leftOut = 0;
  for (const it of run.iterations) {
    if (it.kind !== "family_include") continue;
    const s = foldSummary(it.comparison);
    if (!s) continue;
    if (s.byShortfall) { leftOut += 1; continue; }
    rows.push({ name: `Try ${it.seq}`, tried: Number(s.tryMean.toPrecision(4)), current: Number(s.currentMean.toPrecision(4)), against: againstName(it, run) });
  }
  return { rows, leftOut };
}

/** Try numbers shown as "Try {seq}"; the threshold step has no number. */
export const tryName = (it: Pick<LoopIteration, "kind" | "seq">): string => (it.kind === "operating_point" ? "Threshold step" : `Try ${it.seq}`);

// --- spend ------------------------------------------------------------------------------------

export function durationText(seconds: number | null | undefined): string {
  const s = num(seconds);
  if (s === null || s < 0) return "—";
  if (s < 60) return `${Math.round(s)} s`;
  const minutes = Math.round(s / 60);
  if (minutes < 60) return `${minutes} min`;
  const h = Math.floor(minutes / 60);
  const m = minutes % 60;
  return m ? `${h} h ${m} min` : `${h} h`;
}

export type SpendView = { tries: string; elapsed: string; training: string; ai: string };
/** "4 of 6 tries", elapsed loop time of its time budget, training time used, and the AI cost (AI is off: $0). */
export function spendView(run: Pick<LoopRead, "status" | "iterations" | "budget" | "started_at" | "finished_at" | "training_seconds_spent" | "ai" | "llm_micros_spent">, now: number): SpendView {
  const used = run.iterations.filter((it) => it.kind === "family_include" && it.status !== "planned").length;
  const maxTries = num(run.budget.max_iterations);
  const wall = num(run.budget.max_wall_seconds);
  const total = num(run.budget.max_training_seconds_total);
  const start = run.started_at ? Date.parse(run.started_at) : NaN;
  // Time spent trying changes: up to now while it tries; once it stopped trying, up to the last try's end (waiting for a person and
  // the final run are not part of the time budget).
  const lastTry = Math.max(...run.iterations.map((it) => (it.finished_at ? Date.parse(it.finished_at) : NaN)).filter(Number.isFinite));
  const end = run.status === "queued" || run.status === "running" ? now : Number.isFinite(lastTry) ? lastTry : run.finished_at ? Date.parse(run.finished_at) : now;
  const elapsed = Number.isFinite(start) && Number.isFinite(end) ? Math.max(0, (end - start) / 1000) : null;
  const micros = num(run.llm_micros_spent) ?? 0;
  const cap = num(run.budget.max_llm_micros);
  return {
    tries: maxTries === null ? `${used} ${used === 1 ? "try" : "tries"}` : `${used} of ${maxTries} tries`,
    elapsed: elapsed === null ? "Not started yet" : `${durationText(elapsed)}${wall === null ? "" : ` of ${durationText(wall)}`}`,
    training: `${durationText(run.training_seconds_spent)}${total === null ? "" : ` of ${durationText(total)}`}`,
    ai: run.ai === "off" ? "AI off — $0" : `$${(micros / 1e6).toFixed(2)}${cap === null ? "" : ` of $${(cap / 1e6).toFixed(2)}`}`,
  };
}

// --- the decision -----------------------------------------------------------------------------

export const REASON_MAX = 1000;
export type AcceptGate = { canAccept: boolean; canKeep: boolean; acceptable: number[]; best: number | null; note: string | null };

/** Accept / keep follow the read's `actions` (the server decides; this only mirrors it) and only tries the server marked accepted. */
export function acceptGate(run: Pick<LoopRead, "status" | "actions" | "iterations" | "accepted_iteration_seq"> & { incumbent_experiment_id?: string | null }): AcceptGate {
  const acceptable = run.iterations.filter((it) => it.kind === "family_include" && it.status === "accepted").map((it) => it.seq);
  const locked = run.accepted_iteration_seq;
  const choices = typeof locked === "number" ? acceptable.filter((seq) => seq === locked) : acceptable;
  const waiting = run.status === "waiting_user";
  const canAccept = waiting && run.actions.accept && choices.length > 0;
  const canKeep = waiting && run.actions.keep_current;
  let note: string | null = null;
  if (waiting && !run.actions.accept && !run.actions.keep_current) note = "Waiting for a person who can change this workspace to accept a try or keep the current model. Your role can read this loop only.";
  else if (waiting && run.actions.keep_current && !canAccept) note = "No try can be accepted now; you can keep the current model.";
  // The best so far is the try the loop holds as its incumbent; every other accepted try was beaten beyond the noise by a later one.
  const best = run.iterations.find((it) => it.kind === "family_include" && it.status === "accepted" && !!it.experiment_id && it.experiment_id === run.incumbent_experiment_id)?.seq ?? null;
  const offered = canAccept ? choices : [];
  return { canAccept, canKeep, acceptable: offered, best: best !== null && offered.includes(best) ? best : null, note };
}

/** The accept body: one try (with the person's reason, required here) or keep the current model (reason optional). */
export function acceptBody(choice: { keep: true; reason: string } | { keep: false; seq: number | null; reason: string }):
  { iteration: number; keep_current: false; rationale: string } | { keep_current: true; rationale?: string } | string {
  const reason = choice.reason.trim();
  if (reason.length > REASON_MAX) return `Keep the reason under ${REASON_MAX} characters.`;
  if (choice.keep) return reason ? { keep_current: true, rationale: reason } : { keep_current: true };
  if (choice.seq === null || !Number.isInteger(choice.seq) || choice.seq < 0) return "Pick the try to accept.";
  if (!reason) return "Say why you accept this try (it is saved with the decision).";
  return { iteration: choice.seq, keep_current: false, rationale: reason };
}

// --- the one final-test number ----------------------------------------------------------------

export type FinalTestView = { state: "none" } | { state: "running"; text: string } | { state: "reported"; metric: string; value: string } | { state: "missing"; text: string } | { state: "no_number"; text: string };

/**
 * The single final-test number of the accepted try's final run: only for a loop that finished `improved` after its look, read from
 * the final run (the run page shows the same labelled value). Live or kept loops show none.
 */
export function finalTestView(run: Pick<LoopRead, "status" | "outcome" | "sealed_set_consulted" | "final_experiment_id" | "goal" | "stop_detail">, holdout: Record<string, unknown> | null | undefined): FinalTestView {
  if (run.status === "finalizing") return { state: "running", text: "The accepted try is being re-run with its choices pinned. Its final test is scored once when it finishes." };
  if (run.outcome !== "improved" || !run.sealed_set_consulted || !run.final_experiment_id) return { state: "none" };
  if (run.stop_detail === "final_run_cancelled_after_look" || run.stop_detail === "final_run_failed_after_look") return { state: "no_number", text: own(DETAIL, run.stop_detail)! };
  const metric = goalMetric(run.goal);
  const value = metric ? num(own(holdout ?? {}, metric)) : null;
  if (!metric || value === null) return { state: "missing", text: "The final run's final-test score for the goal was not returned here. Open the final run to see its results." };
  return { state: "reported", metric: metricLabel(metric), value: fmtScore(value) };
}

// --- errors -----------------------------------------------------------------------------------

const ERRORS: Record<string, PlainError> = {
  fresh_split_unavailable: { title: "A fresh split is not available yet", detail: "Start the loop on this run's own test design.", fixable: true },
  fresh_split_human_only: { title: "Only a person can ask for a fresh split", detail: "And it is not available yet.", fixable: false },
  nothing_to_try: { title: "Nothing to try", detail: "Every model family the loop can add is already in this run. That is the usual answer for a default run; the current model stays.", fixable: false },
  start_truncated: { title: "This run cannot start a loop", detail: "It skipped a model for time or cut its tuning short, so tries could not be compared fairly.", fixable: false },
  start_too_slow: { title: "This run trains too slowly for a loop", detail: "A try would not fit the time a single try may take.", fixable: false },
  too_few_folds: { title: "Too few folds", detail: "A loop needs at least 5 cross-validation folds to tell a gain from noise.", fixable: false },
  time_ordered_folds: { title: "This test design is ordered by time", detail: "The loop's comparison rule does not apply to it.", fixable: false },
  start_not_locked: { title: "The run has not finished saving its results", detail: "Start the loop when the run is complete.", fixable: false },
  start_not_open_ingest: { title: "This run cannot start a loop", detail: "Loops start only from runs on uploaded project data.", fixable: false },
  start_sealed: { title: "This run cannot start a loop", detail: "It is itself a sealed try of a loop.", fixable: false },
  start_without_split_plan: { title: "This run has no saved test design", detail: "Run it again to get one, then start the loop.", fixable: false },
  blocking_finding: { title: "A trust check blocks this run", detail: "Fix the target leak or the score that looks too good first.", fixable: false },
  split_plan_exhausted: { title: "This test design has been used enough", detail: "It already carries as many comparisons as it safely allows.", fixable: false },
  improve_run_live: { title: "A loop is already running on this test design", detail: "Open it from the list of loops, or wait until it finishes.", fixable: false },
  improve_run_quota: { title: "Too many loops are running in this workspace", detail: "Wait until one finishes, then try again.", fixable: true },
  improve_rate_limited: { title: "Too many loops were started recently", detail: "Wait a while, then try again.", fixable: true },
  invalid_budget: { title: "The budget was refused", detail: "Check the numbers against the limits shown.", fixable: true },
  invalid_goal: { title: "The goal was refused", detail: "Set the goal again on the Goal page or with a threshold choice.", fixable: true },
  goal_needs_constraint: { title: "This goal needs a rule", detail: "The ranking score depends on a threshold. Choose a threshold with a rule (for example recall at least 0.8) in Try one change yourself, step 1, then start the loop.", fixable: true },
  goal_not_primary_metric: { title: "The goal must be the run's ranking score", detail: "Without a threshold rule, the loop can only aim at the score models are ranked on.", fixable: true },
  goal_objective_binary_only: { title: "Goals with rules are for yes/no problems only", detail: "Remove the threshold choice to use the ranking score.", fixable: true },
  invalid_goal_metric: { title: "That score cannot be the goal", detail: "Choose precision, recall, specificity, F1, accuracy or balanced accuracy.", fixable: true },
  goal_constraint_unsupported: { title: "One of the goal's rules cannot be used by the loop", detail: "A rule on the share of rows flagged is not supported. Choose a rule on precision, recall or another rate.", fixable: true },
  goal_too_large: { title: "The goal is too large", detail: "Use fewer rules.", fixable: true },
  invalid_goal_target: { title: "The goal's target is invalid", detail: "The target must name the goal's own score.", fixable: true },
  token_cannot_cancel: { title: "This loop cannot be stopped from here", detail: "A connected tool may stop only a queued or running loop it started.", fixable: false },
  token_not_authorized: { title: "Not allowed", detail: "This access cannot act on loops here.", fixable: false },
  not_waiting_for_a_person: { title: "This loop is no longer waiting for you", detail: "Someone may have decided already. Reload to see its state.", fixable: false },
  iteration_not_accepted: { title: "That try cannot be accepted", detail: "Only a try that beat the noise can be accepted.", fixable: true },
  acceptance_is_final: { title: "Another try was already accepted", detail: "A loop's accepted try cannot change.", fixable: false },
  final_attempts_exhausted: { title: "The final run was tried three times", detail: "It cannot be tried again; keep the current model.", fixable: false },
  invalid_acceptance: { title: "Choose one", detail: "Accept exactly one try, or keep the current model.", fixable: true },
  probe_unreadable: { title: "The accepted try's results cannot be read", detail: "Reload; if it persists, keep the current model.", fixable: false },
  human_session_required: { title: "Only a person can do this", detail: "Accepting a try needs a signed-in person.", fixable: false },
  precondition_failed: { title: "The loop changed since you loaded it", detail: "Reload to see its current state, then decide again.", fixable: true },
  precondition_required: { title: "The loop's version was missing", detail: "Reload the page and try again.", fixable: true },
  idempotency_key_reused: { title: "That request was already used with different content", detail: "Reload the page and try again.", fixable: true },
  idempotency_key_conflict: { title: "That request was already used with different content", detail: "Reload the page and try again.", fixable: true },
};
const BY_STATUS: Record<number, PlainError> = {
  400: { title: "The request was refused", detail: "Reload the page and try again.", fixable: true },
  403: { title: "Not allowed", detail: "Your role cannot do this in this workspace.", fixable: false },
  404: { title: "Not found", detail: "This loop or run does not exist in the active workspace, or you cannot see it.", fixable: false },
  409: { title: "Not possible right now", detail: "The loop or the run is in a state that does not allow this. Reload to see its state.", fixable: false },
  412: ERRORS.precondition_failed,
  422: { title: "The request was refused", detail: "Check the goal and the budget, then try again.", fixable: true },
  428: ERRORS.precondition_required,
  429: { title: "Too many requests", detail: "Wait a while, then try again.", fixable: true },
};

/** Any error -> a fixed plain sentence by code, then by status. The server's own message is never shown. */
export function loopError(error: unknown): PlainError {
  const status = httpStatus(error);
  if (status === null) return { title: "Could not reach DCLab", detail: "Check your connection and try again.", fixable: true };
  const { code } = envelope((error as { body?: unknown }).body);
  return own(ERRORS, code) ?? own(BY_STATUS as Record<string, PlainError>, String(status)) ?? { title: "Something went wrong", detail: "Try again in a moment.", fixable: true };
}

/** A 412: the loop changed since it was read (the page re-reads it). */
export const isStale = (error: unknown): boolean => httpStatus(error) === 412;

/** The HTTP status of an API error (the client's `ApiError` carries `status` and `body`), or null for anything else. */
function httpStatus(error: unknown): number | null {
  const status = error && typeof error === "object" ? (error as { status?: unknown }).status : undefined;
  return typeof status === "number" && Number.isInteger(status) ? status : null;
}

export type ReadState = "forbidden" | "not_found" | "error";
/** Distinct read failures: forbidden (403), not found (404), anything else. */
export function readState(error: unknown): ReadState {
  const status = httpStatus(error);
  if (status === 403) return "forbidden";
  if (status === 404) return "not_found";
  return "error";
}
export const READ_TEXT: Record<ReadState, string> = {
  forbidden: "Your role cannot see improvement loops in this workspace.",
  not_found: "This loop does not exist in the active workspace, or you cannot see it.",
  error: "The loop could not be loaded. Reload the page to try again.",
};


// --- progress ---------------------------------------------------------------------------------

export type LoopStep = { id: string; label: string; state: "done" | "current" | "todo" };
/** Goal and budget → tries → your decision → final test, from the loop's state (a kept or stopped loop never reaches the final test). */
export function loopSteps(run: Pick<LoopRead, "status" | "outcome" | "sealed_set_consulted" | "stop_detail">): LoopStep[] {
  const order = ["queued", "running", "waiting_user", "finalizing"];
  const at = order.indexOf(run.status);
  const finished = at === -1;
  const looked = run.outcome === "improved" && run.sealed_set_consulted;
  const state = (index: number): LoopStep["state"] => {
    if (finished) return index <= 1 || (looked && index <= 3) || (index === 2 && run.stop_detail === "kept_by_person") ? "done" : "todo";
    const current = at <= 1 ? 1 : at;
    return index < current ? "done" : index === current ? "current" : "todo";
  };
  return [
    { id: "goal", label: "Goal and budget", state: state(0) },
    { id: "tries", label: "Sealed tries", state: state(1) },
    { id: "decision", label: "Your decision", state: state(2) },
    { id: "final", label: "Final test, once", state: state(3) },
  ];
}
