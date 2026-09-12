import { NextResponse, type NextRequest } from "next/server";

const SESSION_COOKIE = process.env.DCLAB_SESSION_COOKIE || "dclab_session";

type Role =
  | "dclab_admin"
  | "dclab_developer"
  | "business_admin"
  | "business_developer"
  | "personal_developer"
  | "client_user"
  | "workspace_owner"
  | "workspace_admin"
  | "ml_engineer"
  | "viewer";

const ROLES: Role[] = [
  "dclab_admin",
  "dclab_developer",
  "business_admin",
  "business_developer",
  "personal_developer",
  "client_user",
  "workspace_owner",
  "workspace_admin",
  "ml_engineer",
  "viewer",
];

function apiOrigin(): string {
  return (
    process.env.DCLAB_API_URL ||
    process.env.NEXT_PUBLIC_API_URL ||
    "http://127.0.0.1:8001"
  ).replace(/\/$/, "");
}

async function roleFromRequest(request: NextRequest): Promise<Role | null> {
  const token = request.cookies.get(SESSION_COOKIE)?.value;
  if (!token) return null;
  try {
    const response = await fetch(`${apiOrigin()}/auth/me`, {
      headers: { "X-DCLab-Session": token, Accept: "application/json" },
      cache: "no-store",
    });
    if (!response.ok) return null;
    const body = (await response.json()) as { role?: string };
    const role = body.role;
    return typeof role === "string" && ROLES.includes(role as Role) ? (role as Role) : null;
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
      `<p style="color:#4B5563">Your current workspace role does not permit this area.</p>` +
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
  const role = await roleFromRequest(request);

  if (!role) {
    const login = new URL("/login", request.url);
    login.searchParams.set("next", pathname);
    const redirect = NextResponse.redirect(login);
    redirect.headers.set("Cache-Control", "no-store");
    return redirect;
  }

  if (
    pathname.startsWith("/admin") &&
    role !== "dclab_admin" &&
    role !== "dclab_developer"
  ) {
    return forbidden("the admin area");
  }

  if (
    pathname.startsWith("/business") &&
    role !== "dclab_admin" &&
    role !== "dclab_developer" &&
    role !== "business_admin" &&
    role !== "business_developer" &&
    role !== "workspace_owner" &&
    role !== "workspace_admin" &&
    role !== "ml_engineer" &&
    role !== "viewer"
  ) {
    return forbidden("the business administration area");
  }

  if (pathname.startsWith("/app") && role === "personal_developer") {
    return forbidden("the Business client area");
  }

  if (
    pathname.startsWith("/development") &&
    role !== "dclab_admin" &&
    role !== "dclab_developer" &&
    role !== "business_admin" &&
    role !== "business_developer" &&
    role !== "personal_developer" &&
    role !== "workspace_owner" &&
    role !== "workspace_admin" &&
    role !== "ml_engineer" &&
    role !== "viewer"
  ) {
    return forbidden("the Development workspace");
  }

  return NextResponse.next();
}

export const config = {
  matcher: [
    "/admin/:path*",
    "/business/:path*",
    "/development/:path*",
    "/app/:path*",
    "/lab/:path*",
  ],
};
