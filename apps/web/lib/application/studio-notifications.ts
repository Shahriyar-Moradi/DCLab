/**
 * D8b: Settings → Notifications (your email choices) and Integrations (signed webhooks). Pure view model, so it runs under
 * `npm run test:components`.
 *
 * Every sentence is fixed and chosen by a closed code (own-key lookups: a code such as `__proto__` reads as "not recognised").
 * The server's own `note`, `label` and error messages are never shown. Webhook names and addresses are customer text: plain
 * single-line text with a cap, never a link. A webhook's signing secret is shown once by the page that received it and is never
 * kept in a cache or in storage; nothing here holds it.
 */
import { plainText } from "./command-search.ts";
import { utcWhen } from "./format-time.ts";
import { apiCode, apiStatus, actionErrorSentence, byCode, readErrorSentence } from "./api-error-text.ts";

// ---------------------------------------------------------------------------------------------- email preferences

/** Fixed words for each notification kind (the server's own label is not shown). */
export const KIND_LABEL: Record<string, string> = {
  decision_needed: "A decision is waiting for a person",
  run_finished: "A run or scoring you started finished",
  run_failed: "A run or scoring you started failed",
  scheduled_scoring_result: "The result of a weekly scheduled scoring",
  drift_alert: "A monitoring alert on the model in use (drift, or real outcomes below a floor)",
  quota_warning: "Usage at 80% or 100% of a plan limit",
  question_asked: "The data team asked the business a question",
  question_answered: "A business user replied to your question",
};

/** Who receives each kind at all (the server's audience rule, in words). */
const WHO_GETS: Record<string, string> = {
  decision_needed: "Sent to people who can decide in this workspace.",
  run_finished: "Sent to the person who started the run.",
  run_failed: "Sent to the person who started the run.",
  scheduled_scoring_result: "Sent to people who can score with the project's model.",
  drift_alert: "Sent to people who work on models in this workspace.",
  quota_warning: "Sent to the workspace's owners and administrators.",
  question_asked: "Sent to the workspace's business users.",
  question_answered: "Sent to the person who asked.",
};

export type PreferenceLike = { kind: string; email_enabled: boolean; default: boolean; applies_to_you: boolean };
export type PreferencesLike = { email_delivery: string; email_verified: boolean; items: PreferenceLike[] };
export type PreferenceRow = { kind: string; label: string; who: string; on: boolean; isDefault: boolean; applies: boolean; note: string | null };

export function preferenceRows(read: PreferencesLike): PreferenceRow[] {
  return read.items.flatMap((item) => {
    const label = byCode(KIND_LABEL, item.kind);
    if (!label) return []; // a kind this page does not know is not offered (the server keeps its value)
    return [{
      kind: item.kind, label, who: byCode(WHO_GETS, item.kind) ?? "", on: item.email_enabled === true, isDefault: item.email_enabled === item.default,
      applies: item.applies_to_you === true,
      note: item.applies_to_you === true ? null : "Your role does not receive this kind, so this choice has no effect for you now.",
    }];
  });
}

/** Banners above the choices: whether this server sends email at all, and whether your address can receive it. */
export function deliveryBanners(read: Pick<PreferencesLike, "email_delivery" | "email_verified">): Array<{ tone: "warn" | "info"; text: string }> {
  const out: Array<{ tone: "warn" | "info"; text: string }> = [];
  if (read.email_delivery === "off") out.push({ tone: "warn", text: "Email is turned off on this server, so DCLab sends no email at all. Your choices are kept for when it is turned on." });
  else if (read.email_delivery === "paused") out.push({ tone: "warn", text: "Notifications are paused on this server, so nothing is sent right now. Your choices are kept." });
  else if (read.email_delivery !== "on") out.push({ tone: "warn", text: "This page does not recognise whether this server sends email, so do not count on email arriving." });
  if (!read.email_verified) out.push({ tone: "info", text: "Your email address is not verified, so DCLab sends you no email. Your choices are kept for when it is verified." });
  return out;
}

/** The kinds whose choice differs from what the server sent (only those are saved). */
export function changedPreferences(rows: readonly PreferenceRow[], draft: Readonly<Record<string, boolean>>): Array<{ kind: string; email_enabled: boolean }> {
  return rows.flatMap((row) => (Object.hasOwn(draft, row.kind) && draft[row.kind] !== row.on ? [{ kind: row.kind, email_enabled: draft[row.kind] === true }] : []));
}

const PREFERENCE_ERRORS: Record<string, string> = {
  session_required: "Email choices can be changed only in a signed-in browser session.",
  duplicate_kind: "The same kind was sent twice. Reload the page and save again.",
  idempotency_key_conflict: "That save was already sent with different choices. Reload the page and save again.",
};

export function preferenceSaveError(error: unknown): string {
  return byCode(PREFERENCE_ERRORS, apiCode(error)) ?? actionErrorSentence(error, "saving your email choices");
}

export type ReadView = { kind: "forbidden" | "error"; text: string };

export function preferencesReadError(error: unknown): ReadView {
  const status = apiStatus(error);
  if (status === 403) return { kind: "forbidden", text: "Email choices can be read only in a signed-in browser session as a member of this workspace." };
  return { kind: "error", text: readErrorSentence(error, "email choices") };
}

// ---------------------------------------------------------------------------------------------- webhooks

export const WEBHOOK_EVENTS: ReadonlyArray<{ id: string; label: string }> = [
  { id: "decision_needed", label: "A decision is waiting" },
  { id: "run_finished", label: "A run finished" },
  { id: "run_failed", label: "A run failed" },
  { id: "scheduled_scoring_result", label: "Weekly scoring result" },
  { id: "drift_alert", label: "Monitoring alert (drift, or real outcomes below a floor)" },
  { id: "quota_warning", label: "Plan limit near or reached" },
];
const EVENT_LABEL: Record<string, string> = Object.fromEntries([...WEBHOOK_EVENTS.map((e) => [e.id, e.label]), ["ping", "Test call"]]);
export const eventLabel = (event: string): string => byCode(EVENT_LABEL, event) ?? "Not recognised";

export const WEBHOOKS_MAX = 5;
export const NAME_MAX = 80;
export const URL_MAX = 2048;
export const SECRET_ONCE =
  "Copy the signing secret now. DCLab shows it only this once; this page does not keep it, and a reload or leaving this tab hides it for good. If it is lost, make a new one.";
export const WEBHOOK_SAFETY =
  "Each call is signed with the secret (header X-DCLab-Signature) and carries ids, codes and the project name only: never data rows, file names or final test values. Addresses must use https; private and local addresses are refused.";

/** Closed delivery error codes (the worker never stores a response body or exception text). */
const DELIVERY_ERROR: Record<string, string> = {
  not_configured: "Webhooks are not set up on this server.",
  endpoint_disabled: "The webhook was turned off.",
  expired: "It waited too long and was not sent.",
  destination_refused: "The address points to a private or local network, so it was refused.",
  dns_failed: "The address's name could not be looked up.",
  connect_failed: "Could not connect to the address.",
  tls_failed: "The secure (https) connection failed.",
  timeout: "The address did not answer in time.",
  redirect_not_followed: "The address answered with a redirect; redirects are not followed.",
  http_error: "The address answered with an error status.",
  invalid_response: "The address's answer could not be read.",
  body_too_large: "The call was too large to send.",
  internal_error: "DCLab had a problem sending it.",
};
export const deliveryErrorText = (code: string | null | undefined): string | null => (code ? byCode(DELIVERY_ERROR, code) ?? "Failed for a reason this page does not recognise." : null);

export type WebhookLike = {
  id: string; name: string; url: string; events: string[]; enabled: boolean; secret_version: number; created_at: string;
  secret_rotated_at?: string | null; consecutive_failures?: number; paused_until?: string | null;
  last_delivery_at?: string | null; last_delivery_outcome?: string | null; last_error_code?: string | null;
};
export type WebhookRow = {
  id: string; name: string; url: string; events: string; enabled: boolean; state: { label: string; tone: "ok" | "warn" | "gray" | "crit" };
  last: string; secret: string; created: string;
};

export function webhookRows(items: readonly WebhookLike[], now: Date = new Date()): WebhookRow[] {
  return items.map((w) => {
    const paused = w.paused_until && new Date(w.paused_until).getTime() > now.getTime();
    const state = !w.enabled ? { label: "Off", tone: "gray" as const }
      : paused ? { label: "Paused after failures", tone: "warn" as const }
        : (w.consecutive_failures ?? 0) > 0 ? { label: "Failing", tone: "crit" as const } : { label: "On", tone: "ok" as const };
    const outcome = w.last_delivery_outcome === "delivered" ? "delivered" : w.last_delivery_outcome === "failed" ? "failed" : null;
    const last = !w.last_delivery_at || !outcome ? "No call yet"
      : `${utcWhen(w.last_delivery_at)} · ${outcome}${outcome === "failed" ? ` · ${deliveryErrorText(w.last_error_code) ?? "no reason recorded"}` : ""}`;
    return {
      id: w.id, name: plainText(w.name, NAME_MAX) || "(no name)", url: plainText(w.url, 200), events: w.events.map(eventLabel).join(" · ") || "No events",
      enabled: w.enabled === true, state, last,
      secret: `Version ${Number.isInteger(w.secret_version) ? w.secret_version : "?"}${w.secret_rotated_at ? `, made ${utcWhen(w.secret_rotated_at)}` : ""}`,
      created: utcWhen(w.created_at),
    };
  });
}

export type DeliveryLike = { id: string; event: string; attempt: number; outcome: string; status_code?: number | null; error_code?: string | null; duration_ms: number; created_at: string };
export type DeliveryRow = { id: string; when: string; event: string; attempt: number; outcome: string; status: string; reason: string; duration: string };

export function deliveryRows(items: readonly DeliveryLike[]): DeliveryRow[] {
  return items.map((d) => ({
    id: d.id, when: utcWhen(d.created_at), event: eventLabel(d.event), attempt: d.attempt,
    outcome: d.outcome === "delivered" ? "Delivered" : d.outcome === "failed" ? "Failed" : "Not recognised",
    status: Number.isInteger(d.status_code) ? String(d.status_code) : "—",
    reason: deliveryErrorText(d.error_code) ?? "—",
    duration: Number.isFinite(d.duration_ms) ? `${Math.max(0, Math.round(d.duration_ms))} ms` : "—",
  }));
}

export type WebhookDraft = { name: string; url: string; events: string[] };
export type WebhookBody = { name: string; url: string; events: string[] };

/** Checks before sending (the server decides; this only catches what is plainly wrong). */
export function validateWebhook(draft: WebhookDraft): { ok: true; body: WebhookBody } | { ok: false; problem: string } {
  const name = draft.name.trim();
  const url = draft.url.trim();
  if (!name) return { ok: false, problem: "Give the webhook a name." };
  if (name.length > NAME_MAX) return { ok: false, problem: `Keep the name under ${NAME_MAX + 1} characters.` };
  if (!/^https:\/\/[^\s@/?#]+([/?][^\s#]*)?$/i.test(url) || url.length > URL_MAX) return { ok: false, problem: "Enter an https address such as https://hooks.example.com/dclab (no spaces, no user name or password, no #part)." };
  const known = new Set(WEBHOOK_EVENTS.map((e) => e.id));
  const events = WEBHOOK_EVENTS.map((e) => e.id).filter((id) => draft.events.includes(id) && known.has(id));
  if (events.length === 0) return { ok: false, problem: "Choose at least one event." };
  return { ok: true, body: { name, url, events } };
}

const WEBHOOK_ERRORS: Record<string, string> = {
  webhooks_not_permitted: "Webhooks are managed by the workspace's owners and administrators. Your role cannot change them.",
  session_required: "Webhooks can be managed only in a signed-in browser session.",
  webhooks_not_configured: "Webhooks are not set up on this server yet, so none can be added. Ask DCLab to turn them on.",
  webhook_limit_reached: `This workspace already has ${WEBHOOKS_MAX} webhooks, the most allowed (turned-off ones count too).`,
  invalid_webhook_url: "That address was refused. Use an https address on the standard port, without a user name, password or #part.",
  destination_refused: "That address points to a private or local network, so it was refused.",
  invalid_name: "Give the webhook a name.",
  not_found: "That webhook is not in this workspace any more. Reload the page.",
  notifications_paused: "Notifications are paused on this server, so a test call cannot be sent now.",
  webhook_disabled: "Turn the webhook on before sending a test call.",
  ping_limit_reached: "Too many test calls in the last hour. Wait, then try again.",
  idempotency_key_conflict: "That request was already sent with different content. Reload the page and try again.",
};

export type WebhookAction = "adding the webhook" | "making a new secret" | "sending a test call" | "turning the webhook on or off";

export function webhookError(error: unknown, action: WebhookAction): string {
  return byCode(WEBHOOK_ERRORS, apiCode(error)) ?? actionErrorSentence(error, action);
}

export function webhooksReadError(error: unknown, what: "webhooks" | "delivery log"): ReadView {
  const code = apiCode(error);
  if (apiStatus(error) === 403) return { kind: "forbidden", text: byCode(WEBHOOK_ERRORS, code) ?? "Your role cannot see this workspace's webhooks." };
  return { kind: "error", text: readErrorSentence(error, what) };
}

/** A secret from a create / rotate answer is shown only when it has the server's prefix and a sane length; a replay answers null. */
export function shownSecret(secret: unknown): string | null {
  return typeof secret === "string" && /^dclab_whsec_[A-Za-z0-9_-]{16,128}$/.test(secret) ? secret : null;
}

export const TEST_QUEUED = "A signed test call was queued. The worker tries it once; the result appears in the delivery log.";
