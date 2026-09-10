"use client";

export const CSRF_COOKIE = "dclab_csrf";
export const CSRF_HEADER = "X-CSRF-Token";

function readCookie(name: string): string | null {
  if (typeof document === "undefined") return null;
  const parts = document.cookie.split(";");
  for (const part of parts) {
    const trimmed = part.trim();
    if (!trimmed.startsWith(`${name}=`)) continue;
    return decodeURIComponent(trimmed.slice(name.length + 1));
  }
  return null;
}

/** Read the non-HttpOnly CSRF cookie. Never used for the session cookie. */
export function readCsrfToken(): string | null {
  return readCookie(CSRF_COOKIE);
}

export async function ensureCsrfToken(apiRoot: string): Promise<string> {
  const existing = readCsrfToken();
  if (existing) return existing;
  const response = await fetch(`${apiRoot}/auth/csrf`, {
    credentials: "include",
    headers: { Accept: "application/json" },
  });
  if (!response.ok) {
    throw new Error("Could not issue a CSRF token.");
  }
  const body = (await response.json()) as { csrf_token?: unknown };
  return typeof body.csrf_token === "string" ? body.csrf_token : "";
}
