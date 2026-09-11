import { NextRequest, NextResponse } from "next/server";

const SESSION_COOKIE = "dclab_session";
const CSRF_COOKIE = "dclab_csrf";
const BODYLESS_STATUSES = new Set([204, 205, 304]);

function apiOrigin(): string {
  return (
    process.env.DCLAB_API_URL ||
    process.env.NEXT_PUBLIC_API_URL ||
    "http://127.0.0.1:8001"
  ).replace(/\/$/, "");
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
      // The API is the cookie-policy authority. In production it refuses to
      // boot with an insecure cookie configuration; the BFF must preserve the
      // resulting attribute instead of inferring it from Next.js mode.
      secure: /(?:^|;)\s*Secure(?:;|$)/i.test(header),
      sameSite: sameSite ?? "lax",
      path: pathMatch?.[1]?.trim() || "/",
      maxAge: deleting ? 0 : maxAgeMatch ? Number(maxAgeMatch[1]) : undefined,
    });
  }
}

function forwardHeaders(req: NextRequest): Headers {
  const headers = new Headers();
  const accept = req.headers.get("accept");
  if (accept) headers.set("accept", accept);
  const contentType = req.headers.get("content-type");
  if (contentType) headers.set("content-type", contentType);
  const workspace = req.headers.get("x-workspace-id");
  if (workspace) headers.set("X-Workspace-Id", workspace);
  const requestId = req.headers.get("x-request-id");
  if (requestId) headers.set("X-Request-Id", requestId);
  const csrf = req.headers.get("x-csrf-token");
  if (csrf) headers.set("X-CSRF-Token", csrf);
  headers.set("Origin", req.headers.get("origin") || req.nextUrl.origin);
  const referer = req.headers.get("referer");
  if (referer) headers.set("Referer", referer);
  const cookieParts: string[] = [];
  const session = req.cookies.get(SESSION_COOKIE)?.value;
  if (session) {
    headers.set("X-DCLab-Session", session);
    cookieParts.push(`${SESSION_COOKIE}=${session}`);
  }
  const csrfCookie = req.cookies.get(CSRF_COOKIE)?.value;
  if (csrfCookie) cookieParts.push(`${CSRF_COOKIE}=${csrfCookie}`);
  if (cookieParts.length > 0) headers.set("cookie", cookieParts.join("; "));
  return headers;
}

async function proxy(req: NextRequest, path: string[]): Promise<NextResponse> {
  const suffix = path.join("/");
  const incoming = new URL(req.url);
  const target = `${apiOrigin()}/${suffix}${incoming.search}`;
  const init: RequestInit = {
    method: req.method,
    headers: forwardHeaders(req),
    redirect: "manual",
  };
  if (req.method !== "GET" && req.method !== "HEAD") {
    init.body = await req.arrayBuffer();
  }
  const apiResponse = await fetch(target, init);
  const body =
    req.method === "HEAD" || BODYLESS_STATUSES.has(apiResponse.status)
      ? null
      : await apiResponse.arrayBuffer();
  const headers = new Headers();
  const contentType = apiResponse.headers.get("content-type");
  if (body !== null && contentType) headers.set("content-type", contentType);
  const nextResponse = new NextResponse(body, {
    status: apiResponse.status,
    headers,
  });
  const disposition = apiResponse.headers.get("content-disposition");
  if (disposition) nextResponse.headers.set("content-disposition", disposition);
  const requestId = apiResponse.headers.get("x-request-id");
  if (requestId) nextResponse.headers.set("X-Request-Id", requestId);
  applyCopiedCookie(apiResponse, nextResponse, SESSION_COOKIE, true);
  applyCopiedCookie(apiResponse, nextResponse, CSRF_COOKIE, false);
  return nextResponse;
}

type RouteCtx = { params: Promise<{ path: string[] }> };

export const runtime = "nodejs";

export async function GET(req: NextRequest, ctx: RouteCtx) {
  const { path } = await ctx.params;
  return proxy(req, path);
}

export async function POST(req: NextRequest, ctx: RouteCtx) {
  const { path } = await ctx.params;
  return proxy(req, path);
}

export async function PUT(req: NextRequest, ctx: RouteCtx) {
  const { path } = await ctx.params;
  return proxy(req, path);
}

export async function PATCH(req: NextRequest, ctx: RouteCtx) {
  const { path } = await ctx.params;
  return proxy(req, path);
}

export async function DELETE(req: NextRequest, ctx: RouteCtx) {
  const { path } = await ctx.params;
  return proxy(req, path);
}
