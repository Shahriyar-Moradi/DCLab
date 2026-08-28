"use client";

export const TOKEN_COOKIE = "dclab_token";
export const SESSION_CHANGED_EVENT = "dclab-session-changed";
const DEFAULT_MAX_AGE_SECONDS = 60 * 60 * 24 * 30;

export type SessionUser = {
  id: string;
  email: string;
  role: "dclab_admin" | "client_user";
  full_name: string;
  workspace_id: string | null;
};

type TokenPayload = {
  sub: string;
  email: string;
  role: SessionUser["role"];
  full_name?: string;
  workspace_id: string | null;
  exp: number;
};

function decodePayload(token: string): TokenPayload | null {
  const parts = token.split(".");
  if (parts.length < 2 || !parts[1]) return null;
  try {
    return JSON.parse(atob(parts[1].replace(/-/g, "+").replace(/_/g, "/"))) as TokenPayload;
  } catch {
    return null;
  }
}

export function cookieMaxAgeSeconds(token: string): number {
  const payload = decodePayload(token);
  if (!payload?.exp) return DEFAULT_MAX_AGE_SECONDS;
  return Math.max(60, Math.floor(payload.exp - Date.now() / 1000));
}

function notifySessionChanged(): void {
  if (typeof window === "undefined") return;
  window.dispatchEvent(new Event(SESSION_CHANGED_EVENT));
}

/**
 * The token lives in a cookie rather than localStorage so Next middleware can
 * verify it before an /admin page is ever rendered. Max-Age matches the JWT
 * expiry so the browser keeps the person signed in until they click Sign out
 * (or the token actually expires).
 */
export function storeToken(token: string): void {
  const secure = window.location.protocol === "https:" ? "; Secure" : "";
  const maxAge = cookieMaxAgeSeconds(token);
  document.cookie = `${TOKEN_COOKIE}=${encodeURIComponent(token)}; Path=/; Max-Age=${maxAge}; SameSite=Lax${secure}`;
  notifySessionChanged();
}

export function readToken(): string | null {
  if (typeof document === "undefined") return null;
  const match = document.cookie.match(new RegExp(`(?:^|; )${TOKEN_COOKIE}=([^;]*)`));
  return match ? decodeURIComponent(match[1]) : null;
}

export function clearToken(): void {
  document.cookie = `${TOKEN_COOKIE}=; Path=/; Max-Age=0; SameSite=Lax`;
  notifySessionChanged();
}

export function roleLabel(role: SessionUser["role"]): string {
  return role === "dclab_admin" ? "Admin" : "Business Client";
}

export function displayName(user: SessionUser): string {
  return user.full_name?.trim() || user.email;
}

/** Reads role/email out of the token for rendering only — never for access control. */
export function readSessionUser(): SessionUser | null {
  const token = readToken();
  if (!token) return null;
  const decoded = decodePayload(token);
  if (!decoded) return null;
  if (decoded.exp * 1000 < Date.now()) {
    clearToken();
    return null;
  }
  if (decoded.role !== "dclab_admin" && decoded.role !== "client_user") return null;
  return {
    id: decoded.sub,
    email: decoded.email,
    role: decoded.role,
    full_name: decoded.full_name || decoded.email,
    workspace_id: decoded.workspace_id,
  };
}
