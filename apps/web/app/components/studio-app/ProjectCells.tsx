"use client";

import { Pill } from "@/components/studio/Pill";
import { statusTone } from "@/app/components/studio-app/StudioParts";
import type { StudioProjectListItem } from "@/lib/application";
import { goalWords, lastRunText } from "@/lib/application/studio-projects";

/** Table cells shared by Home's projects card and the Projects list (V7 UI-B); text only, from the project summary. */
export function GoalCell({ project }: { project: StudioProjectListItem }) {
  const goal = goalWords(project.summary?.goal);
  if (!goal) return <span className="muted">Not set yet</span>;
  if (goal.objective) return <>{goal.objective}{goal.target ? <><br /><small className="muted">Column to predict: <span className="mono">{goal.target}</span></small></> : null}</>;
  return goal.target ? <>Predict <span className="mono">{goal.target}</span></> : <span className="muted">Set, no column yet</span>;
}

export function LastRunCell({ project }: { project: StudioProjectListItem }) {
  const run = lastRunText(project.summary?.latest_run);
  return run ? <Pill tone={statusTone(run.status)}>{run.text}</Pill> : <span className="muted">No runs yet</span>;
}
