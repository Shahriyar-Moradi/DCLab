/**
 * Projects list v7 (V7 UI-B): goal, "where it is", the model in use's CV score, the model in use and the last run, all
 * from `GET /v1/projects` summaries (one read, no per-project calls). Pure: no React, no fetch. Untrusted texts (names,
 * goals) are capped plain text; lookups by server values use own keys only.
 */
import { plainText } from "./command-search.ts";
import type { PillToneName } from "./studio-home.ts";
import { lookup } from "./studio-inbox.ts";
import { modelName } from "./studio-names.ts";
import { statusWords } from "./studio-runs.ts";
import { DIALOG_STEPS, type DialogStepId } from "./studio-wizard.ts";

type Summary = {
  goal?: { target_column?: string | null; objective?: string | null; status?: string } | null;
  champion?: { version: string } | null;
  latest_run?: { status: string; run_number?: number | null } | null;
  stage?: string;
  waiting_decisions?: number;
};

export type Pill = { text: string; tone: PillToneName };

/** The furthest point a project reached (the summary's `stage`), in words; the order sorts the "Where it is" column. */
const STAGES: Record<string, { text: string; tone: PillToneName; order: number }> = {
  no_data: { text: "No file yet", tone: "gray", order: 0 },
  data_uploaded: { text: "File uploaded", tone: "gray", order: 1 },
  goal_set: { text: "Goal set", tone: "det", order: 2 },
  running: { text: "Run in progress", tone: "ai", order: 3 },
  runs_finished: { text: "Runs finished", tone: "ok", order: 4 },
  model_in_use: { text: "Model in use", tone: "ok", order: 5 },
};

/** "Where it is": the waiting decisions first (they need a person), then the stage. Nothing when the summary is missing. */
export function whereItIs(summary: Summary | undefined): Pill[] {
  if (!summary) return [];
  const pills: Pill[] = [];
  const waiting = typeof summary.waiting_decisions === "number" && summary.waiting_decisions > 0 ? Math.floor(summary.waiting_decisions) : 0;
  if (waiting) pills.push({ text: `${waiting} ${waiting === 1 ? "decision" : "decisions"} waiting`, tone: "warn" });
  if (summary.stage) pills.push(stagePill(summary));
  return pills.map(({ text, tone }) => ({ text, tone }));
}

/**
 * The stage pill, made honest with the summary's own fields: "runs finished" is green only when the latest run completed
 * (a failed or stopped latest run says so), and a goal that is still a draft is "Goal drafted", not "Goal set".
 */
function stagePill(summary: Summary): { text: string; tone: PillToneName } {
  const stage = summary.stage ?? "";
  if (stage === "runs_finished") {
    const status = summary.latest_run?.status;
    if (status === "completed") return STAGES.runs_finished;
    if (status === "failed") return { text: "Last run failed", tone: "crit" };
    return { text: "Runs ended", tone: "gray" };
  }
  if (stage === "goal_set" && summary.goal?.status !== "locked") return { text: "Goal drafted", tone: "gray" };
  return lookup(STAGES, stage, { text: plainText(stage.replaceAll("_", " "), 30) || "Unknown", tone: "gray", order: -1 });
}

/** Sort key of the stage (unknown stages first). */
export const stageOrder = (stage: string | undefined): number => lookup(STAGES, stage, { text: "", tone: "gray", order: -1 }).order;

/** The goal in words: the objective when one was written, else the column to predict; null = no goal yet. */
export function goalWords(goal: Summary["goal"]): { objective: string | null; target: string | null } | null {
  if (!goal) return null;
  return { objective: plainText(goal.objective, 120) || null, target: plainText(goal.target_column, 40) || null };
}

/** "Run 6 · Running", or null when the project has no run. */
export function lastRunText(run: Summary["latest_run"]): { text: string; status: string } | null {
  if (!run) return null;
  const name = Number.isInteger(run.run_number) ? `Run ${run.run_number}` : "Latest run";
  return { text: `${name} · ${statusWords(run.status)}`, status: run.status };
}

/** "Model v2", or null when nothing is in use. */
export const modelInUseText = (summary: Summary | undefined): string | null => (summary?.champion ? modelName(plainText(summary.champion.version, 20)) : null);

/** What each step of the New project dialog asks for (D2b). Keyed by the dialog's own step ids. */
const STEP_DETAIL: Record<DialogStepId, string> = {
  file: "A project name and a data file. Uploading saves both; DCLab checks the file's structure and lists its columns.",
  goal: "The column to predict, the kind of answer it holds, the score that picks the best model and an optional success rule.",
  design: "The test design the fixed rules will make. You confirm that the rules may choose it; only then does training start. The run page shows the design they chose.",
};

export const NEW_PROJECT_STEPS: ReadonlyArray<{ id: DialogStepId; label: string; detail: string }> = DIALOG_STEPS.map((step) => ({ ...step, detail: STEP_DETAIL[step.id] }));

export const STARTING_A_PROJECT =
  "New project opens a dialog with three steps: a name and a file, what to predict with an optional success rule, and the test design the rules will make. You confirm that the rules may choose it; only then does training start. The run page shows the design they chose " +
  "and asks you if it needs an answer. Every step is a form, so it works with AI switched off; the older four-step page is still linked from the dialog.";
