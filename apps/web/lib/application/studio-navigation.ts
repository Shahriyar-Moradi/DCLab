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
  improve: true, // guided Improve (P5.5-B-UI) + the improvement loop (D4e = P5.5-A): /v1/improve-runs
  models: true, // GET /v1/model-versions/{id}, champion ref
  predictions: true, // score-new-data tab of the model in use; listed only once the project has one
  monitoring: true, // GET /v1/projects/{id}/monitoring (P7.4-A) + the Monitoring screen (P7.5-A)
  decisions: true, // GET /v1/projects/{id}/decisions
  assistant: false, // assistant panel (A3-UI): slot only
  client_outcomes: true, // GET /v1/projects/{id}/outcomes (P7.6-A Business view)
  client_predictions: false, // the latest list is a section of /outcomes, not its own page
  client_questions: false, // questions from the data team and plain-language Q&A wait for checkpoint G6
  business_view: true, // Studio link to /outcomes (P7.6-A), only for people who may open it (application_access)
  real: true, // "What's real today" (/real, V7 UI-A): static list from lib/application/screen-status.ts
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
  // The Business view link is listed only for people who may open `/outcomes` (the middleware and the API still decide).
  const mayOpenBusinessView = hasCapability(user, CAPABILITIES.applicationAccess);
  const studio = (projectBase ? groups : groups.filter((group) => group.id !== "project"))
    .map((group) => (group.id === "other" && !mayOpenBusinessView ? { ...group, items: group.items.filter((item) => item.id !== "business-view") } : group))
    .filter((group) => group.items.length > 0);
  // Platform administrators keep one link to the operator console from Studio (P8.5-UI), above Help.
  const consoleLink = operatorNavigationForUser(user).map((group) => ({ ...group, items: group.items.filter((item) => item.id === "op-overview") }));
  return [...studio.filter((group) => group.id !== "help"), ...consoleLink.filter((group) => group.items.length > 0), ...studio.filter((group) => group.id === "help")];
}

/** Operator console features whose read exists (P8.5-A): overview, workspaces, failed jobs. Quarantine (P8.7), caps review and benchmarks are planned. */
export const OPERATOR_FEATURES: StudioCapabilities = {
  operator_overview: true, // GET /v1/operator/overview
  operator_workspaces: true, // GET /v1/operator/workspaces
  operator_jobs: true, // GET /v1/operator/failed-jobs
  operator_quarantine: false, // P8.7-A
  operator_caps: false, // no read beyond the overview's code-owned caps
  operator_gates: false, // benchmark results are not in the operator read
};

/** Operator console sidebar (P8.5-UI): platform administrators only (platform read AND platform write). */
export function operatorNavigationForUser(user: Pick<SessionUser, "capabilities"> | null): ResolvedSidebarGroup[] {
  if (!hasCapability(user, CAPABILITIES.platformRead) || !hasCapability(user, CAPABILITIES.platformWrite)) return [];
  return resolveSidebar("operator", { capabilities: OPERATOR_FEATURES });
}

/** Business view sidebar (P7.6-A): workspace readers who may open the business pages see the client items only. */
export function businessNavigationForUser(user: Pick<SessionUser, "capabilities"> | null): ResolvedSidebarGroup[] {
  if (!hasCapability(user, CAPABILITIES.applicationAccess)) return [];
  return resolveSidebar("client", { capabilities: STUDIO_BACKEND_FEATURES });
}
