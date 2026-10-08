/**
 * New project / new run wizard (P4.1-B): pure state, validation and error mapping.
 * No React and no network here, so it runs under `npm run test:components`.
 *
 * Contract: the wizard only sends what the /v1 contract accepts (ProblemSpecCreateRequest
 * objective fields: primary_metric, constraints.metric_constraints) and never reads a holdout value.
 */

export type WizardStepId = "data" | "target" | "objective" | "train";
export const WIZARD_STEPS: ReadonlyArray<{ id: WizardStepId; label: string }> = [
  { id: "data", label: "1 Data" },
  { id: "target", label: "2 What to predict" },
  { id: "objective", label: "3 What good looks like" },
  { id: "train", label: "4 Train" },
];

/** Task types the ProblemSpec accepts; "classification" lets DCLab infer binary vs multiclass at run time. */
export const TASK_OPTIONS = [
  { value: "classification", label: "Classification (binary or multiclass)" },
  { value: "binary", label: "Binary classification" },
  { value: "multiclass", label: "Multiclass classification" },
  { value: "regression", label: "Regression (a number)" },
] as const;
export type TaskType = (typeof TASK_OPTIONS)[number]["value"];

// Mirrors engine/modeling/objective.py PRIMARY_METRICS / CONSTRAINT_METRICS (the API re-validates).
const BINARY = ["pr_auc", "roc_auc", "f1", "balanced_accuracy", "accuracy", "log_loss", "brier_score"];
const MULTI = ["macro_f1", "weighted_f1", "balanced_accuracy", "accuracy", "log_loss", "roc_auc_ovr"];
const REGRESSION = ["mae", "rmse", "mse", "r2", "median_absolute_error"];
export function primaryMetricOptions(task: TaskType): string[] {
  if (task === "binary") return BINARY;
  if (task === "multiclass") return MULTI;
  if (task === "regression") return REGRESSION;
  return [...new Set([...BINARY, ...MULTI])];
}
export function constraintMetricOptions(task: TaskType): string[] {
  if (task === "regression") return [...REGRESSION, "mape", "smape"];
  const threshold = ["precision", "recall", "specificity"];
  if (task === "multiclass") return [...MULTI, "macro_precision", "macro_recall"];
  if (task === "binary") return [...BINARY, ...threshold];
  return [...new Set([...BINARY, ...MULTI, ...threshold, "macro_precision", "macro_recall"])];
}

export type ConstraintDraft = { metric: string; op: ">=" | "<="; value: string };
export type ObjectiveDraft = { primaryMetric: string; constraint: ConstraintDraft | null; businessObjective: string };

export type SpecBody = {
  task_type: TaskType;
  business_objective: string;
  target_column: string | null;
  primary_metric: string | null;
  constraints: { metric_constraints?: Array<{ metric: string; op: ">=" | "<="; value: number }> };
  status: "locked";
};

/** The ProblemSpec request for the wizard's answers; `null` target = let DCLab choose and ask if unclear. */
export function buildSpecBody(task: TaskType, target: string | null, objective: ObjectiveDraft): SpecBody {
  const constraints: SpecBody["constraints"] = {};
  const draft = objective.constraint;
  if (draft && draft.metric) {
    constraints.metric_constraints = [{ metric: draft.metric, op: draft.op, value: Number(draft.value) }];
  }
  return {
    task_type: task,
    business_objective: objective.businessObjective.trim() || "Predict the target column from the other columns.",
    target_column: target && target.trim() ? target.trim() : null,
    primary_metric: objective.primaryMetric || null,
    constraints,
    status: "locked",
  };
}

/** A problem with the objective step, in plain language, or null when it can be sent. */
export function objectiveProblem(objective: ObjectiveDraft): string | null {
  const draft = objective.constraint;
  if (!draft || !draft.metric) return null;
  if (draft.value.trim() === "" || !Number.isFinite(Number(draft.value))) return "Enter a number for the business rule, or remove the rule.";
  return null;
}

export type DataStepInput = { projectId: string | null; projectName: string; file: File | null; datasetId: string | null };
export function dataStepProblem(input: DataStepInput): string | null {
  if (input.datasetId) return null;
  if (!input.file) return "Choose a data file to upload.";
  if (!input.projectId && !input.projectName.trim()) return "Name the project.";
  return null;
}

/** One Idempotency-Key per user action: a retry of the same action and input replays, a changed input is a new action. */
export class ActionKeys {
  private readonly keys = new Map<string, { fingerprint: string; key: string }>();
  private readonly generate: () => string;
  constructor(generate: () => string) {
    this.generate = generate;
  }
  keyFor(action: string, fingerprint: string): string {
    const known = this.keys.get(action);
    if (known && known.fingerprint === fingerprint) return known.key;
    const key = this.generate();
    this.keys.set(action, { fingerprint, key });
    return key;
  }
  /** After a success the next submit of the same action is a new action. */
  done(action: string): void {
    this.keys.delete(action);
  }
}

/** Single flight: a second call while one is in flight returns the same promise (double-click safe). */
export function singleFlight<T>(run: () => Promise<T>): () => Promise<T> {
  let inflight: Promise<T> | null = null;
  return () => {
    if (inflight) return inflight;
    inflight = run().finally(() => {
      inflight = null;
    });
    return inflight;
  };
}

/** A problem with the user's own answers, found before any request is sent. */
export class WizardInputError extends Error {}

export type PlainError = { title: string; detail: string; fixable: boolean };

export function envelope(body: unknown): { code: string; message: string; details: Record<string, unknown> } {
  const error = (body as { error?: Record<string, unknown> } | null)?.error;
  const code = typeof error?.code === "string" ? error.code.toLowerCase() : "";
  const message = typeof error?.message === "string" ? error.message : "";
  const details = error?.details && typeof error.details === "object" ? (error.details as Record<string, unknown>) : {};
  return { code, message, details };
}

const BY_CODE: Record<string, PlainError> = {
  target_not_in_dataset: { title: "That target column is not in the data", detail: "Pick a column that exists in the uploaded file.", fixable: true },
  target_intent_conflict: { title: "The target differs from the objective", detail: "The project's objective already names another target column. Use the same column, or create a new objective.", fixable: true },
  plan_refused: { title: "The run plan was refused", detail: "The plan cannot be used for this objective. The run can start without it; DCLab uses its own rules.", fixable: true },
  split_confirmation_required: { title: "Confirm the test design", detail: "The run waits for your answer on the run page before the data is split.", fixable: true },
  target_confirmation_required: { title: "Confirm the target column", detail: "DCLab found several possible targets. Choose one on the run page, or pick it here.", fixable: true },
  execution_not_waiting: { title: "This run is not waiting for an answer", detail: "It may already have been answered. Reload the run to see its state.", fixable: false },
  idempotency_key_conflict: { title: "That request was already used with different content", detail: "Reload the wizard and submit again.", fixable: false },
  upload_rejected: { title: "That file could not be read as a table", detail: "Use a CSV, TSV, JSON, Parquet or XLSX file with a header row.", fixable: true },
  payload_too_large: { title: "The file is too large", detail: "Upload a smaller file.", fixable: true },
  length_required: { title: "The upload could not be sent", detail: "Try again; if it keeps failing, use a smaller file.", fixable: true },
};

/** Backend error -> plain language. Unknown codes keep the backend's own (plain text) message. */
export function mapWizardError(error: unknown): PlainError {
  if (error instanceof WizardInputError) return { title: "Check your answers", detail: error.message, fixable: true };
  const status = (error as { status?: unknown } | null)?.status;
  const { code, message, details } = envelope((error as { body?: unknown } | null)?.body);
  const known = BY_CODE[code];
  if (known) return known;
  if (code === "validation_failed") {
    const first = (details.errors as Array<{ loc?: unknown[]; msg?: string }> | undefined)?.[0];
    const where = Array.isArray(first?.loc) ? first.loc.filter((p) => p !== "body").join(" / ") : "";
    return { title: "Some answers are not valid", detail: first?.msg ? `${where ? `${where}: ` : ""}${first.msg}` : "Check the highlighted answers.", fixable: true };
  }
  if (status === 429) return { title: "Too many runs at once", detail: message || "Wait for a run to finish, then try again.", fixable: false };
  if (status === 403) return { title: "You cannot do this here", detail: "Starting a run needs a role that can write ML work in this workspace.", fixable: false };
  if (status === 404) return { title: "Not found", detail: message || "The project or dataset is not in this workspace.", fixable: false };
  if (status === 409 || status === 422 || status === 400) return { title: "The request was refused", detail: message || "Check your answers and try again.", fixable: true };
  return { title: "Something went wrong", detail: message || (error instanceof Error ? error.message : "Try again."), fixable: false };
}
