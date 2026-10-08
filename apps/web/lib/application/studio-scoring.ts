/** Score new data (P4.9-UI): pure view-model logic. Every sentence is built from API fields of the prediction read. */
import { isUuid } from "./command-search.ts";
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
