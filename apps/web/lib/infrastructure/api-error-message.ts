/**
 * The readable message of an error body: legacy ``{detail}`` (a string, or an object with a
 * ``message`` such as a 429 ``quota_exceeded`` on a legacy path) and the /v1
 * ``{error: {message}}`` envelope. Never ``[object Object]``.
 */
export function errorMessage(body: unknown): string | null {
  if (typeof body !== "object" || !body) return null;
  if ("detail" in body) {
    const detail = (body as { detail: unknown }).detail;
    if (typeof detail === "string") return detail;
    if (detail && typeof detail === "object" && typeof (detail as { message?: unknown }).message === "string") {
      return (detail as { message: string }).message;
    }
    return detail == null || typeof detail === "object" ? null : String(detail);
  }
  const error = (body as { error?: { message?: unknown } }).error;
  return error && typeof error.message === "string" ? error.message : null;
}
