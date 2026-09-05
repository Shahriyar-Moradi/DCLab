import { jwtVerify } from "jose";
import { NextResponse, type NextRequest } from "next/server";

const TOKEN_COOKIE = "dclab_token";
const JWT_SECRET = process.env.JWT_SECRET ?? "dev-only-insecure-secret-change-me";

type Role =
  | "dclab_admin"
  | "dclab_developer"
  | "business_admin"
  | "business_developer"
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
  "client_user",
  "workspace_owner",
  "workspace_admin",
  "ml_engineer",
  "viewer",
];

async function roleFromRequest(request: NextRequest): Promise<Role | null> {
  const token = request.cookies.get(TOKEN_COOKIE)?.value;
  if (!token) return null;
  try {
    // Verify the signature here rather than just decoding: a hand-edited cookie
    // claiming role=dclab_admin must not get past this point.
    const { payload } = await jwtVerify(token, new TextEncoder().encode(JWT_SECRET));
    const role = payload.role;
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
      `<p style="color:#4B5563">This area is restricted to DCLab platform members.</p>` +
      `<p style="margin-top:1.5rem"><a href="/app/dashboards" style="color:#2563EB">Return to your dashboard</a></p>` +
      `</main></body></html>`,
    { status: 403, headers: { "Content-Type": "text/html; charset=utf-8" } },
  );
}

export async function middleware(request: NextRequest) {
  const { pathname } = request.nextUrl;
  const role = await roleFromRequest(request);

  if (!role) {
    const login = new URL("/login", request.url);
    login.searchParams.set("next", pathname);
    return NextResponse.redirect(login);
  }

  // A business member typing a platform URL directly is blocked with a real 403.
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

  return NextResponse.next();
}

export const config = {
  matcher: ["/admin/:path*", "/business/:path*", "/app/:path*", "/lab/:path*"],
};
