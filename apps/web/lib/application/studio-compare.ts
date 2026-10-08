/**
 * Compare, branch, refs and decisions (P4.4-A): pure logic, no React and no network, so it runs
 * under `npm run test:components`.
 *
 * Contract: the branch builder only shapes the typed change set the API accepts (the closed kinds
 * below mirror `apps/api/app/domain/experiment_changes.py`); every semantic rule (known family,
 * observed classes, leakage exclusions) is answered by the API's own 422 and shown as it comes back.
 * Compare shows cross-validation numbers only: the schema in the hooks has no holdout field.
 */
import { isUuid } from "./command-search.ts";
import { mapWizardError, type PlainError } from "./studio-wizard.ts";

export const COMPARE_MIN = 2;
export const COMPARE_MAX = 10;

/** `?ids=a,b` -> 2..10 distinct UUIDs, or null (the page then explains what to do). */
export function parseCompareIds(raw: string | null | undefined): string[] | null {
  if (!raw) return null;
  const ids = raw.split(",").map((part) => part.trim().toLowerCase()).filter(Boolean);
  if (ids.length < COMPARE_MIN || ids.length > COMPARE_MAX) return null;
  if (!ids.every(isUuid) || new Set(ids).size !== ids.length) return null;
  return ids;
}

export function compareHref(projectId: string, ids: string[]): string | null {
  if (!isUuid(projectId) || ids.length < COMPARE_MIN || !ids.every(isUuid)) return null;
  return `/projects/${projectId}/experiments/compare?ids=${ids.join(",")}`;
}

const NOT_COMPARABLE: Record<string, string> = {
  split_plan_mismatch: "These runs were split differently, so their holdouts and folds are different rows. Only runs on the same split plan can be compared fairly. Branch from one run to get a comparable run.",
  evidence_missing: "One of these runs has no locked winner with scores yet. Wait for it to finish, then compare again.",
  winner_missing: "One of these runs has no locked winner with scores yet. Wait for it to finish, then compare again.",
  not_comparable: "These runs cannot be compared (they belong to different workspaces or lack a shared plan).",
};

/** Plain-language reason for a refused comparison (the API's `409` code), or null when it is not one. */
export function compareRefusal(error: unknown): string | null {
  const status = (error as { status?: unknown } | null)?.status;
  const body = (error as { body?: { error?: { code?: unknown; message?: unknown } } } | null)?.body;
  const code = typeof body?.error?.code === "string" ? body.error.code.toLowerCase() : "";
  if (status !== 409 && !(code in NOT_COMPARABLE)) return null;
  return NOT_COMPARABLE[code] ?? (typeof body?.error?.message === "string" ? body.error.message : "These runs cannot be compared.");
}

export type CompareItem = { experiment_id: string; cv?: Record<string, number>; family?: string | null; selection_metric?: string | null; selected_score?: number | null };
export type MetricRow = { metric: string; values: Array<number | null>; delta: number | null };

/** Metric rows over the metrics every side recorded (`common.cv`); delta = last side minus the first (baseline). */
export function metricRows(items: CompareItem[], common: string[]): MetricRow[] {
  return common.map((metric) => {
    const values = items.map((item) => (typeof item.cv?.[metric] === "number" ? item.cv[metric] : null));
    const first = values[0];
    const last = values[values.length - 1];
    return { metric, values, delta: first !== null && last !== null && values.length > 1 ? last - first : null };
  });
}

/** Higher is better for every metric except errors and losses (used only to word a delta, never to choose). */
const LOWER_IS_BETTER = /(^|_)(mae|rmse|mse|log_loss|brier|brier_score|gap|calibration_gap|median_absolute_error|mape|smape|loss|error)(_|$)/;
export function deltaWording(metric: string, delta: number | null): string {
  if (delta === null) return "not comparable";
  if (delta === 0) return "no change";
  const better = LOWER_IS_BETTER.test(metric) ? delta < 0 : delta > 0;
  return better ? "better" : "worse";
}

export function formatDelta(delta: number | null): string {
  if (delta === null) return "—";
  return `${delta > 0 ? "+" : ""}${delta.toFixed(4)}`;
}

/** Wall-clock time of a run, "—" when it has not both started and ended. */
export function durationText(started: string | null | undefined, ended: string | null | undefined): string {
  if (!started || !ended) return "—";
  const seconds = (new Date(ended).getTime() - new Date(started).getTime()) / 1000;
  if (!Number.isFinite(seconds) || seconds < 0) return "—";
  if (seconds < 90) return `${Math.round(seconds)} s`;
  return `${Math.round(seconds / 60)} min`;
}

/** One line per change of a stored change set; unknown shapes fall back to compact JSON. */
export function describeChanges(changeSet: Record<string, unknown> | null | undefined): string[] {
  const changes = Array.isArray(changeSet?.changes) ? (changeSet.changes as unknown[]) : [];
  return changes.filter((c): c is Record<string, unknown> => !!c && typeof c === "object").map((c) => {
    const kind = String(c.kind ?? "change");
    const label = CHANGE_KINDS.find((k) => k.kind === kind)?.label ?? kind.replaceAll("_", " ");
    const detail = Object.entries(c).filter(([key]) => key !== "kind").map(([key, value]) => `${key} ${typeof value === "string" ? value : JSON.stringify(value)}`).join(", ");
    return detail ? `${label}: ${detail}` : label;
  });
}

/** Lines that differ between two runs' change sets (set difference on the described lines). */
export function changeSetDiff(left: string[], right: string[]): { onlyLeft: string[]; onlyRight: string[]; shared: string[] } {
  const l = new Set(left);
  const r = new Set(right);
  return { onlyLeft: left.filter((x) => !r.has(x)), onlyRight: right.filter((x) => !l.has(x)), shared: left.filter((x) => r.has(x)) };
}

// --- Branch builder ----------------------------------------------------------------------

/** The API's closed set of change kinds and the transform allowlist (a mirror: the API re-validates). */
export const CHANGE_KINDS = [
  { kind: "hyperparameter_override", label: "Hyperparameter override", help: "Fix named parameters of one model family, for example max_depth=4." },
  { kind: "family_include", label: "Include a model family", help: "Add a model family to the search." },
  { kind: "family_exclude", label: "Exclude a model family", help: "Drop a model family from the search. The dummy baselines always stay." },
  { kind: "class_weighting", label: "Class weighting", help: "Weight classes: none, balanced or custom weights per class." },
  { kind: "threshold_objective", label: "Threshold objective", help: "Constraints and error costs used to choose the decision threshold on CV." },
  { kind: "metric_override", label: "Metric override", help: "Select on a different primary metric (a reason is required)." },
  { kind: "feature_transform_add", label: "Add a column treatment", help: "Apply a transform to one column." },
  { kind: "feature_transform_remove", label: "Remove a column treatment", help: "Undo a transform on one column." },
] as const;
export type ChangeKind = (typeof CHANGE_KINDS)[number]["kind"];
export const TRANSFORMS = ["drop_column", "keep", "impute_median", "impute_most_frequent", "datetime_extract"] as const;

export type ChangeDraft = {
  id: number;
  kind: ChangeKind;
  family: string;
  params: string; // key=value per line
  mode: "none" | "balanced" | "custom";
  weights: string; // class=weight per line
  metric: string;
  reason: string;
  column: string;
  transform: (typeof TRANSFORMS)[number];
  constraint: { metric: string; op: ">=" | "<="; value: string };
  costFp: string;
  costFn: string;
};

export function emptyDraft(id: number, kind: ChangeKind = "hyperparameter_override"): ChangeDraft {
  return { id, kind, family: "", params: "", mode: "balanced", weights: "", metric: "", reason: "", column: "", transform: "drop_column", constraint: { metric: "", op: ">=", value: "" }, costFp: "", costFn: "" };
}

/** Typed scalar from text: true/false, a finite number, else the string. */
function scalar(text: string): boolean | number | string {
  const value = text.trim();
  if (value === "true") return true;
  if (value === "false") return false;
  if (value !== "" && Number.isFinite(Number(value))) return Number(value);
  return value;
}

function pairs(text: string): Array<[string, string]> | string {
  const out: Array<[string, string]> = [];
  for (const line of text.split("\n").map((l) => l.trim()).filter(Boolean)) {
    const at = line.indexOf("=");
    if (at < 1 || at === line.length - 1) return `"${line}" must look like name=value.`;
    out.push([line.slice(0, at).trim(), line.slice(at + 1).trim()]);
  }
  return out;
}

/** A draft as the API's change object, or a plain sentence when the text cannot be shaped (never a business rule). */
export function buildChange(draft: ChangeDraft): Record<string, unknown> | string {
  switch (draft.kind) {
    case "hyperparameter_override": {
      const parsed = pairs(draft.params);
      if (typeof parsed === "string") return parsed;
      return { kind: draft.kind, family: draft.family.trim(), parameters: Object.fromEntries(parsed.map(([k, v]) => [k, scalar(v)])) };
    }
    case "family_include":
    case "family_exclude":
      return { kind: draft.kind, family: draft.family.trim() };
    case "class_weighting": {
      if (draft.mode !== "custom") return { kind: draft.kind, mode: draft.mode };
      const parsed = pairs(draft.weights);
      if (typeof parsed === "string") return parsed;
      const weights: Record<string, number> = {};
      for (const [k, v] of parsed) {
        if (!Number.isFinite(Number(v))) return `The weight for "${k}" must be a number.`;
        weights[k] = Number(v);
      }
      return { kind: draft.kind, mode: "custom", weights };
    }
    case "threshold_objective": {
      const c = draft.constraint;
      const change: Record<string, unknown> = { kind: draft.kind };
      if (c.metric.trim() || c.value.trim()) {
        if (!Number.isFinite(Number(c.value)) || c.value.trim() === "") return "Enter a number for the constraint value.";
        change.constraints = [{ metric: c.metric.trim(), op: c.op, value: Number(c.value) }];
      }
      for (const [key, text] of [["cost_false_positive", draft.costFp], ["cost_false_negative", draft.costFn]] as const) {
        if (text.trim() === "") continue;
        if (!Number.isFinite(Number(text))) return "Costs must be numbers.";
        change[key] = Number(text);
      }
      return change;
    }
    case "metric_override":
      return { kind: draft.kind, primary_metric: draft.metric.trim(), reason: draft.reason.trim() };
    case "feature_transform_add":
    case "feature_transform_remove":
      return { kind: draft.kind, column: draft.column.trim(), transform: draft.transform };
  }
}

export type BranchBody = { intent: string; changes: Array<Record<string, unknown>> };

/** The branch request, or the first shaping problem in plain words. */
export function buildBranchBody(intent: string, drafts: ChangeDraft[]): BranchBody | string {
  if (!intent.trim()) return "Say why you are branching (it is recorded with the run).";
  if (!drafts.length) return "Add at least one change.";
  const changes: Array<Record<string, unknown>> = [];
  for (const draft of drafts) {
    const built = buildChange(draft);
    if (typeof built === "string") return built;
    changes.push(built);
  }
  return { intent: intent.trim(), changes };
}

/** The API's `422 invalid_change_set` (reason + path) or its validation message, as shown under the form. */
export function branchProblem(error: unknown): PlainError {
  const body = (error as { body?: { error?: { code?: unknown; message?: unknown; details?: Record<string, unknown> } } } | null)?.body;
  const status = (error as { status?: unknown } | null)?.status;
  const code = typeof body?.error?.code === "string" ? body.error.code : "";
  const details = body?.error?.details ?? {};
  if (code === "invalid_change_set" || code === "new_root_required") {
    const reason = typeof details.reason === "string" ? details.reason : "";
    const where = typeof details.path === "string" ? details.path : "";
    const message = typeof body?.error?.message === "string" ? body.error.message : "";
    return { title: "The API refused this change set", detail: [message, reason && `Reason: ${reason}`, where && `At: ${where}`].filter(Boolean).join(" "), fixable: true };
  }
  if (code === "experiment_not_branchable" || (status === 409 && code.includes("branch"))) {
    return { title: "This run cannot be branched yet", detail: typeof body?.error?.message === "string" ? body.error.message : "Only a completed run with a locked winner can be branched.", fixable: false };
  }
  return mapWizardError(error);
}

// --- Refs and decisions -------------------------------------------------------------------

/** Ref-move and decision failures, with the conflict wording the brief asks for. */
export function mapActionError(error: unknown, noun: "champion" | "decision" | "cancel"): PlainError {
  const status = (error as { status?: unknown } | null)?.status;
  const body = (error as { body?: { error?: { code?: unknown; message?: unknown } } } | null)?.body;
  const code = typeof body?.error?.code === "string" ? body.error.code.toLowerCase() : "";
  const message = typeof body?.error?.message === "string" ? body.error.message : "";
  if (status === 412 || code === "precondition_failed" || code === "ref_version_conflict" || code === "stale_ref_version") {
    return { title: noun === "champion" ? "Someone else moved the champion" : "This changed while you were looking", detail: "Reload to see the current state, then decide again.", fixable: false };
  }
  if (status === 428) return { title: "The page is out of date", detail: "Reload the page and try again.", fixable: false };
  if (code.includes("split_plan_mismatch") || code === "champion_split_plan_mismatch") {
    return { title: "Not comparable with the current champion", detail: "This model was not evaluated on the same split plan as the current champion, so its holdout is different rows. Branch from the champion's run to stay on the same plan.", fixable: false };
  }
  if (code.startsWith("champion_") || code === "ref_target_not_found") {
    return { title: "This model cannot be the champion yet", detail: message || "It needs a locked winner with its final evaluation, and its feature recipe must move with it.", fixable: false };
  }
  if (code === "invalid_decision_transition" || (status === 409 && code !== "proposal_mismatch")) {
    return { title: noun === "decision" ? "This decision was already resolved" : noun === "cancel" ? "This run can no longer be cancelled" : "The request was refused", detail: message || "Reload the list; it was probably resolved by someone else.", fixable: false };
  }
  if (status === 403 && !message) return { title: "You cannot do this here", detail: "Accepting, rejecting and moving refs needs a role that can write ML work in this workspace. The API enforces this; hiding the button is only a convenience.", fixable: false };
  return mapWizardError(error);
}

/** Types only the engine (or its owning service) writes or corrects: the API refuses a person's supersede on them. */
export const ENGINE_OWNED_TYPES = new Set(["winner_locked", "split_plan_created", "ref_initialized", "problem_spec_locked", "ref_moved", "champion_promoted"]);

export type DecisionLike = { effective_state: string; decision_type: string; details?: Record<string, unknown> };
/** A proposed ref move is accepted through the refs endpoint (with its proposal id), any other proposal through /accept. */
export function acceptsViaRefs(decision: DecisionLike): boolean {
  return decision.effective_state === "proposed" && Array.isArray(decision.details?.ref_moves) && proposedMoves(decision).length > 0;
}

const REF_KINDS = new Set(["problem_spec", "dataset", "split_plan", "feature_recipe", "champion_model"]);
export type ProposedMove = { refKind: string; targetId: string };
export function proposedMoves(decision: DecisionLike): ProposedMove[] {
  const moves = Array.isArray(decision.details?.ref_moves) ? (decision.details.ref_moves as unknown[]) : [];
  return moves.flatMap((m) => {
    const move = m as { ref_kind?: unknown; to?: { id?: unknown } } | null;
    return typeof move?.ref_kind === "string" && REF_KINDS.has(move.ref_kind) && isUuid(move.to?.id) ? [{ refKind: move.ref_kind, targetId: move.to.id }] : [];
  });
}

export const DECISION_TYPE_LABEL: Record<string, string> = {
  winner_locked: "Winner locked", split_plan_created: "Split plan created", ref_initialized: "Refs initialised", problem_spec_locked: "Objective locked",
  ref_moved: "Ref moved", champion_promoted: "Champion promoted", experiment_accepted: "Experiment accepted", experiment_rejected: "Experiment rejected",
  proposal_accepted: "Proposal accepted", proposal_rejected: "Proposal rejected", decision_point_resolved: "Decision point resolved", proposal_reverted: "Proposal reverted",
};
