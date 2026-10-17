/**
 * Model screen, v7 parity (UI-D): pure view-model logic, so it runs under `npm run test:components`.
 * Every sentence restates fields of `GET /v1/projects/{id}/model-versions`, `GET /v1/model-versions/{id}`,
 * `GET /v1/model-versions/{id}/feature-contract` or the model card; a value that was not returned is left out, never guessed.
 * Server sentences (check texts, notes, error messages) are never passed through: each fixed sentence is keyed by a code.
 * Final test values appear only in `finalTestView` / `confusionView`, which read the card's labelled single final evaluation.
 */
import { plainText, projectHref } from "./command-search.ts";
import { cardNumber, metricName } from "./studio-card.ts";
import { modelName } from "./studio-names.ts";
import { envelope } from "./studio-wizard.ts";

const own = <T>(table: Record<string, T>, key: string | null | undefined): T | undefined => (key && Object.hasOwn(table, key) ? table[key] : undefined);
const finite = (value: unknown): value is number => typeof value === "number" && Number.isFinite(value);
const count = (value: number): string => value.toLocaleString("en-US");
const percent = (part: number, whole: number): string => `${whole > 0 ? Math.round((part / whole) * 100) : 0}%`;

// --- Versions (GET /v1/projects/{id}/model-versions) -------------------------------------------

export type ModelVersionItemLike = {
  id: string; version: string; experiment_id: string; created_at: string;
  status: string; approved_for_scoring: boolean; waiting_for_approval?: boolean; last_approved_at?: string | null;
  cv?: Record<string, number>; cv_at_locked_threshold?: Record<string, number>; decision_threshold?: number | null; selection_metric?: string | null;
};
export type StatusPill = { text: string; tone: "ok" | "warn" | "det" | "gray" };
export type VersionRow = {
  id: string; model: string; href: string | null; experimentId: string; created: string;
  statusKey: string; status: StatusPill; note: string | null; main: string; precision: string | null; recall: string | null;
};

const STATUS: Record<string, StatusPill> = {
  in_use: { text: "In use", tone: "ok" },
  waiting_for_approval: { text: "Waiting for approval", tone: "warn" },
  approved_earlier: { text: "Approved earlier", tone: "det" },
  not_in_use: { text: "Not in use", tone: "gray" },
};
export const versionStatus = (status: string): StatusPill => own(STATUS, status) ?? { text: "Standing not known", tone: "gray" };

export const THRESHOLD_NOT_RECORDED = "threshold not recorded";

/**
 * A threshold-dependent CV metric at the model's locked threshold: the fold mean the API computed there, or the CV
 * aggregate when the threshold is the default 0.5 (where the aggregate already holds it). With no recorded threshold the
 * aggregate is not passed off as "at the threshold": THRESHOLD_NOT_RECORDED. Null when there is nothing to show.
 */
export function atLockedThreshold(item: ModelVersionItemLike, key: string): number | typeof THRESHOLD_NOT_RECORDED | null {
  const at = item.cv_at_locked_threshold ?? {};
  if (Object.hasOwn(at, key) && finite(at[key])) return at[key];
  const cv = item.cv ?? {};
  if (!Object.hasOwn(cv, key) || !finite(cv[key])) return null;
  const threshold = item.decision_threshold;
  if (threshold === 0.5) return cv[key];
  return threshold === null || threshold === undefined ? THRESHOLD_NOT_RECORDED : null;
}
const rate = (value: number | string | null): string | null => (value === null ? null : typeof value === "string" ? value : cardNumber(value));

/** One row per model version, in the API's order (newest first). Only cross-validation numbers; never a final test value. */
export function versionRows(projectId: string, items: readonly ModelVersionItemLike[], when: (iso: string) => string): VersionRow[] {
  return items.flatMap((item) => {
    const href = projectHref(projectId, "models", item.id);
    if (!href) return [];
    const metric = item.selection_metric ?? null;
    const cv = item.cv ?? {};
    const main = metric && Object.hasOwn(cv, metric) && finite(cv[metric]) ? `${metricName(metric)} ${cardNumber(cv[metric])}` : "—";
    const precision = atLockedThreshold(item, "precision");
    const recall = atLockedThreshold(item, "recall");
    const note = item.status === "in_use"
      ? (item.approved_for_scoring ? "approved for scoring" : "no approval recorded")
      : item.status === "waiting_for_approval"
        ? "a suggestion to put it in use waits for a person in the Inbox"
        : item.status === "approved_earlier" && item.last_approved_at ? `last approved ${when(item.last_approved_at)}` : null;
    return [{
      id: item.id, model: plainText(modelName(plainText(item.version, 40)), 60), href, experimentId: item.experiment_id, created: item.created_at,
      statusKey: item.status, status: versionStatus(item.status), note, main,
      precision: rate(precision), recall: rate(recall),
    }];
  });
}

export const VERSIONS_NOTE =
  "Numbers are cross-validation on the training rows, never the final test. Precision and recall are at each model's locked threshold, averaged over the folds that threshold was chosen on, so they can look a little better than on new data.";

// --- Saved file (GET /v1/model-versions/{id}) --------------------------------------------------

export type ArtifactLike = { role: string; size_bytes: number };
export type SavedFileView =
  | { state: "none"; text: string }
  | { state: "saved"; format: string; formatDetail: string; tone: "ok" | "warn"; size: string | null; includes: string[] };

export function fileSize(bytes: number): string {
  if (!finite(bytes) || bytes < 0) return "—";
  if (bytes < 1024) return `${bytes} bytes`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
}

const ROLE_WORDS: Record<string, string> = {
  model: "the trained model",
  preprocessor: "the steps that prepare each column",
  feature_manifest: "the list of columns new data must have",
};

/**
 * The saved file in plain words. The safe format always holds the model, its preparation steps and the column list (and,
 * for a yes/no model, its locked threshold); an old-format model holds what its stored parts are. Size counts the parts
 * scoring opens (the model and, for old models, the separate preparation part).
 */
export function savedFileView(mv: { saved_format?: string | null; artifacts?: readonly ArtifactLike[]; metrics?: { decision_threshold?: number | null } | null }): SavedFileView {
  const parts = (mv.artifacts ?? []).filter((a) => Object.hasOwn(ROLE_WORDS, a.role));
  if (!mv.saved_format) return { state: "none", text: "No saved model file is recorded for this model." };
  // The safe format is one file that already holds the preparation steps: its size is the model part's. An old-format
  // model keeps the preparation steps as a separate part, which scoring opens too.
  const scored = parts.filter((a) => a.role === "model" || (mv.saved_format !== "package_v1" && a.role === "preprocessor"));
  const size = scored.length ? fileSize(scored.reduce((total, a) => total + (finite(a.size_bytes) ? a.size_bytes : 0), 0)) : null;
  const threshold = mv.metrics?.decision_threshold;
  if (mv.saved_format === "package_v1") {
    return {
      state: "saved", tone: "ok", size,
      format: "Safe format",
      formatDetail: "Each part is opened by a loader that accepts only a fixed list of known types, so opening the file cannot run code hidden in it.",
      includes: [ROLE_WORDS.model, ROLE_WORDS.preprocessor, ROLE_WORDS.feature_manifest, ...(finite(threshold) ? [`the threshold ${cardNumber(threshold)}`] : [])],
    };
  }
  if (mv.saved_format === "legacy_quarantined") {
    return {
      state: "saved", tone: "warn", size,
      format: "Old format",
      formatDetail: "Saved before the safe format existed, in a format that can run code when it is opened. Scoring with it, and approving it for scoring, are refused unless the server is set up to allow old models; train it again to use it normally.",
      includes: parts.map((a) => ROLE_WORDS[a.role]),
    };
  }
  return { state: "none", text: "The saved file is in a format this page does not recognise." };
}

// --- File checks (ModelVersionResourceRead.package_verification) ------------------------------

export type CheckLike = { check: string; passed: boolean };
export type VerificationLike = { status: string; checked_at?: string | null; rows_compared?: number | null; checks?: readonly CheckLike[] };
export type FileChecksView =
  | { state: "verified"; when: string | null; items: Array<{ key: string; passed: boolean; text: string }>; allPassed: boolean }
  | { state: "not_recorded" | "not_applicable" | "none"; text: string };

const CHECK_WORDS: Record<string, (passed: boolean, rows: number | null) => string> = {
  opens: (passed) => (passed ? "It opens with DCLab's safe loader." : "It did not open with DCLab's safe loader."),
  digests: (passed) => (passed
    ? "Every part matched the fingerprint recorded when it was saved."
    : "A part did not match the fingerprint recorded when it was saved."),
  same_predictions: (passed, rows) => `${rows === null ? "Sample" : count(rows)} training rows score ${passed ? "exactly the same" : "differently"} with the saved file as with the trained model.`,
};
export const RECHECKED = "Each time this model scores a file, DCLab checks again that the saved file is unchanged before using it.";

/** The checks DCLab ran on the saved file when it built it, in fixed sentences by check code (the API's own texts are not shown). */
export function fileChecksView(v: VerificationLike | null | undefined, when: (iso: string) => string): FileChecksView {
  if (!v) return { state: "none", text: "No saved model file is recorded, so there is nothing to check." };
  if (v.status === "not_recorded") return { state: "not_recorded", text: "Not recorded: this model was saved before the file checks were kept." };
  if (v.status === "not_applicable") return { state: "not_applicable", text: "The file checks do not apply: this model is not saved in the safe format." };
  if (v.status !== "verified") return { state: "none", text: "The file checks are in a state this page does not recognise." };
  const rows = finite(v.rows_compared) ? v.rows_compared : null;
  const items = (v.checks ?? []).flatMap((c) => {
    const words = own(CHECK_WORDS, c.check);
    return words ? [{ key: c.check, passed: c.passed === true, text: words(c.passed === true, rows) }] : [];
  });
  return { state: "verified", when: v.checked_at ? when(v.checked_at) : null, items, allPassed: items.length > 0 && items.every((i) => i.passed) };
}

// --- Columns new data must have (GET /v1/model-versions/{id}/feature-contract) -----------------

export type ContractColumnLike = { name: string; role?: string | null; training_dtype?: string | null; required?: boolean; is_date?: boolean };
export type ContractLike = { status: string; column_count: number; columns: readonly ContractColumnLike[]; target_column?: string | null; unknown_columns?: string | null; unseen_categories?: string | null };
export type ColumnsView = { namesOnly: boolean; count: number; rows: Array<{ key: string; name: string; kind: string; required: string }>; notes: string[] };

/** A column's kind in plain words, from its role, date flag or training type. */
export function columnKind(c: ContractColumnLike): string {
  if (c.is_date) return "date";
  if (c.role === "categorical") return "category";
  if (c.role === "numeric") return "number";
  const dtype = (c.training_dtype ?? "").toLowerCase();
  if (!dtype) return "not recorded";
  if (dtype.startsWith("bool")) return "yes / no";
  if (dtype.startsWith("datetime")) return "date";
  if (/^u?int|^float/.test(dtype)) return "number";
  if (dtype === "category") return "category";
  return "text";
}

export function columnsView(c: ContractLike): ColumnsView {
  const namesOnly = c.status !== "available";
  const rows = c.columns.map((col, i) => ({
    key: `${i}-${col.name}`, name: plainText(col.name, 80), kind: namesOnly ? "not recorded" : columnKind(col), required: col.required === false ? "optional" : "needed",
  }));
  const notes: string[] = [];
  if (namesOnly) notes.push("This model was saved before column types were recorded, so only the names are listed.");
  notes.push("A file that lacks one of these columns is not scored, and the result names the missing columns.");
  if (c.unknown_columns === "ignored_and_listed") notes.push("Extra columns are fine: they are ignored and listed after scoring.");
  if (c.target_column) notes.push(`The column ${plainText(c.target_column, 80)} is what the model predicts; it is ignored if a file has it.`);
  return { namesOnly, count: finite(c.column_count) ? c.column_count : rows.length, rows, notes };
}

/** The column list read's refusals in fixed sentences (never the server message). */
export function contractError(error: unknown): string {
  const status = (error as { status?: unknown } | null)?.status;
  const { code } = envelope((error as { body?: unknown } | null)?.body);
  if (code === "feature_contract_unavailable" || status === 409) return "The list of columns is not available for this model: no readable column list was saved with it.";
  if (status === 404 || status === 403) return "This model is not in the active workspace, or you cannot see it.";
  return "The list of columns could not be loaded. Reload the page to try again.";
}

// --- Known limits (model card) -----------------------------------------------------------------

/**
 * Limits that hold for every model DCLab builds, worded from what the reads say: the column list (count, unseen
 * categories) when it was read, the kind of answer from the card. Nothing here claims a check that was not run.
 */
export function knownLimits(input: { taskType: string | null | undefined; contract: Pick<ContractLike, "status" | "column_count" | "unseen_categories"> | null }): string[] {
  const c = input.contract;
  const lines = [
    c && c.status === "available" && finite(c.column_count)
      ? `New files must have the same ${count(c.column_count)} ${c.column_count === 1 ? "column" : "columns"} it was trained on (listed under Saved file & checks); extra columns are ignored.`
      : "New files must have the same columns it was trained on; extra columns are ignored.",
  ];
  if (c?.unseen_categories === "encoded_as_unknown") {
    lines.push("It has never seen category values that were not in the training file (for example a new plan type). Such a value is treated as unknown: the row still gets a score, but the model has no experience with it.");
  }
  if (input.taskType === "binary") lines.push("Scores rank rows from most to least likely; they are not exact chances.");
  lines.push("It learned from the rows of its training file. When new data changes, it can do worse: Monitoring shows when new inputs look different from training.");
  return lines;
}

// --- Final test (model card final_evaluation) --------------------------------------------------

export type ConfusionLike = { tp: number; fp: number; fn: number; tn: number; rows?: number };
export type ConfusionView = { tp: string; fp: string; fn: string; tn: string; rows: string; lines: string[] };

/**
 * The 2x2 counts of the single final evaluation at the locked threshold, with the sentences built from them. Null when the
 * API sent none (multiclass, regression, older runs, and always for connected tools) or the counts are not whole numbers.
 */
export function confusionView(c: ConfusionLike | null | undefined, threshold: number | null | undefined): ConfusionView | null {
  if (!c) return null;
  const cells = [c.tp, c.fp, c.fn, c.tn];
  if (!cells.every((n) => Number.isInteger(n) && n >= 0)) return null;
  const rows = c.tp + c.fp + c.fn + c.tn;
  if (rows === 0) return null;
  const flagged = c.tp + c.fp;
  const positives = c.tp + c.fn;
  const at = finite(threshold) ? ` at the threshold ${cardNumber(threshold)}` : "";
  const lines = [`It flagged ${count(flagged)} of ${count(rows)} rows (${percent(flagged, rows)})${at}.`];
  if (positives > 0) lines.push(`It caught ${count(c.tp)} of the ${count(positives)} real positives: recall ${cardNumber(c.tp / positives)}.`);
  if (flagged > 0) lines.push(`${count(c.tp)} of the ${count(flagged)} flagged rows were real positives: precision ${cardNumber(c.tp / flagged)}.`);
  return { tp: count(c.tp), fp: count(c.fp), fn: count(c.fn), tn: count(c.tn), rows: count(rows), lines };
}

export type FinalTestCardLike = {
  cv: { metric?: string | null; mean?: number | null; folds?: number | null; at_locked_threshold?: Record<string, number> };
  split: { evaluation_rows?: number | null; evaluation_split_strategy?: string | null };
  final_evaluation: { status: string; metric?: string | null; value?: number | null; metrics?: Record<string, number>; decision_threshold?: number | null; confusion?: ConfusionLike | null };
};
export type FinalTestView =
  | { state: "reported"; compare: Array<{ key: string; label: string; cv: string; final: string }>; rows: string | null; setAside: string | null; confusion: ConfusionView | null }
  | { state: "withheld" | "missing" };

/**
 * The final test beside cross-validation: the main score against the CV mean, and each threshold metric the run also
 * measured at the locked threshold on the folds. Rows are the count of the final test rows; no dates are shown.
 */
export function finalTestView(card: FinalTestCardLike): FinalTestView {
  const f = card.final_evaluation;
  if (f.status === "withheld") return { state: "withheld" };
  const metrics = f.metrics ?? {};
  const value = finite(f.value) ? f.value : f.metric && Object.hasOwn(metrics, f.metric) && finite(metrics[f.metric]) ? metrics[f.metric] : null;
  if (f.status !== "reported" || (value === null && !Object.values(metrics).some(finite))) return { state: "missing" };
  const compare: Array<{ key: string; label: string; cv: string; final: string }> = [];
  const at = card.cv.at_locked_threshold ?? {};
  if (f.metric && value !== null) {
    // A threshold-dependent main score (precision, recall, ...) is compared with its fold mean at the locked threshold.
    const atMain = Object.hasOwn(at, f.metric) && finite(at[f.metric]) ? at[f.metric] : null;
    const cv = atMain !== null ? cardNumber(atMain) : card.cv.metric === f.metric && finite(card.cv.mean) ? cardNumber(card.cv.mean) : "—";
    compare.push({ key: f.metric, label: atMain !== null ? `${metricName(f.metric)} (at the locked threshold)` : metricName(f.metric), cv, final: cardNumber(value) });
  }
  for (const key of Object.keys(metrics).sort()) {
    if (key === f.metric || key === "decision_threshold" || !finite(metrics[key]) || !Object.hasOwn(at, key) || !finite(at[key])) continue;
    compare.push({ key, label: `${metricName(key)} (at the locked threshold)`, cv: cardNumber(at[key]), final: cardNumber(metrics[key]) });
  }
  const rowsValue = f.confusion && Number.isInteger(f.confusion.rows) ? f.confusion.rows : card.split.evaluation_rows;
  return {
    state: "reported", compare,
    rows: finite(rowsValue) ? count(rowsValue) : null,
    setAside: card.split.evaluation_split_strategy ? plainText(card.split.evaluation_split_strategy.replaceAll("_", " "), 60) : null,
    confusion: confusionView(f.confusion, f.decision_threshold),
  };
}
