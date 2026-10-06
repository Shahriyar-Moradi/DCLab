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
} as const;

/** Developer Studio routes (STUDIO_DESIGN section 3): development workspace role only. */
const STUDIO_PREFIXES = ["/home", "/inbox", "/projects"] as const;

export function isStudioPath(pathname: string): boolean {
  return STUDIO_PREFIXES.some((prefix) => pathname === prefix || pathname.startsWith(`${prefix}/`));
}

const UUID = "[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}";
const PROJECT_SECTIONS = "lab|pipeline|graph|data|experiments|improve|models|monitoring|decisions";
const STUDIO_ROUTE = new RegExp(
  `^/(?:home|inbox|projects(?:/new|/${UUID}(?:/(?:${PROJECT_SECTIONS})|/experiments/(?:${UUID}|new))?)?)$`,
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

export function defaultProductRoute(user: CapabilityPrincipal | null | undefined): string {
  if (hasCapability(user, CAPABILITIES.platformRead)) return "/admin/businesses";
  if (hasCapability(user, CAPABILITIES.businessAccess)) return "/business";
  if (hasCapability(user, CAPABILITIES.developmentAccess)) return "/development";
  if (hasCapability(user, CAPABILITIES.applicationAccess)) {
    return hasCapability(user, CAPABILITIES.legacyDecisionLayer) ? "/app/dashboards" : "/app/labs";
  }
  return "/app/settings";
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
  // Studio design kit: development workspace role only (the page also 404s in production).
  if (pathname === "/dev" || pathname.startsWith("/dev/")) {
    return hasCapability(user, CAPABILITIES.developmentAccess);
  }
  if (isStudioPath(pathname)) {
    return hasCapability(user, CAPABILITIES.developmentAccess);
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
  "/business",
  "/development",
  "/admin/businesses",
  "/home",
  "/inbox",
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
