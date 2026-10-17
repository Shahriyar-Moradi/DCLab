/**
 * D1 (founder decision 8): "View as", an admin-only preview of what each role's sidebar and screen
 * availability look like. Client side only: it never changes the session, permissions or any API call
 * (the API still uses the real session), and it resets when the page is reloaded.
 */
import { CAPABILITIES, canAccessProductRoute, hasCapability } from "../infrastructure/capabilities.ts";
import type { ResolvedSidebarGroup, SidebarTarget } from "../../components/studio/sidebar-config.ts";
import { ADMIN, ANALYST, BUSINESS_USER, DATA_SCIENTIST } from "./role-labels.ts";
import { businessNavigationForUser, studioNavigationForUser } from "./studio-navigation.ts";

export type PreviewRole = "data_scientist" | "analyst" | "business_user";
type Principal = { capabilities: Record<string, boolean> };

export const PREVIEW_ROLES: PreviewRole[] = ["data_scientist", "analyst", "business_user"];

export const PREVIEW_ROLE_LABEL: Record<PreviewRole, string> = {
  data_scientist: DATA_SCIENTIST,
  analyst: ANALYST,
  business_user: BUSINESS_USER,
};

/** The select's options: the empty value is the admin's own view. */
export const VIEW_AS_OPTIONS: Array<{ value: "" | PreviewRole; label: string }> = [
  { value: "", label: `${ADMIN} (your own view)` },
  ...PREVIEW_ROLES.map((role) => ({ value: role, label: PREVIEW_ROLE_LABEL[role] })),
];

/** The stored role whose server description (Settings "What each role can do") a preview shows; a business user has none there. */
export const PREVIEW_ROLE_VALUE: Record<PreviewRole, string | null> = {
  data_scientist: "ml_engineer",
  analyst: "viewer",
  business_user: null,
};

export const PREVIEW_NOTE = "Preview: you still have admin rights; nothing is changed.";

/**
 * What the server gives each role in a business workspace (mirror of `_resolve_effective_capabilities` in
 * apps/api/app/services/workspace_capability_service.py). Used only to draw the previewed sidebar and to say
 * which screens open; no platform rights, nothing sent anywhere.
 */
const ROLE_CAPABILITIES: Record<PreviewRole, Record<string, boolean>> = {
  data_scientist: {
    [CAPABILITIES.accountAccess]: true,
    [CAPABILITIES.workspaceRead]: true,
    [CAPABILITIES.workspaceWrite]: true,
    [CAPABILITIES.workspaceExecuteMl]: true,
    [CAPABILITIES.applicationAccess]: true,
    [CAPABILITIES.businessAccess]: true,
    [CAPABILITIES.developmentAccess]: true,
  },
  analyst: {
    [CAPABILITIES.accountAccess]: true,
    [CAPABILITIES.workspaceRead]: true,
    [CAPABILITIES.applicationAccess]: true,
    [CAPABILITIES.businessAccess]: true,
    [CAPABILITIES.developmentAccess]: true,
  },
  // A business user (client_user) reads the Business view and the legacy Labs pages; no Studio screens.
  business_user: {
    [CAPABILITIES.accountAccess]: true,
    [CAPABILITIES.workspaceRead]: true,
    [CAPABILITIES.workspaceWrite]: true,
    [CAPABILITIES.applicationAccess]: true,
  },
};

export function isPreviewRole(value: unknown): value is PreviewRole {
  return typeof value === "string" && (PREVIEW_ROLES as string[]).includes(value);
}

/** Only admins of the active workspace (they may manage its members) see the control. */
export function canUseViewAs(user: Principal | null | undefined): boolean {
  return hasCapability(user, CAPABILITIES.workspaceManageMembers);
}

/** A stand-in principal with the previewed role's capabilities (a fresh object: the real session is never touched). */
export function previewPrincipal(role: PreviewRole): Principal {
  return { capabilities: { ...ROLE_CAPABILITIES[role] } };
}

/** The sidebar the previewed role would see. A business user gets the Business view entry point only. */
export function previewNavigation(
  role: PreviewRole,
  projectId?: string,
  targets?: Partial<Record<SidebarTarget, string | null>>,
): ResolvedSidebarGroup[] {
  const principal = previewPrincipal(role);
  return role === "business_user" ? businessNavigationForUser(principal) : studioNavigationForUser(principal, projectId, targets);
}

/** Whether the previewed role could open this path (the same route rules the middleware applies). */
export function previewCanOpen(role: PreviewRole, pathname: string): boolean {
  return canAccessProductRoute(previewPrincipal(role), pathname);
}

/** What the "not open for this role" card says. */
export function previewGateText(role: PreviewRole): { title: string; body: string } {
  const label = PREVIEW_ROLE_LABEL[role];
  return {
    title: `This screen is not open to the ${label} role`,
    body: role === "business_user"
      ? "A business user sees only the Business view: plain outcomes and the latest list."
      : `A person with the ${label} role cannot open this screen.`,
  };
}

/** Kept for the browser tab only, so the preview survives moving between screens and ends on a reload. */
export const VIEW_AS_STORAGE_KEY = "dclab.viewAsPreview";

type KeyStore = Pick<Storage, "getItem" | "setItem" | "removeItem">;

/** The stored preview, or null. A reload (navigation type "reload") clears it: the preview resets on reload. */
export function restorePreview(storage: KeyStore | null, navigationType: string | undefined): PreviewRole | null {
  if (!storage) return null;
  try {
    if (navigationType === "reload") {
      storage.removeItem(VIEW_AS_STORAGE_KEY);
      return null;
    }
    const value = storage.getItem(VIEW_AS_STORAGE_KEY);
    return isPreviewRole(value) ? value : null;
  } catch {
    return null;
  }
}

export function storePreview(storage: KeyStore | null, role: PreviewRole | null): void {
  if (!storage) return;
  try {
    if (role) storage.setItem(VIEW_AS_STORAGE_KEY, role);
    else storage.removeItem(VIEW_AS_STORAGE_KEY);
  } catch {
    // Storage can be blocked; the preview then lasts until the next page load.
  }
}
