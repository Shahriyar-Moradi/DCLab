/**
 * Developer Studio navigation (P4.1-A): which sidebar items exist, for whom, and where they link.
 * Pure (no React) so it runs under `npm run test:components`; `app-navigation.ts` re-exports it.
 */
import { resolveSidebar, type ResolvedSidebarGroup, type SidebarTarget, type StudioCapabilities, type StudioRole } from "../../components/studio/sidebar-config.ts";
import { CAPABILITIES, hasCapability } from "../infrastructure/capabilities.ts";
import { isUuid } from "./command-search.ts";
import type { SessionUser } from "../infrastructure/session.ts";

/**
 * Studio sidebar features whose backend exists today (STUDIO_DESIGN sections 1 and 4).
 * Each flag flips in the prompt that ships the backend; the API still authorizes every read.
 */
export const STUDIO_BACKEND_FEATURES: StudioCapabilities = {
  home: true, // GET /v1/projects (summaries), GET /v1/activity
  projects: true, // GET /v1/projects
  inbox: true, // GET /v1/inbox, /v1/inbox/counts
  governance: false, // page arrives with P6.11-UI
  agents: true, // Connect + Service tokens (/v1/service-tokens); tool registry and runs arrive with A2-UI
  settings: true, // GET /v1/workspaces/{id}/members (owners and administrators; others get an explanation)
  lab: false, // assistant chat arrives with A3-UI
  pipeline: true, // GET /v1/model-builds/{id}
  graph: true, // GET /v1/projects/{id}/graph
  data: true, // GET /v1/datasets
  goal: true, // the split-plan page (goal and test design); listed only once the project has a split plan
  experiments: true, // GET /v1/experiments
  improve: true, // guided Improve (P5.5-B-UI): existing branch API + GET /v1/experiments/{id}/verdict; no loop engine
  models: true, // GET /v1/model-versions/{id}, champion ref
  predictions: true, // score-new-data tab of the model in use; listed only once the project has one
  monitoring: true, // GET /v1/projects/{id}/monitoring (P7.4-A) + the Monitoring screen (P7.5-A)
  decisions: true, // GET /v1/projects/{id}/decisions
  assistant: false, // assistant panel (A3-UI): slot only
  client_outcomes: true, // GET /v1/projects/{id}/outcomes (P7.6-A Business view)
  client_predictions: false, // the latest list is a section of /outcomes, not its own page
  client_questions: false, // questions from the data team and plain-language Q&A wait for checkpoint G6
};

export function studioRoleForUser(user: Pick<SessionUser, "capabilities"> | null): StudioRole | null {
  if (hasCapability(user, CAPABILITIES.platformRead)) return "admin";
  if (hasCapability(user, CAPABILITIES.developmentAccess)) return "developer";
  return null;
}

/** Developer Studio sidebar; project pages only inside a project. */
export function studioNavigationForUser(
  user: Pick<SessionUser, "capabilities"> | null,
  projectId?: string,
  targets?: Partial<Record<SidebarTarget, string | null>>,
): ResolvedSidebarGroup[] {
  const role = studioRoleForUser(user);
  if (!role) return [];
  // Project ids are UUIDs; anything else from the URL gets no project-scoped links.
  const projectBase = isUuid(projectId) ? `/projects/${projectId}` : undefined;
  const groups = resolveSidebar(role, { capabilities: STUDIO_BACKEND_FEATURES, projectBase, targets });
  return projectBase ? groups : groups.filter((group) => group.id !== "project");
}

/** Business view sidebar (P7.6-A): workspace readers who may open the business pages see the client items only. */
export function businessNavigationForUser(user: Pick<SessionUser, "capabilities"> | null): ResolvedSidebarGroup[] {
  if (!hasCapability(user, CAPABILITIES.applicationAccess)) return [];
  return resolveSidebar("client", { capabilities: STUDIO_BACKEND_FEATURES });
}
