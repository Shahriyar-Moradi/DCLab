import { jwtVerify } from "jose";
import { NextResponse, type NextRequest } from "next/server";

const TOKEN_COOKIE = "dclab_token";
const JWT_SECRET = process.env.JWT_SECRET ?? "dev-only-insecure-secret-change-me";

type Role =
  | "dclab_admin"
  | "dclab_developer"
  | "business_admin"
  | "business_developer"
  | "personal_developer"
  | "client_user";

const ROLES: Role[] = [
  "dclab_admin",
  "dclab_developer",
  "business_admin",
  "business_developer",
  "personal_developer",
  "client_user",
];

async function roleFromRequest(request: NextRequest): Promise<Role | null> {
  const token = request.cookies.get(TOKEN_COOKIE)?.value;
  if (!token) return null;
  try {
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
      `<p style="color:#4B5563">Your current workspace role does not permit this area.</p>` +
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
    role !== "business_developer"
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
    role !== "personal_developer"
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
