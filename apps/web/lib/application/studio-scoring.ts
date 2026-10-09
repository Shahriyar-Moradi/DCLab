/** Score new data (P4.9-UI): pure view-model logic. Every sentence is built from API fields of the prediction read. */
import { isUuid, plainText } from "./command-search.ts";
import { modelName } from "./studio-names.ts";
import { envelope, mapWizardError, type PlainError } from "./studio-wizard.ts";

export type ContractView = {
  status: "passed" | "failed" | "warning" | "unknown";
  empty: string[];
  poorlyRead: string[];
  required: string[];
  requiredCount: number;
  missing: string[];
  missingCount: number;
  ignored: string[];
  ignoredCount: number;
  target: string | null;
  targetIgnored: boolean;
};

const names = (value: unknown): string[] => (Array.isArray(value) ? value.filter((v): v is string => typeof v === "string") : []);
const count = (value: unknown, fallback: number): number => (typeof value === "number" && Number.isFinite(value) ? value : fallback);

/** Reads the API's `contract_check` object defensively; null when the run has not checked the file yet. */
export function contractView(raw: Record<string, unknown> | null | undefined): ContractView | null {
  if (!raw) return null;
  const required = names(raw.required_columns);
  const missing = names(raw.missing_columns);
  const ignored = names(raw.ignored_columns);
  const rates = raw.parse_rates && typeof raw.parse_rates === "object" && !Array.isArray(raw.parse_rates) ? (raw.parse_rates as Record<string, unknown>) : {};
  return {
    status: raw.status === "passed" || raw.status === "failed" || raw.status === "warning" ? raw.status : "unknown",
    empty: names(raw.empty_columns),
    poorlyRead: Object.entries(rates).filter(([, rate]) => typeof rate === "number" && rate < 0.9).map(([name]) => name),
    required,
    requiredCount: count(raw.required_count, required.length),
    missing,
    missingCount: count(raw.missing_count, missing.length),
    ignored,
    ignoredCount: count(raw.ignored_count, ignored.length),
    target: typeof raw.target_column === "string" ? raw.target_column : null,
    targetIgnored: raw.target_column_ignored === true,
  };
}

const list = (items: string[], total: number) => `${items.join(", ")}${total > items.length ? ` and ${total - items.length} more` : ""}`;
const plural = (n: number, one: string, many: string) => (n === 1 ? one : many);

/** Plain-words lines for the contract check: what the model needs, what is missing, what is left out. */
export function contractSentences(view: ContractView): { tone: "ok" | "warn" | "crit"; lines: string[] } {
  const lines: string[] = [];
  if (view.missingCount > 0) {
    lines.push(`The file is missing ${view.missingCount} ${plural(view.missingCount, "column", "columns")} the model needs: ${list(view.missing, view.missingCount)}.`);
    lines.push("Add the missing columns with the same names and upload the file again.");
  } else if (view.status === "passed") {
    lines.push(`The file has all ${view.requiredCount} ${plural(view.requiredCount, "column", "columns")} the model needs.`);
  }
  if (view.targetIgnored && view.target) lines.push(`The column ${view.target} is the answer the model predicts, so it is ignored.`);
  if (view.ignoredCount > 0) {
    lines.push(`${view.ignoredCount} other ${plural(view.ignoredCount, "column is", "columns are")} not used by the model and ${plural(view.ignoredCount, "is", "are")} ignored: ${list(view.ignored, view.ignoredCount)}.`);
  }
  if (view.empty.length) lines.push(`These columns are empty in the file: ${view.empty.join(", ")}.`);
  if (view.status === "warning" || view.poorlyRead.length) {
    lines.push(`The file was scored, but many values could not be read and were treated as missing${view.poorlyRead.length ? ` in: ${view.poorlyRead.join(", ")}` : ""}. Check those columns before relying on the predictions.`);
  }
  const tone = view.status === "failed" || view.missingCount > 0 ? "crit" : view.status === "warning" || view.poorlyRead.length ? "warn" : "ok";
  return { tone, lines };
}

const BY_CODE: Record<string, PlainError> = {
  training_dataset_not_scoreable: {
    title: "That is the file this model learned from",
    detail: "Predictions on rows the model was fit on look better than they would on new rows. Upload new rows the model has not seen, for example next month's customers.",
    fixable: true,
  },
  model_not_scoreable: { title: "This model version cannot score files yet", detail: "It has no locked, stored model. Wait for the run to finish or pick another version.", fixable: false },
  feature_contract_failed: { title: "The file does not match what the model needs", detail: "See the column check for the missing columns.", fixable: true },
  prediction_not_ready: { title: "The predictions are not ready", detail: "The file can be downloaded once scoring has completed.", fixable: false },
  scoring_failed: { title: "Scoring did not finish", detail: "Try again with the same file.", fixable: false },
  not_found: { title: "Not found", detail: "The model version or the file is not in this workspace.", fixable: false },
  idempotency_key_conflict: { title: "That request was already used with different content", detail: "Reload the page and try again.", fixable: false },
};

/** Backend refusal -> plain language for upload, create and download. Falls back to the wizard's mapping. */
export function mapScoringError(error: unknown): PlainError {
  const { code } = envelope((error as { body?: unknown } | null)?.body);
  const known = BY_CODE[code];
  if (known) return known;
  const status = (error as { status?: unknown } | null)?.status;
  if (status === 403) return { title: "You cannot score files here", detail: "Scoring needs a role that can run ML work in this workspace.", fixable: false };
  if (status === 429) return { title: "Too many jobs at once", detail: "Wait for a run or scoring job to finish, then try again.", fixable: false };
  return mapWizardError(error);
}

/** Why a finished scoring failed, from the API's own code (its message is generic API text, shown as plain text). */
export function failureError(code: string | null | undefined, message: string | null | undefined): PlainError {
  const key = code?.toLowerCase() ?? "";
  if (key === "feature_contract_failed") return { ...BY_CODE.feature_contract_failed, detail: message ? `${message}.` : BY_CODE.feature_contract_failed.detail };
  if (key === "scoring_failed" && message) return { ...BY_CODE.scoring_failed, detail: `${message}.` };
  const known = BY_CODE[key];
  if (known) return known;
  return { title: "Scoring did not finish", detail: message || "Try again with the same file.", fixable: false };
}

export const isTerminal = (status: string | null | undefined) => status === "completed" || status === "failed";

/** Poll delay in ms after `attempt` reads: 1s, growing to a 5s ceiling. */
export function pollDelay(attempt: number): number {
  return Math.min(5000, 1000 + Math.max(0, attempt) * 500);
}

/** The API's download path is used only when it is exactly this prediction's download under /v1 (optionally behind the BFF prefix): no host, query or token. */
export function safeDownloadId(path: string | null | undefined, predictionId: string): string | null {
  if (!path || !isUuid(predictionId)) return null;
  const match = /^(?:\/api\/backend)?\/v1\/predictions\/([0-9a-fA-F-]{36})\/download$/.exec(path);
  return match && match[1].toLowerCase() === predictionId.toLowerCase() ? predictionId : null;
}

/** Session history is kept as ids and file names only; anything else is dropped. */
export type SessionScoring = { id: string; fileName: string };
export function parseSessionScorings(raw: string | null): SessionScoring[] {
  if (!raw) return [];
  try {
    const value: unknown = JSON.parse(raw);
    if (!Array.isArray(value)) return [];
    return value
      .flatMap((item) => {
        const row = item as { id?: unknown; fileName?: unknown } | null;
        return row && typeof row.id === "string" && isUuid(row.id) && typeof row.fileName === "string"
          ? [{ id: row.id, fileName: row.fileName.slice(0, 200) }]
          : [];
      })
      .slice(0, 20);
  } catch {
    return [];
  }
}

// --- V7-A5: Predictions screen -------------------------------------------------------------

const own = <T>(table: Record<string, T>, key: string): T | undefined => (Object.hasOwn(table, key) ? table[key] : undefined);

/** Status of a scoring in words; an unknown status keeps its own cleaned text. */
const STATUS_WORDS: Record<string, string> = { queued: "waiting", running: "scoring", completed: "done", failed: "failed" };
export const scoringStatusWords = (status: string): string => own(STATUS_WORDS, status) ?? plainText(status.replaceAll("_", " "), 30);

type ModelPick = { id: string; champion: boolean };
/** The model to score with: the one asked for if this project has it, else the model in use, else the newest. null = no model yet. */
export function pickModel(models: readonly ModelPick[], requested: string | null | undefined): string | null {
  if (requested && models.some((m) => m.id === requested)) return requested;
  return (models.find((m) => m.champion) ?? models[0])?.id ?? null;
}

/** "Model v2 · Run 3 (in use)" for the picker and the history. */
export function modelChoiceLabel(row: { version: string; champion: boolean }, run: string | null): string {
  return `${plainText(modelName(plainText(row.version, 40)), 60)}${run ? ` · ${run}` : ""}${row.champion ? " (in use)" : ""}`;
}

/** What the downloaded file holds, from the API's own description of it. Only a binary model has a threshold. */
export function downloadWords(threshold: number | null | undefined): string {
  const answer = threshold != null
    ? "the probability and a 0/1 label (1 = flagged at the model's locked threshold)"
    : "the model's answer (one probability per class with a label, or a predicted number)";
  return `The file has one row for each row of your file, in the same order: its row number, the entity column when your file has one, and ${answer}.`;
}

type PredictionLike = { id: string; model_version_id: string; status: string; created_at: string; rows_in?: number | null; rows_out?: number | null };
/** What the page knows of one scoring's read: its data, or that the read failed, or neither yet (still loading). */
export type ReadState = { data?: PredictionLike; failed?: boolean } | undefined;

/** Where this browser tab keeps its list: per workspace and per signed-in user, so another person on the same tab never sees it. */
export type SessionScope = { userId: string; workspaceId: string };
export const sessionStoreKey = (scope: SessionScope, modelVersionId: string): string => `dclab.scorings.${scope.workspaceId}.${scope.userId}.${modelVersionId}`;
export type HistoryRow = { id: string; file: string; model: string; when: string | null; status: string; statusKey: string; rows: string; by: string; href: string | null };
export type SessionEntry = SessionScoring & { modelVersionId: string };

/**
 * One row per scoring started in this browser tab by the signed-in person (the list is stored per workspace and user), newest first;
 * scorings still loading go last. A read that failed or belongs to another model says so instead of "loading". Scorings started by
 * a connected tool, by someone else or in another tab have no list read yet and are not in it.
 */
export function historyRows(
  entries: readonly SessionEntry[],
  reads: ReadonlyMap<string, ReadState>,
  modelLabel: (modelVersionId: string) => string | null,
  hrefFor: (modelVersionId: string) => string | null,
): HistoryRow[] {
  const rows = entries.map((entry, index) => {
    const state = reads.get(entry.id);
    const read = state?.data;
    const mismatch = !!read && read.model_version_id !== entry.modelVersionId;
    const p = read && !mismatch ? read : undefined;
    const done = p?.status === "completed";
    return {
      index,
      created: p?.created_at ?? null,
      row: {
        id: entry.id,
        file: plainText(entry.fileName, 80),
        model: modelLabel(entry.modelVersionId) ?? "A model of this project",
        when: p?.created_at ?? null,
        status: p ? scoringStatusWords(p.status) : mismatch ? "not this model's" : state?.failed ? "could not be read" : "loading",
        // tone keys: a failed read is shown like a failed scoring, a mismatch like a neutral state
        statusKey: p ? p.status : mismatch ? "skipped" : state?.failed ? "failed" : "loading",
        rows: done ? `${p.rows_out ?? "—"} of ${p.rows_in ?? "—"}` : "—",
        by: "You, in this browser tab",
        href: hrefFor(entry.modelVersionId),
      } satisfies HistoryRow,
    };
  });
  rows.sort((a, b) => (a.created && b.created ? b.created.localeCompare(a.created) : a.created ? -1 : b.created ? 1 : a.index - b.index));
  return rows.map((r) => r.row);
}
