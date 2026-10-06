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
} as const;

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
  if (hasCapability(user, CAPABILITIES.applicationAccess)) return "/app/dashboards";
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
