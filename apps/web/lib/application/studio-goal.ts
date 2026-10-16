/**
 * Goal & test design logic (V7-A2), pure so it runs under `npm run test:components`.
 * Everything is read from fields the API already returns (the problem spec and split plan reads, the run detail,
 * the run's model-build stages and the decision records). A value the API did not return is omitted, never guessed.
 * No final test set value is read here: only the strategy, the fraction and row counts.
 */
import { isUuid, plainText } from "./command-search.ts";
import { stageConfig, type BuildLike } from "./studio-inspect.ts";

const str = (value: unknown): string | null => (typeof value === "string" && value.trim() ? value.trim() : null);

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
  return Object.hasOwn(METRICS, name) ? METRICS[name] : { label: plainText(name.replaceAll("_", " "), 40), about: null };
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

/** The business-rule answer in words. Only `not_requested` is "Not requested"; a missing or unknown value is "Not recorded". */
export function ruleStatusText(status: string | null | undefined): string {
  if (status === "not_requested") return "Not requested";
  return constraintText(status) ?? "Not recorded";
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

// --- goal (V7 UI-C: from the problem spec read, GET /v1/projects/{id}/problem-specs) ----------------

export type SpecLike = {
  target_column?: string | null; task_type?: string | null; primary_metric?: string | null; prediction_unit?: string | null;
  prediction_horizon?: string | null; business_objective?: string | null; constraints?: Record<string, unknown> | null;
} | null | undefined;
export type BusinessRule = { key: string; big: string; meaning: string };
export type GoalCards = {
  target: string | null; task: string | null; question: string | null; unitLine: string | null;
  metric: MetricInfo | null; metricWhy: string | null; rules: BusinessRule[]; rulesKnown: boolean;
};

const RULE_LABEL: Record<string, string> = { recall: "Recall", precision: "Precision", specificity: "Specificity", f1: "F1", accuracy: "Accuracy", balanced_accuracy: "Balanced accuracy" };
const share = (v: number) => `${Number((v * 100).toFixed(1))}%`;
const fmtValue = (v: number) => {
  const s = String(Number(v.toFixed(3)));
  return s.includes(".") && s.split(".")[1].length === 1 ? `${s}0` : s;
};

/** One business rule in words: "Recall ≥ 0.80" and what it means; null for a malformed entry. */
export function ruleWords(raw: unknown, index = 0): BusinessRule | null {
  const item = raw as { metric?: unknown; op?: unknown; value?: unknown } | null;
  const metric = typeof item?.metric === "string" ? item.metric.trim().toLowerCase() : "";
  const op = item?.op === ">=" || item?.op === "<=" ? item.op : null;
  const value = typeof item?.value === "number" && Number.isFinite(item.value) ? item.value : null;
  if (!metric || !op || value === null) return null;
  const label = Object.hasOwn(RULE_LABEL, metric) ? RULE_LABEL[metric] : metricInfo(metric)?.label ?? metric;
  const fraction = value >= 0 && value <= 1 && op === ">=";
  const meaning = fraction && metric === "recall" ? `Catch at least ${share(value)} of the rows that really are positive.`
    : fraction && metric === "precision" ? `When the model flags a row, it is right at least ${share(value)} of the time.`
      : fraction && metric === "specificity" ? `Leave alone at least ${share(value)} of the rows that really are negative.`
        : `${label} must be ${op === ">=" ? "at least" : "at most"} ${fmtValue(value)} on cross-validation.`;
  return { key: `${metric}${op}${index}`, big: `${label} ${op === ">=" ? "≥" : "≤"} ${fmtValue(value)}`, meaning };
}

/**
 * The three goal cards: what we predict, how models are ranked and the business rule, from the problem spec (untrusted
 * text is passed through `clean`); the run's recorded plan fills in a target or metric the spec does not name. The
 * ranking reason is the one given with the goal, else a sentence from the training-rows positive share, else the general one.
 */
export function goalCards(spec: SpecLike, fallback: GoalFacts, ctx: { targetRate?: number | null; clean?: (v: string, max?: number) => string } = {}): GoalCards {
  const clean = ctx.clean ?? ((v: string) => v);
  const constraints = spec?.constraints && typeof spec.constraints === "object" ? spec.constraints : {};
  const metricName = str(spec?.primary_metric);
  const metric = metricInfo(metricName) ?? fallback.metric;
  const unit = str(spec?.prediction_unit);
  const horizon = str(spec?.prediction_horizon);
  const given = str(constraints.primary_metric_reason);
  const rate = typeof ctx.targetRate === "number" && ctx.targetRate > 0 && ctx.targetRate < 0.5 ? ctx.targetRate : null;
  const rules = (Array.isArray(constraints.metric_constraints) ? constraints.metric_constraints : []).map((c, i) => ruleWords(c, i)).filter((r): r is BusinessRule => r !== null);
  const cost = constraints.cost_matrix as { false_positive?: unknown; false_negative?: unknown } | undefined;
  if (cost && typeof cost.false_positive === "number" && typeof cost.false_negative === "number") {
    rules.push({ key: "cost", big: "Error costs", meaning: `A missed positive costs ${fmtValue(cost.false_negative)}; a false alarm costs ${fmtValue(cost.false_positive)}.` });
  }
  return {
    target: str(spec?.target_column) ?? fallback.target,
    task: taskLabel(str(spec?.task_type)) ?? fallback.task,
    question: str(spec?.business_objective) ? clean(str(spec?.business_objective) ?? "", 300) : null,
    unitLine: [unit ? `One answer per ${clean(unit, 60)}.` : "", horizon ? `Prediction horizon: ${clean(horizon, 40)}.` : ""].filter(Boolean).join(" ") || null,
    metric,
    metricWhy: given ? `Reason given: ${clean(given, 300)}`
      : rate && metricName === "pr_auc" ? `Only about 1 in ${Math.round(1 / rate)} training rows has the answer the model treats as yes, and PR-AUC rewards finding them without flagging many others.`
        : metric?.about ?? null,
    rules,
    rulesKnown: !!spec,
  };
}

/** The test design the Goal page opens: the project's test design in use, else the newest one in the project graph; null = none yet. */
export function newestSplitPlan(refTarget: string | null | undefined, nodes: ReadonlyArray<{ kind: string; id: string; created_at?: string | null }> | undefined): string | null {
  if (isUuid(refTarget)) return refTarget;
  const plans = (nodes ?? []).filter((n) => n.kind === "split_plan" && isUuid(n.id));
  return plans.sort((a, b) => (b.created_at ?? "").localeCompare(a.created_at ?? ""))[0]?.id ?? null;
}

// --- test design (V7 UI-C: from GET /v1/split-plans/{id}; row counts only, never final-test dates or rates) ---

export type DesignKind = "time" | "group" | "random";

/** Time-ordered, grouped or random, from the recorded strategy names (the first known one wins). */
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

export type SplitPlanLike = {
  row_count: number; train_row_count: number; evaluation_row_count: number; has_evaluation_set: boolean; evaluation_strategy: string;
  validation_strategy: string; validation_folds: number; stratified: boolean; time_column: string | null; group_column: string | null;
  folds_status: string; folds: Array<{ fold_number: number; train_row_count: number; validation_row_count: number }>;
};
export type FoldRows = { fold: number; learn: number; check: number };
export type PlanDesign = {
  /** How the cross-validation folds are cut; the final test set (`testKind`) is planned separately and can differ. */
  kind: DesignKind | null; testKind: DesignKind | null; folds: number; trainRows: number; testRows: number; totalRows: number;
  testShare: number | null; hasTest: boolean; timeColumn: string | null; groupColumn: string | null; stratified: boolean;
  /** Per-fold rows, in fold order; empty when the row map could not be verified. */
  perFold: FoldRows[];
};

const rows = (n: unknown): number | null => (typeof n === "number" && Number.isFinite(n) && n >= 0 ? Math.floor(n) : null);
const fmt = (n: number) => n.toLocaleString("en-GB");

export function planDesign(plan: SplitPlanLike): PlanDesign {
  const trainRows = rows(plan.train_row_count) ?? 0;
  const testRows = plan.has_evaluation_set ? rows(plan.evaluation_row_count) ?? 0 : 0;
  const totalRows = Math.max(rows(plan.row_count) ?? 0, trainRows + testRows);
  const perFold = plan.folds_status !== "computed" ? [] : plan.folds
    .map((f) => ({ fold: rows(f.fold_number), learn: rows(f.train_row_count), check: rows(f.validation_row_count) }))
    .filter((f): f is FoldRows => f.fold !== null && f.learn !== null && f.check !== null)
    .sort((a, b) => a.fold - b.fold);
  return {
    kind: designKind(plan.validation_strategy), testKind: designKind(plan.evaluation_strategy), folds: rows(plan.validation_folds) ?? perFold.length,
    trainRows, testRows, totalRows, testShare: totalRows > 0 && testRows > 0 ? testRows / totalRows : null, hasTest: plan.has_evaluation_set && testRows > 0,
    timeColumn: str(plan.time_column), groupColumn: str(plan.group_column), stratified: plan.stratified === true, perFold,
  };
}

export type Seg = { x: number; w: number };
export type TimelineRow = { fold: number; learn: Seg[]; check: Seg; learnRows: number; checkRows: number };
/** Fractions (0..1) of all modelling rows: drawn to scale by row count. */
export type Timeline = { trainEnd: number; rows: TimelineRow[]; test: Seg | null; capped: boolean };
export const MAX_DRAWN_FOLDS = 10;

/**
 * The time line to scale. Time-ordered folds learn from the first rows (warm-up included) and check on the rows right after
 * them. Other folds are spread through the training rows: each fold's check rows are drawn as one block, side by side.
 * The final test set is the part after the training rows (its rows are counted, nothing else).
 */
export function timeline(d: PlanDesign): Timeline | null {
  if (d.totalRows <= 0) return null;
  const f = (n: number) => Math.min(1, Math.max(0, n / d.totalRows));
  const trainEnd = f(d.trainRows);
  let offset = 0;
  const drawn = d.perFold.slice(0, MAX_DRAWN_FOLDS).map((fold): TimelineRow => {
    const x = d.kind === "time" ? Math.min(f(fold.learn), trainEnd) : Math.min(f(offset), trainEnd);
    const w = Math.min(f(fold.check), trainEnd - x);
    offset += fold.check;
    const learn = d.kind === "time" ? [{ x: 0, w: x }] : [{ x: 0, w: x }, { x: x + w, w: trainEnd - x - w }];
    return { fold: fold.fold, learn: learn.filter((s) => s.w > 0), check: { x, w }, learnRows: fold.learn, checkRows: fold.check };
  });
  return { trainEnd, rows: drawn, test: d.hasTest ? { x: trainEnd, w: Math.min(f(d.testRows), 1 - trainEnd) } : null, capped: d.perFold.length > MAX_DRAWN_FOLDS };
}

const PHRASE: Record<DesignKind, string> = { time: "time-ordered", group: "grouped", random: "random" };
const TEST_WHERE: Record<DesignKind, string> = { time: "the latest rows in time", group: "whole groups set aside", random: "picked at random" };

/** The two chips under the time line: training rows with the folds, and the final test set's rows and share. */
export function designChips(d: PlanDesign): Array<{ key: string; title: string; detail: string }> {
  return [
    { key: "train", title: `Training rows: ${fmt(d.trainRows)}`, detail: d.folds ? `${d.folds} ${d.kind ? `${PHRASE[d.kind]} ` : ""}cross-validation folds` : "Cross-validation folds are not recorded" },
    d.hasTest
      ? { key: "test", title: `Final test set: ${fmt(d.testRows)} rows${d.testShare ? ` (${share(d.testShare)})` : ""}`, detail: `${d.testKind ? `${TEST_WHERE[d.testKind]} · ` : ""}used once per run, at the end` }
      : { key: "test", title: "No final test set", detail: "Every row of this test design is used for cross-validation." },
  ];
}

/** "Why this design?" from the strategies and the row counts only (no dates, no rates). Column names pass through `clean`. */
export function whyFromRows(d: PlanDesign, clean: (value: string) => string = (v) => v): string | null {
  const first = d.perFold[0];
  const last = d.perFold.at(-1);
  const avg = d.perFold.length ? Math.round(d.perFold.reduce((sum, f) => sum + f.check, 0) / d.perFold.length) : null;
  if (d.kind && d.testKind && d.kind !== d.testKind) {
    return `The cross-validation folds are ${PHRASE[d.kind]} and the final test set is ${PHRASE[d.testKind]}; the two are planned separately.`;
  }
  switch (d.kind ?? d.testKind) {
    case "time": {
      const by = d.timeColumn ? ` by ${clean(d.timeColumn)}` : "";
      const folds = first && last && d.kind === "time"
        ? ` Fold ${first.fold} learns from the first ${fmt(first.learn)} rows and is checked on the next ${fmt(first.check)}; each later fold learns from more of the past, up to ${fmt(last.learn)} rows in fold ${last.fold}.`
        : " Each fold learns from earlier rows and is checked on the rows right after them.";
      const test = d.hasTest ? ` The final test set is the latest ${fmt(d.testRows)} rows${d.testShare ? ` (${share(d.testShare)})` : ""}.` : "";
      return `Time-ordered${by}.${folds}${test} Shuffled rows would let the model peek at the future.`;
    }
    case "group":
      return `Grouped${d.groupColumn ? ` by ${clean(d.groupColumn)}` : ""}: all rows of one group stay on the same side of every split${avg !== null ? `, so each of the ${d.perFold.length} folds is checked on about ${fmt(avg)} rows from groups it did not learn from` : ""}.`;
    case "random":
      return `Random${d.stratified ? ", keeping the same class mix in every part" : ""}: rows are assigned by chance${avg !== null ? `; each of the ${d.perFold.length} folds is checked on about ${fmt(avg)} rows and learns from the rest of the ${fmt(d.trainRows)} training rows` : ""}.`;
    default:
      return null;
  }
}

/** Screen-reader summary of the time line, from the same numbers. */
export function timelineSummary(d: PlanDesign): string {
  const folds = d.perFold.slice(0, MAX_DRAWN_FOLDS).map((f) => `fold ${f.fold} learns from ${fmt(f.learn)} rows and checks on ${fmt(f.check)}`).join("; ");
  const test = d.hasTest ? `then a final test set of ${fmt(d.testRows)} rows, used once per run at the end` : "and no final test set";
  return `Test design drawn to scale by rows: ${d.folds} ${d.kind ? `${PHRASE[d.kind]} ` : ""}cross-validation folds on ${fmt(d.trainRows)} training rows${folds ? ` (${folds})` : ""}, ${test}.`;
}

// --- who recorded a decision (History, lineage) -----------------------------------------------

export type DecisionLike = {
  id: string; decision_type: string; effective_state: string; recorded_at: string;
  actor: { kind: string; agent_run_id?: string | null; service_token_id?: string | null }; subject: { kind: string; id: string };
};

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
