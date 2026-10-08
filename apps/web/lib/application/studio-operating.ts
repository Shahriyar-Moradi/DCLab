/**
 * Operating-point view models (P5.2-UI). Pure: they format and select values the API returned and compute
 * nothing about the model. Sentences come from the server's `what_this_means`; no holdout value exists here.
 */
import type { OperatingChosen, OperatingDetail, OperatingPoint, StudioOperatingPoints } from "./studio-operating-hooks.ts";
import { plainText } from "./command-search.ts";
import { envelope, type PlainError } from "./studio-wizard.ts";

export const REASON_MAX = 4000;
export const CHART_MAX_POINTS = 160;
export const BASIS_LABEL = "on training folds (out-of-fold)";
export const SCORING_COPY = "Choosing a point records a decision only. Scoring still uses the model's locked threshold; applying a chosen point to scoring needs a new model version.";

export const OPERATING_TERMS = {
  threshold: "The score above which a row is flagged as positive. Lower it to flag more rows (catch more positives, more false alarms); raise it to flag fewer.",
  precision: "Of the rows flagged as positive, the share that really are positive.",
  recall: "Of the rows that really are positive, the share that were flagged.",
  oof: "Out-of-fold: each training row is predicted by a model that did not see it, one cross-validation fold at a time. The final evaluation rows are never used.",
  pareto: "A point that no other threshold beats on both precision and recall (and expected cost, when there is a cost matrix).",
} as const;

export const NO_CURVE_TEXT: Record<string, string> = {
  not_applicable: "Operating points apply to two-class problems; this run is not binary classification.",
  not_available: "This run predates operating points, so it has no stored out-of-fold curve. Run it again (branch it) to get one.",
  not_evaluated: "Too few rows of one class in the out-of-fold predictions to compare thresholds reliably.",
};

export type OperatingView =
  | { state: "available" }
  | { state: "empty"; status: string; text: string };

export function operatingView(data: StudioOperatingPoints): OperatingView {
  if (data.status === "available" && (data.points?.length ?? 0) > 0) return { state: "available" };
  const status = data.status === "available" ? "not_available" : data.status;
  const base = NO_CURVE_TEXT[status] ?? data.message;
  const minimum = status === "not_evaluated" ? ` At least ${data.min_class_rows} rows of each class are needed.` : "";
  return { state: "empty", status, text: `${base}${minimum}` };
}

export const fmtRate = (value: number | null | undefined): string => (value == null || !Number.isFinite(value) ? "n/a" : value.toFixed(3));
export const fmtThreshold = (value: number | null | undefined): string => (value == null || !Number.isFinite(value) ? "n/a" : String(Number(value.toPrecision(4))));
export const fmtPercent = (value: number | null | undefined): string => (value == null || !Number.isFinite(value) ? "n/a" : `${(value * 100).toFixed(0)}%`);

export type ChartMode = "precision_recall" | "expected_cost";
export type ChartRow = { threshold: number; precision: number; recall: number; expected_cost?: number };

export function chartMode(data: StudioOperatingPoints): ChartMode {
  const points = data.points ?? [];
  return data.cost_matrix && points.length > 0 && points.every((p) => p.expected_cost != null) ? "expected_cost" : "precision_recall";
}

/** Stored curve to chart rows: thresholds ascending, thinned evenly to a cap but never dropping a marked point. */
export function chartRows(data: StudioOperatingPoints, max = CHART_MAX_POINTS): ChartRow[] {
  const sorted = [...(data.points ?? [])].sort((a, b) => a.threshold - b.threshold);
  const keep = new Set<number>([
    ...(data.pareto ?? []).map((p) => p.threshold),
    ...(data.locked?.threshold != null ? [data.locked.threshold] : []),
    ...(data.chosen ? [data.chosen.threshold] : []),
  ]);
  const step = sorted.length > max ? Math.ceil(sorted.length / max) : 1;
  return sorted
    .filter((p, i) => i % step === 0 || i === sorted.length - 1 || keep.has(p.threshold))
    .map((p) => ({ threshold: p.threshold, precision: p.precision, recall: p.recall, ...(p.expected_cost != null ? { expected_cost: p.expected_cost } : {}) }));
}

/** Horizontal constraint lines the chosen objective declared (precision / recall only: the chart's own axes). */
export function constraintLines(data: StudioOperatingPoints): Array<{ metric: "precision" | "recall"; op: string; value: number; label: string }> {
  const out: Array<{ metric: "precision" | "recall"; op: string; value: number; label: string }> = [];
  for (const c of data.chosen?.objective?.constraints ?? []) {
    if (c.metric === "precision" || c.metric === "recall") out.push({ metric: c.metric, op: c.op, value: c.value, label: `${c.metric} ${c.op} ${c.value}` });
  }
  return out;
}

export function chartSummary(data: StudioOperatingPoints): string {
  const mode = chartMode(data);
  const n = data.points?.length ?? 0;
  const marks = [data.locked?.threshold != null ? `locked threshold ${fmtThreshold(data.locked.threshold)}` : null, data.chosen ? `chosen threshold ${fmtThreshold(data.chosen.threshold)}` : null].filter(Boolean).join(", ");
  return `${mode === "expected_cost" ? "Expected cost per row" : "Precision and recall"} by threshold across ${n} candidate thresholds ${BASIS_LABEL}${marks ? `; ${marks}` : ""}. The points table below has the same values.`;
}

export type PointRow = { key: string; threshold: number; point: OperatingPoint; tags: string[] };

/** Table rows: the Pareto points plus the locked and chosen thresholds, each tagged. Values are the API's. */
export function pointRows(data: StudioOperatingPoints): PointRow[] {
  const byThreshold = new Map<number, PointRow>();
  const add = (point: OperatingPoint, tag?: string) => {
    const row = byThreshold.get(point.threshold) ?? { key: String(point.threshold), threshold: point.threshold, point, tags: [] };
    if (tag && !row.tags.includes(tag)) row.tags.push(tag);
    byThreshold.set(point.threshold, row);
  };
  for (const p of data.pareto ?? []) add(p, "Pareto");
  const lockedPoint = data.locked?.point ?? (data.locked?.threshold != null ? data.points?.find((p) => p.threshold === data.locked?.threshold) : undefined);
  if (lockedPoint) add(lockedPoint, "Locked");
  const chosenPoint = data.chosen?.point ?? (data.chosen ? data.points?.find((p) => p.threshold === data.chosen?.threshold) : undefined);
  if (chosenPoint) add(chosenPoint, "Chosen");
  return [...byThreshold.values()].sort((a, b) => a.threshold - b.threshold);
}

/** The point shown in the sentence panel: the picked threshold if any, else the chosen, else the locked point. */
export function focusedPoint(data: StudioOperatingPoints, picked: number | null): { label: string; detail: OperatingDetail | null; point: OperatingPoint | null } {
  const find = (t: number) => data.points?.find((p) => p.threshold === t) ?? null;
  const detailOf = (t: number): OperatingDetail | null => {
    if (data.chosen?.point?.threshold === t) return data.chosen.point;
    if (data.locked?.point?.threshold === t) return data.locked.point;
    return data.pareto?.find((p) => p.threshold === t) ?? null;
  };
  const label = (t: number) => (data.chosen?.threshold === t ? "Chosen point" : data.locked?.threshold === t ? "Locked point" : "Selected point");
  if (picked != null) return { label: label(picked), detail: detailOf(picked), point: detailOf(picked) ?? find(picked) };
  if (data.chosen) return { label: "Chosen point", detail: data.chosen.point ?? detailOf(data.chosen.threshold), point: data.chosen.point ?? find(data.chosen.threshold) };
  if (data.locked?.threshold != null) return { label: "Locked point", detail: data.locked.point ?? null, point: data.locked.point ?? find(data.locked.threshold) };
  return { label: "Point", detail: null, point: null };
}

/** The server's sentence; a plain restatement of its own fields when a point has none (never computed here). */
export function pointSentence(focus: { detail: OperatingDetail | null; point: OperatingPoint | null }): string {
  if (focus.detail?.what_this_means) return focus.detail.what_this_means;
  const p = focus.point;
  if (!p) return "";
  return `At threshold ${fmtThreshold(p.threshold)}: flagged share ${fmtRate(p.flagged_share)}, recall ${fmtRate(p.recall)}, precision ${fmtRate(p.precision)} (out-of-fold training predictions).`;
}

export function intervalText(label: string, interval: { low: number; high: number } | null | undefined): string | null {
  return interval ? `${label} 95% interval ${fmtRate(interval.low)} to ${fmtRate(interval.high)}` : null;
}

export function foldSpreadText(detail: OperatingDetail | null): string[] {
  const s = detail?.fold_spread;
  if (!s) return [];
  const lines: string[] = [];
  if (s.recall_min != null && s.recall_max != null) lines.push(`Recall across ${s.recall_folds} of ${s.folds} folds: ${fmtRate(s.recall_min)} to ${fmtRate(s.recall_max)}`);
  if (s.precision_min != null && s.precision_max != null) lines.push(`Precision across ${s.precision_folds} of ${s.folds} folds: ${fmtRate(s.precision_min)} to ${fmtRate(s.precision_max)}`);
  if (lines.length && s.includes_folds_outside_curve) lines.push("Time-ordered folds: the curve is the most recent fold, the range spans every fold.");
  return lines;
}

// --- The "Use this point" action ---------------------------------------------------------------

export type ChooseGate = { allowed: boolean; reason: string | null };

/** Whether the action shows enabled. A convenience only: the API refuses viewers, agents and tokens itself. */
export function chooseGate(data: StudioOperatingPoints | undefined, canWriteMl: boolean): ChooseGate {
  if (!data || data.status !== "available") return { allowed: false, reason: "There is no operating curve to choose from." };
  if (!canWriteMl) return { allowed: false, reason: "Your role can read operating points but not choose one. Ask a workspace member who can write ML work." };
  return { allowed: true, reason: null };
}

export function reasonProblem(reason: string): string | null {
  const text = reason.trim();
  if (!text) return "Write why you are choosing this point; it is recorded with the decision.";
  if (text.length > REASON_MAX) return `The reason is limited to ${REASON_MAX} characters.`;
  return null;
}

/** A threshold is selectable only when it is exactly one of the stored curve's candidates. */
export function onCurve(data: StudioOperatingPoints, threshold: number): boolean {
  return (data.points ?? []).some((p) => p.threshold === threshold);
}

/** Default picker value: the chosen, else the locked threshold, but only if listed; otherwise the first listed one. */
export function defaultThreshold(data: StudioOperatingPoints): number {
  for (const t of [data.chosen?.threshold, data.locked?.threshold]) if (t != null && onCurve(data, t)) return t;
  return data.points?.[0]?.threshold ?? Number.NaN;
}

export type Pick =
  | { kind: "threshold"; threshold: number }
  | { kind: "objective"; goal: string; metric: string; op: ">=" | "<="; value: string };

export const OBJECTIVE_GOALS = ["f1", "balanced_accuracy", "accuracy", "precision", "recall", "specificity", "expected_cost"] as const;
export const CONSTRAINT_METRICS = ["precision", "recall", "specificity", "f1", "accuracy", "balanced_accuracy", "flagged_share"] as const;

/** Request body, or the first problem with the person's own answers. */
export function buildChoice(pick: Pick, reason: string): { body: import("./studio-operating-hooks.ts").ChoiceBody } | { problem: string } {
  const bad = reasonProblem(reason);
  if (bad) return { problem: bad };
  if (pick.kind === "threshold") {
    if (!Number.isFinite(pick.threshold)) return { problem: "Pick a threshold from the list." };
    return { body: { threshold: pick.threshold, reason: reason.trim() } };
  }
  const constraints: Array<{ metric: string; op: ">=" | "<="; value: number }> = [];
  if (pick.value.trim()) {
    const value = Number(pick.value);
    if (!Number.isFinite(value) || value < 0 || value > 1) return { problem: "A constraint value is a share between 0 and 1." };
    constraints.push({ metric: pick.metric, op: pick.op, value });
  }
  if (["precision", "recall", "specificity"].includes(pick.goal) && !constraints.some((c) => c.metric !== pick.goal)) {
    return { problem: `Maximising ${pick.goal} alone is degenerate. Add a constraint on another metric.` };
  }
  return { body: { objective: { goal: pick.goal, constraints }, reason: reason.trim() } };
}

export type ChooseOutcome = PlainError & { closest?: { threshold: number; summary: string } | null };

export function mapChooseError(error: unknown): ChooseOutcome {
  const status = (error as { status?: unknown } | null)?.status;
  const { code, message, details } = envelope((error as { body?: unknown } | null)?.body);
  if (code === "threshold_not_on_curve") return { title: "That threshold is not on the stored curve", detail: "Pick one of the listed thresholds; the curve may have changed. Reload and choose again.", fixable: true };
  if (code === "objective_infeasible") {
    const closest = details.closest as Partial<OperatingPoint> | null | undefined;
    const summary = closest && typeof closest.threshold === "number"
      ? `Closest candidate: threshold ${fmtThreshold(closest.threshold)} (recall ${fmtRate(closest.recall)}, precision ${fmtRate(closest.precision)}), which does not meet every constraint.`
      : "";
    return { title: "No threshold meets every constraint", detail: `On out-of-fold predictions no candidate threshold satisfies them all. Relax a constraint or pick a point directly. ${summary}`.trim(), fixable: true, closest: closest && typeof closest.threshold === "number" ? { threshold: closest.threshold, summary } : null };
  }
  if (code === "cost_matrix_required") return { title: "Expected cost needs a cost matrix", detail: "This run declares no cost matrix, so cost cannot be the goal.", fixable: true };
  if (code === "idempotency_key_conflict" || status === 409 && code.includes("idempotency")) return { title: "This request was already used for a different choice", detail: "Reload the page and choose again.", fixable: false };
  if (code.startsWith("operating_points_")) return { title: "Operating points are not available for this run", detail: plainText(message, 300) || NO_CURVE_TEXT[code.replace("operating_points_", "")] || "Reload the page.", fixable: false };
  if (code === "human_session_required" || code === "service_token_not_permitted") return { title: "Only people can choose", detail: "An operating point is chosen by a signed-in person, not by an agent or a token.", fixable: false };
  if (status === 403) return { title: "You cannot choose here", detail: "Choosing needs a role that can write ML work in this workspace.", fixable: false };
  if (status === 404) return { title: "Not found", detail: "The experiment is not in this workspace.", fixable: false };
  if (status === 422) return { title: "The choice was not valid", detail: plainText(message, 300) || "Check the reason and the values.", fixable: true };
  if (status === 409) return { title: "The request was refused", detail: plainText(message, 300) || "Reload the page and try again.", fixable: false };
  return { title: "Something went wrong", detail: plainText(message, 300) || (error instanceof Error ? plainText(error.message, 300) : "Try again."), fixable: false };
}

// --- Model card line ---------------------------------------------------------------------------

export type ChosenSummary = { threshold: string; method: string; reason: string; when: string; by: string | null; decisionId: string; note: string | null; sentence: string };

/** The chosen point for the model card; null when none exists. The reason is untrusted: callers render text. */
export function chosenSummary(data: StudioOperatingPoints | undefined): ChosenSummary | null {
  const c: OperatingChosen | null | undefined = data?.chosen;
  if (!data || data.status !== "available" || !c) return null;
  return {
    threshold: fmtThreshold(c.threshold),
    method: c.method === "objective" ? "re-solved from an objective" : "picked directly",
    reason: c.rationale,
    when: c.recorded_at,
    by: c.chosen_by_user_id ?? null,
    decisionId: c.decision_id,
    note: c.curve_changed ? "The stored curve changed after this choice." : null,
    sentence: c.point?.what_this_means ?? "",
  };
}
