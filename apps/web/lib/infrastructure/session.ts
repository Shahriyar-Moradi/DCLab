"use client";

export const SESSION_COOKIE = "dclab_session";
export const SESSION_CHANGED_EVENT = "dclab-session-changed";

export type SessionWorkspace = {
  id: string;
  slug: string;
  name: string;
  kind: string;
  role: string | null;
};

export type SessionUser = {
  id: string;
  email: string;
  role:
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
  full_name: string;
  workspace_id: string | null;
  active_workspace_id: string | null;
  workspaces: SessionWorkspace[];
  request_id?: string | null;
};

export const SESSION_ROLES: SessionUser["role"][] = [
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

export function notifySessionChanged(): void {
  if (typeof window === "undefined") return;
  window.dispatchEvent(new Event(SESSION_CHANGED_EVENT));
}

export function roleLabel(role: SessionUser["role"]): string {
  const labels: Record<SessionUser["role"], string> = {
    dclab_admin: "DCLab Admin",
    dclab_developer: "DCLab Developer",
    business_admin: "Business Admin",
    business_developer: "Business Developer",
    personal_developer: "Personal Developer",
    client_user: "Business Client",
    workspace_owner: "Workspace Owner",
    workspace_admin: "Workspace Admin",
    ml_engineer: "ML Engineer",
    viewer: "Viewer",
  };
  return labels[role];
}

export function isPlatformRole(role: SessionUser["role"]): boolean {
  return role === "dclab_admin" || role === "dclab_developer";
}

export function isDevelopmentRole(role: SessionUser["role"]): boolean {
  return (
    role === "personal_developer" ||
    role === "business_admin" ||
    role === "business_developer" ||
    role === "workspace_owner" ||
    role === "workspace_admin" ||
    role === "ml_engineer" ||
    role === "viewer" ||
    role === "dclab_admin" ||
    role === "dclab_developer"
  );
}

export function isBusinessAdministrationRole(role: SessionUser["role"]): boolean {
  return (
    role === "business_admin" ||
    role === "business_developer" ||
    role === "workspace_owner" ||
    role === "workspace_admin" ||
    role === "ml_engineer" ||
    role === "viewer"
  );
}

export function canWriteWorkspaceSession(role: SessionUser["role"]): boolean {
  return (
    role !== "dclab_developer" &&
    role !== "business_developer" &&
    role !== "viewer"
  );
}

export function displayName(user: SessionUser): string {
  return user.full_name?.trim() || user.email;
}

export function parseSessionUser(value: unknown): SessionUser | null {
  if (!value || typeof value !== "object") return null;
  const row = value as Record<string, unknown>;
  const role = row.role;
  if (typeof row.id !== "string" || typeof row.email !== "string") return null;
  if (typeof role !== "string" || !(SESSION_ROLES as string[]).includes(role)) {
    return null;
  }
  return {
    id: row.id,
    email: row.email,
    role: role as SessionUser["role"],
    full_name: typeof row.full_name === "string" ? row.full_name : row.email,
    workspace_id: typeof row.workspace_id === "string" ? row.workspace_id : null,
    active_workspace_id: typeof row.active_workspace_id === "string" ? row.active_workspace_id : null,
    workspaces: parseSessionWorkspaces(row.workspaces),
    request_id: typeof row.request_id === "string" ? row.request_id : null,
  };
}

function parseSessionWorkspaces(value: unknown): SessionWorkspace[] {
  if (!Array.isArray(value)) return [];
  const rows: SessionWorkspace[] = [];
  for (const item of value) {
    if (!item || typeof item !== "object") continue;
    const row = item as Record<string, unknown>;
    if (typeof row.id !== "string" || typeof row.name !== "string") continue;
    rows.push({
      id: row.id,
      slug: typeof row.slug === "string" ? row.slug : row.id,
      name: row.name,
      kind: typeof row.kind === "string" ? row.kind : "business",
      role: typeof row.role === "string" ? row.role : null,
    });
  }
  return rows;
}
