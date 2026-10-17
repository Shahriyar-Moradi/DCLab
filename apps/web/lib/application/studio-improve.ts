/**
 * The guided Improve screen in plain words (V7-B4, option B of ADR 0007), pure so it runs under `npm run test:components`.
 *
 * Everything here restates fields the API returned: the verdict read (`GET /v1/experiments/{id}/verdict`, cross-validation only,
 * natural units: an error score is positive and "lower is better"), the run reads and the look counts. A value that was not returned
 * is left out, never guessed. No final test set value is read or formatted here. Lookups by server-controlled keys use own-key checks.
 *
 * What the verdict means (ADR 0007 section 5): the same cross-validation folds, the difference of the two runs' scores on each fold,
 * and a Nadeau-Bengio corrected margin that is right for ONE comparison. It is evidence, not proof; the best of several tries looks
 * better than it is, and five folds have little power. Nothing here claims more.
 */
import { plainText } from "./command-search.ts";
import { metricInfo } from "./studio-goal.ts";
import { lowerIsBetter } from "./studio-compare.ts";
import { familyLabel, isBaselineFamily } from "./studio-runs.ts";

const own = <T>(table: Record<string, T>, key: string): T | undefined => (Object.hasOwn(table, key) ? table[key] : undefined);
const num = (value: unknown): number | null => (typeof value === "number" && Number.isFinite(value) ? value : null);

/** Shown above the tries, always. */
export const STANDING_BANNER = "Every try started here is scored on the final test set once. Do not pick a model by its final-test number.";
export const NOTHING_UNATTENDED = "This page never starts a try by itself. Tries on this list can also be started from code or by a connected tool.";
export const SAME_FOLDS_NOTE = "A try trains again on the same folds as the run in use, so the two can be compared fairly on cross-validation. It is also scored once on the same final test set; that number is not used to compare.";
export const SPEND_NOTE = "Every try started here spends one look at the final test set of this test design.";
export const NOISE_NOTE = "A gain smaller than the noise margin could be luck. Most tries end within the noise.";
export const KEEP_CURRENT_NOTE = "Keeping the current model is a normal result. If no try beats the noise, leave the model in use as it is. Nothing is recorded when you keep it.";
export const VIEWER_NOTE = "Your role can read this page but not start a try. Ask someone who can change this workspace.";

// --- the model families a try can add -----------------------------------------------------

/**
 * The model families the training engine knows (a mirror of its registry, baselines left out). The API decides: it refuses a family
 * that is not installed or that the run in use already trained, and the form shows its answer unchanged.
 */
const CLASSIFICATION = ["logistic_regression", "random_forest", "extra_trees", "gradient_boosting", "xgboost", "lightgbm", "catboost"] as const;
const REGRESSION = [
  "linear_regression", "ridge", "lasso", "elasticnet", "random_forest_regressor", "extra_trees_regressor", "gradient_boosting_regressor",
  "xgboost_regressor", "lightgbm_regressor", "catboost_regressor",
] as const;

export type FamilyChoice = { value: string; label: string };

/** Families to offer for a kind of answer, without the ones the run in use already trained. An unknown kind of answer offers none. */
export function familyChoices(taskType: string | null | undefined, tried: ReadonlyArray<string | null | undefined> = []): FamilyChoice[] {
  const kind = (taskType ?? "").trim();
  const all: readonly string[] = kind === "binary" || kind === "multiclass" || kind === "classification" ? CLASSIFICATION : kind === "regression" ? REGRESSION : [];
  const skip = new Set(tried.map((t) => (t ?? "").trim()).filter(Boolean));
  return all.filter((f) => !skip.has(f) && !isBaselineFamily(f)).map((f) => ({ value: f, label: familyLabel(f) ?? f }));
}

/** The branch the form sends: one `family_include` change; the reason is the person's own text, never rewritten. */
export function tryBody(family: string, intent: string): { intent: string; changes: Array<{ kind: "family_include"; family: string }> } | string {
  if (!family.trim()) return "Pick a model family to try.";
  if (!intent.trim()) return "Say why you are trying it (it is saved with the run).";
  return { intent: intent.trim(), changes: [{ kind: "family_include", family: family.trim() }] };
}

export const defaultIntent = (family: string): string => `Try the ${familyLabel(family) ?? "chosen"} model family`;

// --- the verdict --------------------------------------------------------------------------

export type VerdictLike = {
  experiment_id: string;
  verdict: string;
  reason?: string | null;
  metric?: string | null;
  direction?: string | null;
  folds?: number | null;
  mean_difference?: number | null;
  margin?: number | null;
  comparisons_on_plan?: number | null;
  comparisons?: number | null;
  new_candidates?: number | null;
  message?: string | null;
  against_is_parent?: boolean | null;
  fold_scores?: Array<{ fold_number: number; experiment: number; against: number; difference: number }> | null;
  test_set_looks?: { looks_on_plan?: number | null; experiment_look?: number | null; against_look?: number | null } | null;
};

export type VerdictKey = "beats_the_noise" | "within_the_noise" | "worse" | "not_comparable" | "unknown";
export type VerdictTone = "ok" | "gray" | "warn";
const VERDICTS: Record<string, { label: string; tone: VerdictTone }> = {
  beats_the_noise: { label: "Beats the noise", tone: "ok" },
  within_the_noise: { label: "Within the noise", tone: "gray" },
  worse: { label: "Worse beyond the noise", tone: "warn" },
  not_comparable: { label: "Can't compare", tone: "gray" },
};

export function verdictKey(verdict: string | null | undefined): VerdictKey {
  return verdict && Object.hasOwn(VERDICTS, verdict) ? (verdict as VerdictKey) : "unknown";
}
/** Reasons that mean no gain can be claimed, whatever the verdict field says: such a result is never shown as "Beats the noise". */
const NO_GAIN_REASONS = new Set(["suspicious_result", "time_ordered_folds"]);
/** The verdict to show: a result flagged suspicious or time-ordered is "Can't compare", even if it came back as a win. */
export function shownVerdict(v: Pick<VerdictLike, "verdict" | "reason"> | null | undefined): string | undefined {
  if (!v) return undefined;
  return v.verdict === "beats_the_noise" && v.reason && NO_GAIN_REASONS.has(v.reason) ? "not_comparable" : v.verdict;
}
export function verdictBadge(verdict: string | null | undefined): { label: string; tone: VerdictTone } {
  const entry = verdict ? own(VERDICTS, verdict) : undefined;
  return entry ?? { label: "Not recognised", tone: "gray" };
}

const NOT_COMPARABLE: Record<string, string> = {
  same_experiment: "A run cannot be compared with itself.",
  no_parent: "This run is not based on another run, so there is nothing to compare it with.",
  different_split_plan: "The two runs use different test designs, so their folds differ.",
  not_finished: "Both runs must have finished before they can be compared.",
  evidence_not_locked: "Both runs must have saved their results before they can be compared.",
  no_winner: "One of the runs has no chosen best model to compare.",
  different_metric: "The two runs rank models by different scores.",
  different_folds: "The two best models were not scored on the same folds.",
  missing_locked_folds: "A saved fold score is missing for one of the best models.",
  too_few_folds: "There are fewer than 5 cross-validation folds, too few to tell a real gain from noise.",
  constant_difference: "Every fold moved by exactly the same amount, so the noise cannot be measured.",
  time_ordered_folds: "This test design is ordered by time, so the comparison rule does not apply.",
  suspicious_result: "The result looks too good to be true. Check the trust checks of this run first; it is not counted as a gain.",
};

/** Why two runs cannot be compared, in words. An unknown reason gets a generic sentence, never the raw code. */
export function notComparableText(reason: string | null | undefined): string {
  return (reason ? own(NOT_COMPARABLE, reason) : undefined) ?? "These two runs cannot be compared.";
}

/** The size of a number without trailing noise: three significant digits, no sign (a sentence says the direction in words). */
export function fmtNum(value: number): string {
  if (value === 0) return "0";
  const abs = Math.abs(value);
  if (abs < 0.00001) return "about 0";
  const text = Number(abs.toPrecision(3)).toString();
  return /e/i.test(text) ? abs.toFixed(6).replace(/0+$/, "") : text;
}
const MINUS = "\u2212";
/** A number with its sign kept: a minus only when negative (scores such as R² can be negative). */
export const fmtScore = (value: number): string => (value < 0 && fmtNum(value) !== "about 0" ? `${MINUS}${fmtNum(value)}` : fmtNum(value));
/** A difference with an explicit sign: "+0.012" or "\u22120.012". */
export const fmtSigned = (value: number): string => (value === 0 || fmtNum(value) === "about 0" ? fmtNum(value) : `${value > 0 ? "+" : MINUS}${fmtNum(value)}`);

export type FoldView = { fold: number; current: string; tried: string; difference: string; words: string };
/** One row per fold with signs kept and the direction in words ("better" / "worse" for this score). */
export function foldViews(v: Pick<VerdictLike, "direction" | "metric" | "fold_scores">): FoldView[] {
  return (v.fold_scores ?? []).map((f) => {
    const diff = num(f.difference);
    const better = diff === null || diff === 0 ? null : (diff > 0) === higherIsBetter(v);
    return {
      fold: f.fold_number, current: fmtScore(f.against), tried: fmtScore(f.experiment),
      difference: diff === null ? "—" : fmtSigned(diff),
      words: better === null ? "same" : better ? (higherIsBetter(v) ? "higher, better" : "lower, better") : (higherIsBetter(v) ? "lower, worse" : "higher, worse"),
    };
  });
}

function higherIsBetter(v: Pick<VerdictLike, "direction" | "metric">): boolean {
  if (v.direction === "higher_is_better") return true;
  if (v.direction === "lower_is_better") return false;
  return !(v.metric && lowerIsBetter(v.metric));
}

const metricName = (v: VerdictLike): string => metricInfo(v.metric)?.label ?? "the score";

/**
 * "0.012 higher (better)" or "0.5 lower (better)" for an error score. The words say which way the number moved and whether that is
 * good, so a minus sign never has to be read. The difference is the try minus the run in use, in the score's own units.
 */
export function differenceText(v: VerdictLike): string | null {
  const diff = num(v.mean_difference);
  if (diff === null) return null;
  if (diff === 0) return "no difference";
  const better = (diff > 0) === higherIsBetter(v);
  return `${fmtNum(diff)} ${diff > 0 ? "higher" : "lower"} (${better ? "better" : "worse"})`;
}

/** "1 comparison" / "3 comparisons" the margin accounts for, when the read says so. */
export const comparisonsText = (n: number | null | undefined): string | null => (num(n) !== null && num(n)! >= 1 ? `${num(n)} ${num(n) === 1 ? "comparison" : "comparisons"}` : null);

export const marginText = (v: VerdictLike): string | null => (num(v.margin) !== null ? fmtNum(num(v.margin)!) : null);

/** One or two plain sentences for a verdict, built from the numbers only. */
export function verdictSentence(v: VerdictLike): string {
  const key = verdictKey(shownVerdict(v));
  if (key === "unknown") return "This result was not recognised, so no conclusion is drawn.";
  if (key === "not_comparable") return notComparableText(v.reason);
  // Always built here from the numbers: the server's `message` is never shown (it may carry raw keys or wording of its own).
  if (v.reason === "no_change") return `Both runs score the same on every one of the ${v.folds ?? "same"} folds: no change.`;
  const diff = differenceText(v);
  const margin = marginText(v);
  const folds = num(v.folds);
  if (diff === null || margin === null || folds === null) return "The numbers behind this result were not returned, so no conclusion is drawn.";
  const lead = `Across the same ${folds} folds, ${metricName(v)} was ${diff} on average than in the run in use.`;
  if (key === "beats_the_noise") return `${lead} That is more than the noise margin of ${margin}, so it is unlikely to be luck for this one comparison. It is evidence, not proof.`;
  if (key === "worse") return `${lead} That is a drop larger than the noise margin of ${margin}.`;
  if (v.reason === "below_minimum_effect") return `${lead} That is too small a change to matter, so keep the current model.`;
  return `${lead} That is inside the noise margin of ${margin}, so it could be luck. Keep the current model.`;
}

// --- looks at the final test set ----------------------------------------------------------

export const LOOK_EXPLAINED = "A look is one run on this test design that chose its best model and so scored the final test set. The count is conservative: a run that later failed or was stopped still counts. The more looks, the less a final-test number can be trusted as a fresh estimate.";

/** "Look 3 of 4", or what is known when the place of the run is not recorded. */
export function lookText(looks: VerdictLike["test_set_looks"]): string {
  if (!looks) return "Not counted";
  const k = num(looks.experiment_look);
  const n = num(looks.looks_on_plan);
  if (k !== null && n !== null) return `Look ${k} of ${n}`;
  if (n !== null) return `${n} ${n === 1 ? "look" : "looks"} so far, place of this run not recorded`;
  return "Not counted";
}

/** The caveat on how many runs of this test design picked a winner (more tries make a lucky one easier to find). */
export function triesCaveat(n: number | null | undefined): string | null {
  const count = num(n);
  if (count === null || count < 3) return null; // the run in use and one try: nothing to add up
  return `${count} runs on this test design have chosen, or are choosing, a best model (the run in use and runs still training included). Each try is judged at 5% on its own, but with several tries the chance that at least one looks better by luck is higher than 5%: the more you try, the easier it is to be fooled.`;
}

/** "This try added 2 new models" with the consequence, or null when the count was not returned. */
export function newModelsText(n: number | null | undefined): string | null {
  const k = num(n);
  if (k === null || k < 0) return null;
  if (k === 0) return "This try added no new models: the same models were trained again.";
  return `${k} new ${k === 1 ? "model" : "models"}. The more models a try adds, the larger the gain must be.`;
}

// --- tries: the runs based on the run in use ----------------------------------------------

export type RunLike = { id: string; status: string; created_at: string; intent?: string | null; parent_experiment_id?: string | null; evidence_mode?: string | null };

/**
 * The runs whose parent is the run in use, newest first. A run with no recorded parent is never one of them. Sealed improvement-loop
 * tries are left out: they never score the final test set, so the "one look per try" wording here does not apply to them.
 */
export function triesOf(runs: ReadonlyArray<RunLike>, runInUseId: string | null | undefined): RunLike[] {
  if (!runInUseId) return [];
  return runs.filter((r) => r.parent_experiment_id === runInUseId && r.evidence_mode !== "sealed").sort((a, b) => b.created_at.localeCompare(a.created_at) || a.id.localeCompare(b.id));
}

/** How many sealed improvement-loop tries are based on the run in use (shown on the Improvement loop tab, not here). */
export function sealedTriesOf(runs: ReadonlyArray<RunLike>, runInUseId: string | null | undefined): number {
  return runInUseId ? runs.filter((r) => r.parent_experiment_id === runInUseId && r.evidence_mode === "sealed").length : 0;
}
export const LOOP_TRIES_NOTE = "Loop tries are on the Improvement loop tab. They are sealed: judged on cross-validation only, and the final test set is not used.";

/** The finished tries whose verdict is read (a cap keeps the page light; the rest say so). */
export const VERDICT_CAP = 12;

export type Counts = { beats: number; within: number; worse: number; cannot: number; unread: number };
export function verdictCounts(items: ReadonlyArray<VerdictLike | null | undefined>): Counts {
  const counts: Counts = { beats: 0, within: 0, worse: 0, cannot: 0, unread: 0 };
  for (const v of items) {
    switch (verdictKey(shownVerdict(v))) {
      case "beats_the_noise": counts.beats += 1; break;
      case "within_the_noise": counts.within += 1; break;
      case "worse": counts.worse += 1; break;
      case "not_comparable": counts.cannot += 1; break;
      default: counts.unread += 1;
    }
  }
  return counts;
}

export function countsSentence(c: Counts): string | null {
  const parts = [
    c.beats ? `${c.beats} beat${c.beats === 1 ? "s" : ""} the noise` : null,
    c.within ? `${c.within} ${c.within === 1 ? "is" : "are"} within the noise` : null,
    c.worse ? `${c.worse} ${c.worse === 1 ? "is" : "are"} worse` : null,
    c.cannot ? `${c.cannot} can't be compared` : null,
    c.unread ? `${c.unread} not read` : null,
  ].filter((p): p is string => p !== null);
  return parts.length ? `Of the finished tries: ${parts.join(", ")}.` : null;
}

/** Untrusted run intent as short plain text for a table cell. */
export const intentText = (intent: string | null | undefined): string | null => (intent && intent.trim() ? plainText(intent, 120) : null);

/** The most runs on the test design that picked a winner, over the verdicts read (they are counts of one plan). */
export function maxPlanRuns(items: ReadonlyArray<VerdictLike | null | undefined>): number | null {
  const values = items.map((v) => num(v?.comparisons_on_plan)).filter((n): n is number => n !== null);
  return values.length ? Math.max(...values) : null;
}
export function maxLooks(items: ReadonlyArray<VerdictLike | null | undefined>): number | null {
  const values = items.map((v) => num(v?.test_set_looks?.looks_on_plan)).filter((n): n is number => n !== null);
  return values.length ? Math.max(...values) : null;
}
