/**
 * D1 (founder decision 8, 2026-10-16): the one place that turns a stored role value into the words people see.
 * Owner and administrator both read "Admin" (the owner keeps "Owner" as a second detail), ml_engineer reads
 * "Data scientist", viewer reads "Analyst (view only)" and the business client reads "Business user".
 * Legacy stored values read as the role the server translates them to (authorization_service.py).
 * Display only: the server decides every request.
 */
import { plainText } from "./command-search.ts";

export type RoleWords = { label: string; detail: string | null };

/** Every role value the API can send (users.role and workspace memberships). */
export const ROLE_VALUES = [
  "workspace_owner",
  "workspace_admin",
  "ml_engineer",
  "viewer",
  "client_user",
  "business_admin",
  "business_developer",
  "personal_developer",
  "dclab_admin",
  "dclab_developer",
] as const;

export type RoleValue = (typeof ROLE_VALUES)[number];

export const ADMIN = "Admin";
export const DATA_SCIENTIST = "Data scientist";
export const ANALYST = "Analyst (view only)";
export const BUSINESS_USER = "Business user";

const ROLE_WORDS: Record<RoleValue, RoleWords> = {
  workspace_owner: { label: ADMIN, detail: "Owner" },
  workspace_admin: { label: ADMIN, detail: null },
  ml_engineer: { label: DATA_SCIENTIST, detail: null },
  viewer: { label: ANALYST, detail: null },
  client_user: { label: BUSINESS_USER, detail: null },
  // Legacy stored values: business_admin acts as an administrator, business_developer as a viewer and
  // personal_developer as a data scientist (authorization_service.py `_LEGACY_WORKSPACE_ROLE`).
  business_admin: { label: ADMIN, detail: null },
  business_developer: { label: ANALYST, detail: null },
  personal_developer: { label: DATA_SCIENTIST, detail: null },
  // DCLab's own staff, not members of a customer's workspace.
  dclab_admin: { label: "Platform admin", detail: "DCLab staff" },
  dclab_developer: { label: "Platform developer", detail: "DCLab staff" },
};

/** Own keys only: a value such as `__proto__` is never looked up through the prototype. */
export function roleWordsFor(role: unknown): RoleWords | null {
  return typeof role === "string" && Object.hasOwn(ROLE_WORDS, role) ? ROLE_WORDS[role as RoleValue] : null;
}

/** "Admin", "Data scientist", ... An unknown value is shown as cleaned text (never a raw underscore code). */
export function roleLabel(role: unknown): string {
  const known = roleWordsFor(role);
  if (known) return known.label;
  const text = plainText(String(role ?? "").replace(/_/g, " "), 40);
  return text || "Unknown role";
}

/** The label plus its second detail where the distinction matters: "Admin · Owner". */
export function roleLabelWithDetail(role: unknown): string {
  const known = roleWordsFor(role);
  return known?.detail ? `${known.label} · ${known.detail}` : roleLabel(role);
}

/**
 * The words for the signed-in account in a workspace. A business user (users.role `client_user`) always reads
 * "Business user", whatever its membership row stores (often a legacy `business_admin`); everyone else reads
 * their membership role in this workspace, or their account role when there is none.
 */
export function accountRoleLabel(
  user: { role?: string | null } | null | undefined,
  workspace?: { role?: string | null } | null,
): string {
  if (user?.role === "client_user") return BUSINESS_USER;
  return roleLabelWithDetail(workspace?.role ?? user?.role);
}
