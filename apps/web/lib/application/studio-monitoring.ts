/**
 * Monitoring (P7.5-A): pure view-model logic, so it runs under `npm run test:components`.
 * Every sentence restates fields of `GET /v1/projects/{id}/monitoring`, `.../monitoring/windows/{week}` or
 * `.../monitoring/outcomes`; a value that was not returned is left out, never guessed. Drift means "the inputs look
 * different from training" — never "the model is wrong" and never a cause. A column is marked watch / alert only when its
 * PSI passes the line AND a statistical test says the difference is not chance (the backend's gate); this page never says
 * more. Date columns are shown but not judged. Real-world results are real outcomes of scored rows, never the final test
 * set; no final test value is read or formatted here. DCLab never retrains or switches the model on its own. No sentence
 * built here passes the server's own message through; all times say UTC.
 */
import { plainText } from "./command-search.ts";
import { utcWhen } from "./format-time.ts";
import { envelope, type PlainError } from "./studio-wizard.ts";

export const WEEKS_LISTED = 12;
export const BARS_SHOWN = 10;
export const NAME_MAX = 60;
/** The chart's axis stops at this multiple of the alert line; longer bars are clipped and marked "off scale". */
export const AXIS_CAP = 3;

const own = <T>(table: Record<string, T>, key: string): T | undefined => (key && Object.hasOwn(table, key) ? table[key] : undefined);
const num = (v: unknown): number | null => (typeof v === "number" && Number.isFinite(v) ? v : null);
const fixed = (v: number) => v.toFixed(2);
const pct = (v: number) => `${Math.round(v * 100)}%`;
const count = (v: number) => Math.round(v).toLocaleString("en-US");
const isObject = (v: unknown): v is Record<string, unknown> => typeof v === "object" && v !== null && !Array.isArray(v);
const ISO_DAY = /^\d{4}-\d{2}-\d{2}$/;

/** A plain number with up to `digits` significant digits, grouped, never in exponent form for normal magnitudes. */
export function plainNumber(v: number, digits = 4): string {
  if (!Number.isFinite(v)) return "—";
  if (v === 0) return "0";
  const abs = Math.abs(v);
  if (abs >= 1e15 || abs < 1e-6) return v.toPrecision(digits);
  return v.toLocaleString("en-US", { maximumSignificantDigits: digits, useGrouping: true });
}
/** Two numbers shown side by side: more digits when 4 would make different values look the same. */
export function numberPair(a: number, b: number): [string, string] {
  for (let digits = 4; digits <= 7; digits += 1) {
    const pair: [string, string] = [plainNumber(a, digits), plainNumber(b, digits)];
    if (a === b || pair[0] !== pair[1]) return pair;
  }
  return [plainNumber(a, 7), plainNumber(b, 7)];
}

export type Thresholds = {
  psi_watch: number; psi_alert: number; flagged_share_relative_watch: number; min_rows: number; min_labels: number; min_per_class: number;
  significance_level?: number | null; flagged_share_absolute_watch?: number | null;
};
export type WeekSummaryLike = {
  window_start: string; status: string; summary_code: string; n_rows: number; n_batch_predictions: number; model_version_id?: string;
  max_psi?: number | null; prediction_psi?: number | null; outcomes_status: string; n_labelled?: number | null; coverage?: number | null;
  outcome_metric?: string | null; outcome_metric_value?: number | null;
};
export type OverviewLike = { enabled: boolean; status: string; model_version?: string | null; latest?: WeekSummaryLike | null; windows: WeekSummaryLike[]; thresholds: Thresholds };
export type FeatureLike = {
  name?: unknown; kind?: unknown; psi?: unknown; status?: unknown; counted?: unknown; mean?: unknown; mean_reference?: unknown;
  missing_share?: unknown; missing_share_reference?: unknown; top_change?: { bucket?: unknown; value?: unknown; share?: unknown; share_reference?: unknown } | null;
  range?: unknown; range_reference?: unknown; later_than_training?: unknown;
};

/** The feature entries of a week's input read: objects only (a null or broken entry is dropped, never thrown on). */
export function featureList(inputDrift: unknown): FeatureLike[] {
  const raw = isObject(inputDrift) ? inputDrift.features : null;
  return Array.isArray(raw) ? (raw.filter(isObject) as FeatureLike[]) : [];
}
export const isDate = (f: FeatureLike) => f.kind === "date";
/** Columns that are judged: not dates, and not marked as not counted. */
export const isCounted = (f: FeatureLike) => !isDate(f) && f.counted !== false;

const testRule = (t: Thresholds | null | undefined) => (t && num(t.significance_level) !== null
  ? ` (p below ${t.significance_level} divided by the number of columns compared)`
  : "");

/** PSI explained once, in plain words (the Term tooltip). Thresholds come from the read; without them none are named. */
export function psiDefinition(t: Thresholds | null | undefined): string {
  const lines = t
    ? `DCLab marks a column watch at ${fixed(t.psi_watch)} or more and alert at ${fixed(t.psi_alert)} or more, but only when PSI passes the line AND a statistical test says the difference is not chance for this many rows and columns${testRule(t)}.`
    : "The watch and alert lines are unavailable until the status loads.";
  return `PSI (population stability index) compares how a column's values are spread in a week's scored rows with how they were spread in the training data. 0 means the same spread; higher means more different. ${lines} It says the inputs look different, not why, and not that the model is wrong.`;
}
export const WEEK_DEFINITION = "Monitoring groups the files the model in use scored into calendar weeks, Monday 00:00 to Monday 00:00 UTC, and compares each week with the training data.";
export const THRESHOLDS_UNAVAILABLE = "The watch and alert lines are unavailable right now.";

/** "week of Mon, 5 Oct 2026 (UTC)" from a week's start; "—" when the date cannot be read. */
export function weekLabel(iso: string | null | undefined): string {
  if (!iso) return "—";
  const date = new Date(iso);
  if (Number.isNaN(date.getTime())) return "—";
  return `week of ${date.toLocaleDateString("en-GB", { timeZone: "UTC", weekday: "short", day: "numeric", month: "short", year: "numeric" })} (UTC)`;
}
/** The window read's path value (YYYY-MM-DD, UTC); null for anything that is not a date. */
export function weekKey(iso: string | null | undefined): string | null {
  if (!iso) return null;
  const date = new Date(iso);
  if (Number.isNaN(date.getTime())) return null;
  const key = date.toISOString().slice(0, 10);
  return ISO_DAY.test(key) ? key : null;
}

export type Tone = "ok" | "warn" | "crit" | "gray";
const STATUS_WORDS: Record<string, { label: string; tone: Tone }> = {
  ok: { label: "Compared columns look like training", tone: "ok" },
  watch: { label: "Watch", tone: "warn" },
  alert: { label: "Alert", tone: "crit" },
  not_enough_data: { label: "Not enough data", tone: "gray" },
};
export function statusWords(status: string): { label: string; tone: Tone } {
  return own(STATUS_WORDS, status) ?? { label: "Unknown", tone: "gray" };
}

export const NOT_THE_MODEL = "Drift means the inputs look different from training. It does not mean the model is wrong.";
export const NOTHING_AUTOMATIC = "DCLab did not retrain or switch the model; it never does that on its own.";
export const WITHIN_CHANCE = "within what chance allows for this many rows and columns";

const NOT_ENOUGH: Record<string, (w: WeekSummaryLike, t: Thresholds) => string> = {
  too_few_rows: (w, t) => `Fewer than ${count(t.min_rows)} rows could be compared with training in the ${weekLabel(w.window_start)} (${count(w.n_rows)} scored), so DCLab does not judge whether the inputs changed.`,
  no_reference: () => "This model was saved before monitoring existed, so there is nothing from training to compare with. A model trained again can be monitored.",
  inputs_unavailable: (w) => `The files scored in the ${weekLabel(w.window_start)} are no longer available, so their inputs cannot be compared with training.`,
};

/**
 * One week's verdict in plain words, built from its numbers and the read's thresholds. `compared` (the number of judged
 * columns, from the week read) is optional: 0 means nothing could be compared, so the week is not said to look like training.
 */
export function weekSentence(w: WeekSummaryLike, t: Thresholds, compared?: number | null): string {
  const week = weekLabel(w.window_start);
  if (w.status === "not_enough_data") return (own(NOT_ENOUGH, w.summary_code) ?? (() => `There is not enough data to judge the ${week}.`))(w, t);
  const inputs = num(w.max_psi);
  const scores = num(w.prediction_psi);
  const lines = `The watch line is ${fixed(t.psi_watch)} and the alert line ${fixed(t.psi_alert)}; a change counts only when a statistical test says it is not chance.`;
  const facts: string[] = [];
  if (inputs !== null) facts.push(`the largest input change (PSI) is ${fixed(inputs)}${w.status === "ok" && inputs >= t.psi_watch ? `, above the line but ${WITHIN_CHANCE}` : ""}`);
  if (scores !== null) facts.push(`the score change (PSI) is ${fixed(scores)}${w.status === "ok" && scores >= t.psi_watch ? `, above the line but ${WITHIN_CHANCE}` : ""}`);
  const joined = facts.join("; ");
  const measured = joined ? ` ${joined[0].toUpperCase()}${joined.slice(1)}.` : "";
  if (w.status === "ok") {
    if (compared === 0) return `In the ${week}, no column could be compared with training, so nothing is judged.`;
    return `In the ${week}, the columns compared look like training (${count(w.n_rows)} rows scored).${measured} ${lines}`;
  }
  if (w.status === "watch" || w.status === "alert") {
    const below = (inputs === null || inputs < t.psi_watch) && (scores === null || scores < t.psi_watch);
    const lead = w.status === "watch" ? "Watch" : "Alert";
    if (below) {
      // Only the share of rows flagged moved: the PSI numbers stay below the lines, so point to the scores.
      return `${lead}: in the ${week}, the share of rows the model flagged moved (see Scores in the week's details). The inputs and scores stay below the watch line${inputs !== null ? ` (largest input change ${fixed(inputs)})` : ""}. ${NOT_THE_MODEL}${w.status === "alert" ? ` ${NOTHING_AUTOMATIC}` : ""}`;
    }
    const amount = w.status === "watch" ? "somewhat" : "a lot";
    return `${lead}: in the ${week}, the inputs or the scores moved ${amount}.${measured} ${lines} ${NOT_THE_MODEL}${w.status === "alert" ? ` ${NOTHING_AUTOMATIC}` : ""}`;
  }
  return `The ${week} has a result this page does not recognise, so nothing more is said.`;
}

export type Headline =
  | { kind: "loading" } | { kind: "error" }
  | { kind: "no_model" | "no_weeks" | "paused" | "ok" | "watch" | "alert" | "not_enough_data" | "unknown"; label: string; tone: Tone; sentence: string; paused: boolean };

export const PAUSED_NOTE = "Monitoring is switched off on this server right now, so new weeks are not checked; what was checked before is still shown.";

/**
 * The status headline for the model in use. Loading, a failed read, no model in use, no week checked yet, monitoring
 * paused with nothing checked, ok / watch / alert / not enough data are distinct. Paused with weeks keeps the last
 * status (and its tone) and adds the paused note. `model` is the model in use's plain name.
 */
export function headline(input: { pending: boolean; failed: boolean; overview: OverviewLike | undefined; model: string }): Headline {
  if (input.failed) return { kind: "error" };
  const o = input.overview;
  if (input.pending || !o) return { kind: "loading" };
  if (o.status === "no_model_in_use") {
    return { kind: "no_model", label: "No model in use", tone: "gray", paused: false, sentence: "No model is in use yet, so there is nothing to monitor. Approve a model for scoring on the Model screen first." };
  }
  const paused = o.enabled === false;
  if (o.status === "no_windows" || !o.latest) {
    return paused
      ? { kind: "paused", label: "Monitoring paused", tone: "gray", paused, sentence: `${input.model} is the model in use, but monitoring is switched off on this server, so no week is checked. Ask the person who runs DCLab to turn it on.` }
      : { kind: "no_weeks", label: "No week checked yet", tone: "gray", paused, sentence: `${input.model} is the model in use, but no week has been checked yet. A week is checked after the model in use scores a file in it.` };
  }
  const kind = own(STATUS_WORDS, o.status) ? (o.status as "ok" | "watch" | "alert" | "not_enough_data") : "unknown";
  const words = statusWords(o.status);
  return {
    kind, label: paused ? `${words.label} · monitoring paused` : words.label, tone: words.tone, paused,
    sentence: `${input.model}: ${weekSentence(o.latest, o.thresholds)}${paused ? ` ${PAUSED_NOTE}` : ""}`,
  };
}

/** The short real-world-results cell of the weeks table. */
export function outcomeCell(w: WeekSummaryLike): string {
  const labelled = num(w.n_labelled);
  switch (w.outcomes_status) {
    case "not_linked": return "No outcomes file yet";
    case "no_key_in_scores": return "Cannot be joined (no ID column)";
    case "key_not_unique": return "Cannot be joined (IDs repeat)";
    case "not_enough_labels": return `Not enough labelled rows yet (${labelled === null ? "—" : count(labelled)})`;
    case "measured": {
      const value = num(w.outcome_metric_value);
      const metric = w.outcome_metric ? metricLabel(w.outcome_metric) : null;
      return `${metric && value !== null ? `${metric} ${fixed(value)}` : "Measured"} on ${labelled === null ? "—" : count(labelled)} rows`;
    }
    default: return "—";
  }
}

export type WeekRow = { key: string; week: string; label: string; tone: Tone; rows: string; inputs: string; scores: string; outcomes: string };
const psiCell = (v: number | null, status: string, t: Thresholds) => (v === null ? "—" : status === "ok" && v >= t.psi_watch ? `${fixed(v)} (within chance)` : fixed(v));
/** The weeks table: newest first, at most 12, only weeks with a readable date. An ok week above a line says "within chance". */
export function weekRows(windows: readonly WeekSummaryLike[], t: Thresholds): WeekRow[] {
  return windows
    .flatMap((w) => { const key = weekKey(w.window_start); return key ? [{ key, w }] : []; })
    .sort((a, b) => b.key.localeCompare(a.key))
    .slice(0, WEEKS_LISTED)
    .map(({ key, w }) => {
      const words = statusWords(w.status);
      return {
        key, week: weekLabel(w.window_start), label: words.label, tone: words.tone, rows: count(w.n_rows),
        inputs: psiCell(num(w.max_psi), w.status, t), scores: psiCell(num(w.prediction_psi), w.status, t), outcomes: outcomeCell(w),
      };
    });
}
export const TABLE_NOTE = "A change at or above the watch line counts only when a statistical test says it is not chance, so a small week can show a large number and still look like training (marked “within chance”).";

/** A column's status word: an ok column at or above the watch line is "within chance", never a bare number that looks alarming. */
export function featureStatusWord(f: FeatureLike, t: Pick<Thresholds, "psi_watch">): string {
  const psi = num(f.psi);
  if (f.status === "alert") return "Alert";
  if (f.status === "watch") return "Watch";
  if (f.status === "ok") return psi !== null && psi >= t.psi_watch ? "Within chance" : "Little change";
  return "Unknown";
}

export type Bar = { name: string; psi: number; status: string; word: string; offScale: boolean; width: number };
export type BarChartData = { bars: Bar[]; max: number; watch: number; alert: number; stackLabels: boolean; summary: string };
/**
 * The top judged columns as bars (at most 10, largest first) with the watch and alert lines. Date columns and columns
 * not counted are left out of the bars and the scale. The axis always shows the alert line and stops at 3x it; longer
 * bars are clipped and marked off scale. Names are untrusted plain text (capped).
 */
export function driftBars(features: readonly FeatureLike[], t: Pick<Thresholds, "psi_watch" | "psi_alert">): BarChartData {
  const picked = features
    .filter((f) => isObject(f) && isCounted(f))
    .flatMap((f) => { const psi = num(f.psi); return psi === null || psi < 0 ? [] : [{ f, psi }]; })
    .sort((a, b) => b.psi - a.psi)
    .slice(0, BARS_SHOWN);
  const top = picked.length ? picked[0].psi : 0;
  const max = Math.min(Math.max(top, t.psi_alert * 1.25, 0.01), t.psi_alert * AXIS_CAP);
  const bars = picked.map(({ f, psi }) => ({
    name: plainText(f.name, NAME_MAX) || "(unnamed column)", psi, status: typeof f.status === "string" ? f.status : "",
    word: featureStatusWord(f, t), offScale: psi > max, width: Math.max(0.5, Math.min(100, (psi / max) * 100)),
  }));
  const stackLabels = ((t.psi_alert - t.psi_watch) / max) * 100 < 15;
  const summary = bars.length
    ? `Input change (PSI) of the ${bars.length} judged columns that moved most, largest first: ${bars.map((b) => `${b.name} ${fixed(b.psi)} (${b.word.toLowerCase()})`).join(", ")}. Watch line ${fixed(t.psi_watch)}, alert line ${fixed(t.psi_alert)}.`
    : "No judged column could be compared for this week.";
  return { bars, max, watch: t.psi_watch, alert: t.psi_alert, stackLabels, summary };
}

const dayRange = (v: unknown): [string, string] | null =>
  Array.isArray(v) && v.length === 2 && typeof v[0] === "string" && typeof v[1] === "string" && ISO_DAY.test(v[0]) && ISO_DAY.test(v[1]) ? [v[0], v[1]] : null;

/** One date column: its date range vs training, from valid YYYY-MM-DD values only; never a PSI. */
export function dateSentence(f: FeatureLike): string {
  const name = plainText(f.name, NAME_MAX) || "(unnamed column)";
  const span = dayRange(f.range);
  const ref = dayRange(f.range_reference);
  if (!span) return `${name}: no dates could be read this week.`;
  return `${name}: dates from ${span[0]} to ${span[1]}${ref ? ` (training: ${ref[0]} to ${ref[1]})` : ""}.`;
}
export const DATES_NOT_JUDGED = "Date columns are shown but not judged.";
export const DATES_LATER = "Dates in new files are later than in training, as expected over time.";
/** The note under the date columns: always that they are not judged; "later than training" when the read says so. */
export function dateNote(features: readonly FeatureLike[]): string | null {
  const dates = features.filter(isDate);
  if (!dates.length) return null;
  return dates.some((f) => f.later_than_training === true) ? `${DATES_LATER} ${DATES_NOT_JUDGED}` : DATES_NOT_JUDGED;
}

/** One judged column in plain words, from its numbers; never a cause. A date column gets its date range instead. */
export function featureSentence(f: FeatureLike, t: Pick<Thresholds, "psi_watch"> | null = null): string {
  if (isDate(f)) return dateSentence(f);
  const name = plainText(f.name, NAME_MAX) || "(unnamed column)";
  const psi = num(f.psi) ?? 0;
  const amount = f.status === "alert" ? "a lot" : f.status === "watch" ? "somewhat" : "little";
  let text = f.status === "ok" && t && psi >= t.psi_watch
    ? `${name} changed (PSI ${fixed(psi)}), above the line but ${WITHIN_CHANCE}`
    : `${name} moved ${amount} (PSI ${fixed(psi)})`;
  const mean = num(f.mean);
  const ref = num(f.mean_reference);
  const change = isObject(f.top_change) ? f.top_change : null;
  const share = num(change?.share);
  const shareRef = num(change?.share_reference);
  if (f.kind === "numeric" && mean !== null && ref !== null) {
    const [a, b] = numberPair(mean, ref);
    text += `: the average is ${a} vs ${b} in training`;
  } else if (change && share !== null && shareRef !== null) {
    const value = plainText(change.value, NAME_MAX);
    const what = change.bucket === "category" ? (value ? `the value '${value}' is` : "an unnamed value is") : change.bucket === "missing" ? "missing values are" : "values that were rare or unseen in training are";
    text += `: ${what} ${pct(share)} of rows vs ${pct(shareRef)} in training`;
  }
  const missing = num(f.missing_share);
  const missingRef = num(f.missing_share_reference);
  if (missing !== null && missingRef !== null && Math.abs(missing - missingRef) >= 0.05 && change?.bucket !== "missing") {
    text += `; ${pct(missing)} of values are missing vs ${pct(missingRef)} in training`;
  }
  return `${text}.`;
}

const ID_NAME = /^id$|_id$|^uuid$|^(customer|user|account|order)_?(id|no|number|code)$/i;
/** A tight guess from the name only: does it look like an ID column? (Dates come from `kind`, never from the name.) */
export function looksLikeId(name: unknown): boolean {
  return ID_NAME.test(String(name ?? "").trim());
}
/** The caveat when a judged column that moved (watch or alert) has a name that suggests an ID. */
export function idCaveat(features: readonly FeatureLike[]): string | null {
  const names = features.filter((f) => isCounted(f) && (f.status === "watch" || f.status === "alert") && looksLikeId(f.name)).map((f) => plainText(f.name, NAME_MAX));
  if (!names.length) return null;
  return `The name of ${names.join(", ")} suggests an ID. ID-like columns can look different simply because new customers appear in new files; a change here does not by itself mean anything is wrong.`;
}

export type InputScope = { features_compared?: unknown; features_total?: unknown; features_not_monitored?: unknown; features_not_shown?: unknown; dates_compared?: unknown };
/** How much of the data was compared: judged columns (of the total when known), columns not monitored, dates shown only. */
export function scopeText(s: InputScope | null | undefined): string | null {
  if (!s) return null;
  const compared = num(s.features_compared);
  if (compared === null) return null;
  const total = num(s.features_total);
  const parts = [`${count(compared)}${total !== null ? ` of ${count(total)}` : ""} ${compared === 1 && total === null ? "column" : "columns"} compared with training.`];
  const notMonitored = num(s.features_not_monitored);
  if (notMonitored) parts.push(`${count(notMonitored)} ${notMonitored === 1 ? "column is" : "columns are"} not monitored.`);
  const dates = num(s.dates_compared);
  if (dates) parts.push(`${count(dates)} date ${dates === 1 ? "column is" : "columns are"} shown but not judged.`);
  return parts.join(" ");
}

export type ScoresLike = { available?: unknown; reason_code?: unknown; n_scores?: unknown; psi?: unknown; status?: unknown; flagged_share?: unknown; flagged_share_reference?: unknown; flagged_status?: unknown };
/** How the scores moved this week vs the scores the model gave during training (cross-validation). */
export function scoreSentences(p: ScoresLike | null | undefined, t: Thresholds | null = null): string[] {
  if (!p || p.available !== true) {
    if (p?.reason_code === "no_reference") return ["The scores cannot be compared: this model was saved before monitoring existed."];
    if (p?.reason_code === "scores_missing") return ["The scored files have no score column to compare."];
    if (p?.reason_code === "too_few_scores") {
      const n = num(p.n_scores);
      return [`Too few scores this week to compare${n !== null ? ` (${count(n)})` : ""}.`];
    }
    return ["The scores could not be compared for this week."];
  }
  const psi = num(p.psi) ?? 0;
  const lead = "Compared with the scores the model gave during training (cross-validation),";
  const lines = [p.status === "ok" && t && psi >= t.psi_watch
    ? `${lead} the scores changed (PSI ${fixed(psi)}), above the line but ${WITHIN_CHANCE}.`
    : `${lead} the scores moved ${p.status === "alert" ? "a lot" : p.status === "watch" ? "somewhat" : "little"} (PSI ${fixed(psi)}).`];
  const flagged = num(p.flagged_share);
  const ref = num(p.flagged_share_reference);
  if (flagged !== null && ref !== null) {
    lines.push(`The share of rows flagged is ${pct(flagged)} this week vs ${pct(ref)} during training${p.flagged_status === "watch" ? ", a change big enough to watch" : ""}.`);
  }
  return lines;
}

const METRICS: Record<string, string> = {
  precision: "Precision", recall: "Recall", f1: "F1", accuracy: "Accuracy", roc_auc: "ROC AUC", pr_auc: "PR AUC",
  balanced_accuracy: "Balanced accuracy", macro_f1: "Macro F1", weighted_f1: "Weighted F1", mae: "Mean absolute error", rmse: "RMSE", r2: "R²",
};
export function metricLabel(key: string): string {
  return own(METRICS, key.toLowerCase()) ?? plainText(key.replace(/_/g, " "), 40);
}

export type OutcomesLike = {
  status?: unknown; side?: unknown; n_labelled?: unknown; n_scored?: unknown; n_keyed?: unknown; coverage?: unknown;
  metric?: unknown; metric_value?: unknown; metrics?: Record<string, unknown> | null; cv_reference?: { metric?: unknown; value?: unknown } | null; outcome_sets_tried?: unknown;
  unrecognised_outcome_values?: unknown; outcome_spellings?: unknown; refused_sets?: unknown; label_positive_rate?: unknown; positive_rate_reference?: unknown;
};
export type OutcomeView =
  | { kind: "measured"; lead: string; metrics: Array<{ label: string; value: string }>; notes: string[] }
  | { kind: "reason"; lead: string; notes: string[] };
export const REAL_NOT_TEST = "These are real outcomes of rows the model scored, not the final test. They are never used to train or to choose a model.";
export const MAY_DIFFER = "Rows with outcomes may differ from rows without, so these numbers may not describe every row scored.";
export const REFUSED_SET = "A newer outcome file was skipped because an ID repeats in it.";

/** Notes that apply whether or not results were measured: values left out, which spellings count, a skipped file. */
function outcomeNotes(o: OutcomesLike): string[] {
  const notes: string[] = [];
  const unrecognised = num(o.unrecognised_outcome_values);
  if (unrecognised) {
    notes.push(`${count(unrecognised)} outcome ${unrecognised === 1 ? "value" : "values"} did not match the training outcome labels and ${unrecognised === 1 ? "was" : "were"} left out.`);
  }
  if (o.outcome_spellings === "training_labels") notes.push("Only the training outcome's own labels count as outcomes.");
  if (o.outcome_spellings === "yes_no_tokens") notes.push("This model was saved before its outcome labels were recorded, so only yes/no style values count as outcomes.");
  if (Array.isArray(o.refused_sets) && o.refused_sets.length) notes.push(REFUSED_SET);
  return notes;
}

/** The "Real-world results" of one week: the metrics when measured, otherwise the honest reason. */
export function outcomeView(o: OutcomesLike | null | undefined, t: Pick<Thresholds, "min_labels" | "min_per_class">): OutcomeView {
  const safe = isObject(o) ? (o as OutcomesLike) : {};
  const labelled = num(safe.n_labelled);
  const notes = outcomeNotes(safe);
  switch (safe.status) {
    case "measured": {
      const coverage = num(safe.coverage);
      const keyed = num(safe.n_keyed);
      const metrics = Object.entries(isObject(safe.metrics) ? safe.metrics : {}).flatMap(([key, value]) => (num(value) === null ? [] : [{ label: metricLabel(key), value: (value as number).toFixed(3) }]));
      const extra: string[] = [];
      if (coverage !== null && coverage < 1) extra.push(MAY_DIFFER);
      const rate = num(safe.label_positive_rate);
      const rateRef = num(safe.positive_rate_reference);
      if (rate !== null) extra.push(`${pct(rate)} of them had the outcome${rateRef !== null ? `, vs ${pct(rateRef)} of the training rows` : ""}.`);
      const metric = typeof safe.metric === "string" ? metricLabel(safe.metric) : null;
      const primary = num(safe.metric_value);
      const refValue = num(safe.cv_reference?.value);
      if (metric && primary !== null) {
        extra.push(refValue !== null
          ? `${metric} on these real outcomes is ${primary.toFixed(3)}; during training (cross-validation) it was ${refValue.toFixed(3)}.`
          : `${metric} on these real outcomes is ${primary.toFixed(3)}. No cross-validation figure is shown for it: it would not be directly comparable.`);
      }
      return {
        kind: "measured",
        lead: `Measured on ${labelled === null ? "—" : count(labelled)}${keyed !== null ? ` of ${count(keyed)} scored rows with an ID` : " rows with a real outcome"}${coverage !== null ? ` (${pct(coverage)})` : ""}.`,
        metrics, notes: [...extra, ...notes],
      };
    }
    case "not_linked": return { kind: "reason", lead: "No outcomes file is linked yet. Link one below once the real results are known.", notes };
    case "no_key_in_scores": return { kind: "reason", lead: "The scored rows have no ID column to join outcomes by, so real-world results cannot be measured for this model.", notes };
    case "key_not_unique":
      return safe.side === "outcomes"
        ? { kind: "reason", lead: "IDs repeat in the linked outcomes files, so each outcome cannot be matched to one scored row. Link a file where each ID appears once.", notes: notes.filter((n) => n !== REFUSED_SET) }
        : { kind: "reason", lead: "IDs repeat in this week's scored rows, so each outcome cannot be matched to one scored row.", notes };
    case "not_enough_labels":
      if (labelled === 0 && num(safe.outcome_sets_tried)) return { kind: "reason", lead: "The linked outcomes files could not be matched to this week's scored rows yet: no ID was found in both.", notes };
      return { kind: "reason", lead: `Not enough labelled rows yet: ${labelled === null ? "—" : count(labelled)} of this week's scored rows have a recognised real outcome; at least ${count(t.min_labels)} are needed, with at least ${count(t.min_per_class)} of each outcome. Outcomes usually arrive weeks late.`, notes };
    default: return { kind: "reason", lead: "Real-world results are not available for this week.", notes };
  }
}

export function thresholdLines(t: Thresholds): string[] {
  const abs = num(t.flagged_share_absolute_watch);
  return [
    `Input and score change (PSI): watch at ${fixed(t.psi_watch)}, alert at ${fixed(t.psi_alert)}. A column is marked only when PSI passes the line AND a statistical test says it is not chance${testRule(t)}.`,
    `Share of rows flagged: watch when it moves by ${pct(t.flagged_share_relative_watch)} or more of the share during training${abs !== null ? ` AND by at least ${Math.round(abs * 100)} percentage points` : ""} AND the test says it is not chance.`,
    `At least ${count(t.min_rows)} rows compared in a week before drift is judged.`,
    `At least ${count(t.min_labels)} rows with a real outcome (and ${count(t.min_per_class)} of each outcome) before real-world results are shown.`,
    DATES_NOT_JUDGED,
  ];
}
export const THRESHOLDS_READ_ONLY = "These lines are set by DCLab and cannot be changed here.";

/* ---------- linking an outcomes file ---------- */

export type OutcomeSetLike = { id: string; dataset_id?: string; dataset_name?: string | null; key_column: string; outcome_column: string; linked_at: string; linked_by_user_id?: string | null };
export type FileInfo = { name: string; rows?: number | null; uploadedAt?: string | null };
export type LinkedRow = { id: string; file: string; key: string; outcome: string; line: string };
/** "1,200 rows · uploaded Mon, 12 Oct 2026, 09:30 UTC" when known. */
export function fileFacts(info: FileInfo | undefined | null): string {
  if (!info) return "";
  const parts: string[] = [];
  if (num(info.rows) !== null) parts.push(`${count(info.rows as number)} rows`);
  if (info.uploadedAt && utcWhen(info.uploadedAt) !== "—") parts.push(`uploaded ${utcWhen(info.uploadedAt)}`);
  return parts.join(" · ");
}
/** Linked files, newest first; names are untrusted plain text. `files` adds the row count and upload date when known. */
export function linkedRows(items: readonly OutcomeSetLike[], myUserId: string | null, files: ReadonlyMap<string, FileInfo> = new Map()): LinkedRow[] {
  return [...items].sort((a, b) => b.linked_at.localeCompare(a.linked_at)).map((s) => {
    const by = s.linked_by_user_id ? (myUserId && s.linked_by_user_id === myUserId ? "you" : "a person") : null;
    const facts = fileFacts(s.dataset_id ? files.get(s.dataset_id) : undefined);
    return {
      id: s.id, file: plainText(s.dataset_name, 80) || "A file of this project", key: plainText(s.key_column, NAME_MAX), outcome: plainText(s.outcome_column, NAME_MAX),
      line: `Linked ${utcWhen(s.linked_at)}${by ? ` by ${by}` : ""}.${facts ? ` File: ${facts}.` : ""}`,
    };
  });
}

export type LinkIntent = { datasetId: string; file: string; key: string; outcome: string; rows?: number | null; uploadedAt?: string | null };
/** What the one confirm step says happens, before anything is sent. `status` is the overview status (what gets rechecked). */
export function linkConfirmWords(i: LinkIntent, t: Pick<Thresholds, "min_labels">, status = ""): { title: string; button: string; lines: string[] } {
  const file = plainText(i.file, 80) || "this file";
  const key = plainText(i.key, NAME_MAX);
  const outcome = plainText(i.outcome, NAME_MAX);
  const facts = fileFacts({ name: file, rows: i.rows, uploadedAt: i.uploadedAt });
  const recheck = status === "no_model_in_use"
    ? "No model is in use, so nothing is checked again now; once a model is in use and scores files, its weeks use this file."
    : status === "no_windows"
      ? "No week of the model in use has been scored yet, so nothing is checked again now; new weeks use this file."
      : "The recent weeks of the model in use are checked again in the background; reload in a few minutes to see the result.";
  return {
    title: `Link ${file} as real outcomes?`,
    button: "Yes, link this file",
    lines: [
      ...(facts ? [`File: ${file} · ${facts}.`] : []),
      `DCLab joins the outcomes in ${outcome} to the rows the model in use scored, by matching ${key} with the scored rows' ID column. Each ID must appear once in the file.`,
      `Once at least ${count(t.min_labels)} scored rows have a real outcome, this screen shows how the model in use really did on those rows. Until then it says how many are labelled.`,
      recheck,
      "A link cannot be removed; the newest linked file that shares IDs with a week is the one used.",
      "Outcomes are never used to train or to choose a model, and nothing is retrained or switched. A file that trained a model in this project is refused.",
      "The link is saved in History.",
    ],
  };
}

export const UNCONFIRMED: PlainError = { title: "Could not confirm the link", detail: "Look at the linked files before trying again.", fixable: false };
const LINK_ERRORS: Record<string, PlainError> = {
  outcomes_from_training_data: { title: "This file trained a model in this project", detail: "Link a file of real outcomes of scored rows instead.", fixable: true },
  key_column_unknown: { title: "The file has no such key column", detail: "Pick the key column from the file's own columns and try again.", fixable: true },
  outcome_column_unknown: { title: "The file has no such outcome column", detail: "Pick the outcome column from the file's own columns and try again.", fixable: true },
  columns_must_differ: { title: "The key and the outcome must be different columns", detail: "Pick two different columns.", fixable: true },
  already_linked: { title: "This file is already linked with these columns", detail: "Check the list of linked files.", fixable: false },
  rate_limited: { title: "Too many requests", detail: "Too many requests, or more than 10 files linked to this project in the last hour. Try again later.", fixable: false },
  monitoring_disabled: { title: "Monitoring is switched off on this server", detail: "Nothing was linked. Ask the person who runs DCLab to turn it on.", fixable: false },
  dataset_not_found: { title: "That file was not found", detail: "Reload the list of files and pick it again.", fixable: false },
  dataset_not_in_project: { title: "That file belongs to another project", detail: "Pick a file uploaded to this project.", fixable: true },
  dataset_not_uploaded: { title: "That table cannot be linked", detail: "Only a file uploaded to this project can be linked.", fixable: true },
  dataset_not_published: { title: "That file is not ready yet", detail: "It has not finished processing. Try again in a minute, or upload it again.", fixable: false },
  human_session_required: { title: "Only a signed-in person can link outcomes", detail: "A connected tool or an access token cannot link an outcomes file.", fixable: false },
  service_token_not_permitted: { title: "Only a signed-in person can link outcomes", detail: "A connected tool or an access token cannot link an outcomes file.", fixable: false },
  capability_denied: { title: "You do not have permission to link outcomes here", detail: "It needs a role that can change ML work in this workspace.", fixable: false },
  idempotency_key_conflict: { title: "This link may already be saved", detail: "Reload and check the list of linked files before you try again.", fixable: false },
  concurrent_conflict: { title: "Another change was being saved", detail: "Try again in a moment.", fixable: false },
  validation_failed: { title: "The link could not be saved", detail: "Pick a file, a key column and an outcome column, then try again.", fixable: true },
};
/** Link refusals in fixed plain sentences by API code, then status. The server's own message is never shown. */
export function mapLinkError(error: unknown): PlainError {
  const { code } = envelope((error as { body?: unknown } | null)?.body);
  const known = own(LINK_ERRORS, code);
  if (known) return known;
  const status = (error as { status?: unknown } | null)?.status;
  if (status === 403) return LINK_ERRORS.capability_denied;
  if (status === 404) return LINK_ERRORS.dataset_not_found;
  if (status === 409 || status === 412 || status === 428) return { title: "The link was refused", detail: "Reload the page, check the file and the columns, and try again.", fixable: false };
  if (status === 422 || status === 400) return LINK_ERRORS.validation_failed;
  if (status === 429) return LINK_ERRORS.rate_limited;
  // A server error or a lost connection: the link may have been saved, so nothing is claimed either way.
  return UNCONFIRMED;
}
/** True for refusals after which the same request can never succeed (the next try is a new action with a new key). */
export function isLinkConflict(error: unknown): boolean {
  const status = (error as { status?: unknown } | null)?.status;
  if (status !== 409 && status !== 412 && status !== 422 && status !== 428) return false;
  const { code } = envelope((error as { body?: unknown } | null)?.body);
  return !(code === "concurrent_conflict" || (error as { body?: { error?: { retryable?: unknown } } } | null)?.body?.error?.retryable === true);
}

const UPLOAD_ERRORS: Record<string, PlainError> = {
  upload_rejected: { title: "That file could not be read as a table", detail: "Use a CSV, TSV, JSON, Parquet or XLSX file with a header row.", fixable: true },
  payload_too_large: { title: "The file is too large", detail: "Upload a smaller file.", fixable: true },
  length_required: { title: "The upload could not be sent", detail: "Try again; if it keeps failing, use a smaller file.", fixable: true },
  idempotency_key_conflict: { title: "That upload may already be saved", detail: "Reload the list of files and look for it before you upload again.", fixable: false },
};
export function mapUploadError(error: unknown): PlainError {
  const { code } = envelope((error as { body?: unknown } | null)?.body);
  const status = (error as { status?: unknown } | null)?.status;
  return own(UPLOAD_ERRORS, code)
    ?? (status === 413 ? UPLOAD_ERRORS.payload_too_large : status === 403
      ? { title: "You do not have permission to upload here", detail: "It needs a role that can change ML work in this workspace.", fixable: false }
      : { title: "Could not confirm the upload", detail: "Look at the list of files before uploading again.", fixable: false });
}

/** Buttons only for people who can run ML work, and none while a confirm step is open. */
export function canAct(viewer: { canWrite: boolean; confirming: boolean }): boolean {
  return viewer.canWrite && !viewer.confirming;
}

export type GoBack = { kind: "offer"; modelVersionId: string; model: string; text: string; viewerText: string } | { kind: "none"; text: string } | { kind: "hidden" };
/**
 * "Go back to the previous model" on an alert of the model in use: the newest earlier approved model (the same rows the
 * Model screen offers a go-back for). Hidden unless the alerting week belongs to the model in use now. With no earlier
 * approval it says so and offers nothing. It never suggests a retrain.
 */
export function goBackOffer(
  status: string, rows: ReadonlyArray<{ goBack: boolean; modelVersionId: string; model: string }>,
  models: { weekModelId: string | null | undefined; inUseId: string | null | undefined },
): GoBack {
  if (status !== "alert" || !models.weekModelId || models.weekModelId !== models.inUseId) return { kind: "hidden" };
  const previous = rows.find((r) => r.goBack);
  if (!previous) return { kind: "none", text: "No earlier model was approved for scoring, so there is no previous model to go back to." };
  return {
    kind: "offer", modelVersionId: previous.modelVersionId, model: previous.model,
    text: `If these differences mean the model in use should not be used for now, you can go back to the previous approved model, ${previous.model}. Check whether the data source or the population scored has changed first.`,
    viewerText: `The previous approved model is ${previous.model}. A person who can change this workspace can go back to it if these differences mean the model in use should not be used for now.`,
  };
}
