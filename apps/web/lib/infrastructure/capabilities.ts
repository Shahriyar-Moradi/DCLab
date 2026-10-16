type CapabilityPrincipal = { capabilities: Record<string, boolean> };

/** Server-defined display capabilities. The API remains the authorization boundary. */
export const CAPABILITY_MATRIX_VERSION = "workspace-capabilities.v1";
export const CAPABILITIES = {
  accountAccess: "account_access",
  platformRead: "platform_read",
  platformWrite: "platform_write",
  workspaceRead: "workspace_read",
  workspaceWrite: "workspace_write",
  workspaceManageMembers: "workspace_manage_members",
  workspaceExecuteMl: "workspace_execute_ml",
  applicationAccess: "application_access",
  businessAccess: "business_access",
  developmentAccess: "development_access",
  openaiPipelineAudit: "openai_pipeline_audit",
  deepAudit: "deep_audit",
  /** P4.1-A: frozen Decision.ai vertical pages (presentation only; off by default). */
  legacyDecisionLayer: "legacy_decision_layer_enabled",
  /** P4.12-A: frozen Decision.ai marketing pages (presentation only; off by default). */
  legacyMarketingPages: "legacy_marketing_pages_enabled",
} as const;

/** Developer Studio routes (STUDIO_DESIGN section 3): development workspace role only. */
const STUDIO_PREFIXES = ["/home", "/inbox", "/agents", "/settings", "/projects"] as const;

export function isStudioPath(pathname: string): boolean {
  return STUDIO_PREFIXES.some((prefix) => pathname === prefix || pathname.startsWith(`${prefix}/`));
}

/** Business view (P7.6-A): `/outcomes` only, for any workspace reader who may open the business pages. */
export function isBusinessViewPath(pathname: string): boolean {
  return pathname === "/outcomes";
}

/** Operator console (P8.5-UI): `/operator` (other paths below it are 404), for DCLab platform administrators (`platform_write`); the API authorizes. */
export function isOperatorPath(pathname: string): boolean {
  return pathname === "/operator" || pathname.startsWith("/operator/");
}

const UUID = "[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}";
const PROJECT_SECTIONS = "lab|pipeline|graph|data|experiments|improve|models|predictions|monitoring|decisions";
const STUDIO_ROUTE = new RegExp(
  `^/(?:home|inbox|agents|settings|projects(?:/new|/${UUID}(?:/(?:${PROJECT_SECTIONS})|/experiments/(?:${UUID}|new|compare)|/(?:data|splits|features|models|pipeline)/${UUID})?)?)$`,
);

/**
 * Where a Studio path goes before rendering: "ok", "not_found" (unknown section, non-UUID id)
 * or the project's landing page for `/projects/{id}`. Real 404/307 statuses come from here.
 */
export function studioRoute(pathname: string): "ok" | "not_found" | { redirect: string } {
  if (!STUDIO_ROUTE.test(pathname)) return "not_found";
  const parts = pathname.split("/");
  return parts.length === 3 && parts[1] === "projects" && parts[2] !== "new" ? { redirect: `${pathname}/experiments` } : "ok";
}

export function hasCapability(
  user: CapabilityPrincipal | null | undefined,
  capability: string,
): boolean {
  return user?.capabilities[capability] === true;
}

/**
 * Where each role lands after login (STUDIO_DESIGN section 3). Developers and admins with a
 * workspace open the project list (`/home` is one click away in the sidebar); a platform administrator
 * with no workspace opens the operator console `/operator` (P8.5-UI) and a platform developer with no
 * workspace keeps `/admin/monitoring`; a business-only administrator keeps `/business`; business users
 * (clients) open the Business view `/outcomes` (P7.6-A; Labs stays reachable).
 */
export function defaultProductRoute(user: CapabilityPrincipal | null | undefined): string {
  const platform = hasCapability(user, CAPABILITIES.platformRead);
  const development = hasCapability(user, CAPABILITIES.developmentAccess);
  if (development) return "/projects";
  if (platform && hasCapability(user, CAPABILITIES.platformWrite)) return "/operator";
  if (platform) return "/admin/monitoring";
  if (hasCapability(user, CAPABILITIES.businessAccess)) return "/business";
  if (hasCapability(user, CAPABILITIES.applicationAccess)) return "/outcomes";
  return "/app/settings";
}

/**
 * A post-login `next=` target is honored only when it is a plain same-origin path: one leading
 * slash, no scheme, authority, backslash, control character, dot segment or percent-encoding,
 * and it must be a product route this user may open. Anything else returns null.
 */
export function safeNextPath(
  user: CapabilityPrincipal | null | undefined,
  requested: string | null | undefined,
): string | null {
  if (!requested || requested.length > 512) return null;
  if (!/^\/(?!\/)[A-Za-z0-9\-._~/?=&]*$/.test(requested)) return null;
  const path = requested.split(/[?#]/)[0];
  if (path.split("/").some((segment) => segment === "." || segment === "..")) return null;
  return canAccessProductRoute(user, path) ? requested : null;
}

/** Frozen Decision.ai marketing pages: redirected to `/` unless `legacy_marketing_pages_enabled`. */
export const LEGACY_MARKETING_PREFIXES = [
  "/industries",
  "/solutions",
  "/pricing",
  "/showcase",
  "/platform",
  "/company",
  "/resources",
] as const;

export function isLegacyMarketingPath(pathname: string): boolean {
  return LEGACY_MARKETING_PREFIXES.some((prefix) => pathname === prefix || pathname.startsWith(`${prefix}/`));
}

export function canAccessProductRoute(
  user: CapabilityPrincipal | null | undefined,
  pathname: string,
): boolean {
  if (!user) return false;
  if (pathname === "/app/settings" || pathname.startsWith("/app/settings/")) {
    return hasCapability(user, CAPABILITIES.accountAccess);
  }
  if (pathname === "/admin" || pathname.startsWith("/admin/")) {
    return hasCapability(user, CAPABILITIES.platformRead);
  }
  // Founder decision 2026-10-16: the operator console is for platform administrators only.
  if (isOperatorPath(pathname)) {
    return hasCapability(user, CAPABILITIES.platformRead) && hasCapability(user, CAPABILITIES.platformWrite);
  }
  // Studio design kit: development workspace role only (the page also 404s in production).
  if (pathname === "/dev" || pathname.startsWith("/dev/")) {
    return hasCapability(user, CAPABILITIES.developmentAccess);
  }
  if (isStudioPath(pathname)) {
    return hasCapability(user, CAPABILITIES.developmentAccess);
  }
  if (isBusinessViewPath(pathname)) {
    return hasCapability(user, CAPABILITIES.applicationAccess);
  }
  if (pathname === "/business" || pathname.startsWith("/business/")) {
    return hasCapability(user, CAPABILITIES.businessAccess);
  }
  if (pathname === "/development" || pathname.startsWith("/development/")) {
    return hasCapability(user, CAPABILITIES.developmentAccess);
  }
  if (
    pathname === "/app" ||
    pathname.startsWith("/app/") ||
    pathname === "/lab" ||
    pathname.startsWith("/lab/")
  ) {
    return hasCapability(user, CAPABILITIES.applicationAccess);
  }
  return false;
}

const WORKSPACE_SWITCH_STABLE_ROUTES = new Set([
  "/app/dashboards",
  "/app/insights",
  "/app/opportunities",
  "/app/opportunities/upload",
  "/app/decisions",
  "/app/labs",
  "/outcomes",
  "/business",
  "/development",
  "/admin/businesses",
  "/home",
  "/inbox",
  "/agents",
  "/projects",
]);

export function safeWorkspaceSwitchDestination(
  user: CapabilityPrincipal,
  pathname: string,
): string {
  if (WORKSPACE_SWITCH_STABLE_ROUTES.has(pathname) && canAccessProductRoute(user, pathname)) {
    return pathname;
  }
  return defaultProductRoute(user);
}
