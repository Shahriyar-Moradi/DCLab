/**
 * Role-based Studio sidebar config (STUDIO_DESIGN section 3). Pure data + one resolver, no React.
 * An item shows only when its role matches AND its backend feature is available
 * (STUDIO_DESIGN section 1 rule 2). Unavailable items are hidden by default; with
 * `showUnavailable` they render as disabled entries that name the phase that ships them.
 */
import { safeInternalHref } from "./safe-href.ts";

export type StudioRole = "developer" | "admin" | "client" | "operator";

export type StudioFeature =
  | "home" | "inbox" | "governance" | "agents" | "settings"
  | "lab" | "pipeline" | "graph" | "data" | "experiments" | "improve" | "models" | "monitoring" | "decisions"
  | "client_outcomes" | "client_predictions" | "client_questions"
  | "operator_overview" | "operator_jobs" | "operator_quarantine" | "operator_caps" | "operator_gates";

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
};

export type SidebarGroupSpec = { id: string; label: string; items: SidebarItemSpec[] };

const WORK: StudioRole[] = ["developer", "admin"];

export const SIDEBAR_CONFIG: SidebarGroupSpec[] = [
  {
    id: "workspace",
    label: "Workspace",
    items: [
      { id: "home", label: "Home", href: "/home", icon: "⌂", roles: WORK, feature: "home", phase: "P4.15" },
      { id: "inbox", label: "Inbox", href: "/inbox", icon: "✉", roles: WORK, feature: "inbox", phase: "P4.16" },
      { id: "governance", label: "Governance", href: "/governance", icon: "⚖", roles: WORK, feature: "governance", phase: "P6.11" },
      { id: "agents", label: "Agents & tools", href: "/agents", icon: "⚙", roles: WORK, feature: "agents", phase: "P4.12" },
      { id: "settings", label: "Settings", href: "/settings", icon: "≡", roles: WORK, feature: "settings", phase: "P4.14" },
    ],
  },
  {
    id: "project",
    label: "Project",
    items: [
      { id: "lab", label: "Lab (chat)", href: "lab", icon: "◎", roles: WORK, feature: "lab", phase: "P4.5" },
      { id: "pipeline", label: "Pipeline", href: "pipeline", icon: "⇶", roles: WORK, feature: "pipeline", phase: "P4.1-A" },
      { id: "graph", label: "Graph", href: "graph", icon: "⬡", roles: WORK, feature: "graph", phase: "P4.2-A" },
      { id: "data", label: "Data", href: "data", icon: "▤", roles: WORK, feature: "data", phase: "P4.1-C" },
      { id: "experiments", label: "Experiments", href: "experiments", icon: "⚗", roles: WORK, feature: "experiments", phase: "P4.3-A" },
      { id: "improve", label: "Improve", href: "improve", icon: "↗", roles: WORK, feature: "improve", phase: "P4.6" },
      { id: "models", label: "Models", href: "models", icon: "◆", roles: WORK, feature: "models", phase: "P4.7" },
      { id: "monitoring", label: "Monitoring", href: "monitoring", icon: "∿", roles: WORK, feature: "monitoring", phase: "P4.8" },
      { id: "decisions", label: "Decisions", href: "decisions", icon: "✓", roles: WORK, feature: "decisions", phase: "P4.4-A" },
    ],
  },
  {
    id: "business",
    label: "Business workspace",
    items: [
      { id: "client-outcomes", label: "Outcomes", href: "/outcomes", icon: "◉", roles: ["client"], feature: "client_outcomes", phase: "P4.13" },
      { id: "client-predictions", label: "Prediction list", href: "/outcomes#predictions", icon: "▤", roles: ["client"], feature: "client_predictions", phase: "P4.13" },
      { id: "client-questions", label: "Questions & reports", href: "/outcomes#questions", icon: "✉", roles: ["client"], feature: "client_questions", phase: "P4.13" },
    ],
  },
  {
    id: "platform",
    label: "Platform operations",
    items: [
      { id: "op-overview", label: "Overview", href: "/platform", icon: "▣", roles: ["operator"], feature: "operator_overview", phase: "P4.17" },
      { id: "op-jobs", label: "Jobs & workers", href: "/platform#jobs", icon: "⇶", roles: ["operator"], feature: "operator_jobs", phase: "P4.17" },
      { id: "op-quarantine", label: "Quarantine", href: "/platform#quarantine", icon: "▤", roles: ["operator"], feature: "operator_quarantine", phase: "P4.17" },
      { id: "op-caps", label: "Platform AI caps", href: "/platform#caps", icon: "⚖", roles: ["operator"], feature: "operator_caps", phase: "P4.17" },
      { id: "op-gates", label: "Benchmarks & gates", href: "/platform#gates", icon: "✓", roles: ["operator"], feature: "operator_gates", phase: "P4.17" },
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
  config?: SidebarGroupSpec[];
};

export function resolveSidebar(role: StudioRole, options: ResolveOptions = {}): ResolvedSidebarGroup[] {
  const { capabilities = NO_STUDIO_CAPABILITIES, showUnavailable = false, projectBase, config = SIDEBAR_CONFIG } = options;
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
      const href = relative && projectBase !== undefined && base ? `${base === "/" ? "" : base}/${item.href}` : item.href;
      items.push({ id: item.id, label: item.label, href, icon: item.icon, available, phase: item.phase });
    }
    if (items.length > 0) groups.push({ id: group.id, label: group.label, items });
  }
  return groups;
}
