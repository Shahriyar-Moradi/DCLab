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
    case "splits": return "goal";
    case "experiments": case "pipeline": case "features": return "experiments";
    case "improve": return "improve";
    case "models": return tab === "score" ? "predictions" : "models";
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
  /** The `tab` query value of the current page (the score tab of a model is the Predictions step). */
  tab?: string | null;
  /** Ids behind the two steps whose page needs one; null = the project has none yet. */
  targets: { split_plan?: string | null; champion_model?: string | null };
};

function hrefFor(id: FlowStepId, projectId: string, targets: FlowInput["targets"]): string | null {
  const base = `/projects/${projectId}`;
  switch (id) {
    case "goal": return isUuid(targets.split_plan) ? safeInternalHref(`${base}/splits/${targets.split_plan}`) : null;
    case "predictions": return isUuid(targets.champion_model) ? safeInternalHref(`${base}/models/${targets.champion_model}?tab=score`) : null;
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
