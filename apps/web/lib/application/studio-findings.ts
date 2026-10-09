/**
 * Findings panel logic (P4.10-UI): pure, so it runs under `npm run test:components`.
 *
 * Every number comes from `GET /v1/experiments/{id}/findings`; this module only orders, labels and formats
 * them (the message is shown exactly as the API returns it). A recommendation maps to a Branch pre-fill only
 * when the closed change-kind set of P4.4-A can express it; the API still validates the branch itself.
 */
import { isUuid } from "./command-search.ts";
import { CHANGE_KINDS, TRANSFORMS, emptyDraft, type ChangeDraft, type ChangeKind } from "./studio-compare.ts";

export type FindingLike = {
  check: string; status: string; severity: string; message: string;
  evidence?: Record<string, unknown>; recommendation_kind?: string | null;
};
export type FindingsLike = {
  investigated: boolean;
  checks?: FindingLike[];
  summary?: { passed?: number; warnings?: number; failures?: number; not_evaluated?: number };
};

export type FindingTone = "ok" | "warn" | "crit" | "gray";

/** Worst first: fail, warning, not evaluated, pass. Unknown statuses sort between warning and pass. */
const STATUS_RANK: Record<string, number> = { fail: 0, warning: 1, not_evaluated: 3, pass: 4 };
const SEVERITY_RANK: Record<string, number> = { critical: 0, error: 1, warning: 2, info: 3 };

export function findingTone(status: string): FindingTone {
  if (status === "fail") return "crit";
  if (status === "warning") return "warn";
  if (status === "pass") return "ok";
  return "gray";
}

/** Status as text, so colour is never the only signal. */
export function findingStatusLabel(status: string, severity: string): string {
  if (status === "fail") return severity === "critical" ? "Failed (critical)" : "Failed";
  if (status === "warning") return "Warning";
  if (status === "pass") return "Passed";
  if (status === "not_evaluated") return "Not checked";
  return status.replaceAll("_", " ");
}

export function sortFindings<T extends FindingLike>(checks: T[]): T[] {
  return checks.map((c, i) => ({ c, i })).sort((a, b) => {
    const s = (STATUS_RANK[a.c.status] ?? 2) - (STATUS_RANK[b.c.status] ?? 2);
    if (s) return s;
    const v = (SEVERITY_RANK[a.c.severity] ?? 4) - (SEVERITY_RANK[b.c.severity] ?? 4);
    return v || a.i - b.i;
  }).map((x) => x.c);
}

/** Findings that need attention (warning or fail): the badge count. */
export function attentionCount(findings: FindingsLike | null | undefined): number {
  return (findings?.checks ?? []).filter((c) => c.status === "warning" || c.status === "fail").length;
}

export type FindingsState = "pending" | "not_computed" | "all_passed" | "attention";
/** `not_computed` = the run has no recorded investigation (older run, not finished, or the check job did not store one). */
export function findingsState(findings: FindingsLike | null | undefined): FindingsState {
  if (!findings) return "pending";
  if (!findings.investigated || !(findings.checks ?? []).length) return "not_computed";
  return (findings.checks ?? []).every((c) => c.status === "pass") ? "all_passed" : "attention";
}

export const CHECK_LABEL: Record<string, string> = {
  target_leakage: "Leakage", overfit_gap: "Overfitting gap", duplicate_rows: "Duplicates", class_imbalance: "Class imbalance", implausible_score: "Too good to be true",
  fold_instability: "Fold stability", calibration: "Calibration", subgroup_gap: "Weak group", multicollinearity: "Repeated columns",
  feature_drift: "Drift between train and test", temporal_shift: "Time order of the split", missingness_shift: "Missing values: train vs test",
  contamination: "Train rows in the test set", time_travel: "Future rows in the features", new_feature: "One new column's jump",
};
/** Plain name of a check; own keys only (a check named `__proto__` is shown as text). */
export const checkLabel = (check: string) => (Object.hasOwn(CHECK_LABEL, check) ? CHECK_LABEL[check] : check.replaceAll("_", " "));

export const RECOMMENDATION_TEXT: Record<string, string> = {
  review_columns: "Review the flagged columns: confirm each one is known at prediction time, and drop it if not.",
  investigate_leakage: "Investigate the strongest columns for leakage before trusting this model.",
  regularize: "Try a more regularized model, for example a smaller depth or stronger penalty.",
  simpler_model: "Prefer the simpler model: it scores about the same in cross-validation.",
  deduplicate: "Remove duplicate records before splitting. This is a data change: upload a cleaned file as a new dataset version.",
  class_weights: "Train with class weights so the rare class is not ignored.",
  collect_more_data: "Collect more examples of the rare class. This is a data change, not a branch.",
  calibrate: "Recalibrate the scores (for example Platt or isotonic scaling) before reading them as chances; decisions at the chosen threshold are unaffected.",
  review_subgroups: "Look at the weakest group: check whether it has too few rows or behaves differently, and collect more examples of it if it matters.",
  drop_correlated: "Drop one column of each repeated pair; the model loses almost nothing and becomes easier to explain.",
  review_split: "Review how the rows were split (by time or by group) and what differs between training and test rows. This is a split or data decision, not a branch.",
};

// --- evidence formatting ------------------------------------------------------------------

const MAX_TEXT = 300;
const clip = (text: string) => (text.length > MAX_TEXT ? `${text.slice(0, MAX_TEXT)}…` : text);

function number(value: number): string {
  if (Number.isInteger(value)) return value.toLocaleString("en-US");
  const abs = Math.abs(value);
  return abs >= 100 ? value.toFixed(1) : Number(value.toPrecision(3)).toString();
}

/** Unit-aware text for one evidence value: `*_fraction` as percent, counts with separators, lists joined. */
export function formatEvidenceValue(key: string, value: unknown): string {
  if (value === null || value === undefined) return "n/a";
  if (typeof value === "boolean") return value ? "yes" : "no";
  if (typeof value === "number") {
    if (!Number.isFinite(value)) return "n/a";
    if (key.endsWith("_fraction")) return `${(value * 100).toFixed(1)}%`;
    return number(value);
  }
  if (typeof value === "string") return clip(key.endsWith("_reason") || key === "direction" ? value.replaceAll("_", " ") : value);
  if (Array.isArray(value)) {
    if (value.every((v) => v === null || ["string", "number", "boolean"].includes(typeof v))) {
      const shown = value.slice(0, 10).map((v) => formatEvidenceValue("", v)).join(", ");
      return clip(value.length > 10 ? `${shown} and ${value.length - 10} more` : shown || "none");
    }
    return `${value.length} item${value.length === 1 ? "" : "s"}: ${clip(JSON.stringify(value))}`;
  }
  return clip(JSON.stringify(value));
}

export function evidenceLabel(key: string): string {
  const text = key.replaceAll("_", " ").replace(/\bholdout\b/gi, "final test set");
  return text.charAt(0).toUpperCase() + text.slice(1);
}

export type EvidenceRow = { key: string; label: string; value: string };
/** `holdout_comparison` (test-row statistics, shown to people only) is listed entry by entry. */
export function evidenceRows(evidence: Record<string, unknown> | undefined | null): EvidenceRow[] {
  return Object.entries(evidence ?? {}).flatMap(([key, value]) => {
    if (key === "holdout_comparison" && value && typeof value === "object" && !Array.isArray(value)) {
      return Object.entries(value as Record<string, unknown>).map(([inner, v]) => ({
        key: `${key}.${inner}`, label: `Test rows: ${evidenceLabel(inner).toLowerCase()}`, value: formatEvidenceValue(inner, v),
      }));
    }
    return [{ key, label: evidenceLabel(key), value: formatEvidenceValue(key, value) }];
  });
}

// --- recommendation -> Branch pre-fill ----------------------------------------------------

const KIND_SET = new Set<string>(CHANGE_KINDS.map((k) => k.kind));
const MODES = new Set(["none", "balanced", "custom"]);
const FIELD_MAX = 120;

export type BranchPrefill = { intent: string; draft: ChangeDraft };

function firstString(evidence: Record<string, unknown> | undefined, keys: string[]): string {
  for (const key of keys) {
    const value = evidence?.[key];
    const first = Array.isArray(value) ? value[0] : value;
    if (typeof first === "string" && first.trim() && first.length <= FIELD_MAX) return first.trim();
  }
  return "";
}

type PrefillParams = { prefill: ChangeKind; intent: string; family?: string; column?: string; mode?: string; transform?: string };

/** The Branch change a finding's recommendation maps to, or null (data changes and unknown kinds show text only). */
export function prefillFor(finding: FindingLike): PrefillParams | null {
  const ev = finding.evidence;
  const why = `Address the trust check: ${checkLabel(finding.check)}.`;
  switch (finding.recommendation_kind) {
    case "class_weights": return { prefill: "class_weighting", mode: "balanced", intent: why };
    case "regularize": return { prefill: "hyperparameter_override", family: firstString(ev, ["winner_family"]), intent: why };
    case "simpler_model": return { prefill: "family_exclude", family: firstString(ev, ["winner_family"]), intent: why };
    case "review_columns":
    case "investigate_leakage":
      return { prefill: "feature_transform_add", transform: "drop_column", column: firstString(ev, ["risky_columns", "flagged_columns", "excluded_columns", "added_feature", "moved_columns"]), intent: why };
    case "drop_correlated":
      return { prefill: "feature_transform_add", transform: "drop_column", column: firstString(ev, ["drop_candidates"]), intent: why };
    default: return null;
  }
}

/** Same-origin path to the experiment page with the pre-filled Branch form, or null (invalid id, no mapping). */
export function branchPrefillHref(projectId: string, experimentId: string, finding: FindingLike): string | null {
  const p = prefillFor(finding);
  if (!p || !isUuid(projectId) || !isUuid(experimentId)) return null;
  const query = new URLSearchParams();
  for (const [key, value] of Object.entries(p)) if (value) query.set(key, String(value));
  return `/projects/${projectId}/experiments/${experimentId}?${query.toString()}#branch`;
}

/** Validate query params on the way in: closed kind set, bounded text; anything else is ignored. */
export function parseBranchPrefill(params: { get(name: string): string | null }): BranchPrefill | null {
  const kind = params.get("prefill");
  if (!kind || !KIND_SET.has(kind)) return null;
  const text = (name: string) => (params.get(name) ?? "").slice(0, FIELD_MAX);
  const draft = emptyDraft(0, kind as ChangeKind);
  draft.family = text("family");
  draft.column = text("column");
  const mode = params.get("mode");
  if (mode && MODES.has(mode)) draft.mode = mode as ChangeDraft["mode"];
  const transform = params.get("transform");
  if (transform && (TRANSFORMS as readonly string[]).includes(transform)) draft.transform = transform as ChangeDraft["transform"];
  return { intent: text("intent"), draft };
}
