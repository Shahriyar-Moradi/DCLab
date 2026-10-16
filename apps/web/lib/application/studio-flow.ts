/**
 * Project flow bar (V7-A1): the project's steps by name with done / current / next state. Pure: no React, no fetch.
 * A state is set only from data the page already reads (project refs, run list) or from the current route; when a
 * step's state cannot be known it has none (never guessed).
 */
import { safeInternalHref } from "../../components/studio/safe-href.ts";
import { isUuid } from "./command-search.ts";

export type FlowStepId = "data" | "goal" | "experiments" | "improve" | "models" | "predictions" | "monitoring";
export type FlowState = "done" | "current" | "next" | null;
export type FlowStep = { id: FlowStepId; label: string; href: string | null; state: FlowState };

const ORDER: Array<{ id: FlowStepId; label: string }> = [
  { id: "data", label: "Data" },
  { id: "goal", label: "Goal & test design" },
  { id: "experiments", label: "Experiments" },
  { id: "improve", label: "Improve" },
  { id: "models", label: "Model" },
  { id: "predictions", label: "Predictions" },
  { id: "monitoring", label: "Monitoring" },
];

/** Which step a project path belongs to (null on pages outside the flow, such as History). */
export function stepForPath(pathname: string, projectId: string, tab?: string | null): FlowStepId | null {
  const prefix = `/projects/${projectId}/`;
  if (!pathname.startsWith(prefix)) return null;
  const section = pathname.slice(prefix.length).split("/")[0];
  switch (section) {
    case "data": return "data";
    case "splits": case "goal": return "goal";
    case "experiments": case "pipeline": case "features": return "experiments";
    case "improve": return "improve";
    case "models": return tab === "score" ? "predictions" : "models";
    case "predictions": return "predictions";
    case "monitoring": return "monitoring";
    default: return null;
  }
}

export type FlowInput = {
  projectId: string;
  pathname: string;
  /** Step ids whose backend exists (the same flags that gate the sidebar); others are not listed. */
  available: ReadonlySet<string>;
  /** Project ref kinds, or null while unknown (loading or failed). */
  refKinds: readonly string[] | null;
  /** Run statuses of the project, or null while unknown. */
  runStatuses: readonly string[] | null;
  /** The `tab` query value of the current page (the score tab of a model is also the Predictions step). */
  tab?: string | null;
  /** Ids behind the two steps whose page needs one; null = the project has none yet. */
  targets: { split_plan?: string | null; project_model?: string | null };
};

function hrefFor(id: FlowStepId, projectId: string, targets: FlowInput["targets"]): string | null {
  const base = `/projects/${projectId}`;
  switch (id) {
    // Before the first test design exists the project-level Goal page shows the goal (V7 UI-C).
    case "goal": return safeInternalHref(isUuid(targets.split_plan) ? `${base}/splits/${targets.split_plan}` : `${base}/goal`);
    case "predictions": return isUuid(targets.project_model) ? safeInternalHref(`${base}/predictions`) : null;
    default: return safeInternalHref(`${base}/${id}`);
  }
}

/** Done/not-done where a read says so; undefined where nothing the page reads can say. */
function knownDone(id: FlowStepId, input: FlowInput): boolean | undefined {
  const { refKinds, runStatuses } = input;
  switch (id) {
    case "data": return refKinds ? refKinds.includes("dataset") : undefined;
    case "goal": return refKinds ? refKinds.includes("problem_spec") && refKinds.includes("split_plan") : undefined;
    case "experiments": return runStatuses ? runStatuses.includes("completed") : undefined;
    case "models": return refKinds ? refKinds.includes("champion_model") : undefined;
    default: return undefined; // predictions, improve and monitoring have no read on this page
  }
}

export function buildFlow(input: FlowInput): FlowStep[] {
  const listed = ORDER.filter((s) => input.available.has(s.id));
  const current = stepForPath(input.pathname, input.projectId, input.tab);
  let nextTaken = false;
  return listed.map((s) => {
    let state: FlowState = null;
    const done = knownDone(s.id, input);
    if (s.id === current) state = "current";
    else if (done === true) state = "done";
    else if (done === false && !nextTaken) { state = "next"; nextTaken = true; }
    return { id: s.id, label: s.label, href: hrefFor(s.id, input.projectId, input.targets), state };
  });
}

/**
 * Run statuses for the flow bar. The run list is capped (newest first): when more runs exist than were loaded and none of
 * the loaded ones is completed, "no completed run" cannot be told, so the state is unknown (null), never "not done".
 */
export function flowRunStatuses(items: ReadonlyArray<{ status: string }> | undefined, hasMore: boolean): string[] | null {
  if (!items) return null;
  const statuses = items.map((item) => item.status);
  return hasMore && !statuses.includes("completed") ? null : statuses;
}

/** The model the Predictions step opens with: the model in use when the graph shows one, else the first model of the project; null = no model yet. */
export function projectModelId(nodes: ReadonlyArray<{ kind: string; id: string; ref_kinds?: string[] }> | undefined): string | null {
  const models = (nodes ?? []).filter((n) => n.kind === "model_version" && isUuid(n.id));
  return (models.find((n) => (n.ref_kinds ?? []).includes("champion_model")) ?? models[0])?.id ?? null;
}

/** The eight project step screens in flow order (V7 UI-A "Next step"); History ends the flow. */
export type StepScreenId = FlowStepId | "history";
export type NextStep = { id: StepScreenId; title: string; detail: string; href: string };

export const NEXT_STEP_ORDER: ReadonlyArray<{ id: StepScreenId; title: string; detail: string }> = [
  { id: "data", title: "Look at the data", detail: "What is in the file and which columns the model uses." },
  { id: "goal", title: "See the goal and test design", detail: "What to predict, what good looks like, and how every run is tested." },
  { id: "experiments", title: "Run and compare experiments", detail: "Compare a baseline with real models on the same test design." },
  { id: "improve", title: "Try to improve the best run", detail: "Try another model family and see whether the gain is bigger than the noise." },
  { id: "models", title: "Look at the model", detail: "Read the model card, pick a threshold and approve a model for scoring." },
  { id: "predictions", title: "Score new data", detail: "Run the model on a new file, or let DCLab score the newest file every week." },
  { id: "monitoring", title: "Check how the model is doing", detail: "See whether new data still looks like the data the model learned from." },
  { id: "history", title: "See the history", detail: "Every change to this project: who made it, what it was, and when." },
];

/** Which step screen a path is: the step's own page only (a run, a model or a data version page is not a step screen). */
export function stepScreenForPath(pathname: string, projectId: string): StepScreenId | null {
  if (!isUuid(projectId)) return null;
  const rest = pathname.replace(/\/+$/, "").slice(`/projects/${projectId}/`.length);
  if (!pathname.startsWith(`/projects/${projectId}/`)) return null;
  const parts = rest.split("/");
  if (parts.length === 2 && parts[0] === "splits" && isUuid(parts[1])) return "goal";
  if (parts.length !== 1) return null;
  switch (parts[0]) {
    case "data": case "experiments": case "improve": case "monitoring": case "predictions": return parts[0];
    case "goal": return "goal";
    case "models": return "models";
    case "decisions": return "history";
    default: return null;
  }
}

/** The page of a step screen (History included); null for a non-UUID project or a step whose page needs an id the project lacks. */
export function stepScreenHref(id: StepScreenId, projectId: string, targets: FlowInput["targets"]): string | null {
  if (!isUuid(projectId)) return null;
  return id === "history" ? safeInternalHref(`/projects/${projectId}/decisions`) : hrefFor(id, projectId, targets);
}

/**
 * The page's one forward button: the next step after this screen whose page exists (built and, for Goal and Predictions,
 * the project has the test design / a model). None on History, the last step, or outside a step screen.
 */
export function nextStepFor(input: { projectId: string; pathname: string; available: ReadonlySet<string>; targets: FlowInput["targets"] }): NextStep | null {
  const here = stepScreenForPath(input.pathname, input.projectId);
  if (!here) return null;
  const index = NEXT_STEP_ORDER.findIndex((step) => step.id === here);
  for (const step of NEXT_STEP_ORDER.slice(index + 1)) {
    if (!input.available.has(step.id)) continue;
    const href = stepScreenHref(step.id, input.projectId, input.targets);
    if (href) return { ...step, href };
  }
  return null;
}
