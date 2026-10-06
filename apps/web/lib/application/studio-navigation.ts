/**
 * Developer Studio navigation (P4.1-A): which sidebar items exist, for whom, and where they link.
 * Pure (no React) so it runs under `npm run test:components`; `app-navigation.ts` re-exports it.
 */
import { resolveSidebar, type ResolvedSidebarGroup, type StudioCapabilities, type StudioRole } from "../../components/studio/sidebar-config.ts";
import { CAPABILITIES, hasCapability } from "../infrastructure/capabilities.ts";
import { isUuid } from "./command-search.ts";
import type { SessionUser } from "../infrastructure/session.ts";

/**
 * Studio sidebar features whose backend exists today (STUDIO_DESIGN sections 1 and 4).
 * Each flag flips in the prompt that ships the backend; the API still authorizes every read.
 */
export const STUDIO_BACKEND_FEATURES: StudioCapabilities = {
  home: true, // GET /v1/projects (activity arrives with P4.15)
  projects: true, // GET /v1/projects
  inbox: false, // GET /v1/inbox arrives with P4.16
  governance: false, // page arrives with P6.11-UI
  agents: false, // page arrives with P4.8-UI
  settings: false,
  lab: false, // assistant chat arrives with A3-UI
  pipeline: true, // GET /v1/model-builds/{id}
  graph: true, // GET /v1/projects/{id}/graph
  data: true, // GET /v1/datasets
  experiments: true, // GET /v1/experiments
  improve: false, // improve runs arrive with P5.4
  models: true, // GET /v1/model-versions/{id}, champion ref
  monitoring: false, // monitoring windows arrive with P7.4
  decisions: true, // GET /v1/projects/{id}/decisions
  assistant: false, // assistant panel (A3-UI): slot only
};

export function studioRoleForUser(user: Pick<SessionUser, "capabilities"> | null): StudioRole | null {
  if (hasCapability(user, CAPABILITIES.platformRead)) return "admin";
  if (hasCapability(user, CAPABILITIES.developmentAccess)) return "developer";
  return null;
}

/** Developer Studio sidebar; project pages only inside a project. */
export function studioNavigationForUser(user: Pick<SessionUser, "capabilities"> | null, projectId?: string): ResolvedSidebarGroup[] {
  const role = studioRoleForUser(user);
  if (!role) return [];
  // Project ids are UUIDs; anything else from the URL gets no project-scoped links.
  const projectBase = isUuid(projectId) ? `/projects/${projectId}` : undefined;
  const groups = resolveSidebar(role, { capabilities: STUDIO_BACKEND_FEATURES, projectBase });
  return projectBase ? groups : groups.filter((group) => group.id !== "project");
}
