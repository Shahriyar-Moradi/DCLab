/** Browser BFF. Never a second authorization layer. Never log bodies or credentials. */
import { NextRequest, NextResponse } from "next/server";

/** Process-protection bounds, not a product ingest policy. */
export const DEFAULT_MAX_UPLOAD_BYTES = 256 * 1024 * 1024;
export const DEFAULT_MAX_DOWNLOAD_BYTES = 512 * 1024 * 1024;
export const REQUEST_ID_HEADER = "X-Request-Id";
export const SESSION_HEADER = "X-DCLab-Session";
export const BACKEND_UNAVAILABLE = "backend unavailable";
export const PAYLOAD_TOO_LARGE = "payload too large";
export const RESPONSE_TOO_LARGE = "response too large";

const BODYLESS_STATUSES = new Set([204, 205, 304]);
const FORWARD_REQUEST_HEADERS = [
  "accept",
  "content-type",
  "x-workspace-id",
  "x-csrf-token",
  "origin",
  "referer",
  "user-agent",
] as const;

function envInt(name: string, fallback: number): number {
  const raw = Number(process.env[name]);
  return Number.isFinite(raw) && raw > 0 ? Math.floor(raw) : fallback;
}

export function sessionCookieName(): string {
  return process.env.DCLAB_SESSION_COOKIE || "dclab_session";
}

export function csrfCookieName(): string {
  return process.env.DCLAB_CSRF_COOKIE || "dclab_csrf";
}

export function maxUploadBytes(): number {
  return envInt("DCLAB_BFF_MAX_UPLOAD_BYTES", DEFAULT_MAX_UPLOAD_BYTES);
}

export function maxDownloadBytes(): number {
  return envInt("DCLAB_BFF_MAX_DOWNLOAD_BYTES", DEFAULT_MAX_DOWNLOAD_BYTES);
}

export function apiOrigin(): string {
  return (
    process.env.DCLAB_API_URL ||
    process.env.NEXT_PUBLIC_API_URL ||
    "http://127.0.0.1:8001"
  ).replace(/\/$/, "");
}

export function resolveRequestId(incoming: string | null | undefined): string {
  const raw = (incoming || "").trim();
  if (!raw || raw.length > 128 || /[\r\n]/.test(raw)) {
    return crypto.randomUUID();
  }
  return raw;
}

export function parseContentLength(value: string | null): number | null {
  if (!value) return null;
  const parsed = Number(value);
  if (!Number.isFinite(parsed) || parsed < 0) return null;
  return Math.floor(parsed);
}

function setCookieHeaders(response: Response): string[] {
  const headers = response.headers as Headers & { getSetCookie?: () => string[] };
  if (typeof headers.getSetCookie === "function") {
    return headers.getSetCookie();
  }
  const single = response.headers.get("set-cookie");
  return single ? [single] : [];
}

function applyCopiedCookie(
  apiResponse: Response,
  nextResponse: NextResponse,
  name: string,
  httpOnly: boolean,
): void {
  for (const header of setCookieHeaders(apiResponse)) {
    if (!header.toLowerCase().startsWith(`${name}=`)) continue;
    const first = header.split(";")[0] ?? "";
    const rawValue = first.slice(name.length + 1);
    const value = decodeURIComponent(rawValue);
    const maxAgeMatch = header.match(/Max-Age=(\d+)/i);
    const pathMatch = header.match(/(?:^|;)\s*Path=([^;]+)/i);
    const sameSiteMatch = header.match(/(?:^|;)\s*SameSite=(Lax|Strict|None)/i);
    const deleting = value === "" || /max-age=0/i.test(header);
    const sameSite = sameSiteMatch?.[1]?.toLowerCase() as
      | "lax"
      | "strict"
      | "none"
      | undefined;
    nextResponse.cookies.set({
      name,
      value: deleting ? "" : value,
      httpOnly,
      // The API is the cookie-policy authority. Copy attributes; do not infer
      // Secure from Next.js mode.
      secure: /(?:^|;)\s*Secure(?:;|$)/i.test(header),
      sameSite: sameSite ?? "lax",
      path: pathMatch?.[1]?.trim() || "/",
      maxAge: deleting ? 0 : maxAgeMatch ? Number(maxAgeMatch[1]) : undefined,
    });
  }
}

export function contractHeaders(requestId: string, extra?: HeadersInit): Headers {
  const headers = new Headers(extra);
  headers.set("Cache-Control", "no-store");
  headers.set(REQUEST_ID_HEADER, requestId);
  headers.set("X-Content-Type-Options", "nosniff");
  return headers;
}

export function jsonError(status: number, detail: string, requestId: string): NextResponse {
  return NextResponse.json(
    { detail },
    { status, headers: contractHeaders(requestId, { "content-type": "application/json" }) },
  );
}

export function forwardHeaders(req: NextRequest, requestId: string): Headers {
  const headers = new Headers();
  headers.set(REQUEST_ID_HEADER, requestId);
  for (const name of FORWARD_REQUEST_HEADERS) {
    const value = req.headers.get(name);
    if (!value) continue;
    if (name === "x-workspace-id") headers.set("X-Workspace-Id", value);
    else if (name === "x-csrf-token") headers.set("X-CSRF-Token", value);
    else headers.set(name, value);
  }
  if (!headers.has("origin")) {
    headers.set("Origin", req.nextUrl.origin);
  }
  const cookieParts: string[] = [];
  const sessionName = sessionCookieName();
  const csrfName = csrfCookieName();
  const session = req.cookies.get(sessionName)?.value;
  if (session) {
    headers.set(SESSION_HEADER, session);
    cookieParts.push(`${sessionName}=${session}`);
  }
  const csrfCookie = req.cookies.get(csrfName)?.value;
  if (csrfCookie) cookieParts.push(`${csrfName}=${csrfCookie}`);
  if (cookieParts.length > 0) headers.set("cookie", cookieParts.join("; "));
  return headers;
}

async function readBoundedUpload(
  req: NextRequest,
  maxBytes: number,
): Promise<{ body: BodyInit | undefined; tooLarge: boolean; duplex: boolean }> {
  if (req.method === "GET" || req.method === "HEAD") {
    return { body: undefined, tooLarge: false, duplex: false };
  }
  const declared = parseContentLength(req.headers.get("content-length"));
  if (declared !== null && declared > maxBytes) {
    return { body: undefined, tooLarge: true, duplex: false };
  }
  if (declared !== null && req.body) {
    return { body: req.body, tooLarge: false, duplex: true };
  }
  const buffer = await req.arrayBuffer();
  if (buffer.byteLength > maxBytes) {
    return { body: undefined, tooLarge: true, duplex: false };
  }
  return { body: buffer.byteLength > 0 ? buffer : undefined, tooLarge: false, duplex: false };
}

function applyUpstreamCookies(apiResponse: Response, nextResponse: NextResponse): void {
  applyCopiedCookie(apiResponse, nextResponse, sessionCookieName(), true);
  applyCopiedCookie(apiResponse, nextResponse, csrfCookieName(), false);
}

export async function proxyBackend(
  req: NextRequest,
  path: string[],
  fetchImpl: typeof fetch = fetch,
): Promise<NextResponse> {
  const requestId = resolveRequestId(req.headers.get("x-request-id"));
  const upload = await readBoundedUpload(req, maxUploadBytes());
  if (upload.tooLarge) {
    return jsonError(413, PAYLOAD_TOO_LARGE, requestId);
  }
  const suffix = path.join("/");
  const incoming = new URL(req.url);
  const target = `${apiOrigin()}/${suffix}${incoming.search}`;
  const init: RequestInit & { duplex?: "half" } = {
    method: req.method,
    headers: forwardHeaders(req, requestId),
    redirect: "manual",
  };
  if (upload.body !== undefined) {
    init.body = upload.body;
    if (upload.duplex) init.duplex = "half";
  }
  let apiResponse: Response;
  try {
    apiResponse = await fetchImpl(target, init);
  } catch {
    return jsonError(502, BACKEND_UNAVAILABLE, requestId);
  }
  const downloadLimit = maxDownloadBytes();
  const declaredDownload = parseContentLength(apiResponse.headers.get("content-length"));
  if (declaredDownload !== null && declaredDownload > downloadLimit) {
    return jsonError(502, RESPONSE_TOO_LARGE, requestId);
  }
  const bodyless =
    req.method === "HEAD" || BODYLESS_STATUSES.has(apiResponse.status);
  const headers = contractHeaders(
    apiResponse.headers.get(REQUEST_ID_HEADER) || requestId,
  );
  const contentType = apiResponse.headers.get("content-type");
  if (!bodyless && contentType) headers.set("content-type", contentType);
  const disposition = apiResponse.headers.get("content-disposition");
  if (disposition) headers.set("content-disposition", disposition);
  const nextResponse = new NextResponse(bodyless ? null : apiResponse.body, {
    status: apiResponse.status,
    headers,
  });
  applyUpstreamCookies(apiResponse, nextResponse);
  return nextResponse;
}
