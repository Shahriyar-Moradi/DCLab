/**
 * D3b: the Data and Goal views of the D3a reads, pure so they run under `npm run test:components`.
 * - "Target over time": the target rate per month of the TRAINING rows; the final-test months are a sealed band with no
 *   line, point or rate until the final test has run (then drawn apart, "Disclosed after the final test").
 * - The upload log (accepted and rejected uploads; metadata only), the checks run on upload, and "Who decided".
 * Every value comes from an API field; file names, column names, values and reasons are untrusted plain text. Lookups use
 * own keys only (`Object.hasOwn`), so a value such as `__proto__` falls back to the plain default.
 */
import { isUuid, plainText } from "./command-search.ts";
import { designKind, metricInfo } from "./studio-goal.ts";

type Clean = (value: unknown, max?: number) => string;
const clean: Clean = (value, max = 120) => plainText(value, max);
const own = <T>(table: Record<string, T>, key: unknown): T | null => (typeof key === "string" && Object.hasOwn(table, key) ? table[key] : null);
const pct = (share: number) => (share > 0 && share < 0.001 ? "<0.1%" : `${(share * 100).toFixed(share * 100 >= 10 ? 0 : 1)}%`);
const count = (n: number) => n.toLocaleString("en-GB");
const isNum = (v: unknown): v is number => typeof v === "number" && Number.isFinite(v);

// --- fixed error sentences (never the server's message) ---------------------------------------------------

const ERROR_BY_CODE: Record<string, string> = {
  not_found: "This is not in the active workspace, or you cannot see it.",
  forbidden: "Your role cannot read this in the active workspace.",
  invalid_cursor: "The list could not continue from where it stopped. Reload the page to start again.",
  rate_limited: "Too many requests right now. Wait a minute and reload the page.",
  unauthorized: "Your session has ended. Sign in again.",
};
/** By status only when the body has no known code; a 400 is never assumed to be the cursor (only its code says so). */
const ERROR_BY_STATUS: Record<string, string> = {
  "401": ERROR_BY_CODE.unauthorized, "403": ERROR_BY_CODE.forbidden, "404": ERROR_BY_CODE.not_found, "429": ERROR_BY_CODE.rate_limited,
};

/** A read failure in a fixed plain sentence, keyed by the API code, else the HTTP status; the server's own message is never shown. */
export function readErrorText(error: unknown, what: string): string {
  const body = (error as { body?: { error?: { code?: unknown } } } | null)?.body;
  const code = typeof body?.error?.code === "string" ? body.error.code.toLowerCase() : "";
  const status = (error as { status?: unknown } | null)?.status;
  return own(ERROR_BY_CODE, code) ?? own(ERROR_BY_STATUS, String(status ?? "")) ?? `Could not load ${what}. Reload the page to try again.`;
}

// --- months (UTC) -------------------------------------------------------------------------------------------

const MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];
/** `YYYY-MM` -> month index (year * 12 + month - 1); null when it is not a calendar month. */
export function monthIndex(value: unknown): number | null {
  const m = typeof value === "string" ? /^(\d{4})-(\d{2})$/.exec(value) : null;
  if (!m) return null;
  const month = Number(m[2]);
  return month >= 1 && month <= 12 ? Number(m[1]) * 12 + month - 1 : null;
}
export const monthLabel = (index: number) => `${MONTHS[index % 12]} ${Math.floor(index / 12)}`;
const spanText = (a: number, b: number) => (a === b ? monthLabel(a) : `${monthLabel(a)} – ${monthLabel(b)}`);

// --- "Target over time" --------------------------------------------------------------------------------------

export type BucketLike = { month: string; part: string; rows: number; rate: number };
export type FinalTestLike = {
  layout: string; start_month?: string | null; end_month?: string | null; row_count: number; sealed: boolean;
  rate?: number | null; disclosed_after_final_test: boolean;
};
export type TargetByPeriodLike = {
  status: string; target_column?: string | null; time_column?: string | null; training_rate?: number | null;
  buckets: BucketLike[]; truncated: boolean; final_test?: FinalTestLike | null;
};

export const SEALED_LABEL = "Final test — sealed until the final test runs";
export const DISCLOSED_LABEL = "Disclosed after the final test";
export const MONTHS_UTC = "Months are calendar months in UTC.";
export const TRUNCATED_NOTE = "Newest 60 months shown.";
const MIXED_SEALED = "Final-test rows are spread over all months and stay sealed until the final test runs, so no final-test rate is shown.";

/** One plain sentence per status that draws no chart (only `computed` draws one). */
const STATUS_SENTENCE: Record<string, string> = {
  no_split_plan: "No test design uses this data version yet, so nothing is counted. The chart can appear after a run on this version sets aside the final test set.",
  not_binary: "This chart is for yes/no answers only, and what this project predicts is not a yes/no answer.",
  no_time_column: "There is no date column to put the rows in months, so there is no chart.",
  time_not_dates: "The column that orders the rows in time does not hold dates DCLab can read, so there is no chart.",
  unavailable: "DCLab could not read the rows of this test design (the file may be too large, or its row map did not match), so no rate is shown. Nothing was guessed.",
};
export const UNKNOWN_STATUS_SENTENCE = "This chart cannot be shown right now.";
export const boundaryNote = (month: number) => `${monthLabel(month)} has both training rows and final-test rows; only its training rows are counted.`;

export type ChartPoint = { month: number; rate: number; rows: number };
export type ChurnTableRow = { key: string; month: string; part: string; rows: string; rate: string };
export type ChurnView =
  | { kind: "sentence"; text: string }
  | {
    kind: "chart";
    /** Every month on the axis, oldest first (gaps break the line). */
    months: number[];
    training: ChartPoint[];
    /** Final-test months drawn apart; empty while sealed. */
    disclosed: ChartPoint[];
    /** The sealed span: months and a row count, never a rate. */
    band: { start: number; end: number; rows: number } | null;
    max: number;
    summary: string;
    notes: string[];
    table: ChurnTableRow[];
  };

/**
 * The chart model. Training buckets draw the line. While `sealed`, final-test buckets and any final-test rate are ignored
 * even if present (defence in depth): an `after_training` span is a shaded band with its row count only; `mixed` has no band.
 */
export function churnView(read: TargetByPeriodLike): ChurnView {
  if (read.status !== "computed") return { kind: "sentence", text: own(STATUS_SENTENCE, read.status) ?? UNKNOWN_STATUS_SENTENCE };
  const ft = read.final_test ?? null;
  const sealed = ft ? ft.sealed !== false || ft.disclosed_after_final_test !== true : true;
  const toPoint = (b: BucketLike): ChartPoint | null => {
    const month = monthIndex(b.month);
    return month === null || !isNum(b.rate) || b.rate < 0 || b.rate > 1 || !isNum(b.rows) ? null : { month, rate: b.rate, rows: b.rows };
  };
  const training = read.buckets.filter((b) => b.part === "training").map(toPoint).filter((p): p is ChartPoint => p !== null);
  const disclosed = sealed ? [] : read.buckets.filter((b) => b.part === "final_test").map(toPoint).filter((p): p is ChartPoint => p !== null);
  const start = ft ? monthIndex(ft.start_month) : null;
  const end = ft ? monthIndex(ft.end_month) : null;
  const band = ft && sealed && ft.layout === "after_training" && start !== null && end !== null && end >= start && isNum(ft.row_count)
    ? { start, end, rows: ft.row_count } : null;
  if (!training.length && !disclosed.length) {
    return { kind: "sentence", text: "No training row has both a readable date and a yes/no answer, so there is nothing to draw." };
  }
  const all = [...training, ...disclosed].map((p) => p.month).concat(band ? [band.start, band.end] : []);
  const lo = Math.min(...all);
  const hi = Math.max(...all);
  const months = Array.from({ length: Math.min(hi - lo + 1, 240) }, (_, i) => lo + i);
  const top = Math.max(...training.map((p) => p.rate), ...disclosed.map((p) => p.rate), 0.01);
  const max = Math.min(1, Math.ceil(top * 20) / 20 + (Math.ceil(top * 20) / 20 === top ? 0.05 : 0));
  const target = read.target_column ? clean(read.target_column, 60) : "the target";
  const lowest = training.reduce<ChartPoint | null>((a, p) => (!a || p.rate < a.rate ? p : a), null);
  const highest = training.reduce<ChartPoint | null>((a, p) => (!a || p.rate > a.rate ? p : a), null);
  const parts = [
    training.length
      ? `Share of training rows where ${target} is the answer treated as yes, by month (UTC), ${spanText(training[0].month, training[training.length - 1].month)}: lowest ${pct(lowest!.rate)} in ${monthLabel(lowest!.month)}, highest ${pct(highest!.rate)} in ${monthLabel(highest!.month)}.`
      : `No training month has a rate for ${target}.`,
  ];
  const notes: string[] = [MONTHS_UTC];
  if (band) parts.push(`${spanText(band.start, band.end)}: ${SEALED_LABEL} (${count(band.rows)} rows); no rate is shown for these months.`);
  // A month cut by the time-ordered split: its training rows are a point; its final-test rows stay inside the band.
  if (band && training.some((p) => p.month === band.start)) notes.push(boundaryNote(band.start));
  else if (ft && sealed && ft.layout === "after_training" && ft.row_count > 0) notes.push(`The final test set (${count(ft.row_count)} rows) is sealed until the final test runs; its months could not be read.`);
  if (ft && sealed && ft.layout === "mixed" && ft.row_count > 0) notes.push(MIXED_SEALED);
  if (!sealed && ft) {
    if (disclosed.length) parts.push(`${DISCLOSED_LABEL}: final-test months ${spanText(disclosed[0].month, disclosed[disclosed.length - 1].month)} are drawn apart.`);
    if (ft.layout === "mixed") notes.push("Final-test rows are spread over all months, so they are not drawn by month.");
    if (isNum(ft.rate)) notes.push(`${DISCLOSED_LABEL}: ${pct(ft.rate)} of the ${count(ft.row_count)} final-test rows had ${target} as the answer treated as yes. They were never used to train or choose the model.`);
  }
  if (ft && ft.row_count === 0) notes.push("This test design has no final test set.");
  if (read.truncated) notes.push(TRUNCATED_NOTE);
  const rowsAt: Array<ChurnTableRow & { at: number }> = [
    ...training.map((p) => ({ at: p.month, key: `t${p.month}`, month: monthLabel(p.month), part: "Training rows", rows: count(p.rows), rate: pct(p.rate) })),
    ...disclosed.map((p) => ({ at: p.month + 0.5, key: `d${p.month}`, month: monthLabel(p.month), part: DISCLOSED_LABEL, rows: count(p.rows), rate: pct(p.rate) })),
    ...(band ? [{ at: band.start + 0.5, key: "band", month: spanText(band.start, band.end), part: SEALED_LABEL, rows: count(band.rows), rate: "sealed" }] : []),
  ];
  const table: ChurnTableRow[] = rowsAt.sort((a, b) => a.at - b.at).map((r) => ({ key: r.key, month: r.month, part: r.part, rows: r.rows, rate: r.rate }));
  return { kind: "chart", months, training, disclosed, band, max, summary: parts.join(" "), notes, table };
}

// --- upload log -------------------------------------------------------------------------------------------

export type UploadAttemptLike = {
  id: string; created_at: string; file_name: string; size_bytes?: number | null; outcome: string; reason_code?: string | null;
  reason?: string | null; uploaded_by?: { kind: string; is_you?: boolean } | null; dataset_id?: string | null; source?: string | null;
};
export type UploadLogRow = {
  key: string; when: string; fileName: string; size: string; outcome: "accepted" | "rejected" | "unknown"; outcomeText: string;
  reason: string | null; by: string; datasetId: string | null; source: string;
};

/** Same sentences as the API's closed reason set; used only when a rejected row carries no `reason`. */
const REASON_WORDS: Record<string, string> = {
  too_large: "The file is larger than the upload limit.",
  storage_limit: "The workspace has reached its storage limit.",
  empty_file: "The file is empty.",
  unsafe_file_name: "The file name is not allowed.",
  unsupported_type: "This file type is not accepted.",
  content_mismatch: "The file content does not match its type.",
  unreadable_sheets: "The spreadsheet could not be read.",
  unsafe_content: "The file contains parts that are not allowed, such as macros or links.",
  unreadable_file: "The file could not be read as a table.",
};
const SOURCE_WORDS: Record<string, string> = { v1_api: "Studio, the SDK or a connected tool", labs: "The older Labs upload" };

export function sizeText(bytes: number | null | undefined): string {
  if (!isNum(bytes) || bytes < 0) return "Not known";
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 ** 2) return `${(bytes / 1024).toFixed(1)} KB`;
  if (bytes < 1024 ** 3) return `${(bytes / 1024 ** 2).toFixed(1)} MB`;
  return `${(bytes / 1024 ** 3).toFixed(1)} GB`;
}

export function uploadLogRows(items: UploadAttemptLike[], who: (by: UploadAttemptLike["uploaded_by"]) => string): UploadLogRow[] {
  return items.map((a) => {
    const outcome = a.outcome === "accepted" || a.outcome === "rejected" ? a.outcome : "unknown";
    const reason = outcome !== "rejected" ? null
      : (typeof a.reason === "string" && a.reason.trim() ? clean(a.reason, 200) : own(REASON_WORDS, a.reason_code) ?? "No reason was recorded.");
    return {
      key: a.id, when: a.created_at, fileName: clean(a.file_name, 120) || "(no name)", size: sizeText(a.size_bytes), outcome,
      outcomeText: outcome === "accepted" ? "added" : outcome === "rejected" ? "rejected" : "not known",
      reason, by: who(a.uploaded_by), datasetId: outcome === "accepted" && isUuid(a.dataset_id) ? a.dataset_id : null,
      source: own(SOURCE_WORDS, a.source) ?? "Not recorded",
    };
  });
}

/** The standing note under the log; the number comes from the read's `retention_days`. */
export function retentionText(days: unknown): string {
  const kept = isNum(days) && days > 0 && Number.isInteger(days) ? `for ${days} days` : "for a limited time";
  return `Upload attempts are kept ${kept}. Each one keeps the file name, size, outcome and reason, who uploaded it and when — never the file's contents or any row values.`;
}

// --- checked on upload --------------------------------------------------------------------------------------

export type UploadCheckLike = {
  code: string; status: string; count?: number | null; share?: number | null;
  columns: Array<{ name: string; count?: number | null; share?: number | null }>; columns_truncated?: boolean;
};
export type UploadChecksLike = { ran_at: string; row_count: number; checks: UploadCheckLike[] };
export type UploadCheckRow = { key: string; label: string; result: "ok" | "warn" | "unknown"; word: string; text: string };

const CHECK_LABEL: Record<string, string> = {
  duplicate_rows: "Duplicate rows", missing_values: "Missing values", date_parsing: "Dates", id_like_columns: "ID-like columns", constant_columns: "Constant columns",
};
const CHECK_OK: Record<string, string> = {
  duplicate_rows: "No row appears twice.",
  missing_values: "No cell is empty.",
  date_parsing: "Every column that looks like dates could be read as dates.",
  id_like_columns: "No column looks like an ID.",
  constant_columns: "No column has one value or none.",
};
/** ok / warn in plain words; a warning is something to look at, never a failure. */
export const CHECK_WORD = { ok: "ok", warn: "worth a look", unknown: "not known" } as const;

const MAX_NAMES = 6;
function names(c: UploadCheckLike, detail?: (col: UploadCheckLike["columns"][number]) => string | null): string {
  const shown = c.columns.slice(0, MAX_NAMES).map((col) => {
    const extra = detail?.(col);
    return `${clean(col.name, 40)}${extra ? ` (${extra})` : ""}`;
  });
  const more = (isNum(c.count) ? c.count : c.columns.length) - shown.length;
  const tail = more > 0 ? ` and ${more} more` : c.columns_truncated ? " and more not listed" : "";
  return shown.length ? `${shown.join(", ")}${tail}` : "";
}

function warnText(c: UploadCheckLike): string {
  const n = isNum(c.count) ? c.count : c.columns.length;
  const cols = (one: string, many: string) => `${count(n)} column${n === 1 ? ` ${one}` : `s ${many}`}`;
  switch (c.code) {
    case "duplicate_rows": return `${count(n)} row${n === 1 ? " is an exact copy" : "s are exact copies"} of another row${isNum(c.share) ? ` (${pct(c.share)} of rows)` : ""}.`;
    case "missing_values": {
      const list = names(c, (col) => (isNum(col.share) ? `${pct(col.share)} empty` : null));
      return `${isNum(c.share) ? `${pct(c.share)} of all cells are empty.` : "Some cells are empty."}${list ? ` Most empty: ${list}.` : ""}`;
    }
    case "date_parsing": {
      const list = names(c, (col) => (isNum(col.share) ? `${pct(col.share)} of the first 500 filled values could not be read` : null));
      return `${cols("looks like dates, but some values could not be read as dates", "look like dates, but some values could not be read as dates")}${list ? `: ${list}` : ""}.`;
    }
    case "id_like_columns": return `${cols("looks like an ID: almost every value is different", "look like IDs: almost every value is different")}${names(c) ? `: ${names(c)}` : ""}.`;
    case "constant_columns": return `${cols("has one value or none", "have one value or none")}${names(c) ? `: ${names(c)}` : ""}.`;
    default: return names(c) ? `Columns: ${names(c)}.` : "";
  }
}

export function uploadCheckRows(checks: UploadChecksLike): UploadCheckRow[] {
  return checks.checks.map((c) => {
    const result = c.status === "ok" || c.status === "warn" ? c.status : "unknown";
    return {
      key: c.code, label: own(CHECK_LABEL, c.code) ?? clean(c.code.replaceAll("_", " "), 40), result, word: CHECK_WORD[result],
      text: result === "ok" ? own(CHECK_OK, c.code) ?? "Nothing to report." : result === "warn" ? warnText(c) : "This check did not give a clear answer.",
    };
  });
}

const UTC_DAY = new Intl.DateTimeFormat("en-GB", { day: "numeric", month: "short", year: "numeric", hour: "2-digit", minute: "2-digit", timeZone: "UTC", hourCycle: "h23" });
/** "5 Oct 2026, 02:00 UTC"; "—" when it is not a valid time. */
export function utcText(iso: string | null | undefined): string {
  const t = new Date(iso ?? "");
  return Number.isNaN(t.getTime()) ? "—" : `${UTC_DAY.format(t)} UTC`;
}

export const NO_UPLOAD_CHECKS = "No checks were recorded on upload for this file: it was uploaded before these checks existed, its table could not be read, it has more than a million rows, or it was made by a run rather than uploaded.";

// --- who decided --------------------------------------------------------------------------------------------

export type GoalDecisionLike = {
  what: string; suggested_by?: string | null; confirmed_by?: string | null; when?: string | null; why?: string | null;
  why_is_default?: boolean; value?: string | null; decision_id?: string | null;
};
export type WhoDecidedRow = { key: string; what: string; value: string; suggested: string; confirmed: string; when: string; why: string; decisionId: string | null };

const WHAT_LABEL: Record<string, string> = { target: "What we predict", test_design: "Test design", metric: "Ranking metric", rule: "Business rule" };
const WHAT_ORDER = ["target", "test_design", "metric", "rule"];
const SUGGESTED_LABEL: Record<string, string> = { rules: "The rules", assistant: "The assistant", person: "A person", connected_tool: "A connected tool" };
const CONFIRMED_LABEL: Record<string, string> = { you: "You", person: "A person", connected_tool: "A connected tool" };
export const DEFAULT_WHY_MARK = "(DCLab default — no reason given)";

const DESIGN_WORDS: Record<string, string> = { time: "Time-ordered", group: "Grouped", random: "Random" };
/** The decision's value in words: metric codes and test-design codes through the goal helpers; else plain text. */
function valueText(what: string, value: string | null | undefined): string {
  if (typeof value !== "string" || !value.trim()) return "—";
  if (what === "metric") return metricInfo(value)?.label ?? clean(value, 200);
  if (what === "test_design") {
    if (value === "keep_rule_split") return "Kept the rules' test design";
    return own(DESIGN_WORDS, designKind(value)) ?? clean(value.replaceAll("_", " "), 200);
  }
  return clean(value, 200);
}

/**
 * The four rows in a fixed order (target, test design, ranking metric, business rule). An empty "Confirmed by" is
 * "Nobody was asked" only for the rules' own test design; every other unknown or empty value says "Not recorded".
 */
export function whoDecidedRows(items: GoalDecisionLike[]): WhoDecidedRow[] {
  const rank = (what: string) => { const i = WHAT_ORDER.indexOf(what); return i < 0 ? WHAT_ORDER.length : i; };
  return [...items].sort((a, b) => rank(a.what) - rank(b.what)).map((d, i) => {
    const why = typeof d.why === "string" && d.why.trim() ? clean(d.why, 500) : null;
    return {
      key: `${d.what}-${i}`,
      what: own(WHAT_LABEL, d.what) ?? clean(String(d.what).replaceAll("_", " "), 40),
      value: valueText(d.what, d.value),
      suggested: own(SUGGESTED_LABEL, d.suggested_by) ?? "Not recorded",
      confirmed: own(CONFIRMED_LABEL, d.confirmed_by)
        ?? (d.what === "test_design" && d.suggested_by === "rules" && d.confirmed_by == null ? "Nobody was asked" : "Not recorded"),
      when: d.when ? utcText(d.when) : "Not recorded",
      why: why ? (d.why_is_default ? `${why} ${DEFAULT_WHY_MARK}` : why) : d.why_is_default ? DEFAULT_WHY_MARK : "No reason recorded",
      decisionId: isUuid(d.decision_id) ? d.decision_id : null,
    };
  });
}
