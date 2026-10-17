/**
 * Role-based Studio sidebar config (STUDIO_DESIGN section 3). Pure data + one resolver, no React.
 * An item shows only when its role matches AND its backend feature is available
 * (STUDIO_DESIGN section 1 rule 2). Unavailable items are hidden by default; with
 * `showUnavailable` they render as disabled entries that name the phase that ships them.
 */
import { isUuid } from "../../lib/application/command-search.ts";
import { safeInternalHref } from "./safe-href.ts";

export type StudioRole = "developer" | "admin" | "client" | "operator";

export type StudioFeature =
  | "home" | "projects" | "inbox" | "governance" | "agents" | "settings" | "assistant"
  | "lab" | "pipeline" | "graph" | "data" | "goal" | "experiments" | "improve" | "models" | "predictions" | "monitoring" | "decisions"
  | "client_outcomes" | "client_predictions" | "client_questions" | "questions" | "business_view" | "real"
  | "operator_overview" | "operator_workspaces" | "operator_jobs" | "operator_quarantine" | "operator_caps" | "operator_gates";

/** Backend availability per feature. Missing key = not available. */
export type StudioCapabilities = Partial<Record<StudioFeature, boolean>>;

/** Nothing is available until a phase wires its backend and flips the flag. */
export const NO_STUDIO_CAPABILITIES: StudioCapabilities = {};

export type SidebarItemSpec = {
  id: string;
  label: string;
  href: string;
  icon: string;
  roles: StudioRole[];
  feature: StudioFeature;
  /** Roadmap prompt that makes this item real (named in the disabled state). */
  phase: string;
  /**
   * Item whose page needs an id the sidebar cannot know from the URL (for example the test design page of the
   * project). `href` holds `{target}`; without a valid id for it the item is left out instead of guessed.
   */
  target?: SidebarTarget;
  /** Used instead of `href` when the target id is missing (the page then works without it); otherwise the item is left out. */
  fallbackHref?: string;
};

/** `project_model`: any model of the project (the model in use when there is one); Predictions is listed as soon as the project has a model. */
export type SidebarTarget = "split_plan" | "champion_model" | "project_model";

export type SidebarGroupSpec = { id: string; label: string; items: SidebarItemSpec[] };

const WORK: StudioRole[] = ["developer", "admin"];

export const SIDEBAR_CONFIG: SidebarGroupSpec[] = [
  {
    id: "workspace",
    label: "Workspace",
    items: [
      { id: "home", label: "Home", href: "/home", icon: "⌂", roles: WORK, feature: "home", phase: "P4.15" },
      { id: "inbox", label: "Inbox", href: "/inbox", icon: "✉", roles: WORK, feature: "inbox", phase: "P4.16" },
      { id: "projects", label: "Projects", href: "/projects", icon: "▦", roles: WORK, feature: "projects", phase: "P4.1-A" },
    ],
  },
  {
    // The pipeline view (reached from a run) and the lineage graph (a link on History) stay as routes, not sidebar items.
    id: "project",
    label: "Project",
    items: [
      { id: "data", label: "Data", href: "data", icon: "▤", roles: WORK, feature: "data", phase: "P4.1-C" },
      { id: "goal", label: "Goal & test design", href: "splits/{target}", target: "split_plan", fallbackHref: "goal", icon: "◎", roles: WORK, feature: "goal", phase: "P4.3-A" },
      { id: "experiments", label: "Experiments", href: "experiments", icon: "⚗", roles: WORK, feature: "experiments", phase: "P4.1-A" },
      { id: "improve", label: "Improve", href: "improve", icon: "↗", roles: WORK, feature: "improve", phase: "P5.5-B" },
      { id: "models", label: "Model", href: "models", icon: "◆", roles: WORK, feature: "models", phase: "P4.11-UI" },
      { id: "predictions", label: "Predictions", href: "predictions", target: "project_model", icon: "▸", roles: WORK, feature: "predictions", phase: "P4.9-UI" },
      { id: "monitoring", label: "Monitoring", href: "monitoring", icon: "∿", roles: WORK, feature: "monitoring", phase: "P7.5-A" },
      { id: "decisions", label: "History", href: "decisions", icon: "✓", roles: WORK, feature: "decisions", phase: "P4.4-A" },
      { id: "questions", label: "Ask the business", href: "questions", icon: "✉", roles: WORK, feature: "questions", phase: "D7" },
    ],
  },
  {
    id: "admin",
    label: "Admin",
    items: [
      { id: "settings", label: "Settings", href: "/settings", icon: "≡", roles: WORK, feature: "settings", phase: "P4.14" },
      { id: "governance", label: "AI settings", href: "/governance", icon: "⚖", roles: WORK, feature: "governance", phase: "P6.11-UI" },
      { id: "agents", label: "Connect", href: "/agents", icon: "⚙", roles: WORK, feature: "agents", phase: "P4.8-UI" },
    ],
  },
  {
    // What a business user of this workspace sees (V7 UI-A). Listed only for people who may open `/outcomes` (studio-navigation.ts).
    id: "other",
    label: "Other views",
    items: [
      { id: "business-view", label: "Business view", href: "/outcomes", icon: "◉", roles: WORK, feature: "business_view", phase: "P7.6-A" },
    ],
  },
  {
    // The honest list of every screen and its status (V7 UI-A, lib/application/screen-status.ts).
    id: "help",
    label: "Help",
    items: [
      { id: "real", label: "What's real today", href: "/real", icon: "◌", roles: WORK, feature: "real", phase: "V7 UI-A" },
    ],
  },
  {
    id: "business",
    label: "Business workspace",
    items: [
      { id: "client-outcomes", label: "Outcomes", href: "/outcomes", icon: "◉", roles: ["client"], feature: "client_outcomes", phase: "P4.13" },
      { id: "client-predictions", label: "Prediction list", href: "/outcomes#predictions", icon: "▤", roles: ["client"], feature: "client_predictions", phase: "P4.13" },
      { id: "client-questions", label: "Questions from the data team", href: "/outcomes#questions", icon: "✉", roles: ["client"], feature: "client_questions", phase: "P4.13" },
    ],
  },
  {
    // Operator console (P8.5-UI, `/operator`): platform administrators only. The page's tabs follow `?tab=`.
    id: "platform",
    label: "Platform operations",
    items: [
      { id: "op-overview", label: "Operator console", href: "/operator", icon: "▣", roles: ["operator"], feature: "operator_overview", phase: "P8.5-UI" },
      { id: "op-workspaces", label: "Workspaces", href: "/operator?tab=workspaces", icon: "▦", roles: ["operator"], feature: "operator_workspaces", phase: "P8.5-UI" },
      { id: "op-jobs", label: "Failed jobs", href: "/operator?tab=failed-jobs", icon: "⇶", roles: ["operator"], feature: "operator_jobs", phase: "P8.5-UI" },
      { id: "op-quarantine", label: "Quarantine", href: "/operator?tab=quarantine", icon: "▤", roles: ["operator"], feature: "operator_quarantine", phase: "P8.7" },
      { id: "op-caps", label: "Platform AI caps", href: "/operator?tab=caps", icon: "⚖", roles: ["operator"], feature: "operator_caps", phase: "later" },
      { id: "op-gates", label: "Benchmarks & gates", href: "/operator?tab=benchmarks", icon: "✓", roles: ["operator"], feature: "operator_gates", phase: "later" },
    ],
  },
];

export type ResolvedSidebarItem = { id: string; label: string; href: string; icon: string; available: boolean; phase: string };
export type ResolvedSidebarGroup = { id: string; label: string; items: ResolvedSidebarItem[] };

export type ResolveOptions = {
  capabilities?: StudioCapabilities;
  /** Show unavailable items disabled with the phase that ships them (default: hide). */
  showUnavailable?: boolean;
  /** Prefix for project-scoped (relative) hrefs, e.g. `/projects/abc`. Omit to keep them relative. */
  projectBase?: string;
  /** Ids for items that need one (`SidebarItemSpec.target`); a missing or non-UUID id drops the item. */
  targets?: Partial<Record<SidebarTarget, string | null>>;
  config?: SidebarGroupSpec[];
};

export function resolveSidebar(role: StudioRole, options: ResolveOptions = {}): ResolvedSidebarGroup[] {
  const { capabilities = NO_STUDIO_CAPABILITIES, showUnavailable = false, projectBase, targets = {}, config = SIDEBAR_CONFIG } = options;
  // An unsafe or query-bearing base drops project-scoped items instead of building a hostile href.
  const base = projectBase === undefined ? null : safeInternalHref(projectBase.replace(/\/+$/, "") || "/");
  const baseOk = base !== null && !/[?#]/.test(base);
  const groups: ResolvedSidebarGroup[] = [];
  for (const group of config) {
    const items: ResolvedSidebarItem[] = [];
    for (const item of group.items) {
      if (!item.roles.includes(role)) continue;
      const relative = !item.href.startsWith("/");
      if (relative && projectBase !== undefined && !baseOk) continue;
      const available = capabilities[item.feature] === true;
      if (!available && !showUnavailable) continue;
      let path = item.href;
      if (item.target) {
        const id = targets[item.target];
        if (isUuid(id)) path = path.replace("{target}", id);
        else if (item.fallbackHref) path = item.fallbackHref;
        else continue;
      }
      const href = relative && projectBase !== undefined && base ? `${base === "/" ? "" : base}/${path}` : path;
      items.push({ id: item.id, label: item.label, href, icon: item.icon, available, phase: item.phase });
    }
    if (items.length > 0) groups.push({ id: group.id, label: group.label, items });
  }
  return groups;
}
