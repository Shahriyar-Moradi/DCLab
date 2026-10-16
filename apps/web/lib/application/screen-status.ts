/**
 * What's real today (V7 UI-A): ONE typed registry of every screen and how much of it works, written from the real product
 * (not from the v7 demo, whose tags are older than the product). It drives the status tag under each page title and the
 * `/real` page. Pure data + lookups, no React, so it runs under `npm run test:components`.
 *
 * Rules: a screen whose sidebar feature flag is off cannot claim to work, and one whose flag is on cannot claim to be
 * planned (screen-status.test.ts checks both against STUDIO_BACKEND_FEATURES / OPERATOR_FEATURES). Every sentence is plain
 * words and is rendered as text.
 */
import type { StudioFeature } from "../../components/studio/sidebar-config.ts";
import { OPERATOR_FEATURES, STUDIO_BACKEND_FEATURES } from "./studio-navigation.ts";

export type StatusKind = "working" | "partly" | "off" | "planned";
export type StatusTone = "ok" | "warn" | "ai" | "gray";

export type ScreenId =
  | "home" | "inbox" | "projects"
  | "data" | "goal" | "experiments" | "improve" | "model" | "predictions" | "monitoring" | "history"
  | "settings" | "usage" | "notifications" | "webhooks" | "connect" | "aiSettings" | "assistant"
  | "business" | "console";

export type ScreenGroup = "Workspace" | "Project" | "Admin" | "Other views";

export type StatusTag = {
  kind: StatusKind;
  /** Partly working: what is missing, in a few words (shown in the pill). */
  short?: string;
  /** Planned: the roadmap phase that builds it. */
  phase?: number;
  /** A tag about one part of the screen, e.g. "Items from the assistant". */
  part?: string;
};

export type ScreenStatus = {
  id: ScreenId;
  name: string;
  group: ScreenGroup;
  /** Workspace-level link for the `/real` table; project screens are reached inside a project (null). */
  href: string | null;
  /** The sidebar feature flag this status must agree with (null: no sidebar item of its own). */
  feature: StudioFeature | null;
  /** Main tag first, then tags about parts of the screen. */
  tags: [StatusTag, ...StatusTag[]];
  /** One line: what is missing today ("Nothing" when the screen does its whole job). */
  missing: string;
};

export const STATUS_WORDS: Record<StatusKind, { label: string; tone: StatusTone; meaning: string }> = {
  working: { label: "Working today", tone: "ok", meaning: "You can use it now." },
  partly: { label: "Partly working", tone: "warn", meaning: "Some parts are missing; the table says which." },
  off: { label: "Built, switched off", tone: "ai", meaning: "AI pieces that are built but not shown to people until they pass testing." },
  planned: { label: "Planned", tone: "gray", meaning: "Not built yet; the table says what is missing." },
};

export const SCREEN_STATUS: readonly ScreenStatus[] = [
  {
    id: "home", name: "Home", group: "Workspace", href: "/home", feature: "home",
    tags: [{ kind: "working" }],
    missing: "Nothing for the daily job. A running run shows the fold it is on; the things that need you have no evidence numbers yet, only who raised them and where.",
  },
  {
    id: "inbox", name: "Inbox", group: "Workspace", href: "/inbox", feature: "inbox",
    tags: [{ kind: "working" }, { kind: "off", part: "Items from the assistant" }],
    missing: "Suggestions from the assistant appear here only after AI is switched on.",
  },
  {
    id: "projects", name: "Projects", group: "Workspace", href: "/projects", feature: "projects",
    tags: [{ kind: "working" }],
    missing: "Nothing for the daily job. The score column is the cross-validation score of the model in use, not the best run's. New project is a three-step dialog; the older four-step page is still linked from it.",
  },
  {
    id: "data", name: "Data", group: "Project", href: null, feature: "data",
    tags: [{ kind: "working" }],
    missing: "Nothing.",
  },
  {
    id: "goal", name: "Goal & test design", group: "Project", href: null, feature: "goal",
    tags: [{ kind: "working" }],
    missing: "Nothing. The goal and test design are set when you start a run; this screen shows them.",
  },
  {
    id: "experiments", name: "Experiments", group: "Project", href: null, feature: "experiments",
    tags: [{ kind: "working" }],
    missing: "Nothing.",
  },
  {
    id: "improve", name: "Improve", group: "Project", href: null, feature: "improve",
    tags: [{ kind: "working" }],
    missing: "Guided: you try one change at a time and see whether it beats the noise. A loop that tries several changes on its own is not built.",
  },
  {
    id: "model", name: "Model", group: "Project", href: null, feature: "models",
    tags: [{ kind: "working" }],
    missing: "Versions, the final test with its table of right and wrong answers, the saved file and its checks, and the columns new data must have are shown; approving and going back work. A chosen threshold is not applied to scoring without a new model version.",
  },
  {
    id: "predictions", name: "Predictions", group: "Project", href: null, feature: "predictions",
    tags: [{ kind: "working" }],
    missing: "Scoring a file, weekly scoring and the list with the top reasons for each row work. Only scorings from this browser tab and the weekly schedule are listed, and at most the first 10,000 rows of a list show on screen.",
  },
  {
    id: "monitoring", name: "Monitoring", group: "Project", href: null, feature: "monitoring",
    tags: [{ kind: "working" }],
    missing: "Nothing retrains or switches the model on its own, and the alert lines cannot be changed on this screen.",
  },
  {
    id: "history", name: "History", group: "Project", href: null, feature: "decisions",
    tags: [{ kind: "working" }],
    missing: "Nothing.",
  },
  {
    id: "settings", name: "Settings", group: "Admin", href: "/settings", feature: "settings",
    tags: [{ kind: "partly", short: "notifications have no screen yet" }],
    missing: "People and roles, and usage and cost work. Adding people, notifications and integrations have no screen yet.",
  },
  {
    id: "usage", name: "Usage & cost (in Settings)", group: "Admin", href: "/settings?tab=usage", feature: null,
    tags: [{ kind: "working" }],
    missing: "Nothing. The numbers are an estimate from DCLab's own records, not an invoice.",
  },
  {
    id: "notifications", name: "Notifications (email)", group: "Admin", href: null, feature: null,
    tags: [{ kind: "partly", short: "no screen yet" }],
    missing: "Built on the server and switched off until an email service is chosen. There is no screen to choose what you get, and emails have no unsubscribe link yet.",
  },
  {
    id: "webhooks", name: "Integrations (webhooks)", group: "Admin", href: null, feature: null,
    tags: [{ kind: "partly", short: "no screen yet" }],
    missing: "Built on the server and switched off by default. There is no screen to add, test or remove one yet.",
  },
  {
    id: "connect", name: "Connect (API, SDK, MCP)", group: "Admin", href: "/agents", feature: "agents",
    tags: [{ kind: "working" }],
    missing: "Nothing.",
  },
  {
    id: "aiSettings", name: "AI settings", group: "Admin", href: null, feature: "governance",
    tags: [{ kind: "off" }, { kind: "planned", phase: 4, part: "Screen" }],
    missing: "The rules for what AI may do exist on the server; the screen to read and change them is not built, so it is not in the menu.",
  },
  {
    id: "assistant", name: "Assistant", group: "Admin", href: null, feature: "assistant",
    tags: [{ kind: "off" }],
    missing: "Shown to people only after it passes testing. Everything works without it: every action is also a button.",
  },
  {
    id: "business", name: "Business view", group: "Other views", href: "/outcomes", feature: "client_outcomes",
    tags: [{ kind: "working" }],
    missing: "Questions from your data team and asking questions in plain words are not built yet.",
  },
  {
    id: "console", name: "Platform console (DCLab staff only)", group: "Other views", href: null, feature: "operator_overview",
    tags: [{ kind: "partly", short: "some tabs planned" }],
    missing: "Overview, workspaces and failed jobs work. Files held for review, AI limits per workspace and benchmark results are not built yet.",
  },
];

/** Summary sentence of the `/real` page, written from the registry's real state. */
export const REAL_SUMMARY =
  "The whole project flow works today: data, goal and test design, experiments with trust checks, guided improve, the model and approving it for scoring, " +
  "predictions for one file or every week, monitoring and history, plus the business view, usage and the platform console. " +
  "The assistant and AI settings are built and switched off. Notifications and integrations work on the server but have no screen yet.";

/** Words of the `/real` page. */
export const REAL_PAGE_WORDS = {
  subtitle: "Every screen of DCLab with its status, written from what the product does today. Nothing is hidden: what is missing is named.",
  purpose: "An honest list of every screen: what works today, what works partly, what is built but switched off, and what is planned.",
  howTo: "Read the summary first, then find a screen in the table and read what is missing. The tag under each page title opens this list.",
  youGet: "No surprises about what you can use now, and which parts are not built yet.",
  footnote: "Project screens are reached inside a project. AI pieces stay switched off until they pass testing; every action works without them.",
} as const;

const BY_ID: Readonly<Record<string, ScreenStatus>> = Object.fromEntries(SCREEN_STATUS.map((entry) => [entry.id, entry]));

/** Own keys only: an id such as `__proto__` or `constructor` has no status. */
export function screenStatus(id: string): ScreenStatus | null {
  return Object.hasOwn(BY_ID, id) ? BY_ID[id] : null;
}

/** Pill text of one tag, e.g. "Working today", "Partly working · no screen yet", "Screen: Planned". The roadmap phase stays in the data, never on screen. */
export function tagLabel(tag: StatusTag): string {
  let text = STATUS_WORDS[tag.kind].label;
  if (tag.kind === "partly" && tag.short) text += ` · ${tag.short}`;
  return tag.part ? `${tag.part}: ${text}` : text;
}

export function tagTone(tag: StatusTag): StatusTone {
  return STATUS_WORDS[tag.kind].tone;
}

const UUID = "[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}";
const PROJECT_PAGE = new RegExp(`^/projects/${UUID}/([a-z]+)(?:/|$)`);

/** Project route section -> screen. Own keys only. */
const PROJECT_SECTION: Readonly<Record<string, ScreenId>> = {
  data: "data", splits: "goal", goal: "goal", experiments: "experiments", pipeline: "experiments", features: "experiments",
  improve: "improve", models: "model", predictions: "predictions", monitoring: "monitoring",
  decisions: "history", graph: "history", lab: "assistant",
};

const WORKSPACE_PAGE: Readonly<Record<string, ScreenId>> = {
  "/home": "home", "/inbox": "inbox", "/projects": "projects", "/projects/new": "projects",
  "/settings": "settings", "/agents": "connect", "/governance": "aiSettings", "/outcomes": "business", "/operator": "console",
};

/** Which screen a path shows (null for pages without a status, such as `/real` itself). */
export function screenForPath(pathname: string): ScreenId | null {
  const path = pathname.split(/[?#]/)[0].replace(/\/+$/, "") || "/";
  if (Object.hasOwn(WORKSPACE_PAGE, path)) return WORKSPACE_PAGE[path];
  const match = PROJECT_PAGE.exec(path);
  if (match && Object.hasOwn(PROJECT_SECTION, match[1])) return PROJECT_SECTION[match[1]];
  return null;
}

/** True when the screen's status agrees with its feature flag (on = working or partly; off = switched off or planned). */
export function agreesWithFeatureFlag(entry: ScreenStatus): boolean {
  if (entry.feature === null) return true;
  const flags = { ...STUDIO_BACKEND_FEATURES, ...OPERATOR_FEATURES };
  const on = flags[entry.feature] === true;
  const kind = entry.tags[0].kind;
  return on ? kind === "working" || kind === "partly" : kind === "off" || kind === "planned";
}

/** Link of a row on `/real`: none for project screens and staff-only pages; the Business view only for people who may open it. */
export function rowHref(entry: ScreenStatus, mayOpenBusinessView: boolean): string | null {
  if (entry.id === "business" && !mayOpenBusinessView) return null;
  return entry.href;
}
