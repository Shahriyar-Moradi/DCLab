/**
 * V7-B6: Settings, members and roles (read only). Pure view model: plain role words, plain
 * "can / cannot" sentences built from the flags the server sends (the same role sets it enforces),
 * and the caller's own access from the session capabilities. Nothing here adds or changes people.
 */
import { plainText } from "./command-search.ts";
import { roleLabel, roleLabelWithDetail } from "./role-labels.ts";

/** Role words come from the one label map (role-labels.ts): "Admin · Owner", "Admin", "Data scientist", "Analyst (view only)". */
export function roleWords(role: unknown): string {
  return roleLabelWithDetail(role);
}

export type RoleAbilities = { role: string; can_read: boolean; can_run_ml: boolean; can_manage_members: boolean; can_approve_ai_policy: boolean };

const ABILITY_TEXT: Array<{ flag: Exclude<keyof RoleAbilities, "role">; can: string; cannot: string }> = [
  { flag: "can_read", can: "See projects, data, runs, results and the Inbox", cannot: "See anything in this workspace" },
  { flag: "can_run_ml", can: "Create projects, upload data, start runs and decide in the Inbox", cannot: "Create projects, upload data, start runs or decide in the Inbox" },
  { flag: "can_manage_members", can: "See who is in this workspace", cannot: "See who is in this workspace" },
  { flag: "can_approve_ai_policy", can: "Approve changes to the AI rules", cannot: "Approve changes to the AI rules" },
];

/** What a role can and cannot do, from the server's flags. Missing flags count as "cannot". */
export function roleRow(row: RoleAbilities): { role: string; label: string; can: string[]; cannot: string[] } {
  const can: string[] = [];
  const cannot: string[] = [];
  for (const item of ABILITY_TEXT) (row[item.flag] === true ? can : cannot).push(row[item.flag] === true ? item.can : item.cannot);
  return { role: row.role, label: roleWords(row.role), can, cannot };
}

export type MemberRow = { full_name: string; email: string; role: string; joined_at: string; is_you: boolean; legacy_access?: boolean };

export function memberRow(member: MemberRow): { key: string; name: string; email: string; role: string; joined: string; you: boolean; legacy: boolean } {
  const email = plainText(member.email, 120);
  return {
    key: String(member.email),
    name: plainText(member.full_name, 80) || "No name given",
    email,
    // The API lists every client account as `viewer` with legacy_access: it is a business user.
    role: member.legacy_access === true ? roleLabel("client_user") : roleWords(member.role),
    joined: formatDay(member.joined_at),
    you: member.is_you === true,
    legacy: member.legacy_access === true,
  };
}

export function formatDay(value: unknown): string {
  const date = new Date(String(value ?? ""));
  return Number.isNaN(date.getTime()) ? "date unknown" : date.toLocaleDateString(undefined, { year: "numeric", month: "short", day: "numeric" });
}

/** The caller's own access, only from the capabilities the session reports. */
export function myAccess(capabilities: Record<string, boolean> | undefined): { canSeeMembers: boolean; sentences: string[] } {
  const has = (key: string) => capabilities !== undefined && Object.hasOwn(capabilities, key) && capabilities[key] === true;
  const sentences = [
    has("workspace_read") ? "You can see this workspace's projects, data, runs and results." : "You cannot see this workspace's work.",
    has("workspace_execute_ml") ? "You can create projects, upload data, start runs and decide in the Inbox." : "You cannot change work here: no uploads, runs or decisions.",
    has("workspace_manage_members") ? "You can see the people in this workspace." : "Only admins can see the people in this workspace.",
  ];
  return { canSeeMembers: has("workspace_manage_members"), sentences };
}

export const ADDING_PEOPLE_TEXT = "Adding or removing people is not available in Studio yet; contact DCLab.";
export const MORE_SETTINGS_TEXT = "More settings arrive with later releases.";

export function membersCountText(count: number, more: boolean): string {
  return more ? `Showing the first ${count} people; there are more.` : `${count} ${count === 1 ? "person" : "people"}`;
}
