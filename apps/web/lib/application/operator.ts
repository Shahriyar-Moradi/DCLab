/**
 * Operator console view model (P8.5-UI): pure, so it runs under `npm run test:components`.
 *
 * Every sentence is built from fields of the three operator reads (`/v1/operator/overview`, `/workspaces`, `/failed-jobs`).
 * Codes are mapped to fixed sentences with own-key lookups (`Object.hasOwn`), so a code such as `__proto__` reads as
 * "not recognised" instead of reaching a prototype. Workspace names are customer text: plain single-line text with a cap.
 * The reads carry ids, codes and counts only (no customer rows, payloads or free-text failure reasons); nothing here
 * invents a value the reads do not send (per-workspace plan and usage are not in the read yet).
 */
import { isUuid, plainText } from "./command-search.ts";
import { utcWhen } from "./format-time.ts";
import type { PillTone } from "../../components/studio/Pill.tsx";

export const NO_CUSTOMER_ROWS =
  "This console never shows customer rows: only counts, ids, codes and workspace names, read across every workspace.";
export const TIMES_UTC = "Times are UTC.";
export const NAME_CAP = 80;

export type TabStatus = "working" | "planned";
export type OperatorTab = { id: string; label: string; status: TabStatus; planned?: string };

export const OPERATOR_TABS: readonly OperatorTab[] = [
  { id: "overview", label: "Overview", status: "working" },
  { id: "workspaces", label: "Workspaces", status: "working" },
  { id: "failed-jobs", label: "Failed jobs", status: "working" },
  {
    id: "quarantine", label: "Quarantine", status: "planned",
    planned: "Planned in P8.7 (data safety): uploaded files held for review will be listed here, with their metadata only, and a person will clear or reject each one with a reason.",
  },
  {
    id: "caps", label: "Platform AI caps", status: "planned",
    planned: "A page to compare each workspace's AI settings with the platform caps is planned; no read for it exists yet. The platform caps themselves are on Overview, read-only: they are set in code, and changing one needs a code change and an ADR.",
  },
  {
    id: "benchmarks", label: "Benchmarks & gates", status: "planned",
    planned: "Planned: benchmark results (R1, R2, R3) and the gates they feed are not in the operator read yet, so nothing is shown here.",
  },
];

export const statusTag = (status: TabStatus): { label: string; tone: PillTone } =>
  status === "working" ? { label: "Working today", tone: "ok" } : { label: "Planned", tone: "gray" };

/** `?tab=` value to a known tab id; anything else opens Overview. */
export function tabFromQuery(value: string | null | undefined): string {
  return OPERATOR_TABS.some((tab) => tab.id === value) ? (value as string) : "overview";
}

function own<T>(map: Record<string, T>, key: string | null | undefined): T | undefined {
  return typeof key === "string" && Object.hasOwn(map, key) ? map[key] : undefined;
}

/** A short code (job type, status, handler) as monospace text: printable characters only, capped. */
export const codeText = (value: unknown): string => plainText(value, 64) || "—";

// ---------------------------------------------------------------------------------------------- ages and counts

export function count(value: number): string {
  return Number.isFinite(value) ? Math.round(value).toLocaleString("en-US") : "—";
}

/** Seconds as "45 s", "3 min", "2 h 5 min", "3 days"; "—" when not a number. */
export function ageText(seconds: number | null | undefined): string {
  if (typeof seconds !== "number" || !Number.isFinite(seconds) || seconds < 0) return "—";
  if (seconds < 60) return `${Math.round(seconds)} s`;
  const minutes = Math.floor(seconds / 60);
  if (minutes < 60) return `${minutes} min`;
  const hours = Math.floor(minutes / 60);
  if (hours < 48) return minutes % 60 ? `${hours} h ${minutes % 60} min` : `${hours} h`;
  return `${Math.floor(hours / 24)} days`;
}

// ---------------------------------------------------------------------------------------------- jobs

const JOB_TYPES: Record<string, string> = {
  auto_train: "Training run",
  batch_predict: "Scoring",
  monitoring: "Monitoring check",
  agent_run: "Agent run",
  auth_cleanup: "Sign-in cleanup",
};

export const jobTypeLabel = (code: string): string => own(JOB_TYPES, code) ?? "Other job type";

export const JOB_STATUSES = ["queued", "running", "completed", "failed", "cancelled"] as const;
const STATUS_LABEL: Record<string, string> = { queued: "Waiting", running: "Running", completed: "Done", failed: "Failed", cancelled: "Cancelled" };
export const jobStatusLabel = (status: string): string => own(STATUS_LABEL, status) ?? "Other";

export type JobCountLike = { job_type: string; status: string; count: number };
export type JobPivotRow = { type: string; label: string; counts: Record<string, number>; other: number; total: number };

/** Jobs of the last 24 h, one row per job type with a count per status (unknown statuses add up in "other"). */
export function jobsPivot(jobs: readonly JobCountLike[]): { rows: JobPivotRow[]; total: number; sentence: string } {
  const byType = new Map<string, JobPivotRow>();
  let total = 0;
  for (const job of jobs) {
    const n = Number.isFinite(job.count) ? job.count : 0;
    let row = byType.get(job.job_type);
    if (!row) {
      row = { type: job.job_type, label: jobTypeLabel(job.job_type), counts: Object.fromEntries(JOB_STATUSES.map((s) => [s, 0])), other: 0, total: 0 };
      byType.set(job.job_type, row);
    }
    if ((JOB_STATUSES as readonly string[]).includes(job.status)) row.counts[job.status] += n;
    else row.other += n;
    row.total += n;
    total += n;
  }
  const rows = [...byType.values()].sort((a, b) => b.total - a.total || a.type.localeCompare(b.type));
  const sentence = total === 0
    ? "No job was queued in the last 24 hours."
    : `${count(total)} ${total === 1 ? "job was" : "jobs were"} queued in the last 24 hours, of ${rows.length} ${rows.length === 1 ? "type" : "types"}.`;
  return { rows, total, sentence };
}

export function queueSentence(queue: { depth: number; oldest_age_seconds: number | null }): string {
  if (queue.depth <= 0) return "No job is waiting in the queue.";
  const age = queue.oldest_age_seconds === null ? "" : ` The oldest has waited ${ageText(queue.oldest_age_seconds)}.`;
  return `${count(queue.depth)} ${queue.depth === 1 ? "job is" : "jobs are"} waiting in the queue.${age}`;
}

// ---------------------------------------------------------------------------------------------- workers

export type WorkersLike = { dispatcher: string; count: number; heartbeat_age_seconds: number[]; visible: boolean };

export function workersView(workers: WorkersLike): { headline: string; lines: string[]; tone: PillTone; label: string } {
  if (workers.dispatcher !== "postgres") {
    return { headline: "Jobs run inside the API process on this server, so there are no separate workers to count.", lines: [], tone: "gray", label: "In the API" };
  }
  if (!workers.visible) {
    return {
      headline: "Unknown: the database does not show worker sessions to the API's database role, so workers cannot be counted here. This does not mean no worker runs.",
      lines: [], tone: "gray", label: "Unknown",
    };
  }
  if (workers.count === 0) return { headline: "No worker process is connected to the database: queued jobs will wait.", lines: [], tone: "crit", label: "None" };
  const lines = workers.heartbeat_age_seconds.map((age, i) => `Worker ${i + 1}: last seen ${ageText(age)} ago.`);
  return {
    headline: `${count(workers.count)} ${workers.count === 1 ? "worker is" : "workers are"} connected to the database (freshest first; workers have no names here).`,
    lines, tone: "ok", label: count(workers.count),
  };
}

// ---------------------------------------------------------------------------------------------- failures

const FAILURES: Record<string, string> = {
  feature_contract_failed: "The file to score did not have the columns or types the model expects.",
  unsupported_transform: "The model uses a data step that scoring does not support.",
  unsupported_model: "The model's kind cannot be scored by the worker.",
  scoring_failed: "Scoring stopped with an error; the worker log has the details.",
  dataset_unavailable: "The file to score was no longer available.",
  release_mismatch: "The saved model no longer matches the one approved for scoring.",
  schedule_owner_not_allowed: "The person who set up the weekly scoring may no longer score in that workspace.",
  heartbeat_expired: "The worker stopped reporting while it ran the job, so the job was given up.",
  unknown_handler: "No worker code is registered for this job's handler.",
  auto_train_failed: "The training run ended without a model.",
  see_logs: "The reason is free text that can hold customer values, so it is not shown here; the worker log has it.",
};

export const failureSentence = (code: string): string => own(FAILURES, code) ?? "A failure code this page does not recognise; the worker log has the details.";

export type FailureCodeLike = { code: string; count: number };

export function failuresView(codes: readonly FailureCodeLike[], truncated: boolean): { total: number; sentence: string; scope: string; items: Array<{ key: string; sentence: string; count: string }> } {
  const total = codes.reduce((sum, c) => sum + (Number.isFinite(c.count) ? c.count : 0), 0);
  const items = [...codes].sort((a, b) => b.count - a.count || a.code.localeCompare(b.code))
    .map((c, i) => ({ key: `${i}:${c.code}`, sentence: failureSentence(c.code), count: count(c.count) }));
  const sentence = total === 0 && !truncated
    ? "No job failed in the last 24 hours."
    : `${truncated ? "At least " : ""}${count(total)} ${total === 1 ? "job" : "jobs"} failed in the last 24 hours.`;
  const scope = truncated
    ? "More jobs failed than the read scans, so these counts cover only the newest failures: they are a lower bound."
    : "These counts cover every job that failed in the last 24 hours.";
  return { total, sentence, scope, items };
}

// ---------------------------------------------------------------------------------------------- readiness

const CHECK_NAMES: Record<string, string> = { database: "Database", migrations: "Database schema", object_storage: "File storage", worker: "Workers" };
export const checkName = (name: string): string => own(CHECK_NAMES, name) ?? "Other check";

const CHECK_TONE: Record<string, PillTone> = { ok: "ok", failing: "crit", stale: "warn", unknown: "gray", skipped: "gray" };
const CHECK_LABEL: Record<string, string> = { ok: "OK", failing: "Failing", stale: "Stale", unknown: "Unknown", skipped: "Not checked" };
export const checkTone = (status: string): PillTone => own(CHECK_TONE, status) ?? "gray";
export const checkLabel = (status: string): string => own(CHECK_LABEL, status) ?? "Unknown";

const CHECK_SENTENCES: Record<string, string> = {
  "database:ok": "The API reaches the database.",
  "database:unreachable": "The API cannot reach the database.",
  "migrations:ok": "The database schema is at the latest migration.",
  "migrations:not_at_head": "The database schema is not at the latest migration: run the migrations.",
  "migrations:not_versioned": "The database has no migration record yet.",
  "migrations:scripts_unavailable": "This server cannot read its migration scripts, so the schema could not be compared.",
  "migrations:unreachable": "The database could not be asked which migration it is at.",
  "migrations:database_unreachable": "Not checked: the database is unreachable.",
  "object_storage:ok": "The API reaches file storage.",
  "object_storage:unreachable": "The API cannot reach file storage.",
  "worker:ok": "A worker was seen recently.",
  "worker:in_process_dispatcher": "Not checked: jobs run inside the API process on this server.",
  "worker:activity_not_visible": "Unknown: the database does not show worker sessions to the API's database role.",
  "worker:no_worker_seen": "No worker process is connected to the database.",
  "worker:heartbeat_old": "The freshest worker was last seen too long ago.",
  "worker:database_unreachable": "Not checked: the database is unreachable.",
};

const STATUS_FALLBACK: Record<string, string> = {
  ok: "Working.",
  failing: "Failing, with a reason code this page does not recognise.",
  stale: "Stale, with a reason code this page does not recognise.",
  unknown: "Unknown, with a reason code this page does not recognise.",
  skipped: "Not checked on this server.",
};

export type CheckLike = { name: string; status: string; required: boolean; code?: string | null };

export function checkSentence(check: CheckLike): string {
  const exact = check.code ? own(CHECK_SENTENCES, `${check.name}:${check.code}`) : check.status === "ok" ? own(CHECK_SENTENCES, `${check.name}:ok`) : undefined;
  return exact ?? own(STATUS_FALLBACK, check.status) ?? "This page does not recognise this check's status.";
}

const READY: Record<string, { label: string; tone: PillTone; sentence: string }> = {
  ready: { label: "Ready", tone: "ok", sentence: "No check is failing, stale or unknown; checks marked Not checked do not apply on this server." },
  degraded: { label: "Degraded", tone: "warn", sentence: "No required check is failing, but at least one check is stale or unknown (see below)." },
  not_ready: { label: "Not ready", tone: "crit", sentence: "A required check is failing: the readiness probe answers 503 until it is fixed." },
};

export const readinessView = (status: string) =>
  own(READY, status) ?? { label: "Unknown", tone: "gray" as PillTone, sentence: "This page does not recognise the readiness status." };

export const requiredText = (required: boolean): string => (required ? "Required" : "Not required");

// ---------------------------------------------------------------------------------------------- platform AI caps

export type CapsLike = {
  ai_enabled: boolean; max_data_class: string; sample_values_per_column: number; user_text_to_jev: boolean;
  auto_release: boolean; model_allowlist: string[]; budgets: Record<string, number | boolean>;
};

const DATA_CLASS: Record<string, string> = {
  metadata: "Column metadata only (names and types)",
  aggregates: "Aggregates only (counts and summaries, never rows)",
  sample_values: "Sample values per column",
};

const BUDGETS: Record<string, string> = {
  workspace_month_micros: "AI spend per workspace per month",
  project_month_micros: "AI spend per project per month",
  assistant_turn_micros: "AI spend per assistant turn",
  assistant_thread_micros: "AI spend per assistant conversation",
  specialist_run_micros: "AI spend per specialist agent run",
  alert_fraction: "Spend warning at",
  hard_stop: "Stop AI calls when a budget is used up",
};

const money = (micros: number) => `${(micros / 1_000_000).toLocaleString("en-US", { minimumFractionDigits: 2, maximumFractionDigits: 2 })} (billing currency)`;
const yesNo = (value: boolean) => (value ? "Yes" : "No");

export type CapItem = { key: string; label: string; value: string };

/** The code-owned caps in plain words; read-only (no control changes them). Unknown budget keys are counted, not named. */
export function capsItems(caps: CapsLike): { items: CapItem[]; unknownBudgets: number } {
  const items: CapItem[] = [
    { key: "ai", label: "AI on this server", value: caps.ai_enabled ? "On" : "Off" },
    { key: "data", label: "Most detailed data an AI may receive", value: own(DATA_CLASS, caps.max_data_class) ?? "A data class this page does not recognise" },
    { key: "samples", label: "Sample values per column sent to AI", value: count(caps.sample_values_per_column) },
    { key: "jev", label: "People's own text sent to Jev", value: yesNo(caps.user_text_to_jev) },
    { key: "auto", label: "AI may switch the model in use on its own", value: caps.auto_release ? "Yes" : "No: a person always approves" },
    { key: "models", label: "AI models allowed", value: caps.model_allowlist.length ? caps.model_allowlist.map((m) => plainText(m, 40)).join(", ") : "None" },
  ];
  let unknownBudgets = 0;
  for (const [key, value] of Object.entries(caps.budgets)) {
    const label = own(BUDGETS, key);
    if (!label) { unknownBudgets += 1; continue; }
    const shown = typeof value === "boolean" ? yesNo(value)
      : key === "alert_fraction" ? `${Math.round(value * 100)}% of a budget`
        : key.endsWith("_micros") ? money(value) : count(value);
    items.push({ key: `budget:${key}`, label, value: shown });
  }
  return { items, unknownBudgets };
}

export const CAPS_READ_ONLY = "Set in code for the whole platform and read-only here: a workspace can only narrow them. Changing one needs a code change and an ADR.";

// ---------------------------------------------------------------------------------------------- tables

export type WorkspaceLike = {
  id: string; name: string; kind: string; created_at: string; member_count: number; project_count: number;
  jobs_last_30d: number; failed_jobs_last_24h: number; active_last_7d: boolean;
};

const KIND: Record<string, string> = { business: "Business", personal: "Personal" };
export const workspaceKind = (kind: string): string => own(KIND, kind) ?? "Other kind";

export type WorkspaceRow = {
  key: string; id: string | null; shortId: string; name: string; kind: string; created: string;
  members: number; projects: number; jobs30d: number; failed24h: number; active: string;
};

export function workspaceRows(items: readonly WorkspaceLike[]): WorkspaceRow[] {
  return items.map((w, i) => {
    const id = isUuid(w.id) ? w.id : null;
    return {
      key: id ?? `row-${i}`, id, shortId: id ? id.slice(0, 8) : "—",
      name: plainText(w.name, NAME_CAP) || "(no name)", kind: workspaceKind(w.kind), created: utcWhen(w.created_at),
      members: w.member_count, projects: w.project_count, jobs30d: w.jobs_last_30d, failed24h: w.failed_jobs_last_24h,
      active: w.active_last_7d ? "Yes" : "No",
    };
  });
}

export type FailedJobLike = { id: string; workspace_id: string; job_type: string; handler_key: string; code: string; attempts: number; failed_at: string };
export type FailedJobRow = {
  key: string; id: string | null; shortId: string; workspaceId: string | null; workspaceShort: string; type: string; typeCode: string;
  handler: string; sentence: string; attempts: number; when: string;
};

export function failedJobRows(items: readonly FailedJobLike[]): FailedJobRow[] {
  return items.map((j, i) => {
    const id = isUuid(j.id) ? j.id : null;
    const workspaceId = isUuid(j.workspace_id) ? j.workspace_id : null;
    return {
      key: id ?? `row-${i}`, id, shortId: id ? id.slice(0, 8) : "—", workspaceId, workspaceShort: workspaceId ? workspaceId.slice(0, 8) : "—",
      type: jobTypeLabel(j.job_type), typeCode: codeText(j.job_type), handler: codeText(j.handler_key), sentence: failureSentence(j.code),
      attempts: j.attempts, when: utcWhen(j.failed_at),
    };
  });
}

export function pageSentence(shown: number, more: boolean, noun: [string, string]): string {
  const word = shown === 1 ? noun[0] : noun[1];
  return more ? `Showing the first ${count(shown)} ${word}. More are available.` : `Showing all ${count(shown)} ${word}.`;
}

// ---------------------------------------------------------------------------------------------- read states

export type ReadErrorLike = { status?: number; body?: unknown } | null | undefined;
export type ReadState = { kind: "forbidden" | "signed_out" | "invalid_cursor" | "failed"; text: string };

function errorCode(body: unknown): string | null {
  if (!body || typeof body !== "object") return null;
  const error = (body as { error?: unknown }).error;
  if (error && typeof error === "object" && typeof (error as { code?: unknown }).code === "string") return (error as { code: string }).code;
  const detail = (body as { detail?: unknown }).detail;
  if (detail && typeof detail === "object" && typeof (detail as { code?: unknown }).code === "string") return (detail as { code: string }).code;
  return null;
}

/** A failed read in fixed words keyed by status and code (never the server's message). */
export function readFailure(error: ReadErrorLike, what: string): ReadState {
  const status = error?.status ?? 0;
  if (status === 401) return { kind: "signed_out", text: "Your session ended. Sign in again to open the operator console." };
  if (status === 403) return { kind: "forbidden", text: "The operator console is for DCLab platform administrators only. Your account cannot open it." };
  if (status === 400 && errorCode(error?.body) === "invalid_cursor") {
    return { kind: "invalid_cursor", text: `The next page of ${what} could not be read: the list changed or the page link expired. Start again from the first page.` };
  }
  return { kind: "failed", text: `The ${what} could not be loaded. Nothing was changed. Check your connection and reload.` };
}

/**
 * What the button under a failed "show more" does. A cursor the API refused can never succeed again, so the list starts over
 * from the first page (the query is reset); any other next-page failure retries the same page.
 */
export function nextPageAction(error: ReadErrorLike): { label: string; restart: boolean } {
  return readFailure(error, "list").kind === "invalid_cursor"
    ? { label: "Start again from the first page", restart: true }
    : { label: "Try again", restart: false };
}

/** Where "Sign in" returns to: the console tab in view (a known tab id only). */
export function signInHref(tab: string): string {
  const id = tabFromQuery(tab);
  return `/login?next=${encodeURIComponent(id === "overview" ? "/operator" : `/operator?tab=${id}`)}`;
}
