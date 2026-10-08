/**
 * Data page logic (P4.1-C), pure so it runs under `npm run test:components`:
 * which dataset version is shown, what uses each version (from the project graph),
 * and how a profile column reads (rule role vs role used, percentages).
 */

export type GraphNodeRefLike = { kind: string; id: string };
export type GraphEdgeLike = { from: GraphNodeRefLike; to: GraphNodeRefLike; relation: string; attribute?: boolean };
export type UsedBy = { split_plan: number; experiment: number; model_version: number };

/** Nodes that use a dataset version: split plans that partition it, runs and models that train on it. */
export function usedBy(edges: GraphEdgeLike[], datasetId: string): UsedBy {
  const seen = new Set<string>();
  const out: UsedBy = { split_plan: 0, experiment: 0, model_version: 0 };
  for (const edge of edges) {
    if (edge.attribute || edge.to.kind !== "dataset_version" || edge.to.id !== datasetId) continue;
    if (edge.relation !== "uses_dataset" && edge.relation !== "partitions") continue;
    const kind = edge.from.kind as keyof UsedBy;
    if (!(kind in out) || seen.has(`${kind}:${edge.from.id}`)) continue;
    seen.add(`${kind}:${edge.from.id}`);
    out[kind] += 1;
  }
  return out;
}

/** Per-run prepared tables (targets of `prepared_as` attribute edges): versions, but not uploads. */
export function preparedIds(edges: GraphEdgeLike[]): Set<string> {
  return new Set(edges.filter((edge) => edge.relation === "prepared_as" && edge.to.kind === "dataset_version").map((edge) => edge.to.id));
}

export function usedByText(used: UsedBy): string {
  const parts = [
    used.split_plan ? `${used.split_plan} test design${used.split_plan === 1 ? "" : "s"}` : "",
    used.experiment ? `${used.experiment} run${used.experiment === 1 ? "" : "s"}` : "",
    used.model_version ? `${used.model_version} model${used.model_version === 1 ? "" : "s"}` : "",
  ].filter(Boolean);
  return parts.length ? parts.join(" · ") : "Not used yet";
}

/** The version to show: the one in the URL, else the project's dataset ref, else the newest. */
export function pickDatasetId(ids: string[], requested: string | null | undefined, currentRef: string | null | undefined): string | null {
  if (requested && ids.includes(requested)) return requested;
  if (currentRef && ids.includes(currentRef)) return currentRef;
  return ids[0] ?? null;
}

export function percent(fraction: number | null | undefined): string {
  if (fraction === null || fraction === undefined || !Number.isFinite(fraction)) return "—";
  const value = fraction * 100;
  return `${value === 0 || value >= 10 ? value.toFixed(0) : value.toFixed(1)}%`;
}

/** `ignored_free_text` → `ignored (free text)`; null → em dash. */
export function roleLabel(role: string | null | undefined): string {
  if (!role) return "—";
  return role === "ignored_free_text" ? "ignored (free text)" : role.replaceAll("_", " ");
}

export type RoleComparison = "same" | "differs" | "no_run" | "no_rule";

/** Whether the run used the rule's role; a difference names who changed it (`role_source`). */
export function compareRoles(rule: string | null | undefined, used: string | null | undefined): RoleComparison {
  if (!used) return "no_run";
  if (!rule) return "no_rule";
  return rule === used ? "same" : "differs";
}

// --- V7-A2: the file summary, why a column is used or not, and the data checks in plain words ---

export type ProfileColumnLike = {
  name: string; physical_dtype: string; rule_role?: string | null; role_used?: string | null; role_source?: string | null; missing_fraction?: number | null;
  unique_count?: number | null; leakage_excluded: boolean; leakage_risk?: string | null;
};
export type ColumnUse = { used: "yes" | "no" | "target" | "unknown"; reason: string | null };

const PREDICTOR_WORDS: Record<string, string> = { numerical: "A number the model can use", categorical: "A category the model can use", datetime: "A date the model can use", boolean: "A yes/no value the model can use" };

/**
 * Whether a column is a predictor and why, from the role the run used and the leakage plan. Nothing is guessed:
 * without a finished run (`hasRun` false) only the target and ID columns are stated, because the rule role ignores the leakage plan.
 */
export function columnUse(c: ProfileColumnLike, hasRun = true): ColumnUse {
  const role = c.role_used ?? c.rule_role ?? null;
  if (role === "target") return { used: "target", reason: "This is what we predict" };
  if (role === "identifier") return { used: "no", reason: "ID column, not a predictor" };
  if (!hasRun) return { used: "unknown", reason: "Decided when the first run starts" };
  if (c.leakage_excluded) {
    return { used: "no", reason: c.leakage_risk === "none" ? "Left out by the plan: not a predictor" : "Left out by the leakage check: it may give away the answer" };
  }
  if (!role) return { used: "unknown", reason: null };
  if (role === "ignored_free_text") {
    return { used: "no", reason: c.role_source === "branch_change_set" ? "Left out by a change you made" : "Free text, or too empty or constant to use" };
  }
  if (c.unique_count === 1) return { used: "unknown", reason: "Only one distinct value (blanks not counted), so it adds nothing" };
  return { used: "yes", reason: Object.hasOwn(PREDICTOR_WORDS, role) ? PREDICTOR_WORDS[role] : null };
}

/** Columns the leakage plan left out because they may give away the answer (ID, entity and group columns are not leakage). */
export function leakageExcluded<T extends ProfileColumnLike>(columns: T[]): T[] {
  return columns.filter((c) => c.leakage_excluded && c.role_used !== "identifier" && c.rule_role !== "identifier" && c.leakage_risk !== "none");
}

/** A column type in plain words: the role when known (number, category, date), else from the stored type name. */
export function typeLabel(dtype: string, role?: string | null): string {
  if (role === "numerical") return "number";
  if (role === "categorical") return "category";
  if (role === "datetime") return "date";
  if (role === "boolean") return "yes / no";
  const d = dtype.toLowerCase();
  if (/bool/.test(d)) return "yes / no";
  if (/date|time/.test(d)) return "date";
  if (/int|float|double|decimal|number/.test(d)) return "number";
  if (/object|str|text|categor/.test(d)) return "text";
  return dtype;
}

export type SummaryItem = { key: string; label: string; value: string };
type ProfileLike = { statistics_status: string; split_plan: { training_row_count: number; target_column: string } | null; experiment: unknown | null; columns: ProfileColumnLike[] };

/** The strip above the tabs. Rows and columns come from the file; the rest from the profile, and only when it was computed. */
export function fileSummary(dataset: { row_count: number; column_count: number }, profile: ProfileLike | null | undefined, clean: (v: string) => string = (v) => v): SummaryItem[] {
  const out: SummaryItem[] = [
    { key: "rows", label: "Rows", value: dataset.row_count.toLocaleString("en-GB") },
    { key: "cols", label: "Columns", value: String(dataset.column_count) },
  ];
  if (profile?.statistics_status === "computed" && profile.split_plan) {
    out.push({ key: "train", label: "Rows used for training", value: profile.split_plan.training_row_count.toLocaleString("en-GB") });
    out.push({ key: "target", label: "What we predict", value: clean(profile.split_plan.target_column) });
  }
  if (profile?.experiment) {
    const uses = profile.columns.map((c) => columnUse(c, true));
    const used = uses.filter((u) => u.used === "yes").length;
    const left = uses.filter((u) => u.used === "no").length;
    out.push({ key: "used", label: "Columns used", value: `${used} used, ${left} left out` });
  }
  return out;
}

export type CheckRow = { key: string; label: string; result: "ok" | "info" | "attention" | "unknown"; text: string };
type FindingBrief = { check: string; status: string; message: string };
export type CheckContext = { findingsLoading?: boolean; findingsError?: boolean };

const findingResult = (status: string): CheckRow["result"] => (status === "pass" ? "ok" : status === "not_evaluated" ? "unknown" : "attention");

/** Duplicates, missing values and leakage in plain words, from the training-row profile and the newest run's trust checks. */
export function dataCheckRows(
  profile: ProfileLike & { columns: Array<ProfileColumnLike & { leakage_reason?: string | null }> },
  findings: FindingBrief[] | null | undefined,
  clean: (v: string, max?: number) => string = (v) => v,
  ctx: CheckContext = {},
): CheckRow[] {
  const computed = profile.statistics_status === "computed";
  const missing = profile.columns.filter((c) => (c.missing_fraction ?? 0) > 0).sort((a, b) => (b.missing_fraction ?? 0) - (a.missing_fraction ?? 0));
  const dup = findings?.find((f) => f.check === "duplicate_rows");
  const leak = findings?.find((f) => f.check === "target_leakage");
  const excluded = leakageExcluded(profile.columns);
  const top = missing.slice(0, 4).map((c) => `${clean(c.name, 40)} is empty for ${percent(c.missing_fraction)} of rows`);
  const none = !profile.experiment ? "Checked inside each run. No finished run yet."
    : ctx.findingsLoading ? "Loading the run's checks…" : ctx.findingsError ? "The run's checks could not be loaded." : "This run recorded no such check.";
  const left = excluded.length ? `Left out so they cannot give away the answer: ${excluded.slice(0, 6).map((c) => clean(c.name, 40)).join(", ")}${excluded.length > 6 ? ` and ${excluded.length - 6} more` : ""}.` : "";
  return [
    dup ? { key: "dup", label: "Duplicates", result: findingResult(dup.status), text: clean(dup.message, 300) }
      : { key: "dup", label: "Duplicates", result: "unknown", text: none },
    computed
      ? { key: "missing", label: "Missing values", result: missing.length ? "info" : "ok", text: missing.length ? `${top.join("; ")}${missing.length > 4 ? `; ${missing.length - 4} more columns have some empty values` : ""}. Counted on training rows only.` : "No column has empty values in the training rows." }
      : { key: "missing", label: "Missing values", result: "unknown", text: profile.statistics_status === "unavailable" ? "Withheld: the test design's row map could not be verified." : "Counted after the first run fixes the test design." },
    leak ? { key: "leak", label: "Leakage", result: findingResult(leak.status), text: [clean(leak.message, 300), left].filter(Boolean).join(" ") || "See the trust checks below." }
      : excluded.length ? { key: "leak", label: "Leakage", result: "attention", text: left }
        : { key: "leak", label: "Leakage", result: "unknown", text: none },
  ];
}
