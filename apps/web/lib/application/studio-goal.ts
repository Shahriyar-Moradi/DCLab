/**
 * Goal & test design logic (V7-A2), pure so it runs under `npm run test:components`.
 * Everything is read from fields the API already returns (the run's model-build stages, the run detail,
 * the model card split and the decision records). A value the API did not return is omitted, never guessed.
 * No final test set value is read here: only the strategy, the fraction and row counts.
 */
import { stageConfig, type BuildLike } from "./studio-inspect.ts";

const str = (value: unknown): string | null => (typeof value === "string" && value.trim() ? value.trim() : null);
const num = (value: unknown): number | null => (typeof value === "number" && Number.isFinite(value) ? value : null);

export type MetricInfo = { label: string; glossary?: "prauc" | "recall" | "precision"; about: string | null };
const METRICS: Record<string, MetricInfo> = {
  pr_auc: { label: "PR-AUC", glossary: "prauc", about: "Rewards finding the rare outcome without flagging many other rows." },
  roc_auc: { label: "ROC-AUC", about: "How well the model puts true positives above true negatives, whatever the threshold." },
  f1: { label: "F1", about: "A balance of precision and recall at one threshold." },
  accuracy: { label: "accuracy", about: "The share of rows predicted correctly. It can look high when one answer is much more common." },
  balanced_accuracy: { label: "balanced accuracy", about: "Accuracy averaged over each class, so a rare class counts as much as a common one." },
  log_loss: { label: "log loss", about: "Penalizes confident wrong probabilities. Lower is better." },
  brier_score: { label: "Brier score", about: "The average squared error of the predicted probabilities. Lower is better." },
  macro_f1: { label: "macro F1", about: "F1 averaged over classes, so every class counts equally." },
  weighted_f1: { label: "weighted F1", about: "F1 averaged over classes, weighted by how common each class is." },
  mae: { label: "MAE", about: "The average size of the error, in the units of the target. Lower is better." },
  rmse: { label: "RMSE", about: "Like MAE but large errors count more. Lower is better." },
  mse: { label: "MSE", about: "The average squared error. Lower is better." },
  r2: { label: "R²", about: "The share of the target's variation the model explains. Higher is better." },
};

export function metricInfo(metric: string | null | undefined): MetricInfo | null {
  const name = (metric ?? "").trim();
  if (!name) return null;
  return Object.hasOwn(METRICS, name) ? METRICS[name] : { label: name.replaceAll("_", " "), about: null };
}

export function taskLabel(task: string | null | undefined): string | null {
  switch ((task ?? "").trim()) {
    case "binary": return "A yes/no answer (binary classification)";
    case "multiclass": return "One of several categories (classification)";
    case "classification": return "A category (classification)";
    case "regression": return "A number (regression)";
    default: return str(task)?.replaceAll("_", " ") ?? null;
  }
}

/** What the business-rule check of the selected model says, in words; `not_requested` and unknown give nothing to show. */
export function constraintText(status: string | null | undefined): string | null {
  switch (status) {
    case "satisfied": return "The selected model met the business rule on cross-validation.";
    case "unsatisfiable": return "The selected model could not meet the business rule on cross-validation at any threshold; the closest one is used.";
    case "not_satisfied": return "The selected model did not meet the business rule.";
    case "not_verifiable": return "The business rule could not be checked for this run.";
    default: return null;
  }
}

export type RunDetailLike = { target_column?: string | null; task_type?: string | null; metrics?: { selection_metric?: string | null; constraint_status?: string | null } | null } | null | undefined;
export type GoalFacts = { target: string | null; task: string | null; metric: MetricInfo | null; businessRule: string | null; ruleNotRequested: boolean };

export function goalFacts(build: BuildLike | undefined, run: RunDetailLike): GoalFacts {
  const targetTask = stageConfig(build, "target_task");
  return {
    target: str(targetTask.target_column) ?? str(run?.target_column),
    task: taskLabel(str(targetTask.task_type) ?? str(run?.task_type)),
    metric: metricInfo(str(stageConfig(build, "metric_plan").primary_metric) ?? str(targetTask.evaluation_metric) ?? str(run?.metrics?.selection_metric)),
    businessRule: constraintText(run?.metrics?.constraint_status),
    ruleNotRequested: run?.metrics?.constraint_status === "not_requested",
  };
}

// --- test design ------------------------------------------------------------------------

export type DesignKind = "time" | "group" | "random";
export type SplitCardLike = {
  evaluation_split_strategy?: string | null; evaluation_fraction?: number | null; evaluation_rows?: number | null; train_rows?: number | null;
  group_column?: string | null; time_column?: string | null; stratified?: boolean | null; validation_folds?: number | null; validation_strategy?: string | null;
} | null | undefined;
export type TestDesign = {
  /** How the cross-validation folds are cut (from the validation plan). */
  kind: DesignKind | null;
  /** How the final test set was set aside (from the holdout plan); it is planned separately and can differ. */
  testKind: DesignKind | null; folds: number | null; trainRows: number | null; testRows: number | null; testShare: number | null;
  groupColumn: string | null; timeColumn: string | null; stratified: boolean | null; lockedAt: string | null; locked: boolean;
};

/** Time-ordered, grouped or random, from the recorded strategy names (final test set first, then the folds). */
export function designKind(...strategies: Array<string | null | undefined>): DesignKind | null {
  for (const raw of strategies) {
    const s = (raw ?? "").toLowerCase();
    if (!s) continue;
    if (s.includes("temporal") || s.includes("time")) return "time";
    if (s.includes("group")) return "group";
    if (s.includes("random") || s.includes("kfold")) return "random";
  }
  return null;
}

export function testDesign(build: BuildLike | undefined, card: SplitCardLike): TestDesign {
  const holdout = { ...stageConfig(build, "final_holdout_plan"), ...stageConfig(build, "holdout_lock") };
  const validation = stageConfig(build, "validation_plan");
  const trainRows = num(card?.train_rows);
  const testRows = num(card?.evaluation_rows);
  const fraction = num(holdout.test_size) ?? num(card?.evaluation_fraction);
  const share = trainRows !== null && testRows !== null && trainRows + testRows > 0 ? testRows / (trainRows + testRows)
    : fraction !== null ? (fraction > 1 ? fraction / 100 : fraction) : null;
  return {
    kind: designKind(str(validation.strategy), card?.validation_strategy),
    testKind: designKind(str(holdout.strategy), card?.evaluation_split_strategy),
    folds: num(validation.actual_folds) ?? num(card?.validation_folds) ?? num(validation.requested_folds),
    trainRows, testRows, testShare: share !== null && share > 0 && share < 1 ? share : null,
    groupColumn: str(holdout.group_column) ?? str(validation.group_column) ?? str(card?.group_column),
    timeColumn: str(holdout.time_column) ?? str(validation.time_column) ?? str(card?.time_column),
    stratified: typeof card?.stratified === "boolean" ? card.stratified : null,
    locked: Boolean(stageConfig(build, "holdout_lock").locked),
    lockedAt: str(stageConfig(build, "holdout_lock").locked_at),
  };
}

const PHRASE: Record<DesignKind, string> = { time: "time-ordered", group: "grouped", random: "random" };

/**
 * One sentence on why this design, from the strategies the plans recorded. The folds and the final test set are planned
 * separately: when they differ, both are named. Column names are passed through `clean`.
 */
export function whyText(design: TestDesign, clean: (value: string) => string = (v) => v): string | null {
  const { kind, testKind } = design;
  if (kind && testKind && kind !== testKind) {
    return `The cross-validation folds are ${PHRASE[kind]} and the final test set is ${PHRASE[testKind]}; the two are planned separately.`;
  }
  switch (kind ?? testKind) {
    case "time":
      return `Time-ordered${design.timeColumn ? ` by ${clean(design.timeColumn)}` : ""}: each fold learns from earlier rows and is checked on the rows right after them, and the final test set is the latest part. A shuffled split would let the model peek at the future.`;
    case "group":
      return `Grouped${design.groupColumn ? ` by ${clean(design.groupColumn)}` : ""}: all rows of the same group stay on one side of every split, so the model is never tested on a group it trained on.`;
    case "random":
      return `Random${design.stratified ? ", keeping the same class mix in every part" : ""}: rows are assigned by chance. The plan found no time order or repeated group to protect.`;
    default:
      return null;
  }
}

export type Rect = { x: number; w: number };
export type FoldBar = { fold: number; train: Rect[]; validation: Rect };
export type DesignGraphic = { trainWidth: number; folds: FoldBar[]; capped: boolean };
export const MAX_DRAWN_FOLDS = 10;

/**
 * A schematic (widths in 0..100): the final test set on the right, the training period before it cut into folds.
 * Time-ordered folds grow from the start and validate on the block right after; others hold one block out in turn.
 * The share of the final test set uses the real row counts or fraction when known, else a fixed 20%.
 */
export function designGraphic(kind: DesignKind | null, folds: number | null, testShare: number | null): DesignGraphic | null {
  if (kind === null || folds === null || !Number.isInteger(folds) || folds < 2) return null;
  const drawn = Math.min(folds, MAX_DRAWN_FOLDS);
  const trainWidth = 100 * (1 - (testShare ?? 0.2));
  const bars: FoldBar[] = [];
  if (kind === "time") {
    const block = trainWidth / (drawn + 1);
    for (let i = 1; i <= drawn; i += 1) bars.push({ fold: i, train: [{ x: 0, w: i * block }], validation: { x: i * block, w: block } });
  } else {
    const block = trainWidth / drawn;
    for (let i = 0; i < drawn; i += 1) {
      const train: Rect[] = [];
      if (i > 0) train.push({ x: 0, w: i * block });
      if (i < drawn - 1) train.push({ x: (i + 1) * block, w: trainWidth - (i + 1) * block });
      bars.push({ fold: i + 1, train, validation: { x: i * block, w: block } });
    }
  }
  return { trainWidth, folds: bars, capped: folds > drawn };
}

// --- who decided -------------------------------------------------------------------------

export type DecisionLike = {
  id: string; decision_type: string; effective_state: string; recorded_at: string;
  actor: { kind: string; agent_run_id?: string | null; service_token_id?: string | null }; subject: { kind: string; id: string };
};
export type DecidedRow = { key: string; part: string; by: string; when: string; state: string };

/** Who recorded a decision. Outside tools with access tokens are never "the assistant". */
export function actorWords(actor: DecisionLike["actor"]): string {
  if (actor.kind === "rule") return "the rules";
  if (actor.kind === "human") return "a person";
  if (actor.kind === "agent") return actor.service_token_id ? "a connected tool (access token)" : actor.agent_run_id ? "the assistant" : "an agent";
  return "someone else";
}

/** The problem spec a run used (`experiment uses_problem_spec problem_spec`). */
export function problemSpecOf(edges: Array<{ from: { kind: string; id: string }; to: { kind: string; id: string }; relation: string }>, experimentId: string): string | null {
  return edges.find((e) => e.relation === "uses_problem_spec" && e.from.kind === "experiment" && e.from.id === experimentId)?.to.id ?? null;
}

/**
 * The newest decision record of this run's goal (its own problem spec) and of this test design. Only what the record says is shown:
 * who recorded it and when. Nothing is claimed about who confirmed it.
 */
export function decidedRows(items: DecisionLike[], splitPlanId: string, problemSpecId: string | null = null): DecidedRow[] {
  const newest = (match: (d: DecisionLike) => boolean) =>
    items.filter(match).sort((a, b) => b.recorded_at.localeCompare(a.recorded_at) || a.id.localeCompare(b.id))[0];
  const goal = problemSpecId ? newest((d) => d.decision_type === "problem_spec_locked" && d.subject.id === problemSpecId) : undefined;
  const design = newest((d) => d.decision_type === "split_plan_created" && d.subject.id === splitPlanId);
  return [
    goal ? { key: goal.id, part: "What we predict, how models are ranked and the business rule", by: actorWords(goal.actor), when: goal.recorded_at, state: goal.effective_state } : null,
    design ? { key: design.id, part: "Test design", by: actorWords(design.actor), when: design.recorded_at, state: design.effective_state } : null,
  ].filter((row): row is DecidedRow => row !== null);
}
