/**
 * Predictions screen, v7 parity (UI-D): the header tiles and "This week's list" as pure view-model logic, so it runs under
 * `npm run test:components`. Every value restates a field of `GET /v1/predictions/{id}` or `GET /v1/predictions/{id}/rows`.
 * Keys, labels and reasons are cells of the customer's own file: capped plain text, never markup or links. Reasons name what
 * the model used for a score, not causes. The API's notes and messages are never passed through.
 */
import { plainText } from "./command-search.ts";
import { cardNumber } from "./studio-card.ts";
import { envelope } from "./studio-wizard.ts";

export const ROWS_PAGE = 50;
export const REASON_SHOWN = 200;
export const KEY_SHOWN = 80;

const finite = (value: unknown): value is number => typeof value === "number" && Number.isFinite(value);
const count = (value: number): string => value.toLocaleString("en-US");

export type FlaggedLike = { count: number; rows: number; share?: number | null; decision_threshold?: number | null };
export type ScoredLike = {
  id: string; status: string; rows_in?: number | null; rows_out?: number | null; completed_at?: string | null; created_at?: string;
  decision_threshold?: number | null; flagged?: FlaggedLike | null;
};

/** The list to show: the one asked for if it is a finished scoring here, else the newest finished one. Null = none finished. */
export function pickList<P extends ScoredLike>(scorings: readonly P[], requested: string | null | undefined): P | null {
  const done = scorings.filter((p) => p.status === "completed");
  const asked = requested ? done.find((p) => p.id === requested) : undefined;
  if (asked) return asked;
  return [...done].sort((a, b) => String(b.completed_at ?? b.created_at ?? "").localeCompare(String(a.completed_at ?? a.created_at ?? "")))[0] ?? null;
}

export type Tile = { value: string; label: string; hint: string };

/** "16 Oct 2026" with "at 15:38 UTC" in the hint. */
function lastScored(iso: string | null | undefined): Tile {
  const date = iso ? new Date(iso) : null;
  if (!iso || !date || Number.isNaN(date.getTime())) return { value: "—", label: "scored", hint: "when scored" };
  const day = date.toLocaleDateString("en-GB", { timeZone: "UTC", day: "numeric", month: "short", year: "numeric" });
  const time = date.toLocaleTimeString("en-GB", { timeZone: "UTC", hour: "2-digit", minute: "2-digit", hour12: false });
  return { value: day, label: "scored", hint: `at ${time} UTC, when scored` };
}

export const WEEK_MS = 7 * 24 * 60 * 60 * 1000;
/** "This week's list" only for a scoring that finished within the last 7 days; an older one is the "Newest list". */
export function listHeading(completedAt: string | null | undefined, now: number): string {
  const at = completedAt ? new Date(completedAt).getTime() : Number.NaN;
  return Number.isFinite(at) && now - at <= WEEK_MS && at <= now + 60_000 ? "This week's list" : "Newest list";
}
export const LIST_SOURCE = "Chosen from the files you scored in this browser tab and the weeks the schedule scored; other scorings are not listed yet.";
export type Tiles = { flagged: Tile; scored: Tile; last: Tile };

/** Header tiles of one finished scoring. A yes/no model's flagged count comes only from the read's own `flagged`. */
export function predictionTiles(p: ScoredLike): Tiles {
  const threshold = p.flagged?.decision_threshold ?? p.decision_threshold;
  let flagged: Tile;
  if (p.flagged && Number.isInteger(p.flagged.count) && p.flagged.count >= 0) {
    const share = finite(p.flagged.share) ? p.flagged.share : p.flagged.rows > 0 ? p.flagged.count / p.flagged.rows : null;
    flagged = {
      value: `${count(p.flagged.count)}${share === null ? "" : ` (${(share * 100).toFixed(1)}%)`}`,
      label: "rows flagged",
      hint: finite(threshold) ? `score at or above the threshold ${cardNumber(threshold)}` : "at the model's locked threshold",
    };
  } else if (finite(p.decision_threshold)) {
    flagged = { value: "—", label: "rows flagged", hint: "not recorded for this scoring (it finished before counts were kept)" };
  } else {
    flagged = { value: "—", label: "rows flagged", hint: "does not apply: this model gives a class or a number, not a yes or no" };
  }
  const rows = finite(p.rows_out) ? p.rows_out : null;
  return {
    flagged,
    scored: { value: rows === null ? "—" : count(rows), label: "rows scored", hint: finite(p.rows_in) ? `of ${count(p.rows_in)} rows in the file` : "rows in the predictions file" },
    last: lastScored(p.completed_at),
  };
}

// --- This week's list (GET /v1/predictions/{id}/rows) ------------------------------------------

export type RowLike = { row_number: number; entity?: string | null; probability?: number | null; flagged?: boolean | null; label?: string | null; prediction?: number | null; reasons?: readonly string[] };
export type ReasonsLike = { available: boolean; method?: string | null; rows_explained?: number };
export type RowsPageLike = {
  task_type: string; order: string; entity_column?: string | null; decision_threshold?: number | null; rows_total: number; rows_reachable: number;
  items: readonly RowLike[]; reasons?: ReasonsLike | null;
};
export type ListRow = { key: string; row: string; entity: string; score: string; flagged: string; answer: string; reasons: string[] };
export type ListColumns = { entity: string | null; score: boolean; flagged: boolean; label: boolean; prediction: boolean; reasons: boolean };

/** Which columns the list has, from the kind of model and whether any row carries a key or reasons. */
export function listColumns(first: RowsPageLike, rows: readonly RowLike[]): ListColumns {
  const entity = first.entity_column ? plainText(first.entity_column, 60) : rows.some((r) => r.entity) ? "Key" : null;
  return {
    entity,
    score: first.task_type !== "regression",
    flagged: first.task_type === "binary",
    label: first.task_type === "multiclass",
    prediction: first.task_type === "regression",
    reasons: rows.some((r) => (r.reasons ?? []).some((x) => typeof x === "string" && x.trim())),
  };
}

const score = (value: number | null | undefined): string => (finite(value) ? value.toFixed(2) : "—");

export function listRows(rows: readonly RowLike[]): ListRow[] {
  return rows.map((r) => ({
    key: String(r.row_number),
    row: Number.isInteger(r.row_number) ? count(r.row_number) : "—",
    entity: r.entity ? plainText(r.entity, KEY_SHOWN) : "—",
    score: score(r.probability),
    flagged: r.flagged === true ? "yes" : r.flagged === false ? "no" : "—",
    answer: r.label ? plainText(r.label, KEY_SHOWN) : finite(r.prediction) ? cardNumber(r.prediction) : "—",
    reasons: (r.reasons ?? []).filter((x) => typeof x === "string" && x.trim()).slice(0, 3).map((x) => plainText(x, REASON_SHOWN)),
  }));
}

const METHOD_WORDS: Record<string, string> = {
  tree_shap: "Reasons come from the model's own share of each column in a row's score, compared with its average row.",
  linear_contributions: "Reasons come from each column's weight times its value, compared with the training average.",
  occlusion: "Reasons come from setting each column to a typical training value in turn and seeing how the score moves (approximate).",
};
export const NOT_CAUSES = "Reasons name what the model used for a score, not causes: they say which inputs moved the score up or down compared with a typical row.";

/** The sentences under the list, built from the page's numbers. */
export function listNotes(first: RowsPageLike, shown: number): string[] {
  const notes = [
    `Showing ${count(shown)} of ${count(first.rows_total)} rows, ${first.order === "row" ? "in file order" : "highest score first"}.`,
  ];
  if (first.rows_reachable < first.rows_total) notes.push(`Only the first ${count(first.rows_reachable)} rows in this order can be listed here; download the file for every row.`);
  if (first.task_type === "binary" && finite(first.decision_threshold)) notes.push(`Flagged means a score of ${cardNumber(first.decision_threshold)} or more (the model's locked threshold).`);
  if (first.task_type !== "regression") notes.push("Scores rank rows from most to least likely; they are not exact chances.");
  const r = first.reasons;
  if (!r || !r.available) {
    notes.push("This scoring has no reasons in its file. The scores are not affected.");
  } else {
    notes.push(NOT_CAUSES);
    const method = r.method && Object.hasOwn(METHOD_WORDS, r.method) ? METHOD_WORDS[r.method] : null;
    if (method) notes.push(method);
    if (finite(r.rows_explained) && r.rows_explained < first.rows_total) notes.push(`Reasons are filled for the first ${count(r.rows_explained)} rows of the file only.`);
  }
  return notes;
}

/** `restart`: the page cursor is no longer valid, so the list starts again from the top. */
export type ListError = { text: string; retry: boolean; restart?: boolean };
const BY_CODE: Record<string, ListError> = {
  prediction_output_changed: { text: "The saved predictions file changed since it was made; it cannot be listed.", retry: false },
  prediction_rows_unavailable: { text: "This predictions file cannot be listed here; download it.", retry: false },
  invalid_cursor: { text: "The list moved on. Start again from the top.", retry: false, restart: true },
  prediction_rows_too_large: { text: "This file is too large to list here (more than 200,000 rows or 64 MB). Download it to see every row.", retry: false },
  rows_busy: { text: "Too many lists are being read right now. Wait a moment, then try again.", retry: true },
  prediction_not_ready: { text: "The list appears once this scoring has finished.", retry: true },
};

/** The rows read's refusals in fixed sentences, by API code, then status (never the server message). */
export function listError(error: unknown): ListError {
  const status = (error as { status?: unknown } | null)?.status;
  const { code } = envelope((error as { body?: unknown } | null)?.body);
  const known = code && Object.hasOwn(BY_CODE, code) ? BY_CODE[code] : undefined;
  if (known) return known;
  if (status === 429) return BY_CODE.rows_busy;
  if (status === 404) return { text: "This scoring is not in the active workspace, or you cannot see it.", retry: false };
  if (status === 403) return { text: "You cannot read this list in this workspace.", retry: false };
  if (status === 409) return { text: "This list cannot be shown here. Download the file to see every row.", retry: false };
  if (status === 422 || status === 400) return BY_CODE.invalid_cursor;
  return { text: "The list could not be loaded. Check your connection and try again.", retry: true };
}

const DOWNLOAD_BY_CODE: Record<string, string> = {
  prediction_not_ready: "The file can be downloaded once this scoring has finished.",
  prediction_output_changed: "The saved predictions file changed since it was made; it cannot be downloaded.",
};
/** The download's refusals in fixed sentences by API code, then status (never the server message). */
export function downloadError(error: unknown): string {
  const status = (error as { status?: unknown } | null)?.status;
  const { code } = envelope((error as { body?: unknown } | null)?.body);
  if (code && Object.hasOwn(DOWNLOAD_BY_CODE, code)) return DOWNLOAD_BY_CODE[code];
  if (status === 404) return "This scoring is not in the active workspace, or you cannot see it.";
  if (status === 403) return "You cannot download this file in this workspace.";
  if (status === 429) return "Too many downloads at once. Wait a moment, then try again.";
  return "The file could not be downloaded. Check your connection and try again.";
}
