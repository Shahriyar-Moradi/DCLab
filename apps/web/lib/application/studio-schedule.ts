/**
 * "Weekly scoring" (V7-B2b-UI): pure view-model logic, so it runs under `npm run test:components`.
 * Every sentence restates fields of `GET /v1/projects/{id}/scoring-schedule` (and the approvals read for the model that
 * would be used); a value that was not returned is left out, never guessed. States the schedule really does, and no more:
 * each week it scores the newest uploaded file with the model in use if a person approved it; it never retrains, fetches
 * data or switches models. No sentence built here passes the server's own message through; times always say UTC.
 */
import { utcWhen } from "./format-time.ts";
import { failureError } from "./studio-scoring.ts";
import { envelope, type PlainError } from "./studio-wizard.ts";

export const REASON_MAX = 4000;
export const RUNS_SHOWN = 10;
export const WEEKDAYS = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"] as const;
export const HOURS = Array.from({ length: 24 }, (_, h) => h);

const own = <T>(table: Record<string, T>, key: string): T | undefined => (Object.hasOwn(table, key) ? table[key] : undefined);
const pad = (n: number) => String(n).padStart(2, "0");
const validWeekday = (n: unknown): n is number => Number.isInteger(n) && (n as number) >= 0 && (n as number) <= 6;
const validHour = (n: unknown): n is number => Number.isInteger(n) && (n as number) >= 0 && (n as number) <= 23;

export type ScheduleLike = {
  weekday: number; hour_utc: number; enabled: boolean; next_planned_for?: string | null; owner_can_score: boolean; updated_by_user_id?: string | null;
  ignored_token_uploads?: number;
};
export type RunLike = {
  id: string; planned_for: string; outcome: string; reason_code?: string | null; batch_prediction_id?: string | null; model_version_id?: string | null;
};
export type ScheduleStateLike = { status: string; schedule?: ScheduleLike | null; recent_runs?: RunLike[] };

/** "Every Monday at 09:00 UTC". */
export function slotText(weekday: number, hour: number): string {
  return `Every ${validWeekday(weekday) ? WEEKDAYS[weekday] : "week"} at ${validHour(hour) ? pad(hour) : "—"}:00 UTC`;
}

/** The next time (UTC) a weekly slot comes, strictly after `now`. */
export function nextOccurrence(weekday: number, hour: number, now: Date): Date {
  const base = Date.UTC(now.getUTCFullYear(), now.getUTCMonth(), now.getUTCDate(), hour);
  const todayIndex = (now.getUTCDay() + 6) % 7; // Monday = 0
  let days = (weekday - todayIndex + 7) % 7;
  let at = base + days * 86_400_000;
  if (at <= now.getTime()) { days += 7; at = base + days * 86_400_000; }
  return new Date(at);
}

/** A date in UTC, always with "UTC" on it (shared with the Monitoring screen). */
export { utcWhen };

export type ScheduleView =
  | { kind: "loading" } | { kind: "error" }
  | { kind: "not_set_up" | "off" | "on" | "on_idle" | "paused"; sentence: string };

/**
 * The status sentence. Loading, a failed read, not set up, on, on but with nothing it can score, off and paused by the
 * platform are distinct. `model` is null while the approvals are not known yet (no claim is made then).
 */
export function scheduleView(input: { pending: boolean; failed: boolean; state: ScheduleStateLike | undefined; model?: ModelUsed | null }): ScheduleView {
  if (input.failed) return { kind: "error" };
  const state = input.state;
  if (input.pending || !state) return { kind: "loading" };
  const schedule = state.schedule;
  if (!schedule) return { kind: "not_set_up", sentence: "Weekly scoring is not set up. Nothing is scored on a schedule." };
  const slot = slotText(schedule.weekday, schedule.hour_utc);
  if (!schedule.enabled || state.status === "off") {
    return { kind: "off", sentence: `Weekly scoring is off. Nothing is scored on a schedule. It was set for: ${slot.replace(/^Every/, "every")}.` };
  }
  if (state.status === "paused_by_platform") {
    return { kind: "paused", sentence: "Weekly scoring is saved as on, but it is paused by the platform: it is not running on this server right now, so nothing is scored on a schedule. Ask the person who runs DCLab to turn it back on." };
  }
  if (schedule.owner_can_score === false) {
    return { kind: "on_idle", sentence: "Weekly scoring is on, but it will score nothing until someone who can run ML work in this workspace saves the schedule again." };
  }
  if (input.model?.kind === "warning") {
    return { kind: "on_idle", sentence: "Weekly scoring is on, but it will score nothing until the model in use is approved for scoring." };
  }
  return { kind: "on", sentence: "Weekly scoring is on." };
}

const OVERDUE_GRACE_MS = 5 * 60_000;
/** "Next run: …", or why there is none; a next run already in the past is "was due", never a future promise. */
export function nextRunText(view: ScheduleView, schedule: ScheduleLike | null | undefined, now: Date = new Date()): string | null {
  if (!schedule || view.kind === "off" || view.kind === "not_set_up" || view.kind === "error" || view.kind === "loading") return null;
  if (view.kind === "paused") return "Next run: none while it is paused by the platform.";
  if (!schedule.next_planned_for || utcWhen(schedule.next_planned_for) === "—") return null;
  const due = new Date(schedule.next_planned_for).getTime();
  if (due < now.getTime() - OVERDUE_GRACE_MS) {
    return `Was due ${utcWhen(schedule.next_planned_for)}; it has not started. If it is not handled within 24 hours that week is skipped.`;
  }
  return `Next run: ${utcWhen(schedule.next_planned_for)}.`;
}

/** The next weekly instant in the viewer's own time zone, as a hint (never replaces the UTC text). */
export function localHint(weekday: number, hour: number, now: Date, local: (d: Date) => string): string | null {
  if (!validWeekday(weekday) || !validHour(hour)) return null;
  return `The next run is ${local(nextOccurrence(weekday, hour, now))} where you are; local time shifts with daylight saving, UTC does not.`;
}

/** How many files connected tools uploaded for scoring with a service token that the schedule will not score (null: none, or unknown). */
export function ignoredUploadsText(schedule: ScheduleLike | null | undefined): string | null {
  const n = schedule?.ignored_token_uploads;
  if (!schedule || typeof n !== "number" || !Number.isInteger(n) || n <= 0) return null;
  return n === 1
    ? "1 file a connected tool uploaded with a service token was not scored. Only files uploaded by a person are scored."
    : `${n} files connected tools uploaded with a service token were not scored. Only files uploaded by a person are scored.`;
}

/** The warning when the person scheduled runs act as can no longer score. */
export function ownerWarning(schedule: ScheduleLike | null | undefined): string | null {
  return schedule && schedule.enabled && schedule.owner_can_score === false
    ? "The person who last saved this schedule can no longer score in this workspace, so runs will score nothing until someone who can saves it again."
    : null;
}

export type ModelUsed =
  | { kind: "approved"; text: string }
  | { kind: "warning"; text: string }
  | { kind: "unknown"; text: string };
/**
 * Which model the next run would use: the model in use, only if a person approved it (its approval is the current one).
 * `checked` is true only when both the model-in-use read and the approvals read succeeded: otherwise nothing is claimed.
 */
export function modelUsed(input: { inUseId: string | null; inUseName: string | null; approvedId: string | null; checked?: boolean }): ModelUsed {
  if (input.checked === false) return { kind: "unknown", text: "Could not check which model would be used." };
  if (input.inUseId && input.approvedId === input.inUseId) {
    return { kind: "approved", text: `${input.inUseName ?? "The model in use"} — approved for scoring` };
  }
  return { kind: "warning", text: "No approved model is in use: nothing will be scored until one is approved." };
}

/** What a viewer may do: change the schedule only with ML-write access, and not while a confirm step is open. */
export function scheduleAction(viewer: { canWrite: boolean; confirming: boolean }): boolean {
  return viewer.canWrite && !viewer.confirming;
}

const REASON_SENTENCES: Record<string, string> = {
  run_limit: "too many jobs were running",
  worker_was_down: "DCLab's background processing was not running",
  owner_missing: "the person who set up the schedule could not be found",
  model_not_scoreable: "the model in use could not score files",
  dataset_unavailable: "the newest file could not be read",
  release_changed: "the model in use changed before the run started",
  internal_error: "an unexpected problem stopped it",
};
export const UNKNOWN_OUTCOME = "This week was handled, but this page does not recognise the result, so nothing more is said.";
const OUTCOME_SENTENCES: Record<string, string> = {
  queued: "Started scoring the newest file a person uploaded for scoring with the model in use.",
  skipped_no_new_file: "Nothing scored: no person uploaded a file for scoring after weekly scoring was first set up and after the last week it scored.",
  skipped_no_model_in_use: "Nothing scored: the project had no model in use.",
  skipped_no_approval: "Nothing scored: the model in use was not approved for scoring.",
  skipped_owner_not_allowed: "Nothing scored: the person who set up this schedule can no longer score in this workspace. Someone who can should save the schedule again.",
  skipped_missed: "Nothing scored: DCLab did not get to it within 24 hours. Weeks DCLab never reached have no row here.",
};
/** One handled week in plain words, by the closed outcome code. Only `failed_to_queue` is a failure. */
export function runSentence(outcome: string, reasonCode?: string | null): string {
  if (outcome === "failed_to_queue") {
    const why = reasonCode ? own(REASON_SENTENCES, reasonCode) : undefined;
    return `Failed: the scoring could not be started${why ? ` (${why})` : ""}. Nothing was scored.`;
  }
  return own(OUTCOME_SENTENCES, outcome) ?? UNKNOWN_OUTCOME;
}

export type RunRow = { id: string; week: string; sentence: string; failed: boolean; predictionId: string | null };
/** What the page knows of the scoring a queued week started (the prediction read). */
export type PredictionState = { status: string; rows_out?: number | null; error_code?: string | null; error_message?: string | null };
/**
 * The newest runs (at most 10), each with its week (UTC). A queued week only started a scoring: it reads as scoring while
 * that waits or runs, done (with the weekly list link, valid UUID) only when completed, and failed with the plain reason
 * when the scoring failed.
 */
export function runRows(runs: readonly RunLike[], isUuid: (v: unknown) => boolean, predictions: ReadonlyMap<string, PredictionState> = new Map()): RunRow[] {
  return runs.slice(0, RUNS_SHOWN).map((r) => {
    const base = { id: r.id, week: utcWhen(r.planned_for), sentence: runSentence(r.outcome, r.reason_code), failed: r.outcome === "failed_to_queue", predictionId: null as string | null };
    const pid = r.outcome === "queued" && isUuid(r.batch_prediction_id) ? (r.batch_prediction_id as string) : null;
    const p = pid ? predictions.get(pid) : undefined;
    if (!pid || !p) return base;
    if (p.status === "queued" || p.status === "running") return { ...base, sentence: "Scoring the newest file a person uploaded for scoring with the model in use…" };
    if (p.status === "completed") return { ...base, sentence: `Done: ${p.rows_out ?? "—"} rows scored.`, predictionId: pid };
    if (p.status === "failed") {
      const why = failureError(p.error_code, p.error_message, true);
      return { ...base, sentence: `Failed: ${why.title}. ${why.detail}`, failed: true };
    }
    return base;
  });
}

export type Intent = { enabled: boolean; weekday: number; hour: number };

export const FILE_RULE = "the newest file uploaded for scoring after weekly scoring was first set up and after the last week it scored (never the file the model learned from or one it already scored). Only one file is scored per week; older new files in the same week are not scored.";
/** Who can supply the file: the API never picks a file a connected tool uploaded with a service token; a person's own sign-in or own API token is the person. */
export const PERSON_ONLY_RULE = "Only files uploaded by a person are scored; files a connected tool uploaded with a service token are ignored. A file uploaded with a person's own sign-in or own API token counts as that person's.";
/** The static paragraph of the card; the same wording rules as the confirm step. */
export const ABOUT_LINES = [
  `Each week on the chosen day and hour (UTC), DCLab scores ${FILE_RULE}`,
  "It scores with the model in use if a person approved it, as the person who last saved the schedule. It does not retrain, does not fetch data and does not switch models. With no new file or no approved model it scores nothing and records why.",
  `${PERSON_ONLY_RULE} It runs between DCLab's background jobs, so a long-busy DCLab can skip a week.`,
];

/** What the one confirm step says will happen, and what will not, before anything is sent. */
export function confirmWords(intent: Intent, model: ModelUsed, ctx: { paused?: boolean; current?: ScheduleLike | null } = {}): { title: string; button: string; lines: string[] } {
  if (!intent.enabled) {
    return {
      title: "Turn off weekly scoring?",
      button: "Yes, turn it off",
      lines: [
        "Nothing will be scored on a schedule from now on. A scoring that has already started still finishes.",
        "The schedule and the weeks already handled stay, and you can turn it on again.",
        "Predictions already made are not changed.",
        "The change and your reason are saved in History.",
      ],
    };
  }
  const slot = slotText(intent.weekday, intent.hour);
  const lines = [
    `${slot}, DCLab will score ${FILE_RULE} It scores with the model in use if a person approved it, acting as you.`,
    model.kind === "approved" ? `Right now that is ${model.text.replace(" — approved for scoring", "")}.` : model.text,
  ];
  if (ctx.current) lines.push(`This replaces: ${slotText(ctx.current.weekday, ctx.current.hour_utc).replace(/^Every/, "every")}${ctx.current.enabled ? "" : " (currently off)"}.`);
  if (ctx.paused) lines.push("Weekly scoring is paused by the platform: you can save it, but nothing is scored until the platform turns it on.");
  lines.push(
    "With no new file, or no approved model in use, nothing is scored and the week says why.",
    `It does not retrain, does not fetch data and does not switch models. It scores only files uploaded for scoring. ${PERSON_ONLY_RULE}`,
    "If you lose access to run ML work here, the scheduled runs stop and say so. Only the latest missed week within 24 hours is considered, and a busy DCLab can skip a week.",
    "The change and your reason are saved in History.",
  );
  return { title: "Turn on weekly scoring?", button: "Yes, save weekly scoring", lines };
}

/** The reason sent: what was typed (capped like the API), or undefined so the API records its own marked default. */
export function reasonToSend(typed: string): string | undefined {
  const text = typed.trim().slice(0, REASON_MAX);
  return text || undefined;
}

/** The done message, from the state the save returned. */
export function savedText(intent: Intent, status: string): string {
  if (!intent.enabled) return "Weekly scoring is off. The change was saved in History.";
  const slot = slotText(intent.weekday, intent.hour).replace(/^Every/, "every");
  return status === "paused_by_platform"
    ? `Weekly scoring is saved (${slot}), but it is paused by the platform: nothing is scored until the platform turns it on. The change was saved in History.`
    : `Weekly scoring is saved: ${slot}. The change was saved in History.`;
}

const CONFLICT: PlainError = { title: "The schedule was changed by someone else", detail: "Reload to see the schedule now, then decide again.", fixable: false };
const BY_CODE: Record<string, PlainError> = {
  human_session_required: { title: "Only a signed-in person can change the schedule", detail: "A connected tool or an access token cannot set weekly scoring.", fixable: false },
  service_token_not_permitted: { title: "Only a signed-in person can change the schedule", detail: "A connected tool or an access token cannot set weekly scoring.", fixable: false },
  capability_denied: { title: "You do not have permission to change weekly scoring", detail: "It needs a role that can change ML work in this workspace.", fixable: false },
  forbidden: { title: "You do not have permission to change weekly scoring", detail: "It needs a role that can change ML work in this workspace.", fixable: false },
  idempotency_key_conflict: { title: "Your change may already be saved", detail: "Reload and check the schedule before you try again.", fixable: false },
  validation_failed: { title: "The schedule could not be saved", detail: "Pick a day and an hour, and keep the reason to plain text of up to 4000 characters.", fixable: true },
  project_not_found: { title: "This project was not found", detail: "Reload the page: it may have been removed or you may not have access.", fixable: false },
  precondition_failed: CONFLICT,
  concurrent_conflict: { title: "Another save was in progress", detail: "Nothing was changed. Try again in a moment.", fixable: false },
  idempotency_key_required: { title: "The change could not be sent", detail: "Reload the page and try again.", fixable: false },
};
/**
 * True for refusals after which the same request can never succeed (the next try must be a new action with a new key).
 * A retryable conflict (`concurrent_conflict`, another save in flight) is the opposite: the same request is sent again with the SAME key.
 */
export function isConflict(error: unknown): boolean {
  const status = (error as { status?: unknown } | null)?.status;
  if (status !== 409 && status !== 412 && status !== 428) return false;
  const { code } = envelope((error as { body?: unknown } | null)?.body);
  const retryable = ((error as { body?: { error?: { retryable?: unknown } } } | null)?.body?.error?.retryable) === true;
  return !(code === "concurrent_conflict" || retryable);
}
/** Save refusals in fixed plain sentences by API code, then status. The server's own message is never shown. */
export function mapScheduleError(error: unknown): PlainError {
  const { code } = envelope((error as { body?: unknown } | null)?.body);
  const known = code ? own(BY_CODE, code) : undefined;
  if (known) return known;
  const status = (error as { status?: unknown } | null)?.status;
  if (status === 403) return BY_CODE.capability_denied;
  if (status === 404) return BY_CODE.project_not_found;
  if (status === 409 || status === 412 || status === 428) return CONFLICT;
  if (status === 422) return BY_CODE.validation_failed;
  if (status === 400) return { title: "The request could not be understood", detail: "Nothing changed. Reload the page and try again.", fixable: false };
  if (status === 429) return { title: "Too many requests at once", detail: "Wait a moment, then try again.", fixable: false };
  return { title: "The schedule was not saved", detail: "Nothing changed. Check your connection, reload and try again.", fixable: false };
}
