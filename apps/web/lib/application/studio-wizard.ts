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
  const known = Object.hasOwn(BY_CODE, code) ? BY_CODE[code] : undefined;
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

// --- fixed error sentences for the New project flow (D2b review) ---------------------------

/** Which call was refused, so a 403 sentence names it. */
export type WizardContext = "project" | "upload" | "goal" | "run" | "answer";
const ROLE_NEEDED: Record<WizardContext, string> = {
  project: "Creating a project needs a role that can write ML work in this workspace.",
  upload: "Uploading a file needs a role that can write ML work in this workspace.",
  goal: "Saving what to predict needs a role that can write ML work in this workspace.",
  run: "Starting a run needs a role that can write ML work in this workspace.",
  answer: "Answering the run's question needs a role that can write ML work in this workspace.",
};

/**
 * Like `mapWizardError`, but every sentence is fixed and chosen by the HTTP status and the error code only: the server's
 * message, an exception's message and validation details are never shown (they go to the console for developers).
 * Codes are looked up by own keys, so a code such as `__proto__` falls through to the status sentence.
 */
export function plainWizardError(error: unknown, context: WizardContext, log: (...parts: unknown[]) => void = console.warn): PlainError {
  if (error instanceof WizardInputError) return { title: "Check your answers", detail: error.message, fixable: true };
  const status = (error as { status?: unknown } | null)?.status;
  const { code, message } = envelope((error as { body?: unknown } | null)?.body);
  log("[new project]", context, typeof status === "number" ? status : "no status", code || "no code", message || (error instanceof Error ? error.message : ""));
  const known = Object.hasOwn(BY_CODE, code) ? BY_CODE[code] : undefined;
  if (known) return known;
  if (code === "validation_failed") return { title: "Some answers are not valid", detail: "Check the answers in this step and try again.", fixable: true };
  if (status === 429) return context === "run"
    ? { title: "Too many runs at once", detail: "Wait for a run to finish, then try again.", fixable: false }
    : { title: "Too many requests at once", detail: "Wait a moment, then try again.", fixable: false };
  if (status === 403) return { title: "You cannot do this here", detail: ROLE_NEEDED[context], fixable: false };
  if (status === 404) return { title: "Not found", detail: "The project or file is not in this workspace.", fixable: false };
  if (status === 413) return BY_CODE.payload_too_large;
  if (status === 409 || status === 422 || status === 400) return { title: "The request was refused", detail: "Check your answers and try again.", fixable: true };
  return { title: "Something went wrong", detail: "Try again. If it keeps failing, reload the page.", fixable: false };
}

// --- New project dialog (D2b, founder decision 15) ------------------------------------------

export type DialogStepId = "file" | "goal" | "design";
export const DIALOG_STEPS: ReadonlyArray<{ id: DialogStepId; label: string }> = [
  { id: "file", label: "1 Name and file" },
  { id: "goal", label: "2 What to predict" },
  { id: "design", label: "3 Test design" },
];

export function dialogStepState(id: DialogStepId, at: DialogStepId): "done" | "current" | "todo" {
  const order = DIALOG_STEPS.findIndex((s) => s.id === id);
  const now = DIALOG_STEPS.findIndex((s) => s.id === at);
  return order < now ? "done" : order === now ? "current" : "todo";
}

export type TestDesignRules = { suggestion: string; exceptions: string[]; after: string };

/**
 * The fixed rules that make the test design when the run starts (engine/modeling/holdout_planner.py `plan_holdout`,
 * called with a 20% final test set). Nothing here reads the file: the rules apply to the cleaned table inside the run,
 * and the run page shows the design they chose before any model is trained. Words only, no numbers but the rule's 20%.
 */
export function testDesignRules(task: TaskType): TestDesignRules {
  const keep = "keeping the share of each answer the same in both parts";
  const suggestion =
    task === "regression" ? "A random 20% of the rows is set aside as the final test set."
      : task === "multiclass" ? `A random 20% of the rows is set aside as the final test set, ${keep}. If an answer has fewer than 3 rows, or there are too few rows per answer, the 20% is drawn at random without that.`
        : task === "binary" ? `A random 20% of the rows is set aside as the final test set, ${keep}.`
          : `A random 20% of the rows is set aside as the final test set, ${keep} (the run decides between two answers and more than two at the start).`;
  return {
    suggestion,
    exceptions: [
      "If the rows follow time strongly, the final test set is the latest 20% instead, so the model is tested on later rows than it learned from.",
      "If the same customer or other ID appears in many rows, whole IDs go to one side, so none is in both the training rows and the final test set. If there are fewer than 2 different IDs, the run stops and says so.",
      "If both are true, the run stops and says so: there is no safe test design for that case yet.",
    ],
    after: "The other 80% is split into cross-validation folds to compare models. The final test set is used once, at the end, for the chosen model.",
  };
}
