/**
 * D8b: fixed sentences for failed requests. Pure, so it runs under `npm run test:components`.
 *
 * The server's own message text (the /v1 `error.message`, a legacy `detail`, an exception's `message`) is never shown: a
 * sentence is chosen by the error code (own keys only, so a code such as `__proto__` falls through) and then by the HTTP
 * status. Without a status (no answer, or an unreadable one) the sentence says nothing was confirmed.
 */

export type ErrorLike = { status?: unknown; body?: unknown } | null | undefined;

/** The HTTP status of a failed request, or null when the request never got an answer. */
export function apiStatus(error: unknown): number | null {
  const status = (error as ErrorLike)?.status;
  return typeof status === "number" && Number.isInteger(status) ? status : null;
}

/** The closed error code of a failed request (lowercase), from the /v1 envelope or a legacy `detail.code`; "" when none. */
export function apiCode(error: unknown): string {
  const body = (error as ErrorLike)?.body;
  if (!body || typeof body !== "object") return "";
  const inner = (body as { error?: unknown }).error;
  const fromEnvelope = inner && typeof inner === "object" ? (inner as { code?: unknown }).code : undefined;
  const detail = (body as { detail?: unknown }).detail;
  const fromDetail = detail && typeof detail === "object" ? (detail as { code?: unknown }).code : undefined;
  const code = typeof fromEnvelope === "string" ? fromEnvelope : typeof fromDetail === "string" ? fromDetail : "";
  return /^[a-z0-9_.-]{1,64}$/i.test(code) ? code.toLowerCase() : "";
}

/** Own-key lookup of a code in a fixed table (never reaches a prototype). */
export function byCode<T>(table: Record<string, T>, code: string): T | undefined {
  return code && Object.hasOwn(table, code) ? table[code] : undefined;
}

/** A failed read in fixed words keyed by status only. `what` is a fixed noun of the page ("experiment list"). */
export function readErrorSentence(error: unknown, what: string): string {
  const status = apiStatus(error);
  if (status === null) return `Could not load the ${what}. Check your connection and reload the page.`;
  if (status === 401) return `Could not load the ${what}. Your session ended; sign in again.`;
  if (status === 403) return `Your role cannot see the ${what}.`;
  if (status === 404) return `The ${what} does not exist in the active workspace, or you cannot see it.`;
  if (status === 429) return `Could not load the ${what}. Too many requests at once; wait a moment and reload.`;
  if (status >= 500) return `Could not load the ${what}. DCLab had a problem on its side; reload in a moment.`;
  return `Could not load the ${what}. Reload the page.`;
}

/** A refused or failed write in fixed words keyed by status only. `action` is a fixed phrase ("saving the webhook"). */
export function actionErrorSentence(error: unknown, action: string): string {
  const status = apiStatus(error);
  if (status === null) return `Nothing was confirmed (${action}). Check your connection, reload the page and look before you try again.`;
  if (status === 401) return `Your session ended while ${action}. Sign in again; nothing was changed.`;
  if (status === 403) return `Your role cannot do this (${action}). Nothing was changed.`;
  if (status === 404) return `What you were changing is not in the active workspace any more (${action}). Reload the page.`;
  if (status === 409 || status === 412 || status === 428) return `This changed while you were looking (${action}). Reload the page and try again.`;
  if (status === 413) return `What you sent is too large (${action}). Nothing was changed.`;
  if (status === 400 || status === 422) return `The request was refused (${action}). Check what you entered and try again.`;
  if (status === 429) return `Too many requests at once (${action}). Wait a moment, then try again.`;
  if (status >= 500) return `DCLab had a problem on its side (${action}). Reload, check whether it happened, then try again.`;
  return `Something went wrong (${action}). Reload the page and try again.`;
}
