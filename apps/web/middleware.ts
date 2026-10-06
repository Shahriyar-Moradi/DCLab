import { NextResponse, type NextRequest } from "next/server";
import { CAPABILITY_MATRIX_VERSION, canAccessProductRoute, isStudioPath, studioRoute } from "@/lib/infrastructure/capabilities";

const SESSION_COOKIE = process.env.DCLAB_SESSION_COOKIE || "dclab_session";

function apiOrigin(): string {
  return (
    process.env.DCLAB_API_URL ||
    process.env.NEXT_PUBLIC_API_URL ||
    "http://127.0.0.1:8001"
  ).replace(/\/$/, "");
}

async function capabilitiesFromRequest(
  request: NextRequest,
): Promise<{ capabilities: Record<string, boolean> } | null> {
  const token = request.cookies.get(SESSION_COOKIE)?.value;
  if (!token) return null;
  try {
    const response = await fetch(`${apiOrigin()}/auth/me`, {
      headers: { "X-DCLab-Session": token, Accept: "application/json" },
      cache: "no-store",
    });
    if (!response.ok) return null;
    const body = (await response.json()) as Record<string, unknown>;
    if (
      body.capability_matrix_version !== CAPABILITY_MATRIX_VERSION ||
      !body.capabilities ||
      typeof body.capabilities !== "object" ||
      Array.isArray(body.capabilities)
    ) {
      return null;
    }
    const capabilities = Object.fromEntries(
      Object.entries(body.capabilities).filter((entry): entry is [string, boolean] =>
        typeof entry[1] === "boolean",
      ),
    );
    return { capabilities };
  } catch {
    return null;
  }
}

function forbidden(area: string): NextResponse {
  return new NextResponse(
    `<!doctype html><html><head><title>403 — Not authorized</title>` +
      `<meta name="viewport" content="width=device-width,initial-scale=1"></head>` +
      `<body style="font-family:system-ui;margin:0;display:grid;place-items:center;height:100vh;background:#F6F7F9;color:#111827">` +
      `<main style="text-align:center;max-width:32rem;padding:2rem">` +
      `<p style="font-size:.75rem;letter-spacing:.1em;text-transform:uppercase;color:#6B7280">403 Forbidden</p>` +
      `<h1 style="font-size:1.5rem;margin:.5rem 0">You do not have access to ${area}</h1>` +
      `<p style="color:#4B5563">Your current workspace access does not permit this area.</p>` +
      `</main></body></html>`,
    {
      status: 403,
      headers: {
        "Content-Type": "text/html; charset=utf-8",
        "Cache-Control": "no-store",
        "X-Content-Type-Options": "nosniff",
        "X-Frame-Options": "DENY",
        "Content-Security-Policy":
          "default-src 'none'; style-src 'unsafe-inline'; frame-ancestors 'none'",
      },
    },
  );
}

export async function middleware(request: NextRequest) {
  // Session probe only. Area 403 HTML is presentation; the API authorizes.
  const { pathname } = request.nextUrl;
  const principal = await capabilitiesFromRequest(request);

  if (!principal) {
    const login = new URL("/login", request.url);
    login.searchParams.set("next", pathname);
    const redirect = NextResponse.redirect(login);
    redirect.headers.set("Cache-Control", "no-store");
    return redirect;
  }

  if (!canAccessProductRoute(principal, pathname)) {
    const area = pathname.startsWith("/admin")
      ? "the admin area"
      : pathname.startsWith("/business")
        ? "the business administration area"
        : pathname.startsWith("/development") || pathname === "/dev" || pathname.startsWith("/dev/") || isStudioPath(pathname)
          ? "the Development workspace"
          : "the Business client area";
    return forbidden(area);
  }

  if (isStudioPath(pathname)) {
    const route = studioRoute(pathname);
    // Unknown section or non-UUID id: render the app's not-found page with a real 404.
    if (route === "not_found") return NextResponse.rewrite(new URL("/_studio-not-found", request.url));
    if (route !== "ok") return NextResponse.redirect(new URL(route.redirect, request.url));
  }

  return NextResponse.next();
}

export const config = {
  matcher: [
    "/admin/:path*",
    "/business/:path*",
    "/development/:path*",
    "/dev/:path*",
    "/app/:path*",
    "/lab/:path*",
    // Developer Studio (P4.1-A).
    "/home/:path*",
    "/inbox/:path*",
    "/agents/:path*",
    "/projects/:path*",
  ],
};
